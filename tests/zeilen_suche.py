"""Suche in Datenbank-Zeilen mit Vorfilter (2.2, dbquery.q_prefilter fuer GET .../rows?q=...): GENAU dasselbe Ergebnis
wie ohne Vorfilter - Treffer, Reihenfolge, Anzahl (matched/total), Gruppen und Fehler (400 wie im Web).
1. Testvektoren aus dem Web-Code (tests/dbquery_vectors.json) mit ihren Suchbegriffen und vielen weiteren (Teilwoerter
   aus dem Zeilentext, Optionsnamen, Gross/Klein) - Zeilen-JSON einmal woertlich, einmal mit \\u-Escapes.
2. Zufallsdaten: Text (de/en/es/ru/th, tuerkisches I, Schluss-Sigma, Ligatur, Emoji, Anfuehrungszeichen, Backslash,
   Tab), Zahlen (1e-7, 1e21, -0, NaN), Wahrheitswerte, leere Werte, Listen, Objekte, Auswahl mit Text- und Zahl-ids,
   Relationen, kaputtes JSON; Filter aller Arten (auch die, bei denen das Web abbricht). Zusaetzlich die Eigenschaft
   selbst: jede Zeile, deren Suchtext q enthaelt, ist Kandidat.
3. HTTP gegen den Server (Vorfilter aktiv) = Referenz im Testprozess ohne Vorfilter.
Wegwerf-Konto zs-a, raeumt auf. Umgebung wie die anderen Tests (HK_TEST_BASE, BASE_URL, SESSION_SECRET)."""
import base64, http.client, json, os, random, time, urllib.parse
from datetime import date
from zoneinfo import ZoneInfo
from itsdangerous import TimestampSigner
import auth
from db import connect
from saga import dbquery
from saga.app_api import JSONResponse, _db_rows

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
A = "zs-a"
HERE = os.path.dirname(os.path.abspath(__file__))
res, n500 = [], [0]


def ok(n, c, detail=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, flush=True)
    if not c and detail is not None:
        print("     ", str(detail)[:1500], flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def get(p, u):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=60)
    c.request("GET", p, headers={"Host": HOST, "Accept-Language": "de", "Cookie": "saga=" + ck(u), "X-Saga": "1"})
    r = c.getresponse()
    raw = r.read()
    if r.status >= 500:
        n500[0] += 1
    return r.status, raw


class NoPrefilter:
    def __enter__(self):
        self.orig = dbquery.q_prefilter
        dbquery.q_prefilter = lambda schema, args: None

    def __exit__(self, *a):
        dbquery.q_prefilter = self.orig


# --- Vergleich mit/ohne Vorfilter -------------------------------------------------------------------------------------
ST = {"faelle": 0, "ohne": 0, "weg": 0, "zeilen": 0}


def outcome(schema, rows, args, labels):
    try:
        page, matched, groups = dbquery.run(schema, rows, args, labels=labels, untitled="Unbenannt")
        return ("ok", [r["id"] for r in page], matched, groups)
    except dbquery.JsError as e:
        return ("fehler", str(e))


def check(schema, rows, raws, args, labels, bad, tag):
    pf = dbquery.q_prefilter(schema, args)
    if pf is None:
        ST["ohne"] += 1
        return
    full = outcome(schema, rows, args, labels)
    keep = [r for r, raw in zip(rows, raws) if pf(r.get("title"), raw)]
    ST["faelle"] += 1
    ST["zeilen"] += len(rows)
    ST["weg"] += len(rows) - len(keep)
    got = outcome(schema, keep, args, labels)
    if got != full:
        bad.append((tag, args.get("q"), full, got))
    # die Eigenschaft selbst: Suchtext enthaelt q -> Kandidat
    qq = dbquery.Query(schema, q=args["q"], labels=labels)
    for r, raw in zip(rows, raws):
        try:
            txt = qq.row_text(r)
        except dbquery.JsError:
            continue
        if qq.q in txt and not pf(r.get("title"), raw):
            bad.append((tag, "Kandidat fehlt", args["q"], r.get("id"), txt[:200], raw[:200] if isinstance(raw, str) else raw))


