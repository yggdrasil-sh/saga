"""KI im Chat (2.1): "nur auf Zuruf" - die KI liest nur Nachrichten, in denen sie mit @Name erwaehnt wird.

Chats sind Ende-zu-Ende-verschluesselt (saga/chat.py); der Server kann sie nicht lesen. Deshalb schickt die App des
Fragenden GEZIELT die erwaehnende Nachricht und einen kleinen Verlauf (hoechstens CTX_MAX Nachrichten, CTX_CHARS Zeichen)
im Klartext an POST /api/chats/{cid}/ai. Alles andere bleibt verschluesselt. Die Antwort kommt als Stream zurueck (wie
/api/ai/ask) und wird nirgends gespeichert - nur ihr SHA-256 als Beleg (chat_ai_reply). Die App verschluesselt sie wie
jede Nachricht fuer alle Mitglieder und sendet sie mit ai_reply=<beleg>; die anderen Clients sehen in der
Nachrichtenliste ai={ref, sha256, by} und zeigen die Nachricht als Antwort der KI (Name + Bild), wenn der Hash zum
entschluesselten Text passt.

Freigabe in zwei Stufen: Admin-Bereich (KI-Assistent -> "KI in Chats erlauben", Standard aus) und je Chat
(PUT /api/chats/{cid}/ai {allowed}): freie Gruppe - Chat-Admins; Direkt-Chat - beide; Gruppen-Chat - Saga-Admins;
Seiten-Chat - Eigentuemer der Seite. Wer fragt, muss den Assistenten nutzen duerfen (Gruppen, Kontingent wie sonst).

Die KI bekommt KEINE Seiten, Aufgaben oder Kalender - nur die Frage und den mitgeschickten Verlauf. Sonst koennte eine
Person ueber die KI Inhalte ihrer eigenen Seiten in einen Chat mit anderen tragen, ohne es zu merken.

Websuche (2.1, saga/ai_web.py): Hat der Admin sie erlaubt, entscheidet das Modell vorab, ob die Frage eine Websuche
braucht (Wetter, Nachrichten ...); dann gehen hoechstens zwei kurze Suchbegriffe ueber SearXNG an Suchmaschinen. Die
Treffer stehen als DATEN im Prompt; die genutzten Quellen haengt Saga als Links an die Antwort (Teil des Belegs)."""
import asyncio
import hashlib
import re
import secrets
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import auth
from db import connect as _db
from saga import ai, ai_web, chat
from saga.core import _api_guard, _bad, _body, t, user_info

router = APIRouter()

CTX_MAX = 12                     # Nachrichten Verlauf je Anfrage
CTX_CHARS = 4000                 # Zeichen Verlauf insgesamt
LINE_MAX = 600                   # Zeichen je Verlaufs-Nachricht
NAME_MAX = 80
RATE_USER = 6                    # Anfragen je Person und Minute
RATE_CHAT = 20                   # Anfragen je Chat in 10 Minuten
REPLY_TTL = chat.AI_REPLY_TTL    # so lange kann eine Antwort als Nachricht gesendet werden
_HEX32 = re.compile(r"[0-9a-f]{32}")


def server_on(c=None):
    c = c or ai.cfg()
    return bool(c.get("chat")) and ai.configured(c)


def mention_re(name):
    """@Name am Wortanfang, Gross-/Kleinschreibung egal, danach kein Buchstabe/keine Ziffer."""
    return re.compile(r"(?:^|(?<=[\s(\[\"']))@" + re.escape(name) + r"(?![\w])", re.I | re.U)


def may_toggle(c, uid, ch, m):
    if ch["kind"] == "free":
        return m["role"] == "admin"
    if ch["kind"] == "dm":
        return True
    if ch["kind"] == "group":
        u = user_info(uid)
        return bool(u and u.get("is_admin"))
    if ch["kind"] == "page" and ch["note_id"]:
        r = c.execute("SELECT 1 FROM note WHERE id=? AND uid=?", (ch["note_id"], uid)).fetchone()
        return bool(r)
    return False


def _clean(s, n):
    s = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", str(s or "")).strip()
    return ai.clean_data(s[:n])


