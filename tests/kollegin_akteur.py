"""Akteur-Spalten (2.6, Bugs-DB 2266 K-A1, Konzept K22): saga/actor.py und Migration 36 - note.updated_actor,
note_version.actor_kind/actor/on_behalf_of, notification.actor_kind/actor/on_behalf_of.

Prueft: Migration 36 ab Stand 35 (Spalten weg, Schema-Stand 35) und beliebig oft; Bestand bleibt NULL; Schreiben unter
acting(Actor("service", "svc:kollegin", on_behalf_of=<uid>)) -> note.updated_actor, die gesicherte Fassung dieses Stands
traegt den Dienst (author bleibt die Person), Folgeversion ohne Akteur NULL; Anlegen, Bloecke verschieben, Version
wiederherstellen und checkpoint ebenso; Benachrichtigung mit Akteur bzw. ohne NULL; versions_list unveraendert;
Kontoloeschung leert on_behalf_of. Laeuft im Prozess gegen die Datenbank aus DATABASE_URL (Wegwerf-Konten ka-*).
ACHTUNG: entfernt kurz die Spalten der Migration 36 (Stand 35 nachstellen) - nicht parallel zu anderen Reihen auf
derselben Datenbank laufen lassen.
Aufruf (saga-test): PYTHONPATH=tests/pgshim:. python tests/kollegin_akteur.py"""
import json
import os
import re
import time
import sys
import threading

import psycopg

import db

db.wait_ready()
import auth  # noqa: E402
from saga import actor, notes, notify, userdata  # noqa: E402
from saga.actor import Actor, acting  # noqa: E402
from saga.api_v1 import _relay  # noqa: E402
from saga.page_blocks import blocks_move  # noqa: E402
from saga.page_tools import version_restore, versions_list  # noqa: E402
from saga.pages import _Reply, _note_update, note_create, note_get  # noqa: E402

A, B, GONE = "ka-anna", "ka-bert", "ka-weg"
K = Actor("service", "svc:kollegin", on_behalf_of=A)
COLS36 = (("note", "updated_actor"), ("note_version", "actor_kind"), ("note_version", "actor"), ("note_version", "on_behalf_of"),
          ("notification", "actor_kind"), ("notification", "actor"), ("notification", "on_behalf_of"))
res = []


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, "" if c else str(info)[:300], flush=True)


def relay(r):
    try:
        return _relay(r)
    except _Reply as x:
        return _relay(x.resp)


def pg():
    return psycopg.connect(db.DATABASE_URL, autocommit=True)


def cols_now():
    with pg() as c:
        return {(r[0], r[1]) for r in c.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema="
                                                "current_schema() AND table_name IN ('note','note_version','notification')")}


def blocks(*texts):
    return json.dumps({"blocks": [{"id": f"p{i}", "type": "paragraph", "data": {"text": tx}} for i, tx in enumerate(texts, 1)]})


def save(uid, nid, *texts):
    rev = relay(note_get(uid, nid))["rev"]
    return relay(_note_update(uid, nid, {"format": "blocks", "body": blocks(*texts), "rev": rev}, "web"))


def note_row(nid):
    with pg() as c:
        return c.execute("SELECT updated_by, updated_actor, created_by FROM note WHERE id=%s", (nid,)).fetchone()


def versions(nid):
    """gesicherte Fassungen, aelteste zuerst: (inhalt, author, actor_kind, actor, on_behalf_of)"""
    with pg() as c:
        rows = c.execute("SELECT body, author, actor_kind, actor, on_behalf_of FROM note_version WHERE note_id=%s ORDER BY created, id",
                         (nid,)).fetchall()
    return [((json.loads(r[0] or "{}").get("blocks") or [{}])[0].get("data", {}).get("text"), *r[1:]) for r in rows]


def cleanup():
    with pg() as c:
        for u in (A, B, GONE):
            c.execute("DELETE FROM note_version WHERE uid=%s", (u,))
            c.execute("DELETE FROM notification WHERE uid=%s OR actor_uid=%s", (u, u))
            c.execute("DELETE FROM note_base WHERE note_id IN (SELECT id FROM note WHERE uid=%s)", (u,))
            c.execute("DELETE FROM note WHERE uid=%s", (u,))