def texts_of(schema, rows, labels):
    qq = dbquery.Query(schema, labels=labels)
    out = []
    for r in rows:
        try:
            out.append(qq.row_text(r))
        except dbquery.JsError:
            pass
    return out


def derived_qs(r, schema, rows, labels, n):
    """Suchbegriffe aus den Daten: Teilwoerter des Zeilentexts (auch mit Leerzeichen), Optionsnamen, Gross/Klein."""
    qs = []
    tx = [t for t in texts_of(schema, rows, labels) if t]
    for _ in range(n):
        if tx:
            t = r.choice(tx)
            i = r.randint(0, max(0, len(t) - 1))
            s = t[i:i + r.randint(1, 9)]
            qs.append(s.upper() if r.random() < 0.2 else s)
    for p in (schema.get("props") or []) if isinstance(schema, dict) else []:
        for o in (p.get("options") or []) if isinstance(p, dict) and isinstance(p.get("options"), list) else []:
            if isinstance(o, dict) and isinstance(o.get("name"), str) and o["name"]:
                nm = o["name"]
                qs.append(nm[r.randint(0, len(nm) - 1):][:r.randint(1, 6)])
    return [q for q in dict.fromkeys(qs) if q.strip()]


def vectors():
    v = json.load(open(os.path.join(HERE, "dbquery_vectors.json"), encoding="utf-8"))
    bad, r = [], random.Random(31)
    for c in v["cases"]:
        d = v["dbs"][c["db"]]
        labels = (lambda m: (lambda ids: {i: m[i] for i in ids if i in m}))({int(a): b for a, b in d["labels"]})
        rows = d["rows"]
        # wie im Web-Code und - damit der Vorfilter greift - ohne Relationen (Spalte als Text: String(wert))
        plain = [dict(p, type="text") if isinstance(p, dict) and p.get("type") == "relation" else p for p in d["schema"].get("props") or []]
        for schema in (dict(d["schema"], views=c["views"]), dict(d["schema"], views=c["views"], props=plain)):
            qs = ([c["q"]] if c["q"] else []) + derived_qs(r, schema, rows, labels, 4)
            for q in qs:
                args = {"view": c["view"], "tz": ZoneInfo(c["tz"]), "today": date.fromisoformat(c["today"]), "q": q}
                for asc in (False, True):
                    raws = [x.get("props") if isinstance(x.get("props"), str) else json.dumps(x.get("props"), ensure_ascii=asc) for x in rows]
                    check(schema, rows, raws, args, labels, bad, f"Vektor {c['k']}")
    ok(f"Testvektoren (Web-Code): mit Vorfilter = ohne ({ST['faelle']} Faelle vorgefiltert, {ST['weg']} von {ST['zeilen']} "
       f"Zeilen vorab ausgeschlossen, {ST['ohne']} ohne Vorfilter)", not bad and ST["weg"] > 500, bad[:3])


# --- Zufallsdaten ------------------------------------------------------------------------------------------------------
WORDS = ("Server server SERVER Projekt Straße Größe café niño Mañana Проект встреча Счёт โครงการ การประชุม ภาษาไทย "
         "İstanbul ISPARTA ıslak ΟΔΟΣ Σοφία ΣΣ ﬁle 😀 a,b x%y 100% a_b true false null NaN Infinity object 1e-7 "
         "Kunde Intern Doku").split() + ['an"f', "back\\slash", "tab\tx", "zeile\nneu", " ", "İ", "KK"]
OPS = ["contains", "not_contains", "is", "is_not", "is_empty", "not_empty", "checked", "unchecked", "eq", "gt", "lt",
       "before", "after", "today", "this_week"]


def rval(r, depth=0):
    k = r.random()
    if k < 0.35:
        return " ".join(r.choice(WORDS) for _ in range(r.randint(1, 3)))
    if k < 0.55:
        return r.choice([0, 1, -3, 42, 100.0, 0.1, 1e-7, 1.5e-5, 1e21, 123.456, -0.0, 2 ** 60, float("nan"), 2026])
    if k < 0.62:
        return r.choice([True, False])
    if k < 0.70:
        return None
    if k < 0.82 and depth < 2:
        return [rval(r, depth + 1) for _ in range(r.randint(0, 3))]
    if k < 0.87 and depth < 2:
        return {"a": rval(r, depth + 1)}
    return f"2026-{r.randint(1, 12):02d}-{r.randint(1, 28):02d}"


