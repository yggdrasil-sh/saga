"""Lasttest-Daten fuer Saga: realistische Seiten, Datenbanken, Kommentare, Freigaben, Chat fuer ein Testkonto.

Laeuft IM Saga-Container (braucht db/auth/saga), schreibt direkt per COPY in Postgres (schnell, konsistent: gleiche
Spalten wie die API, Eltern/Kinder, Dateien auf der Platte, Freigaben, Kommentare, Chat-Schluessel je Empfaenger):

    docker exec -i -w /app <saga-container> python - --stufe M < tools/perf/gen.py

Stufen: S (500 Seiten), M (5.000 Seiten + 20.000 DB-Zeilen in 50 Datenbanken), L (10.000 Seiten + 50.000 DB-Zeilen,
eine Datenbank mit 10.000 Zeilen, Baum bis Tiefe 6, lange Inhalte, Bilder, Kommentare, Freigaben an ein zweites Konto,
Chat mit 5.000 Nachrichten), V (wie S, Chat mit 1.000 Video-Nachrichten samt lueckenhafter Blobs 50-500 MB und Poster;
--art bild = Bild-Nachrichten zum Vergleich). Konten: perf-a (Daten), perf-b (Freigaben, Kommentare, Chat-Gegenueber). Vorhandene
Daten beider Konten werden vorher geloescht. Am Ende: Zeile "PERF {json}" mit IDs und Sitzungs-Cookies fuer run.sh.
NUR auf Test-Stacks verwenden (loescht die Konten perf-a/perf-b samt Daten)."""
import argparse
import base64
import json
import os
import random
import struct
import sys
import time
import zlib

sys.path.insert(0, "/app")
import auth  # noqa: E402
import db  # noqa: E402
from saga.notes import FILES_DIR  # noqa: E402
from saga.userdata import delete_user_data  # noqa: E402

STUFEN = {
    #     Seiten  DBs  Zeilen  groesste  Kommentare  Chat   Aufgaben  Bilder  Freigaben
    "S": (500,    10,  1000,   300,      60,         500,   100,      40,     3),
    "M": (5000,   50,  20000,  2000,     500,        2000,  300,      200,    5),
    "L": (10000,  100, 50000,  10000,    1000,       5000,  500,      400,    8),
    # 2.4 (Konzept §8): wie S, Chat mit 1.000 Video-Nachrichten (saga-file/v2, lueckenhafte Blobs 50-500 MB + Poster);
    # --art bild: dieselbe Menge Bild-Nachrichten (Vergleich "vorher", laeuft auch gegen 2.3)
    "V": (500,    10,  1000,   300,      60,         1000,  100,      40,     3),
}
ART = {"art": "video"}
A, B = "perf-a", "perf-b"
GROUP_N = 50                       # (2.4) Gruppen-Chat fuer Gelesen-Bestaetigungen/Online-Status: A, B + 48 weitere Konten
GU = [f"perf-g{i:02d}" for i in range(1, GROUP_N - 1)]
PW = "Perf-Pass-1234"
WORDS = ("Projekt Angebot Rechnung Kunde Server Netzwerk Firewall Backup Wartung Planung Termin Besprechung Notiz Idee Entwurf "
         "Konzept Anleitung Checkliste Protokoll Zusammenfassung Ergebnis Aufgabe Fehler Loesung Update Migration Lizenz Vertrag "
         "Urlaub Familie Garten Rezept Einkauf Reise Buch Film Musik Training Gesundheit Auto Haus Steuer Versicherung Bank "
         "und oder aber mit ohne fuer gegen bei nach vor ueber unter zwischen heute morgen gestern bald immer nie oft selten "
         "schnell langsam gross klein neu alt gut schlecht wichtig dringend offen erledigt geplant laufend").split()
R = random.Random(42)


def words(n):
    return " ".join(R.choice(WORDS) for _ in range(n))


def title():
    return words(R.randint(1, 4)).capitalize()