def part_actor():
    ok("ohne Akteur: current() None, cols() nur NULL, note_actor() None",
       actor.current() is None and actor.cols() == (None, None, None) and actor.note_actor() is None)
    with acting(K) as a:
        ok("acting: current() ist der Akteur, cols() = (service, svc:kollegin, uid)",
           a is K and actor.current() is K and actor.cols() == ("service", "svc:kollegin", A) and actor.note_actor() == "svc:kollegin")
        inner = Actor("person", B)
        with acting(inner):
            ok("verschachtelt: der innere gilt; Person -> note_actor() None (steht in updated_by)",
               actor.current() is inner and actor.cols() == ("person", B, None) and actor.note_actor() is None)
        ok("danach wieder der aeussere", actor.current() is K)
        seen = []
        th = threading.Thread(target=lambda: seen.append(actor.current()))
        th.start(); th.join()
        ok("neuer Thread erbt den Akteur nicht", seen == [None], seen)
    ok("nach dem Block wieder kein Akteur", actor.current() is None)
    try:
        with acting(K):
            raise RuntimeError("x")
    except RuntimeError:
        pass
    ok("Ausnahme im Block: Akteur trotzdem zurueckgesetzt", actor.current() is None)
    bad = []
    for kw in (dict(kind="robot", actor="svc:kollegin"), dict(kind="service", actor="svc:fremd"), dict(kind="service", actor=""),
               dict(kind="person", actor="svc:kollegin"), dict(kind="service", actor="svc:bot", on_behalf_of="svc:kollegin")):
        try:
            Actor(**kw)
            bad.append(kw)
        except ValueError:
            pass
    ok("ungueltige Akteure abgelehnt (Art, Dienst, leer, Person als Dienst, Auftrag an Dienst)", not bad, bad)
    bad = []
    for obo in ("anna@saga.example", "@anna@saga.example", "anna@", "svc:bridge", " ka-anna", "", 7):
        try:
            Actor("service", "svc:kollegin", on_behalf_of=obo)
            bad.append(obo)
        except ValueError:
            pass
    ok("on_behalf_of nur lokale uid: keine Foederations-Kennung (@), kein svc:, nicht leer (Pruefer K-A1)", not bad, bad)
    ok("on_behalf_of lokale uid erlaubt", Actor("service", "svc:bot", on_behalf_of="ka-anna").on_behalf_of == "ka-anna")
    try:
        Actor("service", "svc:kollegin", on_behalf_of=A, idem="")
        ok("leerer Schritt-Schluessel abgelehnt", False)
    except ValueError:
        ok("leerer Schritt-Schluessel abgelehnt", True)
    seen = []
    with acting(K):
        th = threading.Thread(target=lambda: seen.append(actor.current()))
        th.start(); th.join()
        import asyncio
        import contextvars

        async def inner():
            return actor.current()
        copied = contextvars.copy_context().run(lambda: actor.current())
        loop = asyncio.new_event_loop()
        try:
            task_actor = loop.run_until_complete(inner())
        finally:
            loop.close()
    ok("Kontext-Kopien (Task, copy_context) erben den Akteur - deshalb acting(None) in Langlebigem (Doku actor.py)",
       seen == [None] and copied is K and task_actor is K, (seen, copied, task_actor))
    ok("actor.py dokumentiert acting(None) fuer langlebige Tasks/Threads",
       "acting(None)" in (actor.__doc__ or "") and "create_task" in (actor.__doc__ or ""))
    ok("SERVICES fest", actor.SERVICES == ("svc:kollegin", "svc:bridge", "svc:bot"))
    try:
        K.kind = "person"
        frozen = False
    except AttributeError:                         # dataclasses.FrozenInstanceError
        frozen = True
    ok("Actor ist unveraenderlich (frozen)", frozen)