def chat_messages(q, asker, history, web=None):
    """Anweisungen fuer eine Antwort im Chat. Frage, Verlauf und Webtreffer sind DATEN zwischen den Markierungen."""
    ql = ai.LANG_NAMES.get(ai.q_lang(q))
    lang = ql or ai._lang_name()
    web = web or {}
    task = ("Someone in a chat mentioned you and asked you something. Reply to their message directly, like a helpful chat "
            "participant: short and friendly, at most about 120 words, plain text, no headings, no tables. Use the recent "
            "chat messages only as context. You have NO access to the person's pages, tasks, calendar or files - if the "
            "question needs them, say so briefly. Do not invent facts about the people in the chat. " +
            (ai.WEB_TASK if web.get("material") else "") + (ai.WEB_FAILED if web.get("failed") else "") +
            "Reply in the language of the message" + (f" ({ql})" if ql else "") + ".")
    lines = [f"{_clean(h['name'], NAME_MAX)}: {_clean(h['text'], LINE_MAX)}" for h in history]
    data = ("Recent chat messages (oldest first):\n" + "\n".join(lines) + "\n\n" if lines else "") + \
        f"Message that mentions you, from {_clean(asker, NAME_MAX)}:\n{_clean(q, ai.Q_MAX)}"
    if web.get("material"):
        data = web["material"] + "\n\n" + data
    return [{"role": "system", "content": ai.system_prompt(task)},
            {"role": "user", "content": f"{ai.MARK_A}\n{data}\n{ai.MARK_B}\n\n(Reply to the message above in {lang}. "
                                        "Only the reply text.)"}]


WEB_CTX = 3                      # so viele Verlaufs-Nachrichten bekommt die Entscheidung "Websuche noetig?" mit
_CITE = re.compile(r"\[(\d{1,2})(?:\s*[,;]\s*\d{1,2})*\]")


def with_sources(answer, srcs, notice=None):
    """Antwort im Chat + genutzte Webquellen als Links (zitierte; ohne Zitat die ersten drei) + ggf. Hinweis.
    Alles ist Teil des Textes, den die App verschluesselt und der Beleg (SHA-256) abdeckt."""
    out = answer
    if srcs:
        cited = {int(n) for m in _CITE.finditer(answer) for n in re.findall(r"\d{1,2}", m.group(0))}
        use = [s for s in srcs if s["n"] in cited] or srcs[:3]
        out += "\n\n" + t("Quellen:") + "\n" + "\n".join(f"[{s['n']}] {s['title']} – {s['url']}" for s in use)
    if notice:
        out += "\n\n(" + notice + ")"
    return out


def _history(v):
    """[{name, text}] -> bereinigte Liste (neueste zuletzt) oder None bei falscher Form."""
    if v is None:
        return []
    if not isinstance(v, list) or len(v) > CTX_MAX:
        return None
    out = []
    for h in v:
        if not isinstance(h, dict) or not isinstance(h.get("text"), str) or not isinstance(h.get("name", ""), str):
            return None
        out.append({"name": h.get("name") or "?", "text": h["text"][:LINE_MAX]})
    while out and sum(len(h["text"]) + len(h["name"]) for h in out) > CTX_CHARS:     # aelteste zuerst weglassen
        out.pop(0)
    return out