def png(w=48, h=48):
    """kleines, echtes PNG (Rauschen, ~7 KB)"""
    raw = b"".join(b"\x00" + bytes(R.getrandbits(8) for _ in range(w * 3)) for _ in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def bid():
    return "%010x" % R.getrandbits(40)


def body(links, images, size="normal"):
    """Editor.js-Dokument; size: normal (1-3 KB), long (60-150 KB), row (kurz, Datenbank-Zeile)"""
    blocks = []
    n = {"row": R.randint(0, 2), "normal": R.randint(3, 14), "long": R.randint(250, 600)}[size]
    for _ in range(n):
        k = R.random()
        if k < 0.12:
            blocks.append({"id": bid(), "type": "header", "data": {"text": title(), "level": R.choice((2, 3))}})
        elif k < 0.22:
            blocks.append({"id": bid(), "type": "list", "data": {"style": "unordered", "meta": {}, "items": [
                {"content": words(R.randint(3, 9)), "meta": {}, "items": []} for _ in range(R.randint(2, 6))]}})
        elif k < 0.26 and images:
            blocks.append({"id": bid(), "type": "image", "data": {"file": {"url": "/files/" + R.choice(images)}, "caption": words(3),
                                                                 "withBorder": False, "stretched": False, "withBackground": False}})
        else:
            txt = words(R.randint(15, 60))
            if links and R.random() < 0.15:
                lid, lt = R.choice(links)
                txt += f' <a href="/notes#{lid}">{lt}</a> ' + words(5)
            blocks.append({"id": bid(), "type": "paragraph", "data": {"text": txt}})
    if size == "row" and images and R.random() < 0.1:
        blocks.insert(0, {"id": bid(), "type": "image", "data": {"file": {"url": "/files/" + R.choice(images)}, "caption": ""}})
    return json.dumps({"time": int(time.time() * 1000), "version": "2.30.0", "blocks": blocks}, ensure_ascii=False)


def schema():
    def opt(i, n, c):
        return {"id": i, "name": n, "color": c}
    props = [
        {"id": "status", "name": "Status", "type": "select", "options": [opt("todo", "Offen", "gray"), opt("doing", "In Arbeit", "blue"),
                                                                         opt("done", "Erledigt", "green")]},
        {"id": "datum", "name": "Datum", "type": "date"},
        {"id": "prio", "name": "Prioritaet", "type": "select", "options": [opt("h", "Hoch", "red"), opt("m", "Mittel", "yellow"),
                                                                           opt("l", "Niedrig", "gray")]},
        {"id": "tags", "name": "Tags", "type": "multi_select", "options": [opt(f"t{i}", w, c) for i, (w, c) in enumerate(
            zip(("Kunde", "Intern", "Privat", "Server", "Netz", "Doku"), ("blue", "purple", "pink", "orange", "green", "brown")))]},
        {"id": "betrag", "name": "Betrag", "type": "number", "fmt": "euro"},
        {"id": "stunden", "name": "Stunden", "type": "number"},
        {"id": "satz", "name": "Satz", "type": "calc", "op": "div", "a": "betrag", "b": "stunden"},
        {"id": "ok", "name": "Erledigt", "type": "checkbox"},
        {"id": "notiz", "name": "Notiz", "type": "text"},
        {"id": "link", "name": "Link", "type": "url"},
        {"id": "angelegt", "name": "Angelegt", "type": "created"},
    ]
    views = [{"id": "tabelle", "name": "Tabelle", "type": "table", "calcs": {"betrag": "sum", "ok": "pct_checked"}},
             {"id": "board", "name": "Board", "type": "board", "group_by": "status"},
             {"id": "offen", "name": "Offen", "type": "table", "filters": [{"pid": "status", "op": "is_not", "value": "done"}],
              "sorts": [{"pid": "datum", "dir": "desc"}]},
             {"id": "kal", "name": "Kalender", "type": "calendar", "date_by": "datum"},
             {"id": "galerie", "name": "Galerie", "type": "gallery"}]
    return json.dumps({"props": props, "views": views, "srev": 1}, ensure_ascii=False)


def props():
    d = {"status": R.choice(("todo", "doing", "done")), "prio": R.choice(("h", "m", "l"))}
    if R.random() < 0.8:
        d["datum"] = f"2026-{R.randint(1, 12):02d}-{R.randint(1, 28):02d}"
    if R.random() < 0.6:
        d["tags"] = R.sample([f"t{i}" for i in range(6)], R.randint(1, 3))
    if R.random() < 0.7:
        d["betrag"] = round(R.uniform(10, 5000), 2)
        d["stunden"] = R.randint(1, 40)
    if R.random() < 0.4:
        d["ok"] = True
    if R.random() < 0.5:
        d["notiz"] = words(R.randint(3, 20))
    if R.random() < 0.2:
        d["link"] = "https://example.org/" + bid()
    return json.dumps(d, ensure_ascii=False)


def b64(n):
    return base64.b64encode(os.urandom(n)).decode()


def copy(c, table, cols, rows):
    with c.cursor() as cur, cur.copy(f"COPY {table} ({', '.join(cols)}) FROM STDIN") as cp:
        for r in rows:
            cp.write_row(r)


def nextids(c, seq, n):
    return [r[0] for r in c.execute(f"SELECT nextval('{seq}') FROM generate_series(1, %s)", (n,)).fetchall()]


def cookie(uid):
    """Sitzungs-Cookie 'saga' (wie tests/teilen.py) - fuer Messungen ohne Anmeldeformular"""
    from itsdangerous import TimestampSigner
    from saga.core import SECRET
    return TimestampSigner(SECRET).sign(base64.b64encode(json.dumps({"uid": uid, "name": uid, "pwc": time.time() + 5}).encode())).decode()


def reset_users():
    for u in (A, B):
        if auth.get_user(u):
            delete_user_data(u, drop_user=True)
        user, err = auth.create_user(u, PW, "Perf " + u[-1].upper())
        if not user:
            raise SystemExit(f"Konto {u}: {err}")
    for u in GU:                                   # (2.4) Gruppenmitglieder: ohne Passwort, nur Sitzungs-Cookie
        if auth.get_user(u):
            delete_user_data(u, drop_user=True)
        auth.create_user(u, None, "Perf " + u[-2:])


def gen(stufe):
    n_pages, n_db, n_rows, biggest, n_comments, n_msgs, n_tasks, n_img, n_share = STUFEN[stufe]
    t0 = time.time()
    reset_users()
    now = time.time()
    out = {"stufe": stufe}
    os.makedirs(FILES_DIR, exist_ok=True)
    with db.pool().connection() as c:
        # --- Bilder ---------------------------------------------------------------------------------------
        images, frows = [], []
        for _ in range(n_img):
            fid = os.urandom(12).hex()
            data = png()
            with open(os.path.join(FILES_DIR, fid), "wb") as f:
                f.write(data)
            images.append(fid)
            frows.append((fid, A, f"bild-{fid[:6]}.png", "image/png", len(data), 1, now - R.uniform(0, 3e7)))
        copy(c, "file", ("id", "uid", "name", "mime", "size", "is_image", "created"), frows)

        # --- Seitenbaum (bis Tiefe 6) -----------------------------------------------------------------------
        n_norm = n_pages - n_db
        pids = nextids(c, "note_id_seq", n_pages + n_rows)
        depth, parent, created, titles = {}, {}, {}, {}
        eligible = []                                   # (id) mit Tiefe < 6
        t_start = now - 2 * 365 * 86400
        for i in range(n_norm):
            nid = pids[i]
            if i < 25 or R.random() < 0.02 or not eligible:
                p, d = None, 1
            else:
                p = R.choice(eligible[-300:]) if R.random() < 0.5 else R.choice(eligible)
                d = depth[p] + 1
            depth[nid], parent[nid], titles[nid] = d, p, title()
            created[nid] = t_start + (now - t_start) * i / n_norm
            if d < 6:
                eligible.append(nid)
        dbs = []
        for k in range(n_db):
            nid = pids[n_norm + k]
            p = R.choice(eligible)
            depth[nid], parent[nid], titles[nid] = depth[p] + 1, p, "DB " + title()
            created[nid] = R.uniform(created[p], now)
            dbs.append(nid)
        # Zeilen verteilen: groesste DB fest, Rest zufaellig
        per_db = {dbs[0]: biggest}
        rest = n_rows - biggest
        weights = [R.random() ** 2 for _ in dbs[1:]]
        sw = sum(weights) or 1
        for d_, w in zip(dbs[1:], weights):
            per_db[d_] = int(rest * w / sw)
        per_db[dbs[1]] += n_rows - sum(per_db.values())
        out["db_big"], out["db_big_rows"] = dbs[0], biggest
        med = sorted(dbs[1:], key=lambda x: per_db[x])[len(dbs) // 2]
        out["db_mid"], out["db_mid_rows"] = med, per_db[med]

        normal = pids[:n_norm]
        links = [(x, titles[x]) for x in R.sample(normal, min(400, n_norm))]
        long_ids = set(R.sample(normal[25:], max(1, n_norm // 50)))
        arch = set(R.sample(normal[25:], n_norm // 50))
        trash = set(R.sample([x for x in normal[25:] if x not in arch], n_norm // 100))
        pinned = set(R.sample(normal, 8))
        tpl = set(R.sample([x for x in normal[25:] if x not in arch and x not in trash], 5))
        cols = ("id", "uid", "title", "body", "created", "updated", "icon", "parent_id", "format", "sort", "tags", "pinned", "deleted_at",
                "is_template", "kind", "schema", "props", "cover", "wide", "rev", "archived", "locked", "updated_by", "created_by")
        icons = ["📄", "📝", "📁", "💡", "🔧", "📌", "", "", "", ""]

        def page_rows():
            for nid in normal:
                cr = created[nid]
                up = min(now, cr + R.uniform(0, 3e7))
                tags = json.dumps(R.sample(["arbeit", "privat", "server", "kunde", "idee", "doku"], R.randint(0, 2)))
                yield (nid, A, titles[nid], body(links, images, "long" if nid in long_ids else "normal"), cr, up, R.choice(icons),
                       parent[nid], "blocks", cr, tags, 1 if nid in pinned else 0, now - 2 * 86400 if nid in trash else None,
                       1 if nid in tpl else 0, "page", None, None, ("/files/" + R.choice(images)) if R.random() < 0.05 else None,
                       0, R.randint(1, 30), now - 86400 if nid in arch else None, 0, A, A)
            sch = schema()
            for nid in dbs:
                yield (nid, A, titles[nid], json.dumps({"blocks": []}), created[nid], created[nid], "🗃️", parent[nid], "blocks", created[nid],
                       "[]", 0, None, 0, "database", sch, None, None, 0, 1, None, 0, A, A)
            k = n_pages
            for d_ in dbs:
                for _ in range(per_db[d_]):
                    nid = pids[k]
                    k += 1
                    cr = R.uniform(created[d_], now)
                    yield (nid, A, title(), body(None, images, "row"), cr, cr, "", d_, "blocks", cr, "[]", 0, None, 0, "page", None,
                           props(), None, 0, 1, None, 0, A, A)
        copy(c, "note", cols, page_rows())
        out["pages"], out["rows"] = n_pages, n_rows
        out["page_normal"] = next(x for x in normal[100:] if x not in long_ids | arch | trash | tpl)
        out["page_long"] = next(x for x in long_ids if x not in arch | trash | tpl)
        out["page_deep"] = next(x for x in normal if depth[x] == 6 and x not in arch | trash | tpl) if any(depth[x] == 6 for x in normal) else None
        out["max_depth"] = max(depth.values())

        # --- Versionen ------------------------------------------------------------------------------------
        vrows = []
        for nid in R.sample(normal, n_norm // 20):
            for j in range(3):
                vrows.append((nid, A, titles[nid], body(links, images), "blocks", created[nid] + j * 3600, A))
        copy(c, "note_version", ("note_id", "uid", "title", "body", "format", "created", "author"), vrows)

        # --- Freigaben an perf-b ----------------------------------------------------------------------------
        kids = {}
        for x, p in parent.items():
            kids.setdefault(p, []).append(x)

        def subtree(r):
            s, st = set(), [r]
            while st:
                x = st.pop()
                s.add(x)
                st.extend(kids.get(x, []))
            return s
        # mittelgrosse Teilbaeume (nicht die groessten: "Mit mir geteilt" soll realistisch einen Teil sehen)
        by_size = sorted(normal[:25], key=lambda r: -len(subtree(r)))
        roots = by_size[2:2 + n_share - 1]
        shares = [(r, A, "user", B, ("view", "comment", "edit")[i % 3], now, A) for i, r in enumerate(roots)]
        shares.append((dbs[0], A, "user", B, "edit", now, A))
        copy(c, "note_share", ("note_id", "owner_uid", "target_type", "target", "role", "created", "created_by"), shares)
        out["shared_root"] = roots[0]
        shared_set = set().union(*(subtree(r) for r in roots))
        out["shared_pages"] = len(shared_set)

        # --- Kommentare (Unterhaltungen + Antworten, ein Teil ungelesen) ---------------------------------------
        th_ids = nextids(c, "note_comment_id_seq", n_comments)
        sh_list = [x for x in shared_set if x in normal]
        crow, rrow = [], []
        for tid in th_ids:
            on_shared = sh_list and R.random() < 0.4
            nid = R.choice(sh_list) if on_shared else R.choice(normal)
            who = B if on_shared and R.random() < 0.6 else A
            ts = R.uniform(created[nid], now)
            crow.append((tid, nid, A, "", None, who, words(R.randint(4, 30)), ts, None, now if R.random() < 0.3 else None))
            if on_shared:
                for _ in range(R.randint(0, 3)):
                    rrow.append((nid, A, "", tid, R.choice((A, B)), words(R.randint(3, 20)), ts + R.uniform(60, 86400)))
        copy(c, "note_comment", ("id", "note_id", "owner_uid", "block_id", "thread_id", "uid", "body", "created", "edited", "resolved_at"), crow)
        copy(c, "note_comment", ("note_id", "owner_uid", "block_id", "thread_id", "uid", "body", "created"), rrow)
        out["comments"] = len(crow) + len(rrow)

        # --- Aufgaben, Benachrichtigungen --------------------------------------------------------------------
        copy(c, "task", ("uid", "text", "done", "due", "created", "done_at", "prio"),
             [(A, words(R.randint(2, 8)), 1 if R.random() < 0.4 else 0, f"2026-{R.randint(9, 12):02d}-{R.randint(1, 28):02d}" if R.random() < 0.6 else None,
               now - R.uniform(0, 3e7), now - R.uniform(0, 1e6), R.randint(1, 3)) for _ in range(n_tasks)])
        copy(c, "notification", ("uid", "kind", "actor_uid", "note_id", "data", "created", "read_at"),
             [(A, "comment.new", B, R.choice(sh_list or normal), "{}", now - R.uniform(0, 5e6), None if R.random() < 0.2 else now)
              for _ in range(min(300, n_comments))])

        # --- Chat: Direkt-Chat perf-a <-> perf-b mit n_msgs Nachrichten (Ciphertext-Attrappen, je 2 Schluessel) ---
        devs = {}
        for u in (A, B):
            c.execute("INSERT INTO chat_identity (uid, ident_pub, backup_pub, backup_sig, bundle, created, updated) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                      (u, b64(32), b64(32), b64(64), json.dumps({"v": 1, "kdf": "argon2id13", "ops": 3, "mem": 1 << 26, "salt": b64(16),
                                                                  "nonce": b64(24), "ct": b64(200)}), now, now))
            devs[u] = os.urandom(16).hex()
            c.execute("INSERT INTO chat_device (id, uid, name, enc_pub, sign_pub, sig, created, last_seen) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                      (devs[u], u, "perf", b64(32), b64(32), b64(64), now, now))
        cid = c.execute("INSERT INTO chat (kind, dm_key, created_by, created) VALUES ('dm', %s, %s, %s) RETURNING id",
                        (f"{A}|{B}", A, now - 3e7)).fetchone()[0]
        for u in (A, B):
            c.execute("INSERT INTO chat_member (chat_id, uid, joined) VALUES (%s,%s,%s)", (cid, u, now - 3e7))
        seqs = nextids(c, "chat_msg_seq_seq", n_msgs)
        mrows, krows, frows = [], [], []
        media = stufe == "V"
        if media:
            from saga.chat import CHAT_DIR
            os.makedirs(CHAT_DIR, mode=0o700, exist_ok=True)
            has_fmt = c.execute("SELECT 1 FROM information_schema.columns WHERE table_name='chat_file' AND column_name='fmt'").fetchone()
            poster = os.urandom(20 * 1024)
        for j, sq in enumerate(seqs):
            u = A if R.random() < 0.5 else B
            ts = now - 3e7 + 3e7 * j / n_msgs
            if media:                                # Ciphertext so lang wie eine echte Video- bzw. Bild-Nachricht
                fid, tid = b64(18).replace("+", "-").replace("/", "_"), b64(18).replace("+", "-").replace("/", "_")
                if ART["art"] == "video":
                    plain = 50 * 1048576 + R.randrange(450 * 1048576)
                    size = 48 + plain + 16 * -(-plain // 262144)
                    f = {"id": fid, "k": b64(32), "n": f"VID_{j:04d}.mp4", "m": "video/mp4", "s": plain, "x": 2, "cs": 262144,
                         "dur": round(R.uniform(10, 600), 1), "w": 1280, "h": 720, "th": {"id": tid, "k": b64(32), "m": "image/jpeg", "w": 320, "h": 180, "s": 21000}}
                else:
                    plain = 1048576 + R.randrange(4 * 1048576)
                    size = 40 + plain
                    f = {"id": fid, "k": b64(32), "n": f"IMG_{j:04d}.jpg", "m": "image/jpeg", "s": plain, "w": 4032, "h": 3024,
                         "th": {"id": tid, "k": b64(32), "m": "image/jpeg", "w": 320, "h": 240, "s": 21000}}
                mbody = json.dumps({"v": 1, "type": "text", "text": "", "files": [f]}, separators=(",", ":"))
                ct = b64(len(mbody.encode()) + 40)
                with open(os.path.join(CHAT_DIR, fid), "wb") as fh:
                    fh.truncate(size)                # lueckenhaft: belegt keinen Platz
                with open(os.path.join(CHAT_DIR, tid), "wb") as fh:
                    fh.write(poster)
                for x, sz in ((fid, size), (tid, len(poster))):
                    frows.append((x, cid, u, sz, ts, sq) + ((2 if x == fid and ART["art"] == "video" else 1,) if has_fmt else ()))
            else:
                ct = b64(R.randint(60, 400))
            mrows.append((sq, os.urandom(16).hex(), cid, "msg", u, devs[u], int(ts * 1000), ts, ct, b64(64), len(ct)))
            for v in (A, B):
                krows.append((sq, devs[v], b64(80)))
                krows.append((sq, "backup:" + v, b64(80)))
        copy(c, "chat_msg", ("seq", "id", "chat_id", "kind", "sender_uid", "sender_device", "ts", "created", "body_ct", "sig", "size"), mrows)
        copy(c, "chat_msg_key", ("msg_seq", "recipient", "sealed"), krows)
        if frows:
            copy(c, "chat_file", ("id", "chat_id", "uid", "size", "created", "msg_seq") + (("fmt",) if has_fmt else ()), frows)
            out["chat_files"] = len(frows)
            out["art"] = ART["art"]
        c.execute("UPDATE chat SET last_seq=%s, last_at=%s WHERE id=%s", (seqs[-1], now, cid))
        c.execute("UPDATE chat_member SET last_read=%s WHERE chat_id=%s AND uid=%s", (seqs[-30], cid, A))
        out.update(chat=cid, msgs=n_msgs, dev_a=devs[A], dev_b=devs[B])

        # --- (2.4) freie Gruppe mit GROUP_N Mitgliedern (Gelesen-Bestaetigungen, Online-Status), 200 Nachrichten ---------
        for u in GU:
            c.execute("INSERT INTO chat_identity (uid, ident_pub, backup_pub, backup_sig, bundle, created, updated) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                      (u, b64(32), b64(32), b64(64), "{}", now, now))
            devs[u] = os.urandom(16).hex()
            c.execute("INSERT INTO chat_device (id, uid, name, enc_pub, sign_pub, sig, created, last_seen) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                      (devs[u], u, "perf", b64(32), b64(32), b64(64), now, now))
        gid = c.execute("INSERT INTO chat (kind, created_by, created) VALUES ('free', %s, %s) RETURNING id", (A, now - 1e6)).fetchone()[0]
        gm = [A, B] + GU
        for u in gm:
            c.execute("INSERT INTO chat_member (chat_id, uid, joined, role) VALUES (%s,%s,%s,%s)", (gid, u, now - 1e6, "admin" if u == A else "member"))
        gseq = nextids(c, "chat_msg_seq_seq", 200)
        mrows, krows = [], []
        for j, sq in enumerate(gseq):
            u = R.choice(gm)
            ts = now - 1e6 + 1e6 * j / 200
            ct = b64(R.randint(60, 300))
            mrows.append((sq, os.urandom(16).hex(), gid, "msg", u, devs[u], int(ts * 1000), ts, ct, b64(64), len(ct)))
            for v in gm:
                krows.append((sq, devs[v], b64(80)))
                krows.append((sq, "backup:" + v, b64(80)))
        copy(c, "chat_msg", ("seq", "id", "chat_id", "kind", "sender_uid", "sender_device", "ts", "created", "body_ct", "sig", "size"), mrows)
        copy(c, "chat_msg_key", ("msg_seq", "recipient", "sealed"), krows)
        c.execute("UPDATE chat SET last_seq=%s, last_at=%s WHERE id=%s", (gseq[-1], now, gid))
        out.update(group=gid, group_last=gseq[-1], group_last_id=mrows[-1][1], group_members=gm)

        # --- ein paar eigene Seiten fuer perf-b ------------------------------------------------------------
        copy(c, "note", ("uid", "title", "body", "created", "updated", "format", "sort", "updated_by", "created_by"),
             [(B, title(), body(None, None), now, now, "blocks", now + i, B, B) for i in range(50)])
        c.commit()
        c.execute("ANALYZE")
        c.commit()
        tot = c.execute("SELECT pg_size_pretty(pg_database_size(current_database())), (SELECT count(*) FROM note WHERE uid=%s)", (A,)).fetchone()
    out["db_size"], out["notes_total"] = tot
    out["seconds"] = round(time.time() - t0, 1)
    out["cookie_a"], out["cookie_b"] = cookie(A), cookie(B)
    out["cookies_g"] = {u: cookie(u) for u in GU}
    print("PERF " + json.dumps(out), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stufe", choices=sorted(STUFEN), default="S")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--art", choices=("video", "bild"), default="video")
    a = ap.parse_args()
    ART["art"] = a.art
    R.seed(a.seed)
    gen(a.stufe)