def part_migration():
    ok("Migration 36 vorhanden, SCHEMA_VERSION >= 36", 36 in db.MIGRATIONS and db.SCHEMA_VERSION >= 36)
    sql = db.MIGRATIONS[36]
    stmts = [s.strip() for s in sql.split(";") if s.strip()]
    ok("Migration 36 nur ADD COLUMN IF NOT EXISTS ohne Default/Constraint",
       stmts and all(s.startswith("ALTER TABLE ") and " ADD COLUMN IF NOT EXISTS " in s and s.endswith(" TEXT") for s in stmts), stmts)
    tabs = {m.group(1): m.group(2) for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (note|note_version|notification) \((.*?)\);",
                                                         db.SCHEMA, re.S)}
    ok("SCHEMA (Neuinstallation) ohne die Spalten - sie kommen nur ueber die Migration (wie 4/5/19)",
       len(tabs) == 3 and not any(re.search(rf"\b{x}\b", tabs[tb]) for tb, x in COLS36), sorted(tabs))
    # Schutz (Pruefer K-A1): Spalten nur wegnehmen, wenn ausser den Wegwerf-Konten ka-* nichts darin steht - sonst liefe
    # die Reihe gegen eine Datenbank mit echten Akteur-Daten (die das DROP COLUMN unwiderruflich loeschte)
    have = cols_now()
    with pg() as c:
        foreign = sum(c.execute(f"SELECT count(*) FROM {tb} WHERE {col} IS NOT NULL AND uid NOT LIKE %s", ("ka-%",)).fetchone()[0]
                      for tb, col in COLS36 if (tb, col) in have)
    ok("Datenbank ohne fremde Akteur-Daten (sonst kein Stand-35-Nachstellen)", foreign == 0, foreign)
    if foreign:
        return
    # Stand 35 nachstellen: Spalten weg, Schema-Stand 35
    with pg() as c:
        for tb, col in COLS36:
            c.execute(f"ALTER TABLE {tb} DROP COLUMN IF EXISTS {col}")
        c.execute("DELETE FROM schema_version WHERE v > 35")
    ok("Stand 35: Spalten weg", not (set(COLS36) & cols_now()))
    # Bestand anlegen (alter Spaltensatz)
    with pg() as c:
        nid = c.execute("INSERT INTO note (uid, title, body, format, created, updated, updated_by) VALUES (%s,'Bestand',%s,'blocks',1,1,%s) "
                        "RETURNING id", (A, blocks("alt"), A)).fetchone()[0]
        c.execute("INSERT INTO note_version (note_id, uid, title, body, format, created, author) VALUES (%s,%s,'Bestand',%s,'blocks',1,%s)",
                  (nid, A, blocks("uralt"), A))
        c.execute("INSERT INTO notification (uid, kind, actor_uid, note_id, created) VALUES (%s,'share.added',%s,%s,1)", (B, A, nid))
    old = os.environ.get("SAGA_SKIP_BACKUP")
    os.environ["SAGA_SKIP_BACKUP"] = "1"          # Backup vor Migrationen prueft tests/versionen.py
    try:
        db.init_schema()
    finally:
        if old is None:
            os.environ.pop("SAGA_SKIP_BACKUP", None)
        else:
            os.environ["SAGA_SKIP_BACKUP"] = old
    ok(f"Migration 35 -> {db.SCHEMA_VERSION}: Stand aktuell, Spalten da", db.schema_version() == db.SCHEMA_VERSION and set(COLS36) <= cols_now())
    with pg() as c:
        c.execute(db.MIGRATIONS[36]); c.execute(db.MIGRATIONS[36])
        ok("Migration 36 beliebig oft ausfuehrbar", set(COLS36) <= cols_now())
        n = c.execute("SELECT updated_actor FROM note WHERE id=%s", (nid,)).fetchone()
        v = c.execute("SELECT actor_kind, actor, on_behalf_of, author FROM note_version WHERE note_id=%s", (nid,)).fetchone()
        m = c.execute("SELECT actor_kind, actor, on_behalf_of, actor_uid FROM notification WHERE uid=%s AND note_id=%s", (B, nid)).fetchone()
        typ = {r[0] for r in c.execute("SELECT data_type FROM information_schema.columns WHERE table_schema=current_schema() AND "
                                       "column_name IN ('updated_actor','actor_kind','actor','on_behalf_of') AND "
                                       "table_name IN ('note','note_version','notification')")}
        dflt = c.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema=current_schema() AND column_name IN "
                         "('updated_actor','actor_kind','actor','on_behalf_of') AND table_name IN ('note','note_version','notification') "
                         "AND (column_default IS NOT NULL OR is_nullable<>'YES')").fetchone()[0]
    ok("Bestand: alles NULL, author/actor_uid unveraendert", n == (None,) and v == (None, None, None, A) and m == (None, None, None, A), (n, v, m))
    ok("Spalten TEXT, ohne Default, NULL erlaubt", typ == {"text"} and dflt == 0, (typ, dflt))


