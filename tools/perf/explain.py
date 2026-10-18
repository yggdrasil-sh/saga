"""EXPLAIN (ANALYZE, BUFFERS) der teuersten Saga-Abfragen mit den Daten von gen.py. Laeuft IM Saga-Container:

    docker exec -i -w /app <saga-container> python - '<PERF-json>' < tools/perf/explain.py

Gibt je Abfrage Ausfuehrungszeit, Plan-Knoten (Seq Scan = fehlender Index?) und gelesene Puffer aus."""
import json
import sys
import time

sys.path.insert(0, "/app")
import db  # noqa: E402

I = json.loads(sys.argv[1])
A, B = "perf-a", "perf-b"
META = "id, title, icon, parent_id, updated, tags, pinned, is_template, kind"
Q = [
    ("api_notes: Seitenbaum (pages.api_notes)",
     f"SELECT {META}, archived, locked FROM note WHERE uid=%s AND deleted_at IS NULL ORDER BY sort NULLS FIRST, created", (A,)),
    ("_purge_old: Papierkorb-Frist (bei JEDEM /api/notes)",
     "SELECT id FROM note WHERE uid=%s AND deleted_at IS NOT NULL AND deleted_at < %s", (A, time.time() - 30 * 86400)),
    ("unread_notes: Kommentar-Punkte im Baum",
     "SELECT DISTINCT r.note_id FROM note_comment r WHERE r.owner_uid IN (%s) AND r.thread_id IS NULL AND r.resolved_at IS NULL AND EXISTS "
     "(SELECT 1 FROM note_comment x WHERE (x.id=r.id OR x.thread_id=r.id) AND (x.uid IS NULL OR x.uid<>%s) AND x.created > "
     "COALESCE((SELECT s.seen_at FROM note_comment_seen s WHERE s.uid=%s AND s.thread_id=r.id), 0))", (A, A, A)),
    ("_db_rows: Zeilen der groessten DB inkl. body (app_api._db_rows)",
     "SELECT id, title, icon, props, updated, created, cover, body, format FROM note WHERE uid=%s AND parent_id=%s "
     "AND deleted_at IS NULL AND is_template=0 ORDER BY sort NULLS FIRST, created", (A, I["db_big"])),
    ("Access.tree: ganzer Baum des Eigentuemers fuer B (sharing.Access.tree)",
     "SELECT n.id, n.parent_id, (n.deleted_at IS NOT NULL OR n.archived IS NOT NULL OR n.is_template<>0) FROM note n WHERE n.uid=%s "
     "ORDER BY n.sort NULLS FIRST, n.created", (A,)),
    ("_note_search: alle Seiten mit body (pages._note_search)",
     f"SELECT {META}, body, format FROM note WHERE uid=%s AND deleted_at IS NULL AND archived IS NULL ORDER BY updated DESC", (A,)),
    ("backlinks: instr(body) ueber alle Seiten (pages.api_note_backlinks)",
     f"SELECT {META} FROM note WHERE uid=%s AND id<>%s AND format='blocks' AND deleted_at IS NULL AND strpos(body, %s) > 0 ORDER BY updated DESC",
     (A, I["page_normal"], f'/notes#{I["page_normal"]}\\"')),
    ("note_chain: Kette bis zur Wurzel (Tiefe 6)",
     "WITH RECURSIVE t(id, parent_id, bad, depth) AS (SELECT id, parent_id, (deleted_at IS NOT NULL OR archived IS NOT NULL OR is_template<>0), 0 "
     "FROM note WHERE id=%s AND uid=%s UNION ALL SELECT n.id, n.parent_id, (n.deleted_at IS NOT NULL OR n.archived IS NOT NULL OR n.is_template<>0), "
     "t.depth + 1 FROM note n JOIN t ON n.id=t.parent_id WHERE n.uid=%s AND t.depth < 64) SELECT id, bad FROM t ORDER BY depth",
     (I.get("page_deep") or I["page_normal"], A, A)),
    ("NOTE_MAX_COUNT: COUNT(*) bei jedem Anlegen (pages.api_note_add)", "SELECT COUNT(*) FROM note WHERE uid=%s", (A,)),
    ("announce_row: DB der Zeile (notes.announce_row)",
     "SELECT d.id FROM note n JOIN note d ON d.id=n.parent_id AND d.uid=n.uid WHERE n.id=%s AND n.uid=%s AND d.kind='database'",
     (I["page_normal"], A)),
    ("comments.counts: ungelesen je Seite",
     "SELECT COUNT(*) FROM note_comment r WHERE r.note_id=%s AND r.thread_id IS NULL AND r.resolved_at IS NULL AND EXISTS ("
     " SELECT 1 FROM note_comment x WHERE (x.id=r.id OR x.thread_id=r.id) AND (x.uid IS NULL OR x.uid<>%s)"
     " AND x.created > COALESCE((SELECT s.seen_at FROM note_comment_seen s WHERE s.uid=%s AND s.thread_id=r.id), 0))",
     (I["shared_root"], A, A)),
    ("note_share: Freigabe an der Seite? (note_get)", "SELECT 1 FROM note_share WHERE note_id=%s LIMIT 1", (I["page_normal"],)),
    ("note_publish: veroeffentlicht? (note_get)",
     "SELECT p.slug, n.name FROM note_publish p LEFT JOIN public_name n ON n.uid=p.uid WHERE p.note_id=%s AND p.uid=%s AND p.active=1",
     (I["page_normal"], A)),
    ("Papierkorb-Liste (page_tools.api_trash)",
     f"SELECT {META}, deleted_at FROM note n WHERE uid=%s AND deleted_at IS NOT NULL AND NOT EXISTS (SELECT 1 FROM note p WHERE "
     "p.id=n.parent_id AND p.uid=n.uid AND p.deleted_at=n.deleted_at) ORDER BY deleted_at DESC", (A,)),
    ("chat._msgs: neueste 50 Nachrichten",
     "SELECT m.seq, (SELECT sealed FROM chat_msg_key k WHERE k.msg_seq=m.seq AND k.recipient=%s), (SELECT sealed FROM chat_msg_key k WHERE "
     "k.msg_seq=m.seq AND k.recipient=%s) FROM chat_msg m WHERE m.chat_id=%s AND (m.expires_at IS NULL OR m.expires_at>%s) AND (m.kind='call' OR "
     "EXISTS (SELECT 1 FROM chat_msg_key k WHERE k.msg_seq=m.seq AND k.recipient IN (%s,%s))) AND (m.kind='title' OR m.seq > COALESCE((SELECT "
     "p.cleared_seq FROM chat_pref p WHERE p.chat_id=m.chat_id AND p.uid=%s), 0)) AND m.kind='msg' ORDER BY m.seq DESC LIMIT 51",
     (I["dev_a"], "backup:" + A, I["chat"], time.time(), I["dev_a"], "backup:" + A, A)),
    ("chat.unread_total (Dashboard/Seiten-Kopf)",
     "SELECT COUNT(*) FROM chat_msg x JOIN chat_member m ON m.chat_id=x.chat_id AND m.uid=%s AND m.left_at IS NULL AND m.muted=0 "
     "WHERE x.kind='msg' AND x.seq>m.last_read AND (x.sender_uid IS NULL OR x.sender_uid<>%s) AND (x.expires_at IS NULL OR x.expires_at>%s) "
     "AND NOT EXISTS (SELECT 1 FROM chat_pref p WHERE p.chat_id=x.chat_id AND p.uid=m.uid AND p.archived IS NOT NULL)", (A, A, time.time())),
    ("Freigaben an B (Access.owners)",
     "SELECT DISTINCT owner_uid FROM note_share WHERE ((target_type='user' AND target=%s))", (B,)),
    ("Kinder einer Seite (note_parent)", "SELECT id FROM note WHERE uid=%s AND parent_id=%s AND deleted_at IS NULL", (A, I["shared_root"])),
]

with db.pool().connection() as c:
    print(f"{'Abfrage':<66} {'ms':>8} {'Zeilen':>7}  Knoten")
    for name, sql, args in Q:
        plan = c.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, args).fetchone()[0][0]
        nodes = []

        def walk(p):
            s = p["Node Type"]
            if p.get("Relation Name"):
                s += " " + p["Relation Name"]
            if p.get("Index Name"):
                s += f" ({p['Index Name']})"
            nodes.append(s)
            for ch in p.get("Plans", []):
                walk(ch)
        walk(plan["Plan"])
        bufs = plan["Plan"].get("Shared Hit Blocks", 0) + plan["Plan"].get("Shared Read Blocks", 0)
        print(f"{name:<66} {plan['Execution Time']:>8.2f} {plan['Plan'].get('Actual Rows', 0):>7}  {' > '.join(nodes)}  [Puffer {bufs}]")
    c.rollback()
