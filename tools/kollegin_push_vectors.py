#!/usr/bin/env python3
"""Testvektoren fuer Mitteilungen und Aktionen der Kollegin (saga-push-kollegin/1, Bugs-DB 2270 K-C, FEATURES jobpush1).

  python3 tools/kollegin_push_vectors.py gen      tests/vectors/push_kollegin.json neu schreiben (feste Seeds -> gleiche Ausgabe)
  python3 tools/kollegin_push_vectors.py check    Datei gegen diese unabhaengige Umsetzung pruefen (Exit 1 bei Abweichung)

Vertrag: docs/APP-API.md "Push-Nutzlast 3 und Auftraege (jobpush1)", docs/APP-API-KI.md Abschnitt 12, docs/PUSH-RELAY.md.
Bewusst unabhaengig von saga/push.py: Verschluesselung (RFC 8291 mit Auffuellen), APNs-Planung und collapse-id kommen aus
tools/push_e2e_vectors.py (nur "cryptography"), dieselben App-Schluessel wie tests/vectors/push_e2e.json. Die Saetze t2
sind feste Beispiele (so baut sie der Server heute aus saga/say/*.json, Kanal push) - die Apps zeigen t1/t2 nur an und
rechnen sie nie selbst; die Gegenprobe des Servers macht tests/kollegin_push.py. Alles andere (Kategorie, Android-Kanal,
Knoepfe nach K15, Klartext-Wege, Ablaufzeiten) rechnet diese Datei aus Regeln nach, die die Apps ebenso umsetzen."""
import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import push_e2e_vectors as pe  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tests", "vectors", "push_kollegin.json")
E2E_PATH = os.path.join(ROOT, "tests", "vectors", "push_e2e.json")
FORMAT = "saga-push-kollegin/1"
NOW = pe.NOW
LANGS = ("de", "en", "es", "ru", "th")
PERSONA = "Marin"
BASE_URL = "https://saga.example.org"
PAYLOAD_V = 3
KINDS = {"needs": "job.needs_you", "done": "job.done", "failed": "job.failed"}
UPDATE_KIND = "job.changed"
GROUP_OF = {"wartet_dich": "needs", "wartet_ok": "needs", "erledigt": "done", "fehlgeschlagen": "failed"}
SILENT_STATES = ("abgebrochen", "zurueckgenommen")        # nie eine Mitteilung; nur stilles update removed
T1_MAX = 60                                               # Grapheme des Auftragstitels in t1 (gekuerzt wie saga.ai_say._sv)
T2_MAX = 100                                              # Grapheme, Kanal push (saga.ai_say)
ACT_TTL, ACT_MAX = 24 * 3600, 14 * 86400
LATER_S, LATER_MAX, QUIET_HOUR, QUIET_WINDOW = 3 * 3600, 2, 7, 60
TOK_BYTES = 18

# Kategorien (iOS categoryIdentifier, Web/Android/Linux gleiche Namen) -> Knoepfe in dieser Reihenfolge
CATS = {
    "saga.job.needs": ["open", "later"],
    "saga.job.needs.ok": ["approve", "open", "later"],
    "saga.job.done": ["undo", "quiet"],
    "saga.job.done.plain": ["quiet"],
    "saga.job.failed": ["open"],
}
CHANNELS = [
    {"id": "saga.jobs.needs.v1", "name": "Braucht dich", "importance": "high", "group": "G_NOTIFY", "states": ["needs"]},
    {"id": "saga.jobs.feedback.v1", "name": "Rückmeldungen", "importance": "default", "group": "G_NOTIFY",
     "states": ["done", "failed"]},
]
CHANNEL_OF = {"needs": "saga.jobs.needs.v1", "done": "saga.jobs.feedback.v1", "failed": "saga.jobs.feedback.v1"}
REST = ("later", "quiet")                                  # Ruhe-Knoepfe: gehen gesperrt, auf Uhr/Auto, mit hk_
AUTH = ("approve", "undo")                                 # nur entsperrt (iOS .authenticationRequired, Android 12+)
RESERVED = ("retry", "reply", "ideas_off")                 # spaeter (K-D/K-E); heute 409 code open
ACTIONS = [
    {"a": "approve", "token": True, "auth": True, "foreground": False},
    {"a": "undo", "token": True, "auth": True, "foreground": False},
    {"a": "later", "token": True, "auth": False, "foreground": False},
    {"a": "quiet", "token": True, "auth": False, "foreground": False},
    {"a": "open", "token": False, "auth": False, "foreground": True},
]
DEVICES = ("phone", "phone_noauth", "watch", "car", "web")