@router.put("/api/chats/{cid}/ai")
async def api_chat_ai_toggle(cid: int, request: Request):
    """KI in diesem Chat erlauben/verbieten: {allowed: bool}. Mitglieder bekommen chat.changed."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request)
    if b is None or not isinstance(b.get("allowed"), bool):
        return _bad(t("ungültige Angabe"))

    def run():
        with _db() as c, c:
            ch, m, mem, err = chat._chat_for(c, uid, cid)
            if err:
                return err
            if b["allowed"] and not server_on():
                return _bad(t("KI in Chats ist auf diesem Server nicht freigeschaltet."), 403)
            if not may_toggle(c, uid, ch, m):
                return _bad(t("Dafür fehlt dir die Berechtigung."), 403)
            if b["allowed"]:
                c.execute("INSERT INTO chat_ai (chat_id, by_uid, at) VALUES (?,?,?) ON CONFLICT (chat_id) DO NOTHING", (ch["id"], uid, time.time()))
            else:
                c.execute("DELETE FROM chat_ai WHERE chat_id=?", (ch["id"],))
            chat._changed(c, ch["id"], mem)
            print(f"chat: KI in Chat {ch['id']} {'erlaubt' if b['allowed'] else 'verboten'} von {uid}", flush=True)
            return JSONResponse({"ai": b["allowed"]}, headers={"Cache-Control": "no-store"})
    return await asyncio.to_thread(run)


@router.post("/api/chats/{cid}/ai")
async def api_chat_ai_ask(cid: int, request: Request):
    """{q: Text der Nachricht mit @Name, msg_id?: ihre Kennung, history?: [{name, text}] (aelteste zuerst)} ->
    text/event-stream wie /api/ai/ask (delta, status, done{reply:{id, sha256}, text, quota_left} | error).
    Der Server speichert weder Frage noch Antwort - nur den Hash der Antwort als Beleg."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    q = b.get("q") if isinstance(b.get("q"), str) else ""
    q = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", q).strip()
    hist = _history(b.get("history"))
    qmsg = b.get("msg_id")
    if not q or hist is None or (qmsg is not None and not (isinstance(qmsg, str) and _HEX32.fullmatch(qmsg))):
        return _bad(t("ungültige Angabe"))
    if len(q) > ai.Q_MAX:
        return _bad(t("Die Frage ist zu lang (höchstens {n} Zeichen).", n=ai.Q_MAX))
    name = ai.persona_name()
    if not mention_re(name).search(q):
        return _bad(t("Die KI antwortet nur, wenn sie mit @{name} erwähnt wird.", name=name))

    def check():
        c = ai.cfg()
        if not ai.allowed(uid, c):
            return _bad(t("Der KI-Assistent ist für dich nicht eingeschaltet."), 403), None
        if not server_on(c):
            return _bad(t("KI in Chats ist auf diesem Server nicht freigeschaltet."), 403), None
        with _db() as conn, conn:
            ch, m, mem, err = chat._chat_for(conn, uid, cid)
            if err:
                return err, None
            if not conn.execute("SELECT 1 FROM chat_ai WHERE chat_id=?", (ch["id"],)).fetchone():
                return _bad(t("Die KI ist in diesem Chat nicht erlaubt."), 403), None
            nm = auth.get_user(uid) or {}
            return None, (ch["id"], nm.get("name") or uid)
    err, info = await asyncio.to_thread(check)
    if err is not None:
        return err
    chat_id, asker = info
    if not chat._rate("ai-chat", uid, RATE_USER) or not chat._rate("ai-chat-c", str(chat_id), RATE_CHAT, 600.0):
        return _bad(t("Zu viele Fragen an die KI in kurzer Zeit – bitte kurz warten."), 429)
    c, err = await ai._gate(uid)
    if err is not None:
        return err
    try:
        quota = int(c.get("quota") or 0)
        left = await asyncio.to_thread(ai._take_quota, uid, quota)
    except BaseException:
        ai._release(uid)
        raise
    if left == -1:
        ai._release(uid)
        return _bad(t("Dein Tageskontingent für den Assistenten ist aufgebraucht."), 429)

    st = {"web": {}}

    def prep(emit, stop):
        ctxl = "\n".join(f"{_clean(h['name'], NAME_MAX)}: {_clean(h['text'], 300)}" for h in hist[-WEB_CTX:])
        st["web"] = w = ai.web_step(uid, c, q, emit, stop, context=ctxl, start=1, quota=quota)
        return chat_messages(q, asker, hist, web=w)

    def finish(answer):
        answer = answer.strip()
        if not answer:
            return {"text": ""}
        w = st["web"]
        answer = with_sources(answer, w.get("sources"), w.get("notice"))
        rid = secrets.token_hex(16)
        h = hashlib.sha256(answer.encode("utf-8")).hexdigest()
        with _db() as conn, conn:
            conn.execute("DELETE FROM chat_ai_reply WHERE created<? AND msg_id IS NULL", (time.time() - REPLY_TTL,))
            conn.execute("INSERT INTO chat_ai_reply (id, chat_id, uid, created, q_msg, sha256) VALUES (?,?,?,?,?,?)",
                         (rid, chat_id, uid, time.time(), qmsg, h))
        out = {"text": answer, "reply": {"id": rid, "sha256": h}, "persona": {"name": ai.persona_name()}}
        if w.get("sources"):
            out["web"] = len(w["sources"])
        if "quota_left" in w:
            out["quota_left"] = w["quota_left"]
        return out
    web = ai_web.enabled(c)
    print(f"chat: Frage an die KI in Chat {chat_id} ({len(q)} Zeichen, {len(hist)} Nachrichten Verlauf"
          f"{', Websuche erlaubt' if web else ''})", flush=True)
    return ai._respond(uid, c, chat_messages(q, asker, hist), [], finish, left, quota, prep=prep if web else None)


def reply_sha(text):
    """Fuer Tests und Clients: so wird der Beleg berechnet (UTF-8, ohne Leerraum am Anfang/Ende)."""
    return hashlib.sha256(str(text).strip().encode("utf-8")).hexdigest()