def part_versions():
    notes_every = notes.VERSION_EVERY
    notes.VERSION_EVERY = 0                       # jede Speicherung sichert den alten Stand (sonst 5-Minuten-Drossel)
    try:
        with acting(K):
            nid = relay(note_create(A, {"title": "Akteur-Seite"}))["id"]
        ok("Anlegen unter acting: updated_actor = svc:kollegin, updated_by/created_by = Person", note_row(nid) == (A, "svc:kollegin", A),
           note_row(nid))
        with acting(K):
            save(A, nid, "von der Kollegin")
        ok("Speichern unter acting: updated_actor bleibt svc:kollegin, updated_by Person", note_row(nid)[:2] == (A, "svc:kollegin"))
        v = versions(nid)
        ok("Fassung des angelegten Stands: Dienst im Auftrag der Person, author Person",
           len(v) == 1 and v[0][1:] == (A, "service", "svc:kollegin", A), v)
        save(A, nid, "von Anna")
        ok("Speichern ohne Akteur: updated_actor NULL", note_row(nid)[:2] == (A, None), note_row(nid))
        v = versions(nid)
        ok("gesicherter Stand der Kollegin traegt den Dienst (nicht, wer gerade speichert)",
           len(v) == 2 and v[1] == ("von der Kollegin", A, "service", "svc:kollegin", A), v)
        save(A, nid, "nochmal Anna")
        v = versions(nid)
        ok("Folgeversion ohne Akteur: NULL", len(v) == 3 and v[2] == ("von Anna", A, None, None, None), v)
        with acting(K):
            save(A, nid, "Kollegin zwei")
        v = versions(nid)
        ok("Schreiben unter acting sichert den Stand der Person ohne Akteur", len(v) == 4 and v[3] == ("nochmal Anna", A, None, None, None), v)

        # versions_list: Antwort unveraendert (gleiche Felder, by "me", kein Akteur nach aussen)
        lst = relay(versions_list(A, nid))
        ok("versions_list: gleiche Felder wie bisher, by 'me', author None",
           len(lst) == 4 and all(set(x) == {"id", "title", "created", "size", "author", "by"} and x["by"] == "me" and x["author"] is None
                                 for x in lst), lst)

        # checkpoint: aktueller Stand (von der Kollegin) -> Fassung mit Dienst
        cp_min, notes.CHECKPOINT_MIN = notes.CHECKPOINT_MIN, 0
        try:
            with db.connect() as c, c:
                made = notes.checkpoint(c, A, nid, A, now=time.time())
        finally:
            notes.CHECKPOINT_MIN = cp_min
        v = versions(nid)
        ok("checkpoint: aktueller Stand der Kollegin -> Fassung mit Dienst", made and v[-1] == ("Kollegin zwei", A, "service", "svc:kollegin", A), v)

        # Version wiederherstellen unter acting: Seite traegt den Dienst; ohne Akteur NULL
        vid = relay(versions_list(A, nid))[-1]["id"]          # aelteste Fassung
        with acting(K):
            relay(version_restore(A, nid, vid))
        ok("Wiederherstellen unter acting: updated_actor svc:kollegin", note_row(nid)[:2] == (A, "svc:kollegin"), note_row(nid))
        relay(version_restore(A, nid, vid))
        ok("Wiederherstellen ohne Akteur: updated_actor NULL", note_row(nid)[:2] == (A, None), note_row(nid))

        # Bloecke verschieben (page_blocks) unter acting: beide Seiten tragen den Dienst
        a = relay(note_create(A, {"title": "Quelle"}))["id"]
        b = relay(note_create(A, {"title": "Ziel"}))["id"]
        ok("Anlegen ohne Akteur: updated_actor NULL", note_row(a) == (A, None, A), note_row(a))
        save(A, a, "eins", "zwei")
        rev_a = relay(note_get(A, a))["rev"]
        with acting(K):
            r = relay(blocks_move(A, a, {"ids": ["p2"], "to": b, "rev_a": rev_a}))
        ok("Bloecke verschieben unter acting: beide Seiten updated_actor svc:kollegin",
           note_row(a)[1] == "svc:kollegin" and note_row(b)[1] == "svc:kollegin", (r, note_row(a), note_row(b)))
        save(A, a, "eins neu")
        v = versions(a)
        ok("Fassung nach dem Verschieben: Dienst; Seite danach ohne Akteur NULL",
           v and v[-1][2:] == ("service", "svc:kollegin", A) and note_row(a)[1] is None, (v, note_row(a)))
    finally:
        notes.VERSION_EVERY = notes_every
    return nid