TEXTS = {
    "de": {"title.feedback": "{name} · KI – Rückmeldung", "title.needs": "{name} · KI – braucht dich", "body": "In Saga ansehen",
           "t1.done": "Erledigt: {text}", "t1.failed": "Hat nicht geklappt: {text}", "t1.needs": "Wartet auf dich: {text}",
           "t2.plan": "Der Plan steht und wartet auf ein OK.", "hint": "Bitte in Saga ansehen",
           "btn.approve": "Passt, los", "btn.undo": "Rückgängig", "btn.later": "Später", "btn.quiet": "Gerade nicht",
           "btn.open": "Ansehen", "ch.needs": "Braucht dich", "ch.feedback": "Rückmeldungen"},
    "en": {"title.feedback": "{name} · AI – update", "title.needs": "{name} · AI – needs you", "body": "View in Saga",
           "t1.done": "Done: {text}", "t1.failed": "Didn't work: {text}", "t1.needs": "Needs you: {text}",
           "t2.plan": "The plan is ready and waiting for an OK.", "hint": "Please take a look in Saga",
           "btn.approve": "Go ahead", "btn.undo": "Undo", "btn.later": "Later", "btn.quiet": "Not now",
           "btn.open": "View", "ch.needs": "Needs you", "ch.feedback": "Updates"},
    "es": {"title.feedback": "{name} · IA – novedades", "title.needs": "{name} · IA – te necesita", "body": "Ver en Saga",
           "t1.done": "Hecho: {text}", "t1.failed": "No ha funcionado: {text}", "t1.needs": "Te necesita: {text}",
           "t2.plan": "El plan está listo y espera un OK.", "hint": "Échale un vistazo en Saga",
           "btn.approve": "Adelante", "btn.undo": "Deshacer", "btn.later": "Más tarde", "btn.quiet": "Ahora no",
           "btn.open": "Ver", "ch.needs": "Te necesita", "ch.feedback": "Novedades"},
    "ru": {"title.feedback": "{name} · ИИ – новости", "title.needs": "{name} · ИИ – ждёт тебя", "body": "Открыть в Saga",
           "t1.done": "Готово: {text}", "t1.failed": "Не получилось: {text}", "t1.needs": "Ждёт тебя: {text}",
           "t2.plan": "План готов и ждёт подтверждения.", "hint": "Посмотри в Saga",
           "btn.approve": "Давай", "btn.undo": "Отменить", "btn.later": "Позже", "btn.quiet": "Не сейчас",
           "btn.open": "Открыть", "ch.needs": "Ждёт тебя", "ch.feedback": "Новости"},
    "th": {"title.feedback": "{name} · AI – อัปเดต", "title.needs": "{name} · AI – ต้องการคุณ", "body": "ดูใน Saga",
           "t1.done": "เสร็จแล้ว: {text}", "t1.failed": "ไม่สำเร็จ: {text}", "t1.needs": "ต้องการคุณ: {text}",
           "t2.plan": "แผนพร้อมแล้ว รอการยืนยัน", "hint": "โปรดดูใน Saga",
           "btn.approve": "ตกลง ทำเลย", "btn.undo": "เลิกทำ", "btn.later": "ภายหลัง", "btn.quiet": "ไม่ใช่ตอนนี้",
           "btn.open": "ดู", "ch.needs": "ต้องการคุณ", "ch.feedback": "อัปเดต"},
}

# Beispielsaetze t2 (Kanal push, Persona Marin "sie", warm, du) - so liefert sie saga.ai_say heute; Apps zeigen sie nur an
F_DONE = {"changes": [{"kind": "task", "title": "Grillkohle besorgen"}, {"kind": "event", "title": "Grillabend"}]}
T2_DONE = {
    "de": "Aufgabe „Grillkohle besorgen“ angelegt. „Grillabend“ steht im Kalender.",
    "en": "Task “Grillkohle besorgen” added. “Grillabend” is in the calendar.",
    "es": "Tarea «Grillkohle besorgen» creada. «Grillabend» está en el calendario.",
    "ru": "Задача «Grillkohle besorgen» создана. «Grillabend» – в календаре.",
    "th": "สร้างงาน “Grillkohle besorgen” แล้วค่ะ “Grillabend” อยู่ในปฏิทินแล้วค่ะ",
}
F_ROW = {"changes": [{"kind": "note", "title": "Einkaufsliste"}]}
T2_ROW = {
    "de": "Auf „Einkaufsliste“ eingetragen.", "en": "Added to “Einkaufsliste”.", "es": "Apuntado en «Einkaufsliste».",
    "ru": "Внесено на странице «Einkaufsliste».", "th": "บันทึกไว้ในหน้า “Einkaufsliste” แล้วค่ะ",
}
F_RECHT = {"reason": "recht", "changes": []}
T2_RECHT = {
    "de": "Dafür fehlt mir jetzt das Recht. Der Auftrag wartet. Geändert wurde nichts.",
    "en": "I'm no longer allowed to do this. The job is waiting. Nothing was changed.",
    "es": "Ya no tengo permiso para esto. El encargo está en espera. No se ha cambiado nada.",
    "ru": "Для этого больше нет прав. Задание ждёт. Ничего не изменено.",
    "th": "ตอนนี้ไม่มีสิทธิ์ทำสิ่งนี้แล้ว งานนี้กำลังรออยู่ค่ะ ไม่มีการเปลี่ยนแปลงใดๆค่ะ",
}
F_KONFLIKT = {"reason": "konflikt", "changes": []}
T2_KONFLIKT = {
    "de": "Eine Seite wurde inzwischen geändert – überschrieben habe ich nichts. Geändert wurde nichts.",
    "en": "A page was changed in the meantime – I didn't overwrite anything. Nothing was changed.",
    "es": "Una página ha cambiado mientras tanto; no he sobrescrito nada. No se ha cambiado nada.",
    "ru": "Одна из страниц за это время изменилась – ничего не перезаписано. Ничего не изменено.",
    "th": "มีหน้าหนึ่งถูกแก้ไขไปแล้วในระหว่างนี้ จึงไม่ได้เขียนทับอะไรค่ะ ไม่มีการเปลี่ยนแปลงใดๆค่ะ",
}
F_PLAN = {"n": 2}
TH_BASE = "รายการซื้อของ สำหรับงานเลี้ยงปิ้งย่าง ของครอบครัว ที่สวนหลังบ้าน ในวันเสาร์นี้ตอนเย็น พร้อมถ่านและน้ำแข็ง และของหวานสำหรับเด็ก ๆ"
F_TH = {"changes": [{"kind": k, "title": TH_BASE + " " + s} for k, s in
                    (("note", "หน้าแรก"), ("row", "แถวที่สอง"), ("task", "งานที่สาม"), ("event", "นัดหมายที่สี่"), ("comment", "ความเห็นที่ห้า"))]}
T2_TH = "บันทึกไว้ในหน้า “รายการซื้อของ สำหรับงานเลี้ยงปิ้งย่าง ของครอบครัว ที่สวนหลังบ้าน…” แล้วค่ะ"
TITLE = "Grillkohle besorgen"
TITLE_TH = "เตรียมงานเลี้ยงปิ้งย่างของครอบครัวที่สวนหลังบ้านในวันเสาร์นี้ตอนเย็น พร้อมซื้อถ่าน น้ำแข็ง และของหวาน"
# Kanarien: duerfen nie im sichtbaren Teil (aps), auf Klartext-Wegen, in Topics/collapse-ids oder Klartext-Koepfen stehen
CANARIES = ("Grillkohle", "Grillabend", "Einkaufsliste", "รายการซื้อของ", "เตรียมงานเลี้ยงปิ้งย่าง")


