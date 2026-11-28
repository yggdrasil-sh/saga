"""Diktat ueber den Server (Saga 2.6, Bugs-DB 2274 K-F, Konzept K16/K17, FEATURES aistt1) - Vertrag docs/APP-API-KI.md
Abschnitt 15, Vektoren tests/vectors/stt_contract.json.

Der Server reicht eine kurze Aufnahme (Ogg/Opus oder WAV, hoechstens 60 s / 2 MiB) an einen Whisper-kompatiblen Dienst
weiter (POST {url}/v1/audio/transcriptions) und gibt nur den Text zurueck. Der Ton bleibt im Speicher, landet nie auf
der Platte, nie im Log, nie in ai_history/ai_turn/job - der Text wird erst zur Bitte, wenn die Person ihn sendet.
Standard nur Adressen im eigenen Netz; Cloud nur per Admin-Schalter "Cloud erlauben", nur https, mit eigenem Hinweis.

Diese Datei haelt Grenzen und Texte (Hinweis "wohin geht die Aufnahme", Fehler, Herkunft "Diktiert") - Texte bildet nur
der Server, die Apps zeigen sie unveraendert (wie Abschnitt 11.1)."""
from saga.i18n import t

LANGS = ("de", "en", "es", "ru", "th")              # Sprachen des Diktats (= App-Sprachen)
FORMATS = ("ogg", "wav")                            # Ogg/Opus, WAV (PCM 16 bit mono)
MAX_S = 60                                          # Sekunden je Aufnahme (Pruefung mit 0,5 s Spiel)
MAX_SLACK = 0.5
MAX_BYTES = 2 * 1024 * 1024                         # Koerper hoechstens 2 MiB
TEXT_MAX = 1000                                     # Zeichen Text (sonst gekuerzt, cut)
RATE_H, RATE_D = 30, 120                            # Diktate je Person und Stunde / Tag
PER_USER, SERVER_MAX = 1, 2                         # gleichzeitig je Person / serverweit
INPUTS = ("text", "dictation")                      # POST /api/ai/jobs input


def where_to(h):
    """Ziel fuer den Satz "... uebers Internet an {where} ..." - ohne "(im Internet)" (steht schon im Satz)."""
    if not h:
        return ""
    if h.get("provider") and h.get("country_name"):
        return t("{host} ({provider}, {country})", host=h["host"], provider=h["provider"], country=h["country_name"])
    if h.get("provider"):
        return t("{host} ({provider})", host=h["host"], provider=h["provider"])
    return h["host"]


def notice_text(h, persona=None, lang=None):
    """Hinweis am Knopf "Diktieren" (vor dem ersten Start und waehrend der Aufnahme) -> str. h = Hinweis wie
    ai_saga.endpoint_hint (host, net, provider?, country_name?, note?, label). Sie-Fassung ueber die Bausteine stt.home /
    stt.internet (nur de/es/ru); der Zusatzsatz des Admins (note) haengt hinten an."""
    if not h:
        return ""
    home = h.get("net") == "home"
    where = h.get("label") if home else where_to(h)
    du = (t("Deine Aufnahme geht an {where} und wird dort zu Text. Saga speichert sie nicht.", where=where) if home else
          t("Deine Aufnahme geht übers Internet an {where} und wird dort zu Text. Saga speichert sie nicht.", where=where))
    out = du
    try:
        from saga import ai_say, i18n
        out = ai_say.formal("stt.home" if home else "stt.internet", lang or i18n.get_lang(), persona, where=where) or du
    except Exception:  # noqa: BLE001 - Baustein kaputt: Systemtext (du)
        out = du
    return out + (" " + h["note"] if h.get("note") else "")


def origin_text():
    return t("Diktiert")


def warn_text():
    return t("Im diktierten Text stehen Anweisungen an die KI – vielleicht aus Radio oder Fernseher. Prüf, ob das deine Bitte ist.")


def message(code, **kw):
    """Fehlertext zu einem code der Fehlertabelle (APP-API-KI 15.1) in der Sprache der Anfrage."""
    if code == "key":
        return t("Diktat gibt es nur in Saga selbst – nicht über API-Schlüssel.")
    if code == "ai":
        return t("Der KI-Assistent ist für dich nicht eingeschaltet.")
    if code == "off":
        return t("Diktat ist auf diesem Server nicht eingerichtet.")
    if code == "device_only":
        return t("Du hast eingestellt, dass Diktat nur auf dem Gerät läuft.")
    if code == "lang":
        return t("Diese Sprache geht beim Diktat nicht.")
    if code == "format":
        return t("Dieses Tonformat geht nicht – nur Ogg/Opus oder WAV (PCM, 16 Bit, mono).")
    if code == "format_service":
        return t("Dieses Tonformat nimmt der Diktat-Dienst nicht an.")
    if code == "empty":
        return t("Die Aufnahme ist leer.")
    if code == "too_long":
        return t("Die Aufnahme ist zu lang (höchstens {n} Sekunden).", n=kw.get("n", MAX_S))
    if code == "too_big":
        return t("Die Aufnahme ist zu groß.")
    if code == "busy":
        return t("Es läuft schon ein Diktat.")
    if code == "full":
        return t("Gerade diktieren zu viele gleichzeitig – versuch es gleich noch mal.")
    if code == "rate":
        return t("Für jetzt sind es genug Diktate – versuch es später noch mal.")
    if code == "blocked":
        return t("Der Diktat-Dienst ist gerade nicht erlaubt – es wurde nichts gesendet. Sag deinem Admin Bescheid.")
    if code == "timeout":
        return t("Der Diktat-Dienst hat zu lange gebraucht.")
    if code == "input":
        return t("Unbekannte Eingabeart.")
    return t("Der Diktat-Dienst hat einen Fehler gemeldet.")       # upstream
