#!/usr/bin/env python3
"""Testvektoren der Tageszeile "Kurz gesagt" (saga-today/v1, Bugs-DB 2271 K-D) erzeugen bzw. pruefen.

  python3 tools/today_vectors.py gen      tests/today_vectors.json neu schreiben (feste Fakten/Zeit -> gleiche Ausgabe)
  python3 tools/today_vectors.py check    Datei gegen den heutigen Server-Wortlaut pruefen (Exit 1 bei Abweichung)

Die Antworten kommen aus saga.ai_today.build() - derselben Funktion, die GET /api/ai/today nach dem Einsammeln der Fakten
aufruft (rein, ohne Datenbank und Netz). Aendert sich ein Baustein (saga/say/*.json) oder ein Systemtext (saga/i18n), schlaegt
check fehl: dann gen und die Aenderung im Bericht nennen (Web/iOS/Android lesen dieselbe Datei). Die Gegenprobe ueber HTTP
mit echten Aufgaben, Auftraegen und Kalender macht tests/kollegin_ideen.py (Fall "normal" Wort fuer Wort).
Format und Anzeige-Regeln: Kopf der Datei ("fields", "display"), docs/APP-API-KI.md §13."""
import copy
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tests", "today_vectors.json")
FORMAT = "saga-today/v1"
TZ = ZoneInfo("Europe/Berlin")
PERSONA = {"name": "Marin", "form": "sie", "lively": "warm", "address": "du"}
WAIT_T = {"type": "jobs", "group": "wartet_auf_dich"}


def _env():
    os.environ.setdefault("SECRET_KEY", "today-vectors-" + "x" * 32)
    os.environ.setdefault("BASE_URL", "http://127.0.0.1:1")
    os.environ.setdefault("DATABASE_URL", "postgresql://saga@/saga?host=/nonexistent")
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)


def at(s):
    return datetime.fromisoformat(s).timestamp()


# --- feste Fakten (Demo-Welt Anna/Ben/Clara/Lena) ------------------------------------------------------------------
def F(kind, key, title, target, **kw):
    from saga.ai_today import Fact
    return Fact(kind, key, {"event": 1, "over": 2, "due": 3, "tomorrow": 4}[kind], dict(title=title, **kw), target)


def ev(title, time, day="2026-07-07", i=0):
    return F("event", f"e:{i}", title, {"type": "events", "date": day}, time=time, sort=f"{day}T{time}")


def tomorrow(title, time, day="2026-07-08", i=9):
    return F("tomorrow", f"e:{i}", title, {"type": "events", "date": day}, time=time, sort=f"{day}T{time}")


def over(title, tid, n=1):
    first = F("over", f"t:{tid}", title, {"type": "task", "id": tid}, prio=1, due="2026-07-05", time="")
    return [first] + [F("over", f"t:{tid + 100 + i}", f"Rest {i}", {"type": "task", "id": tid + 100 + i}, prio=2,
                        due="2026-07-06", time="") for i in range(n - 1)]


def due(title, target, n=1):
    first = F("due", f"r:{target['id']}", title, target, prio=1, due="2026-07-07", time="")
    return [first] + [F("due", f"t:{900 + i}", f"Mehr {i}", {"type": "task", "id": 900 + i}, prio=2, due="2026-07-07", time="")
                      for i in range(n - 1)]


def resp(fs, lang="de", now="2026-07-07T07:40:00+02:00", name="Anna", address="du", **kw):
    from saga import ai_today
    kw.setdefault("events_n", sum(1 for f in fs if f.kind == "event"))
    return ai_today.build(fs, now=at(now), tz=TZ, lang=lang, persona=dict(PERSONA, address=address), name=name, **kw)


def show(d):
    """Was ein Client zeigt - nur aus der Antwort abgeleitet (Regeln im Kopf 'display')."""
    if not isinstance(d, dict) or not d.get("enabled"):
        return {"kurz": None, "pill": None, "ask": None, "waiting": None}
    ln = d.get("line")
    return {"kurz": {"label": d["label"], "parts": [p["text"] for p in ln["parts"]], "a11y": ln["a11y"]} if ln else None,
            "pill": {"text": d["quiet"]["text"], "undo": d["quiet"]["undo"]} if d.get("quiet") and not d.get("ask") else None,
            "ask": d.get("ask"), "waiting": d["waiting"]["text"] if d.get("waiting") else None}