# --- Regeln -------------------------------------------------------------------------------------------------------------
def graphemes(s):
    """Sichtbare Zeichen wie saga.ai_say.graphemes (kombinierende Zeichen, auch Thai-Vokal-/Tonzeichen, zaehlen nicht)."""
    n, join = 0, False
    for ch in s:
        o = ord(ch)
        if ch == "‍":
            join = True
            continue
        if unicodedata.category(ch) in ("Mn", "Me", "Cf") or 0xFE00 <= o <= 0xFE0F or 0x1F3FB <= o <= 0x1F3FF or 0xE0020 <= o <= 0xE007F:
            continue
        if join:
            join = False
            continue
        n += 1
    return n


def cut_graphemes(s, n):
    """Die ersten n Grapheme (nie mitten in einem Graphem), ohne "…" - wie saga.ai_say.cut_graphemes (2543)."""
    out = ""
    for ch in s:
        if graphemes(out + ch) > n:
            break
        out += ch
    return out


_SENT = re.compile(r"(?<=[.!?…])\s+")


def cut(s, budget):
    """Wie saga.ai_say._cut (lang en): am Satzende, sonst an der letzten Wortgrenze, sonst an einer Graphem-Grenze
    (2543: Thai/CJK ohne Leerzeichen), dann "…"."""
    if graphemes(s) <= budget:
        return s
    out = []
    for x in [x for x in _SENT.split(s.strip()) if x]:
        if graphemes(" ".join(out + [x])) > budget:
            break
        out.append(x)
    if out:
        return " ".join(out)
    c = ""
    for w in re.split(r"(\s+)", s):
        if graphemes(c + w) > budget - 1:
            break
        c += w
    if not c.strip():
        c = cut_graphemes(s, budget - 1)
    return c.rstrip() + "…"