def rschema(r):
    props, ids = [], []
    for i in range(r.randint(2, 7)):
        t = r.choice(["text", "number", "select", "multi_select", "checkbox", "date", "url", "created", "edited", "calc"]
                     + (["relation"] if r.random() < 0.15 else []))
        p = {"id": r.choice([f"p{i}", f"p{i}", i]), "name": f"Spalte {i}", "type": t}
        if t in ("select", "multi_select"):
            p["options"] = [{"id": r.choice([f"o{i}{j}", f"o{i}{j}", j, f"ö{j}", f'q"{j}']), "name": r.choice(WORDS + [None, 7]),
                             "color": "gray"} for j in range(r.randint(1, 5))]
        if t == "calc":
            p.update(op="div", a="p0", b="p1")
        props.append(p)
        ids.append(p)
    views = [{"id": "v", "name": "V", "type": r.choice(["table", "board", "list", "gallery"]), "group": None,
              "filters": [], "sorts": [{"pid": r.choice([p["id"] for p in props] + ["title"]), "dir": r.choice(["asc", "desc"])}]}]
    if r.random() < 0.7:
        for _ in range(r.randint(1, 2)):
            p = r.choice(props + [{"id": "title"}])
            f = {"pid": p["id"], "op": r.choice(OPS), "value": r.choice([rval(r), "", "server", 3, None])}
            views[0]["filters"].append(f)
    if r.random() < 0.05:
        views[0]["filters"].append(None)
    sel = [p for p in props if p["type"] == "select"]
    if sel and r.random() < 0.5:
        views[0]["group"] = sel[0]["id"]
    return {"props": props, "views": views}


def rrow(r, schema, i):
    pr = {}
    for p in schema["props"]:
        if r.random() < 0.25:
            continue
        key = dbquery.js_str(p["id"])
        if p["type"] in ("select", "multi_select") and r.random() < 0.8:
            oids = [o["id"] for o in p["options"]]
            pr[key] = r.choice(oids) if p["type"] == "select" else r.sample(oids, r.randint(0, len(oids)))
        elif p["type"] == "relation":
            pr[key] = r.choice([[1, 2], [], 5, None])
        else:
            pr[key] = rval(r)
    return {"id": i, "title": r.choice([None, "", " ".join(r.choice(WORDS) for _ in range(r.randint(1, 3)))]), "props": pr,
            "created": 1.7e9 + i, "updated": 1.7e9 + i * 7}


def randoms():
    bad, r = [], random.Random(77)
    for k in range(60):
        schema = rschema(r)
        rows = [rrow(r, schema, i + 1) for i in range(r.randint(5, 60))]
        labels = lambda ids: {i: "Server-Seite " + str(i) for i in ids if i % 2}
        raws = []
        for x in rows:
            s = json.dumps(x["props"], ensure_ascii=r.random() < 0.3)
            raws.append(r.choice([s, s, s, None if not x["props"] else s, "kaputt {" if r.random() < 0.05 else s]))
        rows = [dict(x, props=dbquery.Query._props({"props": json.loads(raw)}) if raw and raw != "kaputt {" else {})
                for x, raw in zip(rows, raws)]                      # Werte genau wie aus dem JSON gelesen
        qs = derived_qs(r, schema, rows, labels, 25) + ["server", "SERVER", "σ", "ς", "i̇", "i", "k", "1e-7", "nan", "true",
                                                         "object", "[object", "\\", '"', "o1", '"o1"', "a,b", "an\"f", "ок",
                                                         "ภาษา", "ﬁ", "fi", "ss", "x", "e", "2026-0"]
        for q in qs:
            args = {"view": "v", "q": q, "tz": ZoneInfo("Europe/Berlin"), "today": date(2026, 10, 19)}
            check(schema, rows, raws, args, labels, bad, f"Zufall {k}")
            if r.random() < 0.3:
                check(schema, rows, raws, dict(args, offset=r.randint(0, 3), limit=r.randint(1, 5)), labels, bad, f"Zufall {k} Seite")
    ok(f"Zufallsdaten: mit Vorfilter = ohne, jede passende Zeile ist Kandidat ({ST['faelle']} Faelle, "
       f"{ST['weg']} von {ST['zeilen']} Zeilen vorab ausgeschlossen)", not bad and ST["weg"] > 1000, bad[:3])


