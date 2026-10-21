"""Chat-Stufe 3 (2.5): Reaktionen, Bearbeiten und Vertrauens-Uebernahme - Server-Referenz (Konzept saga-2.5-chat).

Kein neues Nachrichtenformat: Chats der Stufe 3 sind saga-chat/v2 (saga/chat_v2.py, gebunden an den stabilen Handle
'~x@host' und chat_ref) - auch lokale Chats. Neu sind nur die Ereignisse fuer Reaktionen und Bearbeitungen.

  B64 = Standard-Base64 mit Padding; LP = je Feld uint32_be(len) || bytes (Strings UTF-8); V3 = "saga-chat/v3/"
  Reaktion      K_r  = crypto_generichash(32, LP(V3+"react-key", chat_ref, msg_id), key = K der Nachricht)
                body = {"e": "<ein Emoji>" | "", "ts": <ms>}  (kompaktes JSON; "" = zuruecknehmen; e hoechstens 32 Bytes)
                ct   = B64(nonce24 || xchacha20poly1305_ietf(body, AD, nonce, K_r)), AD = LP(V3+"react", chat_ref, msg_id, handle, geraet)
                sig  = sign_detached(LP(V3+"react-sig", chat_ref, msg_id, handle, geraet, ts, generichash32(raw(ct))), device_sign_sk)
                Der Server sieht wer/wann/worauf (Metadaten wie bei Gelesen), nicht womit; er prueft die Signatur.
  Bearbeitung   normale v2-Nachricht mit kind "edit", Klartextfeld edit_of und Inhalt {"edit": "<msg_id>", "t": "<neuer Text>"}.
                Clients pruefen: body.edit == edit_of == Original, gleicher sender_handle wie das Original, Original keine
                KI-Antwort, innerhalb des Fensters (EDIT_WINDOW ab created des Originals, hoechstens EDIT_MAX).
  TOFU          Erster Start einer 2.5-App: aus dem v1-Eintrag trust:<benutzer> wird trust2:<handle> - aber nur, wenn der v1-
                Identitaetsschluessel gleich dem selbst geprueften v2-Schluessel ist (verified und changed gehen mit).
Testvektoren: tests/vectors/chat_v3.json (tools/chat_vectors_v3.py, Gegenprobe node tools/chat_vectors_v3.mjs mit
static/chat-crypto.js). Eigenstaendig (ohne Saga-Konfiguration), wie saga/chat_v2.py."""
import hashlib
import json

from saga import chat_v2 as V2

V3 = "saga-chat/v3/"
EMOJI_MAX = 32                   # Bytes (UTF-8) je Reaktion
REACT_CT_MAX = 400               # B64-Laenge des Ciphertexts (24 Nonce + 16 Tag + Inhalt mit Emoji und Zeit)
EDIT_WINDOW = 24 * 3600          # Sekunden ab dem Original (Serverzeit created)
EDIT_MAX = 20                    # Bearbeitungen je Nachricht
lp, b64d, verify = V2.lp, V2.b64d, V2.verify


def react_key(K, chat_ref, msg_id):
    """Schluessel der Reaktionen auf eine Nachricht (wer die Nachricht lesen kann, liest auch ihre Reaktionen)."""
    return hashlib.blake2b(lp(V3 + "react-key", chat_ref, msg_id), digest_size=32, key=bytes(K)).digest()


def react_ad(chat_ref, msg_id, handle, device):
    return lp(V3 + "react", chat_ref, msg_id, handle, device)


def react_sig_msg(chat_ref, msg_id, handle, device, ts, ct):
    return lp(V3 + "react-sig", chat_ref, msg_id, handle, device, str(int(ts)), V2.gh(b64d(ct) or b""))


def verify_reaction(sign_pub, chat_ref, msg_id, r):
    """r: {by (Handle), dev, ts, ct, sig} - Signatur des Geraets pruefen (Inhalt kennt nur, wer die Nachricht lesen kann)."""
    ct = r.get("ct")
    raw = b64d(ct) if isinstance(ct, str) and len(ct) <= REACT_CT_MAX else None
    if raw is None or len(raw) < 24 + 16 + 2:
        return False
    return verify(sign_pub, r.get("sig"), react_sig_msg(chat_ref, msg_id, r.get("by") or "", r.get("dev") or "", r.get("ts") or 0, ct))


def react_body_ok(body, ts):
    """Entschluesselter Inhalt einer Reaktion (Text) -> Emoji ('' = zurueckgenommen) oder None. Ein Graphem pruefen die Clients."""
    try:
        d = json.loads(body)
    except (TypeError, ValueError):
        return None
    if not isinstance(d, dict) or set(d) != {"e", "ts"} or d["ts"] != ts or not isinstance(d["e"], str):
        return None
    e = d["e"]
    if len(e.encode("utf-8")) > EMOJI_MAX or any(ord(ch) < 0x20 or 0x7f <= ord(ch) < 0xa0 for ch in e):
        return None
    return e


def edit_check(orig, edit, body):
    """Bearbeitung pruefen (wie die Clients): orig/edit = Nachrichten aus GET .../messages (id, sender_handle, ai, created),
    edit mit edit_of; body = entschluesselter Inhalt der Bearbeitung (dict). -> None (gueltig) oder Grund."""
    if not isinstance(body, dict) or not isinstance(body.get("t"), str) or body.get("edit") != edit.get("edit_of"):
        return "edit_of_mismatch"
    if edit.get("edit_of") != orig.get("id"):
        return "edit_of_mismatch"
    if not edit.get("sender_handle") or edit.get("sender_handle") != orig.get("sender_handle"):
        return "other_sender"
    if orig.get("ai"):
        return "ai_original"
    if (edit.get("created") or 0) - (orig.get("created") or 0) > EDIT_WINDOW:
        return "window"
    return None


def tofu_migrate(v1, ident, ver=None):
    """Vertrauens-Uebernahme v1 -> v2 (nur, solange es keinen Eintrag trust2:<handle> gibt). v1 = {ident, verified, changed?,
    old?} | None (trust:<benutzer>), ident = selbst gepruefter v2-Identitaetsschluessel (B64), ver = Version der Geraete-Liste.
    -> (ergebnis, neuer eintrag | None); Regeln wie tofu_v2 (tests/vectors/chat_v2.json "tofu")."""
    ver = ver or 0
    if not v1:
        return "trust", {"ident": ident, "verified": False, "dver": ver}
    if v1.get("ident") == ident:                      # derselbe Schluessel: verified und changed gehen mit
        st = {"ident": ident, "verified": bool(v1.get("verified")), "dver": ver}
        if v1.get("changed"):
            st.update(changed=True, old=v1.get("old"))
            return "warn", st
        return "trust", st
    if v1.get("verified"):                            # ueberprueft, jetzt ein anderer Schluessel: Server schiebt etwas unter
        return "block", None
    return "warn", {"ident": ident, "verified": False, "changed": True, "old": v1.get("ident"), "dver": ver}