def wl(d):
    from saga.ai_today import WIDGET_KEYS
    return {k: d[k] for k in WIDGET_KEYS if k in d}


def wshow(resp_):
    ln = resp_.get("line")
    if not ln or not ln.get("enabled"):
        return {"small": "counts", "medium": None, "lock": "counts"}
    return {"small": "counts", "lock": "counts", "medium": ln["line"]["text"] if ln.get("line") else None,
            "waiting": ln["waiting"]["text"] if ln.get("waiting") else None}


def req(lang="de", now="2026-07-07T07:40:00+02:00", account=None, path="/api/ai/today?tz=Europe/Berlin", method="GET",
        body=None, auth="session", headers=None):
    r = {"method": method, "path": path, "lang": lang, "auth": auth, "now": now,
         "account": account or {"name": "Anna", "salutation": "du"}}
    if headers:
        r["headers"] = headers
    if body is not None:
        r["body"] = body
    return r


# --- Faelle ---------------------------------------------------------------------------------------------------------
def build_all():
    _env()
    from saga import ai_today
    cases = []

    def case(id_, desc, rq, rs, status=200, headers=None, show_=None):
        c = {"id": id_, "desc": desc, "request": rq, "status": status}
        if headers is not None:
            c["headers"] = headers
        c["response"] = rs
        c["show"] = show_ if show_ is not None else show(rs)
        cases.append(c)

    NORMAL_F = [ev("Elternabend", "19:30")] + over("Pavillon abholen", 7, 3) + due("Kuchen backen", {"type": "row", "id": 512})
    normal = resp(NORMAL_F, waiting_n=1)
    case("normal", "Normaler Tag: Termin, wichtigste überfällige Aufgabe (+2 weitere), heute fällige Zeile einer Aufgabenquelle; "
         "1 Auftrag wartet. Server-Gegenprobe Wort für Wort (tests/kollegin_ideen.py).",
         req(), normal, headers={"ETag": '"%s"' % normal["v"], "Cache-Control": "private, no-cache"})
    case("nicht_geaendert", "Gleicher Stand: If-None-Match mit dem ETag aus 'normal' -> 304 ohne Inhalt, Anzeige bleibt.",
         req(headers={"If-None-Match": '"%s"' % normal["v"]}), None, status=304, headers={"ETag": '"%s"' % normal["v"]},
         show_=show(normal))

    en = resp([ev("Dentist", "14:00"), ev("School play", "17:00", i=1)] + due("Call Clara", {"type": "task", "id": 31}),
              lang="en", now="2026-07-07T12:10:00+02:00", name="Ben")
    case("normal_en", "Englisch, Mittag: zwei Termine (+1), eine heute fällige Aufgabe, nichts wartet (waiting null). "
         "Uhrzeit wie im Kalender (en-US 12 h, schmales Leerzeichen vor AM/PM).",
         req("en", "2026-07-07T12:10:00+02:00", {"name": "Ben", "salutation": "du"}), en)
    es = resp([ev("Reunión de padres", "19:30")] + over("Recoger la carpa", 7, 3), lang="es", name="Clara", waiting_n=2)
    case("normal_es", "Spanisch: Termin und überfällige Aufgabe, 2 Aufträge warten (Plural).",
         req("es", account={"name": "Clara", "salutation": "du"}), es)

    case("ruhig", "Ruhiger Tag: nichts Nennenswertes -> line null und waiting null; Clients zeigen an dieser Stelle nichts "
         "(kein Platzhalter).", req(), resp([]))

    QU = at("2026-07-08T05:00:00+02:00")
    ruhe = resp(NORMAL_F, now="2026-07-07T08:15:00+02:00", waiting_n=1, quiet_until=QU,
                st={"x": [at("2026-07-07T08:15:00+02:00")]})
    case("ruhe", "Nach ✕ (oder Wischen): Ruhe bis morgen 05:00 Ortszeit auf allen Geräten. line null, an ihrer Stelle die "
         "Pille (gleiche Höhe); 'Wartet auf dich' bleibt. GET liefert danach dasselbe.",
         req(method="POST", path="/api/ai/today/quiet?tz=Europe/Berlin", now="2026-07-07T08:15:00+02:00"), ruhe)
    frage = resp(NORMAL_F, now="2026-07-07T08:15:00+02:00", waiting_n=1, quiet_until=QU,
                 st={"x": [at("2026-07-01T09:00:00+02:00"), at("2026-07-04T09:00:00+02:00"), at("2026-07-07T08:15:00+02:00")]})
    case("frage", "Drittes ✕ in 14 Tagen: Ruhe wie 'ruhe' und einmal die Frage (statt der Pille). Ja -> PUT {on:false, "
         "asked:true} ('aus'), Nein -> PUT {asked:true} ('ruhe'). Danach nie wieder.",
         req(method="POST", path="/api/ai/today/quiet?tz=Europe/Berlin", now="2026-07-07T08:15:00+02:00"), frage)
    case("aus", "Tageszeile ausgeschaltet (Frage mit Ja beantwortet, ⋯ 'Tageszeile ausblenden' oder Einstellungen › Saga): "
         "line null, keine Pille; 'Wartet auf dich' bleibt.",
         req(method="PUT", body={"on": False, "asked": True}), resp(NORMAL_F, waiting_n=1, st={"off": True, "asked": True}))
    case("wieder_an", "'Wieder an' an der Pille: DELETE /api/ai/today/quiet -> Antwort wie GET, Zeile wieder da.",
         req(method="DELETE", path="/api/ai/today/quiet?tz=Europe/Berlin"), normal)
    case("ohne_ai", "Konto ohne Fähigkeit 'ai': 200 {enabled:false} - nichts zeigen, auch kein 'Wartet auf dich'.",
         req(account={"name": "Clara", "salutation": "du", "ai": False}), {"enabled": False})
    case("kalt", "Kalender noch nicht im Speicher (z. B. nach Neustart): Antwort sofort ohne Termine, pending ['calendar'], "
         "counts.events null (= noch unbekannt). Sobald der Kalender da ist, kommt ai.today.changed - dann neu laden "
         "(-> 'normal').", req(), resp(NORMAL_F[1:], waiting_n=1, pending=("calendar",), events_n=None))
    case("abend", "Ab 18:00, wenn heute nichts mehr kommt: erster Termin von morgen (kind tomorrow).",
         req(now="2026-07-07T19:05:00+02:00"), resp([tomorrow("Zahnarzt", "08:00")], now="2026-07-07T19:05:00+02:00"))
    case("sie", "Anrede Sie (Lena): Teile unverändert, 'Wartet auf Sie', info gesiezt.",
         req(account={"name": "Lena", "salutation": "sie"}),
         resp([ev("Zahnarzt", "10:00")] + due("Steuererklärung abgeben", {"type": "task", "id": 44}), name="Lena", address="sie",
              waiting_n=2))
    case("th", "Thai: Teile ohne Höflichkeitspartikel (ค่ะ/ครับ), nur sentence mit Partikel der Persona (Marin: sie -> ค่ะ). "
         "Zeilenhöhe >= 1,6.",
         req("th"), resp([ev("ประชุมผู้ปกครอง", "19:30")] + over("รับเต็นท์", 7, 3) + due("อบเค้ก", {"type": "row", "id": 512}),
                         lang="th", waiting_n=1))
    case("ru", "Russisch: Plural in 'waiting' (1 / 2–4 / 5+, siehe waiting_plural).",
         req("ru", account={"name": "Ben", "salutation": "du"}),
         resp([ev("Родительское собрание", "19:30")] + over("Забрать шатёр", 7, 5) + due("Испечь торт", {"type": "row", "id": 512}),
              lang="ru", name="Ben", waiting_n=2))
    from saga import i18n
    case("schluessel", "Bearer-Schlüssel (sga_, OAuth, hk_) an /api/ai/today* -> 403; das Widget nimmt /api/app/today?line=1.",
         req(auth="hk_"), {"error": i18n.t_lang("de", "Die Tageszeile gibt es nur in Saga selbst – nicht über API-Schlüssel.")},
         status=403, show_=show(None))
    case("alter_server", "Server ohne Tageszeile: 404 -> nichts zeigen, kein Fehlerhinweis.",
         req(), {"error": "Not Found"}, status=404, show_=show(None))

    W_BASE = {"date": "2026-07-07",
              "events": [{"summary": "Elternabend", "start": "2026-07-07T19:30:00+02:00", "end": "2026-07-07T21:00:00+02:00",
                          "allday": False, "location": "", "color": "#4184e4", "calendar": "Familie"}],
              "tasks": [{"id": 7, "text": "Pavillon abholen", "due": "2026-07-05", "prio": 1, "overdue": True}], "errors": []}
    wp = "/api/app/today?line=1&tz=Europe/Berlin"
    for id_, desc, part in (
            ("widget", "Widget (hk_): GET /api/app/today?line=1&tz=… hängt 'line' an (ohne sentence/ask/info). Mittel/Groß "
             "zeigen line.line.text (privacySensitive), Klein und Sperrbildschirm nur counts.", wl(normal)),
            ("widget_ruhe", "Widget bei aktiver Ruhe: line.line null, line.quiet gesetzt -> Mittel/Groß ohne Zeile (keine "
             "Pille im Widget).", wl(ruhe)),
            ("widget_ohne_ai", "Widget, Konto ohne 'ai': line {enabled:false} -> wie bisher.", {"enabled": False})):
        rs = dict(W_BASE, line=part)
        case(id_, desc, req(path=wp, auth="hk_"), rs, show_=wshow(rs))
    case("widget_alt", "Älterer Server bzw. ohne line=1: Antwort ohne 'line' (unverändert) -> wie bisher.",
         req(path=wp, auth="hk_"), W_BASE, show_={"small": "counts", "medium": None, "lock": "counts"})

    plural = {}
    for lang in ("de", "en", "es", "ru", "th"):
        for addr in ("du", "sie"):
            if addr == "sie" and lang in ("en", "th"):
                continue
            plural.setdefault(lang, {})[addr] = {str(n): ai_today._say(lang, dict(PERSONA, address=addr), part="waiting", n=n)
                                                 for n in (1, 2, 5, 21)}
    return {
        "format": FORMAT,
        "note": ("Gemeinsame Testvektoren der Tageszeile „Kurz gesagt“ auf „Heute“ (Bugs-DB 2271 K-D, docs/APP-API-KI.md §13). "
                 "Web (static/heute-kurz.js, tests/browser_kollegin_heute.js), iOS (Shared/TodayLineLogic.swift, "
                 "Tests/Logic/TodayLineTests.swift) und Android (data/TodayLine.kt, TodayLineTest.kt) decodieren jede 'response' "
                 "und prüfen die Anzeige gegen 'show'. Erzeugt mit tools/today_vectors.py gen aus saga.ai_today.build() - also "
                 "genau der Wortlaut des Servers; tests/kollegin_ideen.py prüft die HTTP-Antwort für 'normal' Wort für Wort. "
                 "Texte sind fertige Sätze des Servers in der Sprache der Anfrage - Clients zeigen sie unverändert, bauen keine "
                 "Sätze und vergleichen keine Texte, um Zustände zu erkennen. Unbekannte Felder ignorieren (spätere Server hängen an)."),
        "fields": {
            "enabled": "false = Konto ohne Fähigkeit 'ai' -> sonst keine Felder, nichts zeigen",
            "on": "Schalter der Person (PUT on); false -> line null, waiting bleibt",
            "day": "Tag der Person YYYY-MM-DD in tz", "tz": "IANA-Zone der Berechnung (?tz=, sonst Konto)",
            "v": "Stand (12 Hex, undurchsichtig) = ETag; If-None-Match -> 304; Live ai.today.changed {v}",
            "label": "Überschrift der Zeile ('Kurz gesagt')",
            "line": "null (ruhiger Tag, Ruhe, aus) oder {text, a11y, parts}; text = parts[].text mit ' · ' verbunden",
            "line.parts[]": "{kind: event|over|due|tomorrow, text, n?, target}; n = Anzahl dieser Art, wenn > 1 (Text endet dann auf '(+n-1)')",
            "target": "{type: task, id} | {type: row, id} | {type: tasks, filter: overdue|today} | {type: events, date} | {type: jobs, group}; öffnet nur, hakt nie ab; unbekannter type -> Teil nicht antippbar",
            "a11y": "Vorlesetext der ganzen Zeile (ohne Symbole); ohne aria-live, nie Fokus",
            "sentence": "Gruß + Tag als ganzer Satz in der Persona (für K-G/Siri) - noch nicht anzeigen",
            "waiting": "null oder {n, text, target} - eigene Zeile darunter, auch bei on false / Ruhe",
            "counts": "{events, over, due, waiting} - Zahlen (Sperrbildschirm/Widget klein); events null = Kalender noch unbekannt",
            "quiet": "null oder {until (ISO mit Versatz), text, undo} -> Pille statt Zeile, undo = DELETE /api/ai/today/quiet",
            "ask": "null oder {text, yes, no} -> einmalige Frage statt der Pille; yes = PUT {on:false, asked:true}, no = PUT {asked:true}",
            "pending": "noch fehlende Quellen (z. B. ['calendar']) - danach kommt ai.today.changed",
            "info": "Text hinter ⓘ / 'Warum sehe ich das?'"},
        "display": [
            "enabled false oder Status 4xx/5xx: nichts zeigen (kein Fehlerhinweis).",
            "line gesetzt: label + parts (Knöpfe, >= 44 pt / 48 dp, Web >= 32 px), daneben ✕ (= POST /api/ai/today/quiet) und ⋯.",
            "quiet gesetzt und ask null: Pille quiet.text + Knopf quiet.undo (gleiche Höhe wie die Zeile).",
            "ask gesetzt: Frage ask.text mit Knöpfen ask.yes / ask.no (statt der Pille).",
            "line, quiet und ask null: an der Stelle nichts.",
            "waiting gesetzt: eigene Zeile waiting.text, Tipp -> Saga › Aufträge, Gruppe 'Wartet auf dich'.",
            "Widget: Mittel/Groß line.line.text (privacySensitive), Klein und Sperrbildschirm nur counts; ohne 'line' wie bisher."],
        "live": {"event": "ai.today.changed", "data": {"v": normal["v"]}, "debounce_ms": 400,
                 "note": "neu laden, wenn data.v != eigenes v (Aufgaben, Aufträge in/aus 'Wartet auf dich', Kalender fertig, "
                         "Ruhe/Schalter auf einem anderen Gerät). v ist in Konto-Sprache und -Zone gerechnet - weicht die des "
                         "Geräts ab, liefert das Neuladen eben 304."},
        "waiting_plural": plural,
        "cases": cases,
    }