def sv(v, n):
    """Wie saga.ai_say._sv: eine Zeile ohne Steuerzeichen und Markierungen, gekuerzt auf n Grapheme."""
    s = re.sub(r"[\x00-\x1f\x7f\u2028\u2029]+", " ", str(v if v is not None else ""))
    s = re.sub(r"[<>`*_#\[\]]", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return cut(s, n) if graphemes(s) > n else s


def t1(lang, group, title):
    return TEXTS[lang]["t1." + group].format(text=sv(title, T1_MAX))


def head(lang, group):
    """title/body (neutral, auf allen Wegen gleich) - ohne Auftragstitel, ohne Namen von Personen."""
    return TEXTS[lang]["title.needs" if group == "needs" else "title.feedback"].format(name=PERSONA), TEXTS[lang]["body"]


def atok(name):
    """Beispiel-Token (Server: secrets.token_urlsafe(18) -> 24 Zeichen Base64url; in der DB nur sha256-Hex)."""
    return pe.b64u(pe.h32("atok:" + name))[:24]


def tok_hash(t):
    return hashlib.sha256(t.encode()).hexdigest()


def issue(state, source, steps, e2e, caps, content="unlocked", changes=1):
    """Was der Server beim Zustellen an EIN Abo mitgibt -> None (keine Mitteilung) oder
    {group, kind, detail: bool, actions: [..], category, channel}. steps: [(klass, undo_class)], read-Schritte undo_class None."""
    if state in SILENT_STATES or state not in GROUP_OF or (state == "wartet_ok" and source == "saga"):
        return None
    group = GROUP_OF[state]
    detail = bool(e2e and "detail" in caps and content != "never")
    acts = []
    if e2e and "actions" in caps:
        own = [u for k, u in steps if k == "own"]
        if state == "wartet_ok":
            if detail and steps and all(k in ("read", "own") for k, _ in steps) and all(u == "undo" for u in own):
                acts.append("approve")
            acts.append("later")
        elif state == "wartet_dich":
            acts.append("later")
        elif state == "erledigt":
            if detail and changes and own and all(u == "undo" for u in own) and all(k in ("read", "own") for k, _ in steps):
                acts.append("undo")
            acts.append("quiet")
    return {"group": group, "kind": KINDS[group], "detail": detail, "actions": acts, "category": category(group, acts),
            "channel": CHANNEL_OF[group]}


def category(group, acts):
    if group == "needs":
        return "saga.job.needs.ok" if "approve" in acts else "saga.job.needs"
    if group == "done":
        return "saga.job.done" if "undo" in acts else "saga.job.done.plain"
    return "saga.job.failed"


def buttons(cat, locked, content, device, tokens=None):
    """K15: Knoepfe einer Mitteilung -> (buttons [{a, auth}], direct [a]). direct = ohne Entsperren ausfuehrbar.
    tokens: vorhandene Aktionen (None = alle der Kategorie). Inhalt aus (kein detail bzw. Vorschau verborgen): kein
    approve/undo. Uhr/Auto: nur Ruhe-Knoepfe. Android < 12 und Web: nie approve/undo. Web zeigt zusaetzlich "Ansehen"."""
    out = []
    for a in CATS[cat]:
        if a != "open" and tokens is not None and a not in tokens:
            continue
        if a in AUTH and (content == "off" or device in ("phone_noauth", "watch", "car", "web")):
            continue
        if device in ("watch", "car") and a not in REST:
            continue
        out.append({"a": a, "auth": a in AUTH})
    if device == "web" and not any(b["a"] == "open" for b in out):
        out.append({"a": "open", "auth": False})
    direct = [b["a"] for b in out if not (locked and b["auth"] and device in ("phone",))]
    return out, direct


def fit(msg):
    """Innerer Text hoechstens E2E_MAX Byte: zuerst detail.f weg, dann t2 kuerzen -> (msg, dropped)."""
    dropped = []
    if len(pe.plain_of(msg)) > pe.E2E_MAX and "f" in msg.get("detail", {}):
        msg["detail"].pop("f")
        dropped.append("detail.f")
    while len(pe.plain_of(msg)) > pe.E2E_MAX and msg.get("detail", {}).get("t2"):
        msg["detail"]["t2"] = cut_graphemes(msg["detail"]["t2"], graphemes(msg["detail"]["t2"]) - 1)
        if "detail.t2" not in dropped:
            dropped.append("detail.t2")
    return msg, dropped


def job_msg(jid, group, lang, e2e, pv=2, b=None, f=None, t1s=None, t2s=None, acts=(), silent=False, ts=NOW, tok_prefix=""):
    title, body = head(lang, group)
    m = {"title": title, "body": body, "url": f"/saga?tab=jobs&job={jid}" if e2e else "/saga?tab=jobs",
         "tag": f"job-{jid}" if e2e else "saga-job", "kind": KINDS[group], "ts": ts, "v": PAYLOAD_V, "lang": lang}
    if e2e:
        m.update({"group_key": f"job:{jid}", "thread": f"job-{jid}", "job": jid})
    m["state"] = group
    if e2e:
        m["pv"] = pv
    if silent:
        m["silent"] = True
    if e2e and b:
        d = {"b": b}
        if f is not None:
            d["f"] = f
        d.update({"lang": lang, "t1": t1s, "t2": t2s})
        m["detail"] = d
    if e2e and acts:
        m["actions"] = [{"a": a, "t": atok(f"{tok_prefix}{jid}:{a}")} for a in acts]
    return m


def update_msg(jid, lang):
    return {"title": "", "body": "", "url": f"/saga?tab=jobs&job={jid}", "tag": f"job-{jid}", "kind": UPDATE_KIND, "ts": NOW,
            "v": PAYLOAD_V, "lang": lang, "group_key": f"job:{jid}", "job": jid, "update": True, "removed": True}


# --- Faelle -------------------------------------------------------------------------------------------------------------
SUB_FULL = ["update", "detail", "actions"]


def _case_specs():
    """(name, Weg, Abo-caps, Eingabe {state, source, steps, content, silent, lang, ...}, Saetze)"""
    own_undo = [("read", None), ("own", "undo"), ("own", "undo")]
    return [
        ("done_undo_webpush", "webpush", SUB_FULL, {"job": 42, "state": "erledigt", "steps": own_undo, "b": "report", "f": F_DONE, "t2": T2_DONE}),
        ("done_undo_apns", "apns", SUB_FULL, {"job": 42, "state": "erledigt", "steps": own_undo, "b": "report", "f": F_DONE, "t2": T2_DONE}),
        ("done_undo_stream", "stream", SUB_FULL, {"job": 42, "state": "erledigt", "steps": own_undo, "b": "report", "f": F_DONE, "t2": T2_DONE}),
        ("done_deleteonly_unifiedpush", "unifiedpush", SUB_FULL,
         {"job": 43, "state": "erledigt", "steps": [("own", "delete_only")], "b": "report", "f": F_ROW, "t2": T2_ROW}),
        ("needs_ok_apns", "apns", SUB_FULL,
         {"job": 44, "state": "wartet_ok", "source": "share", "steps": own_undo, "b": "plan", "f": F_PLAN, "t2": "plan"}),
        ("needs_ok_out_webpush", "webpush", SUB_FULL,
         {"job": 45, "state": "wartet_ok", "source": "api", "steps": [("own", "undo"), ("out", "final")], "b": "plan", "f": F_PLAN, "t2": "plan"}),
        ("needs_you_webpush", "webpush", SUB_FULL,
         {"job": 46, "state": "wartet_dich", "steps": own_undo, "b": "fail", "f": F_RECHT, "t2": T2_RECHT}),
        ("failed_apns", "apns", SUB_FULL,
         {"job": 47, "state": "fehlgeschlagen", "steps": own_undo, "b": "fail", "f": F_KONFLIKT, "t2": T2_KONFLIKT}),
        ("done_nocaps_apns", "apns", [],
         {"job": 42, "state": "erledigt", "steps": own_undo, "b": "report", "f": F_DONE, "t2": T2_DONE}),
        ("done_content_never_webpush", "webpush", SUB_FULL,
         {"job": 42, "state": "erledigt", "steps": own_undo, "content": "never", "b": "report", "f": F_DONE, "t2": T2_DONE}),
        ("done_silent_apns", "apns", SUB_FULL,
         {"job": 42, "state": "erledigt", "steps": own_undo, "silent": True, "b": "report", "f": F_DONE, "t2": T2_DONE}),
        ("gross_th_webpush", "webpush", SUB_FULL,
         {"job": 77, "state": "erledigt", "steps": own_undo, "lang": "th", "title": TITLE_TH, "b": "report", "f": F_TH, "t2": {"th": T2_TH}}),
        ("plain_ntfy", "ntfy", [], {"job": 42, "state": "erledigt", "steps": own_undo}),
        ("plain_unifiedpush", "unifiedpush-plain", SUB_FULL, {"job": 46, "state": "wartet_dich", "steps": own_undo}),
        ("plain_apns_direct", "apns-plain", SUB_FULL, {"job": 47, "state": "fehlgeschlagen", "steps": own_undo}),
    ]


E2E_WAYS = ("webpush", "unifiedpush", "stream", "apns")


def _transport(way, msg, auth, update=False):
    """Koepfe/Planung je Weg (ohne Authorization/VAPID)."""
    tag = msg["tag"]
    ctag = ("u" if update else "") + tag
    if way in ("webpush", "unifiedpush"):
        return {"headers": {"TTL": str(pe.TTL), "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
                            "Urgency": "high" if msg.get("state") == "needs" and not update else "normal",
                            "Topic": pe.collapse(auth, ctag)[:32]}}
    if way == "stream":
        return {"topic": pe.collapse(auth, ctag)[:32], "ttl": pe.TTL}
    pt, prio, ttl, ct, rate = pe.plan(msg, False)
    return {"push_type": pt, "priority": prio, "ttl": ttl, "expiration": 0 if pt == "background" else NOW + ttl, "rate": rate,
            "collapse_tag": ct, "collapse_id": pe.collapse(auth, ct), "aps": pe.aps_for(msg, pt)}


def build_cases(keys):
    ua_pub, auth = pe.unb64u(keys["ua_pub"]), pe.unb64u(keys["auth"])
    out = []
    for name, way, caps, inp in _case_specs():
        lang = inp.get("lang", "de")
        e2e = way in E2E_WAYS
        iss = issue(inp["state"], inp.get("source", "saga"), inp["steps"], e2e, caps, inp.get("content", "unlocked"))
        group = iss["group"]
        title = inp.get("title", TITLE)
        t2s = inp.get("t2")
        t2v = TEXTS[lang]["t2.plan"] if t2s == "plan" else (t2s or {}).get(lang)
        msg = job_msg(inp["job"], group, lang, e2e, b=inp.get("b") if iss["detail"] else None,
                      f=json.loads(json.dumps(inp.get("f"))) if inp.get("f") is not None else None,
                      t1s=t1(lang, group, title), t2s=t2v, acts=iss["actions"], silent=inp.get("silent", False))
        msg, dropped = fit(msg)
        case = {"name": name, "way": way, "sub": {"caps": caps, "keys": e2e or way == "apns-plain", "lang": lang},
                "input": {"job": inp["job"], "state": inp["state"], "source": inp.get("source", "saga"),
                          "steps": [{"klass": k, "undo_class": u} for k, u in inp["steps"]],
                          "notify_content": inp.get("content", "unlocked"), "silent": inp.get("silent", False), "title": title},
                "msg": msg}
        if dropped:
            case["dropped"] = dropped
        if e2e:
            plain = pe.plain_of(msg)
            assert len(plain) <= pe.E2E_MAX, (name, len(plain))
            salt, as_key = pe.h32("kc-salt:" + name)[:16], pe.p256_key("kc-as:" + name)
            pad = pe.pad_size(len(plain))
            body = pe.encrypt(plain, ua_pub, auth, salt, as_key, pad)
            case.update({"plain": plain.decode(), "plain_len": len(plain), "pad_to": pad, "salt": pe.b64u(salt),
                         "as_priv": pe.b64u(pe.scalar(as_key)), "body": pe.b64u(body), "body_len": len(body)})
            tr = _transport(way, msg, auth)
            if way == "apns":
                tr["apns_body"] = {"aps": tr["aps"], "saga": {"e2e": 1, "k": keys["k"], "c": pe.b64u(body)}}
            case["transport"] = tr
        elif way == "ntfy":
            case["endpoint"] = "https://ntfy.example.org/saga-k7f3"
            case["request"] = {"topic": "saga-k7f3", "title": msg["title"], "message": msg["body"], "click": BASE_URL + msg["url"],
                               "tags": ["bell"], "priority": 2 if msg.get("silent") else 3}
        elif way == "unifiedpush-plain":
            case["request"] = msg
            case["headers"] = {"Content-Type": "application/json", "TTL": str(pe.TTL)}
        elif way == "apns-plain":
            case["note"] = "eigener APNs-Weg, Abo mit Schluesseln, push.apns_e2e_direct aus -> Klartext wie bis 2.5"
            ids = {k: msg[k] for k in ("v", "lang", "state", "silent") if msg.get(k) is not None}
            aps = {"alert": {"title": msg["title"], "body": msg["body"]}, "sound": "default", "thread-id": msg["tag"]}
            if msg.get("silent"):
                aps.pop("sound")
            case["transport"] = {"push_type": "alert", "priority": 10, "collapse_id": msg["tag"],
                                 "apns_body": dict({"aps": aps, "url": msg["url"], "kind": msg["kind"]}, **ids)}
        acts = [a["a"] for a in msg.get("actions", [])]
        case["expect"] = {"category": category(group, acts), "android_channel": CHANNEL_OF[group],
                          "thread_id": msg.get("thread") or msg["tag"], "notification_tag": msg["tag"],
                          "text": {"title": msg["detail"]["t1"], "body": msg["detail"]["t2"]} if msg.get("detail")
                          else {"title": msg["title"], "body": msg["body"]},
                          "buttons_unlocked_phone": [b["a"] for b in buttons(category(group, acts), False, "on" if msg.get("detail") else "off",
                                                                             "phone", tokens=acts)[0]],
                          "deeplink": f"saga://job/{inp['job']}" if e2e else "saga://jobs"}
        out.append(case)
    # stilles "update removed" (abgebrochen / zurueckgenommen / aus wartet_dich heraus) - nur E2E-Abos mit caps update
    for way in ("apns", "webpush"):
        msg = update_msg(42, "de")
        plain = pe.plain_of(msg)
        salt, as_key = pe.h32("kc-salt:update_" + way)[:16], pe.p256_key("kc-as:update_" + way)
        pad = pe.pad_size(len(plain))
        body = pe.encrypt(plain, ua_pub, auth, salt, as_key, pad)
        tr = _transport(way, msg, auth, update=True)
        if way == "apns":
            tr["apns_body"] = {"aps": tr["aps"], "saga": {"e2e": 1, "k": keys["k"], "c": pe.b64u(body)}}
        out.append({"name": f"update_removed_{way}", "way": way, "sub": {"caps": SUB_FULL, "keys": True, "lang": "de"},
                    "input": {"job": 42, "state": "zurueckgenommen", "from": "erledigt"}, "msg": msg, "plain": plain.decode(),
                    "plain_len": len(plain), "pad_to": pad, "salt": pe.b64u(salt), "as_priv": pe.b64u(pe.scalar(as_key)),
                    "body": pe.b64u(body), "body_len": len(body), "transport": tr,
                    "expect": {"remove_tag": "job-42", "sound": False}})
    return out


def build_texts():
    out = {}
    for lang in LANGS:
        tx = TEXTS[lang]
        out[lang] = {
            "title": {"needs": head(lang, "needs")[0], "feedback": head(lang, "done")[0]}, "body": tx["body"],
            "t1": {g: t1(lang, g, TITLE) for g in ("needs", "done", "failed")},
            "t2": {"done": T2_DONE[lang], "done.row": T2_ROW[lang], "needs.recht": T2_RECHT[lang], "needs.plan": tx["t2.plan"],
                   "failed.konflikt": T2_KONFLIKT[lang]},
            "hint": tx["hint"], "buttons": {a: tx["btn." + a] for a in ("approve", "undo", "later", "quiet", "open")},
            "channels": {"saga.jobs.needs.v1": tx["ch.needs"], "saga.jobs.feedback.v1": tx["ch.feedback"]},
        }
    out["th"]["t1"]["done.long"] = t1("th", "done", TITLE_TH)
    return out


def build_k15():
    rows = []
    for cat in CATS:
        for device in DEVICES:
            for locked in (False, True):
                for content in ("on", "off"):
                    b, d = buttons(cat, locked, content, device)
                    rows.append({"cat": cat, "device": device, "locked": locked, "content": content, "buttons": b, "direct": d})
    return rows


def build_issue():
    own_undo = [("read", None), ("own", "undo")]
    specs = [
        ("wartet_ok", "share", own_undo, True, SUB_FULL, "unlocked"),
        ("wartet_ok", "saga", own_undo, True, SUB_FULL, "unlocked"),
        ("wartet_ok", "api", [("own", "undo"), ("own", "delete_only")], True, SUB_FULL, "unlocked"),
        ("wartet_ok", "api", [("own", "undo"), ("out", "final")], True, SUB_FULL, "unlocked"),
        ("wartet_ok", "thunderbird", own_undo, True, SUB_FULL, "never"),
        ("wartet_dich", "saga", own_undo, True, SUB_FULL, "unlocked"),
        ("erledigt", "saga", own_undo, True, SUB_FULL, "unlocked"),
        ("erledigt", "saga", [("own", "delete_only")], True, SUB_FULL, "unlocked"),
        ("erledigt", "saga", [("own", "final")], True, SUB_FULL, "unlocked"),
        ("erledigt", "saga", own_undo, True, ["update", "actions"], "unlocked"),
        ("erledigt", "saga", own_undo, True, ["update", "detail"], "unlocked"),
        ("erledigt", "saga", own_undo, True, SUB_FULL, "never"),
        ("erledigt", "saga", own_undo, False, SUB_FULL, "unlocked"),
        ("fehlgeschlagen", "saga", own_undo, True, SUB_FULL, "unlocked"),
        ("abgebrochen", "saga", own_undo, True, SUB_FULL, "unlocked"),
        ("zurueckgenommen", "saga", own_undo, True, SUB_FULL, "unlocked"),
        ("laeuft", "saga", own_undo, True, SUB_FULL, "unlocked"),
    ]
    rows = []
    for state, source, steps, e2e, caps, content in specs:
        r = issue(state, source, steps, e2e, caps, content)
        rows.append({"state": state, "source": source, "steps": [{"klass": k, "undo_class": u} for k, u in steps], "e2e": e2e,
                     "caps": caps, "notify_content": content, "push": r})
    return rows


def _at(tz, *a):
    return int(datetime(*a, tzinfo=ZoneInfo(tz)).timestamp())


def next_hour(now, tz, hour=QUIET_HOUR):
    """Naechstes hh:00 in der Zeitzone, strikt nach now (07:00 genau -> morgen 07:00)."""
    z = ZoneInfo(tz)
    d = datetime.fromtimestamp(now, z)
    cand = d.replace(hour=hour, minute=0, second=0, microsecond=0)
    if cand <= d:
        cand = (d + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)
        cand = datetime(cand.year, cand.month, cand.day, hour, tzinfo=z)   # Sommerzeitwechsel: Wanduhr 07:00
    return int(cand.timestamp())


def build_quiet():
    specs = [("Europe/Berlin", (2026, 11, 26, 21, 30)), ("Europe/Berlin", (2026, 11, 27, 6, 30)), ("Europe/Berlin", (2026, 11, 27, 7, 0)),
             ("Europe/Berlin", (2027, 3, 27, 23, 0)), ("Asia/Bangkok", (2026, 11, 26, 23, 0)), ("America/New_York", (2026, 11, 1, 1, 30))]
    out = []
    for tz, a in specs:
        now = _at(tz, *a)
        until = next_hour(now, tz)
        out.append({"tz": tz, "now": now, "now_local": datetime.fromtimestamp(now, ZoneInfo(tz)).isoformat(),
                    "until": until, "until_local": datetime.fromtimestamp(until, ZoneInfo(tz)).isoformat()})
    return out


def build_tokens():
    ex = atok("42:undo")
    reply = {200: "remove", 409: "hint_open", 410: "hint_open", 403: "open_app", 404: "remove", "net": "hint_open"}
    cases = [
        # name, Zugang, Aktion, Vorbedingung -> status, code, verbraucht
        ("undo_ok", "hkd_A", "undo", "erledigt, Token fuer Abo von Geraet A", 200, None, True),
        ("undo_again", "hkd_A", "undo", "derselbe Token ein zweites Mal", 410, "used", False),
        ("undo_conflict", "hkd_A", "undo", "Ziel seitdem geaendert (ai_undo.conflicts)", 409, "changed", False),
        ("approve_ok", "hkd_A", "approve", "wartet_ok, nur read/own, alle undo, pv/plan_hash passen", 200, None, True),
        ("approve_stale_pv", "hkd_A", "approve", "pv im Koerper != job.plan_v", 409, "stale", False),
        ("approve_replanned", "hkd_A", "approve", "Plan inzwischen geaendert (plan_hash des Tokens alt)", 409, "stale", False),
        ("approve_state", "hkd_A", "approve", "anderswo schon freigegeben (laeuft)", 409, "state", False),
        ("approve_out", "hkd_A", "approve", "Plan enthaelt inzwischen einen Schritt nach aussen (out)", 409, "open", False),
        ("later_ok", "hkd_A", "later", "wartet_dich; gleiche Mitteilung nach 3 h erneut (hoechstens 2x)", 200, None, True),
        ("quiet_ok", "hkd_A", "quiet", "erledigt; Ruhe bis naechstes 07:00 Kontozeit, wartende job.* aus push_queue weg", 200, None, True),
        ("expired", "hkd_A", "undo", "jetzt > expires", 410, "expired", False),
        ("sub_gone", "hkd_A", "undo", "Abo inzwischen entfernt (Token per CASCADE weg)", 410, "gone", False),
        ("wrong_action", "hkd_A", "quiet", "Token gehoert zu einer anderen Aktion bzw. einem anderen Auftrag", 410, "gone", False),
        ("device_other", "hkd_B", "undo", "Token fuer Geraet A, Aufruf mit Geraet B", 403, "device", False),
        ("device_less_hkd", "hkd_A", "later", "Token ohne Geraet (Browser-Abo), Aufruf mit hkd_ statt Sitzung", 403, "device", False),
        ("session_web", "session", "later", "Token ohne Geraet (Browser-Abo), Sitzung + X-Saga: 1", 200, None, True),
        ("no_ai", "hkd_A", "undo", "Faehigkeit ai inzwischen entzogen", 403, "cap", False),
        ("widget_later", "hk_", "later", "Widget-Schluessel, Ruhe-Aktion", 200, None, True),
        ("widget_undo", "hk_", "undo", "Widget-Schluessel, keine Ruhe-Aktion", 403, "key", False),
        ("api_token", "sga_", "later", "API-Token/MCP/OAuth (RT-42)", 403, "key", False),
        ("foreign", "hkd_A", "undo", "Auftrag eines anderen Kontos", 404, None, False),
        ("reserved", "hkd_A", "retry", "retry/reply/ideas_off (reserviert)", 409, "open", False),
        ("open", "hkd_A", "open", "ohne Token: nur die Adresse", 200, None, False),
    ]
    return {"path": "/api/ai/jobs/{id}/act",
            "request": {"action": "undo", "atok": ex, "pv": 2},
            "auth": {"hkd_A": "Authorization: Bearer hkd_… (Geraet des Abos)", "hkd_B": "Authorization: Bearer hkd_… (anderes Geraet)",
                     "session": "Sitzung + X-Saga: 1", "hk_": "Authorization: Bearer hk_… (Widget)",
                     "sga_": "Authorization: Bearer sga_… (API-Token)"},
            "example": {"atok": ex, "len": len(ex), "hash": tok_hash(ex)},
            "responses": {"200": {"ok": True, "action": "undo", "state": "zurueckgenommen"},
                          "200_later": {"ok": True, "action": "later", "state": "wartet_dich", "at": NOW + LATER_S},
                          "200_quiet": {"ok": True, "action": "quiet", "state": "erledigt", "until": next_hour(NOW, "Europe/Berlin")},
                          "200_open": {"ok": True, "action": "open", "url": "/saga?tab=jobs&job=42"},
                          "409": {"error": "…", "code": "stale", "url": "/saga?tab=jobs&job=42"},
                          "410": {"error": "…", "code": "used"}, "403": {"error": "…", "code": "device"}, "404": {"error": "…"}},
            "client": {str(k): v for k, v in reply.items()},
            "order": ["key", "job (404)", "cap ai (403 cap)", "reserved/open", "token (410)", "device (403)", "pv/plan_hash (409 stale)",
                      "state (409 state)", "action (409 changed/open)", "ausfuehren + verbrauchen (200)"],
            "cases": [{"name": n, "auth": au, "action": a, "given": g, "status": s, "code": c, "consumed": u}
                      for n, au, a, g, s, c, u in cases]}


def build():
    with open(E2E_PATH, encoding="utf-8") as f:
        keys = json.load(f)["keys"]
    return {
        "format": FORMAT,
        "note": "Erzeugt von tools/kollegin_push_vectors.py gen - nicht von Hand aendern. Vertrag: docs/APP-API.md (Push-Nutzlast 3), "
                "docs/APP-API-KI.md Abschnitt 12, docs/PUSH-RELAY.md. Schluessel = tests/vectors/push_e2e.json keys.",
        "params": {"payload_v": PAYLOAD_V, "kinds": KINDS, "update_kind": UPDATE_KIND, "cat": "jobs", "kanal": "notify",
                   "group_of_state": GROUP_OF, "silent_states": list(SILENT_STATES), "persona": PERSONA, "base_url": BASE_URL,
                   "now": NOW, "e2e_max": pe.E2E_MAX, "pad_steps": list(pe.PAD_STEPS), "pad_round": pe.PAD_ROUND, "ttl": pe.TTL,
                   "t1_max": T1_MAX, "t2_max": T2_MAX, "e2e_ways": list(E2E_WAYS), "plain_ways": ["ntfy", "unifiedpush-plain", "apns-plain"],
                   "never": ["apns-voip"],
                   "ids": {"tag": "job-{id}", "group_key": "job:{id}", "thread": "job-{id}", "url": "/saga?tab=jobs&job={id}",
                           "plain_tag": "saga-job", "plain_url": "/saga?tab=jobs"},
                   "token": {"bytes": TOK_BYTES, "len": 24, "alphabet": "base64url", "db": "sha256 hex", "ttl_s": ACT_TTL,
                             "needs_until": "due_at", "max_s": ACT_MAX},
                   "later_s": LATER_S, "later_max": LATER_MAX, "quiet_hour": QUIET_HOUR, "quiet_tz": "push.quiet.tz, sonst Server-TIMEZONE",
                   "done_window_s": QUIET_WINDOW, "sub_caps": ["update", "widgets", "detail", "actions"]},
        "keys": keys,
        "texts": build_texts(),
        "categories": [{"id": k, "buttons": v, "group": k.split(".")[2]} for k, v in CATS.items()],
        "android_channels": CHANNELS,
        "actions": ACTIONS, "reserved_actions": list(RESERVED), "devices": list(DEVICES),
        "cases": build_cases(keys),
        "issue": build_issue(),
        "k15": build_k15(),
        "tokens": build_tokens(),
        "quiet": build_quiet(),
        "deeplinks": [{"link": "saga://saga", "route": "/saga"}, {"link": "saga://jobs", "route": "/saga?tab=jobs"},
                      {"link": "saga://job/42", "route": "/saga?tab=jobs&job=42"}],
        "digest": {"msg": {"title": "Saga", "body": "Während „Nicht stören“: 1 verpasster Anruf, 2 Rückmeldungen von Marin, "
                                                    "1 weitere Mitteilung",
                           "url": "/", "tag": "status-digest", "kind": "status.digest", "ts": NOW, "v": 2, "lang": "de", "n": 4,
                           "digest": {"messages": 0, "calls": 1, "other": 3, "jobs": 2}},
                   "note": "jobs ist in other enthalten (aeltere Apps: '3 weitere Mitteilungen'); Apps zeigen body"},
    }


# --- Pruefen -------------------------------------------------------------------------------------------------------------
def _leaks(s):
    return [x for x in CANARIES if x in s]


def check(v):
    bad = []
    want = build()
    for k in ("format", "params", "keys", "texts", "categories", "android_channels", "actions", "reserved_actions", "devices",
              "issue", "k15", "tokens", "quiet", "deeplinks", "digest"):
        if v.get(k) != want[k]:
            bad.append(f"{k}: weicht ab")
    with open(E2E_PATH, encoding="utf-8") as f:
        if v.get("keys") != json.load(f)["keys"]:
            bad.append("keys: nicht die aus push_e2e.json")
    if [c["name"] for c in v.get("cases", [])] != [c["name"] for c in want["cases"]]:
        bad.append("cases: andere Faelle")
    ua = pe.ec.derive_private_key(int.from_bytes(pe.unb64u(v["keys"]["ua_priv"]), "big"), pe.P256)
    auth = pe.unb64u(v["keys"]["auth"])
    for c, w in zip(v.get("cases", []), want["cases"]):
        n, m = c["name"], c["msg"]
        if c != w:
            bad.append(f"{n}: weicht von der Erzeugung ab")
        if c["way"] in E2E_WAYS:
            try:
                plain, rec = pe.decrypt(pe.unb64u(c["body"]), ua, auth)
            except Exception as ex:  # noqa: BLE001
                bad.append(f"{n}: entschluesseln: {type(ex).__name__}")
                continue
            if plain.decode() != c["plain"] or json.loads(plain) != m or rec != c["pad_to"] or len(plain) > pe.E2E_MAX:
                bad.append(f"{n}: Inhalt/Auffuellen/Groesse falsch")
            tr = c["transport"]
            vis = json.dumps({k: x for k, x in tr.items() if k != "apns_body"}, ensure_ascii=False)
            if _leaks(vis) or "job" in json.dumps(tr.get("headers", {})) + str(tr.get("topic", "")) + str(tr.get("collapse_id", "")):
                bad.append(f"{n}: Klartext in Koepfen/Topic/aps")
            if "apns_body" in tr:
                ab = tr["apns_body"]
                if set(ab) != {"aps", "saga"} or "category" in ab["aps"] or "thread-id" in ab["aps"] or _leaks(json.dumps(ab["aps"], ensure_ascii=False)):
                    bad.append(f"{n}: APNs-Koerper nicht nur aps+saga")
                if tr["push_type"] == "voip" or len(json.dumps(ab, separators=(",", ":"))) > 4096:
                    bad.append(f"{n}: voip oder zu gross")
            if m["kind"] == UPDATE_KIND:
                if not (m.get("update") and m.get("removed")) or "detail" in m or "actions" in m:
                    bad.append(f"{n}: Aktualisierung falsch")
                continue
            if m.get("detail") and not ("detail" in c["sub"]["caps"] and c["input"]["notify_content"] != "never"):
                bad.append(f"{n}: detail ohne caps/bei never")
            acts = [a["a"] for a in m.get("actions", [])]
            if acts and "actions" not in c["sub"]["caps"]:
                bad.append(f"{n}: actions ohne caps")
            if any(a in AUTH for a in acts) and not m.get("detail"):
                bad.append(f"{n}: approve/undo ohne detail")
            if any(len(a["t"]) != 24 for a in m.get("actions", [])):
                bad.append(f"{n}: Token-Laenge")
        else:
            blob = json.dumps({k: c.get(k) for k in ("msg", "request", "transport")}, ensure_ascii=False)
            if _leaks(blob) or any(k in m for k in ("job", "detail", "actions", "pv", "group_key", "thread")) or "job=" in blob:
                bad.append(f"{n}: Klartext-Weg mit Kennung/Inhalt")
        if _leaks(m["title"] + m["body"]):
            bad.append(f"{n}: title/body nicht neutral")
        if c["expect"]["category"] != category(m["state"], [a["a"] for a in m.get("actions", [])]):
            bad.append(f"{n}: Kategorie")
    for r in v.get("k15", []):
        names = [b["a"] for b in r["buttons"]]
        if r["device"] in ("watch", "car") and any(a not in REST for a in names):
            bad.append(f"k15 {r['cat']}/{r['device']}: mehr als Ruhe-Knoepfe")
        if r["locked"] and any(a in AUTH for a in r["direct"]):
            bad.append(f"k15 {r['cat']}/{r['device']}: approve/undo gesperrt direkt")
        if r["content"] == "off" and any(a in AUTH for a in names):
            bad.append(f"k15 {r['cat']}/{r['device']}: approve/undo ohne Inhalt")
    for lang, tx in v.get("texts", {}).items():
        for g, s in tx["t1"].items():
            if graphemes(s) > T1_MAX + 30:
                bad.append(f"texts {lang} t1 {g}: zu lang")
        for g, s in tx["t2"].items():
            if graphemes(s) > T2_MAX:
                bad.append(f"texts {lang} t2 {g}: zu lang")
        if any(_leaks(tx["title"][k] + tx["body"]) for k in tx["title"]):
            bad.append(f"texts {lang}: Titel nicht neutral")
    ex = v["tokens"]["example"]
    if tok_hash(ex["atok"]) != ex["hash"] or len(pe.unb64u(ex["atok"])) != TOK_BYTES:
        bad.append("tokens: Beispiel")
    return bad


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "gen":
        with open(PATH, "w", encoding="utf-8") as f:
            json.dump(build(), f, ensure_ascii=False, indent=1)
            f.write("\n")
        print("geschrieben:", PATH)
        return 0
    with open(PATH, encoding="utf-8") as f:
        bad = check(json.load(f))
    for b in bad:
        print("FAIL", b)
    print("ALLES GRUEN" if not bad else f"{len(bad)} Abweichungen")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