# --- HTTP ---------------------------------------------------------------------------------------------------------------
def ins(c, title, parent=None, kind="page", schema=None, props=None, archived=None, tpl=0):
    now = time.time()
    return c.execute("INSERT INTO note (uid, title, body, format, parent_id, kind, schema, props, created, updated, archived, is_template) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (A, title or "", '{"blocks":[]}', "blocks", parent, kind, json.dumps(schema, ensure_ascii=False) if schema else None,
                      props, now, now + 1, archived, tpl)).lastrowid


def http_round():
    bad, n, r = [], 0, random.Random(5)
    for k in range(6):
        schema = rschema(r)
        if k == 0:                                   # wie die Lastmessung: Ansicht "Offen" mit Auswahl-Optionen
            schema = {"props": [{"id": "status", "name": "Status", "type": "select", "options": [
                {"id": "todo", "name": "Offen"}, {"id": "done", "name": "Erledigt"}]},
                {"id": "tags", "name": "Tags", "type": "multi_select", "options": [{"id": "t3", "name": "Server"}, {"id": "t4", "name": "Netz"}]},
                {"id": "notiz", "name": "Notiz", "type": "text"}, {"id": "betrag", "name": "Betrag", "type": "number"}],
                "views": [{"id": "v", "name": "Offen", "type": "table", "filters": [{"pid": "status", "op": "is_not", "value": "done"}],
                           "sorts": [{"pid": "betrag", "dir": "desc"}]}]}
        with connect() as c, c:
            db = ins(c, f"DB {k}", kind="database", schema=schema)
            rows = []
            for i in range(r.randint(20, 120)):
                x = rrow(r, schema, i)
                # ohne NaN (JSON-Antworten erlauben kein NaN - so schon vor 2.2)
                x["props"] = json.loads(json.dumps(x["props"]).replace("NaN", "null"))
                raw = json.dumps(x["props"], ensure_ascii=r.random() < 0.3) if x["props"] else None
                rows.append(ins(c, x["title"], db, props=raw, archived=time.time() if r.random() < 0.05 else None,
                                tpl=1 if r.random() < 0.03 else 0))
        qs = ["server", "Server", "erver", "SERV", "netz", "offen", "ок", "ภาษา", "i̇", "σ", "a,b", "2026", "true", "ﬁ", "o1",
              "zzz-nichts", "an\"f", "Seite"]
        for q in qs:
            for extra in ({"view": "v"}, {"view": "v", "limit": 7, "offset": r.randint(0, 5)}, {}):
                params = dict(extra, q=q)
                s, raw = get(f"/api/notes/{db}/rows?" + urllib.parse.urlencode(params), A)
                with NoPrefilter():
                    try:
                        exp = _db_rows(A, db, query=dbquery.parse_args(params), viewer=A)
                        exp = dict(exp, role="owner")
                        good = s == 200 and raw == JSONResponse(exp).body          # byte-gleich
                    except dbquery.JsError:
                        good = s == 400
                n += 1
                if not good:
                    bad.append((k, params, s, raw[:300]))
    ok(f"HTTP: /rows mit Suche (Server mit Vorfilter) = Referenz ohne Vorfilter ({n} Anfragen)", not bad, bad[:3])


def cleanup():
    from saga.userdata import delete_user_data
    if auth.get_user(A):
        delete_user_data(A)
    with connect() as c, c:
        c.execute("UPDATE note SET parent_id=NULL WHERE uid=?", (A,))
        c.execute("DELETE FROM note WHERE uid=?", (A,))
        c.execute("DELETE FROM users WHERE username=?", (A,))


def main():
    from saga.i18n import set_lang
    set_lang("de")
    cleanup()
    auth.create_user(A, "Zeilen-Pass-1234", "ZS")
    try:
        vectors()
        randoms()
        http_round()
        ok("Keine Serverfehler", n500[0] == 0, n500)
    finally:
        cleanup()
    n_ok = sum(res)
    print(f"\n{n_ok}/{len(res)} " + ("ALLES GRUEN" if n_ok == len(res) else "FEHLER"))


if __name__ == "__main__":
    main()