def dump(d):
    return json.dumps(d, ensure_ascii=False, indent=1) + "\n"


def check():
    """-> Liste der Abweichungen (leer = Datei passt zum Server-Wortlaut)."""
    want = build_all()
    try:
        have = json.load(open(PATH, encoding="utf-8"))
    except (OSError, ValueError) as ex:
        return [f"Datei: {ex}"]
    out = []
    if have.get("format") != FORMAT:
        out.append("format")
    hc = {c["id"]: c for c in have.get("cases") or ()}
    for c in want["cases"]:
        h = hc.get(c["id"])
        if h is None:
            out.append(f"fehlt: {c['id']}")
        elif h != c:
            out.append(f"weicht ab: {c['id']}")
    out += [f"zusaetzlich: {k}" for k in set(hc) - {c["id"] for c in want["cases"]}]
    for k in ("fields", "display", "live", "waiting_plural"):
        if have.get(k) != want[k]:
            out.append(f"weicht ab: {k}")
    return out


def main():
    cmd = (sys.argv[1:] or ["check"])[0]
    if cmd == "gen":
        d = build_all()
        with open(PATH, "w", encoding="utf-8") as f:
            f.write(dump(copy.deepcopy(d)))
        print(f"{PATH}: {len(d['cases'])} Faelle")
        return 0
    probs = check()
    for p in probs:
        print("FAIL", p)
    print("today_vectors:", "OK" if not probs else f"{len(probs)} Abweichungen")
    return 1 if probs else 0


if __name__ == "__main__":
    sys.exit(main())