def part_notify(nid):
    with db.connect() as c, c:
        with acting(K):
            n1 = notify.create(c, B, "share.added", actor=A, note_id=nid, data={"role": "view"})
        n2 = notify.create(c, B, "share.role", actor=A, note_id=nid, data={"role": "edit"})
        with acting(K):
            n3 = notify.create(c, A, "share.added", actor=A, note_id=nid)
    with pg() as c:
        r1 = c.execute("SELECT actor_uid, actor_kind, actor, on_behalf_of FROM notification WHERE id=%s", (n1,)).fetchone()
        r2 = c.execute("SELECT actor_uid, actor_kind, actor, on_behalf_of FROM notification WHERE id=%s", (n2,)).fetchone()
    ok("Benachrichtigung unter acting: actor_uid Person + Dienst im Auftrag", r1 == (A, "service", "svc:kollegin", A), r1)
    ok("Benachrichtigung ohne Akteur: NULL", r2 == (A, None, None, None), r2)
    ok("nie an sich selbst - auch unter acting", n3 is None)
    # gebuendelt (Kommentare derselben Unterhaltung): fortgeschriebene Zeile traegt den Akteur der neuesten
    with db.connect() as c, c:
        with acting(K):
            b1 = notify.create(c, B, "comment.new", actor=A, note_id=nid, comment_id=1, bundle="t-ka")
        b2 = notify.create(c, B, "comment.new", actor=A, note_id=nid, comment_id=2, bundle="t-ka")
    with pg() as c:
        rb = c.execute("SELECT actor_kind, actor, on_behalf_of, data FROM notification WHERE id=%s", (b2,)).fetchone()
        gone = c.execute("SELECT 1 FROM notification WHERE id=%s", (b1,)).fetchone()
    ok("gebuendelt: neueste Zeile ohne Akteur NULL, alte ersetzt", rb and rb[:3] == (None, None, None) and json.loads(rb[3])["n"] == 2
       and gone is None, rb)


def part_delete():
    if not auth.get_user(GONE):
        auth.create_user(GONE, "Ka-Pass-12345", GONE)
    nb = relay(note_create(B, {"title": "Bert-Seite"}))["id"]
    with pg() as c:
        c.execute("INSERT INTO note_version (note_id, uid, title, body, format, created, author, actor_kind, actor, on_behalf_of) "
                  "VALUES (%s,%s,'x',%s,'blocks',2,%s,'service','svc:kollegin',%s)", (nb, B, blocks("weg"), GONE, GONE))
    with db.connect() as c, c:
        with acting(Actor("service", "svc:kollegin", on_behalf_of=GONE)):
            nn = notify.create(c, B, "share.added", actor=GONE, note_id=nb)
        with acting(Actor("person", GONE)):                # Akteur 'person': die uid steht in notification.actor
            np_ = notify.create(c, B, "share.role", actor=GONE, note_id=nb, data={"role": "edit"})
    userdata.delete_user_data(GONE, drop_user=True)
    with pg() as c:
        v = c.execute("SELECT author, actor_kind, actor, on_behalf_of FROM note_version WHERE note_id=%s", (nb,)).fetchone()
        m = c.execute("SELECT actor_uid, actor_kind, actor, on_behalf_of FROM notification WHERE id=%s", (nn,)).fetchone()
        mp = c.execute("SELECT actor_uid, actor_kind, actor, on_behalf_of FROM notification WHERE id=%s", (np_,)).fetchone()
    ok("Kontoloeschung: Fassung auf fremder Seite ohne author und on_behalf_of, Dienst bleibt",
       v == (None, "service", "svc:kollegin", None), v)
    ok("Kontoloeschung: Benachrichtigung ohne actor_uid und on_behalf_of", m == (None, "service", "svc:kollegin", None), m)
    ok("Kontoloeschung: Benachrichtigung mit Akteur 'person' ohne dessen uid", mp == (None, "person", None, None), mp)


def main():
    for u in (A, B):
        if not auth.get_user(u):
            auth.create_user(u, "Ka-Pass-12345", u)
    cleanup()
    try:
        part_actor()
        part_migration()
        nid = part_versions()
        part_notify(nid)
        part_delete()
    finally:
        cleanup()
    print(f"\nKOLLEGIN-AKTEUR {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)


if __name__ == "__main__":
    main()
