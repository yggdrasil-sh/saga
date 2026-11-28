"""Herkunfts-Hinweis fuer geteilten Inhalt (Saga 2.6, Bugs-DB 2275 K-G, Konzept K17/G7, EV-25) - ohne Modell.

Geteilter Inhalt (Teilen-Ziel der Apps und der PWA) ist fremder Inhalt: er geht nur als Daten an das Modell (ai_plan),
und die Plan-Karte nennt ihn als Herkunft ("Angeregt durch: geteilten Inhalt (host)"). Enthaelt er erkennbar Anweisungen
an eine KI, bekommt die Herkunft zusaetzlich die Warnung warn ("Der geteilte Inhalt enthaelt Anweisungen an die KI ...").

Erkennung (detect): feste Muster in de/en/es/ru/th auf einer normalisierten Kopie (NFKC + casefold, unsichtbare Zeichen
und Unicode-Tag-Zeichen wie in ai_frame.fold - "ig​nore", Vollbreite und Tag-Schmuggel werden so erkannt). Ein
Treffer ist eine der Arten
  strong   "vorherige Anweisungen ignorieren", "du bist jetzt ...", Rollen-Marken ([SYSTEM], <|im_start|>), heimlich /
           "ohne es der Person zu sagen"
  exfil    Weitergabe privater Daten (Kalender, Passwoerter, Kontakte, Finanzen, Notizen ...) an eine Mail-Adresse oder
           an eine Adresse mit Ziel-Wort davor ("an/to/a/на/ไปที่ https://...") im selben Satz
  address  Anrede einer KI ("Hinweis fuer die KI:", "AI agents reading this page", "si eres una IA", "ถึง AI", "Saga, ...")
           - zaehlt nur zusammen mit einem Befehlswort (senden, teilen, loeschen, eintragen ...)
Gewollt eng (Fehlalarm < 10 %, Vektoren tests/vectors/share_hint.json): Bitten an Menschen ("Bitte senden Sie die
Rueckmeldung an sekretariat@..."), Artikel ueber KI und "teilt den Kalender mit euren Nachbarn" loesen nichts aus.
Die Warnung ist ein Hinweis fuer die Person, keine Sicherung - die Sicherung ist, dass Geteiltes nie als Bitte zaehlt
(Datenrahmen, Pruefer, Plan-Karte). Nur Standardbibliothek."""
import re
import unicodedata

MAX_SCAN = 20000                     # so viel nimmt POST /api/ai/jobs an (shared.text)
_INVISIBLE = re.compile(r"[­͏؜ᅟᅠ឴឵᠋-᠎​-‏‪-‮⁠-⁯"
                        r"ㅤ︀-️﻿ﾠ]")


def norm(s):
    """Kopie fuer die Muster: Tag-Zeichen -> ASCII, Unsichtbares weg, NFKC, casefold, Leerraum zusammengefasst."""
    s = str(s or "")[:MAX_SCAN]
    out = []
    for ch in s:
        o = ord(ch)
        if 0xE0020 <= o <= 0xE007E:
            out.append(chr(o - 0xE0000))
        elif 0xE0000 <= o <= 0xE007F or _INVISIBLE.match(ch):
            continue
        else:
            out.append(ch)
    s = unicodedata.normalize("NFKC", "".join(out)).casefold()
    return re.sub(r"[ \t  -   　]+", " ", s)


def _rx(*parts):
    return re.compile("|".join(f"(?:{p})" for p in parts), re.S)


# --- strong: Befehle an das Modell selbst -------------------------------------------------------------------------
_STRONG = _rx(
    # en
    r"\b(?:ignore|disregard|forget|override|bypass)\b.{0,30}\b(?:previous|prior|above|earlier|all|any|your|the|system)\b.{0,20}"
    r"\b(?:instructions?|rules|prompts?|guidelines|directions|orders)\b",
    r"\bsystem ?prompt\b", r"\bnew instructions?\b", r"\byou are now\b", r"\bfrom now on,? you\b",
    r"\b(?:developer|admin|god|dan) mode\b", r"\bjailbreak", r"\bsecretly\b",
    r"\b(?:do not|don't|never) (?:tell|inform|mention|notify)\b.{0,30}\b(?:user|person|owner|anyone|them)\b",
    r"\bwithout (?:telling|informing|asking|notifying)\b.{0,20}\b(?:user|person|owner|them)\b",
    # Rollen-Marken von Chat-Vorlagen
    r"\[/?(?:system|inst|assistant)\]", r"<\|?(?:im_start|im_end|system|endoftext)\|?>", r"#{2,} ?(?:instruction|system)\b",
    # de
    r"\b(?:ignorier|vergiss|vergesst|missacht|übergeh|uberg[eä]h)\w*\b.{0,40}\b(?:anweisung|instruktion|regel|vorgabe|befehl)",
    r"\bsystem-?prompt", r"\bneue (?:anweisung|instruktion|befehle)", r"\bdu bist (?:jetzt|ab sofort|nun|ab heute)\b",
    r"\bab (?:jetzt|sofort) bist du\b", r"\bheimlich\b",
    r"\bohne\b.{0,25}\b(?:nutzer|benutzer|person|anwender|besitzer)\w*\b.{0,20}\b(?:zu (?:sagen|informieren|fragen|benachrichtigen)|bescheid)",
    r"\b(?:erwähn|sag|verrat|erzähl)\w* (?:das|es|dies|davon) (?:nicht|niemandem|keinem)\b",
    # es
    r"\b(?:ignora|ignore|olvida|omite|descarta|desobedece)\w*\b.{0,40}\b(?:instrucci[oó]n|instrucciones|reglas|indicaciones|[oó]rdenes)\b",
    r"\b(?:a partir de ahora|desde ahora|ahora) (?:eres|ser[aá]s|act[uú]as?)\b", r"\bnuevas instrucciones\b",
    r"\bsin (?:dec[ií]rselo|decirle|avisar|informar|preguntar)\w*", r"\ben secreto\b",
    r"\bno (?:se lo |le )?(?:digas|menciones|informes|cuentes)\b",
    # ru
    r"(?:игнорируй|игнорируйте|проигнорируй|забудь|забудьте|не обращай\w* внимани\w* на)\b.{0,40}(?:инструкци|указани|правил|команд)",
    r"\b(?:ты|вы) теперь\b", r"\bтеперь (?:ты|вы)\b", r"\bс этого момента (?:ты|вы)\b", r"\bновые (?:инструкции|указания)\b",
    r"\bсистемн\w* (?:промпт|подсказк|инструкц)", r"\bтайно\b", r"\bнезаметно\b",
    r"\bне (?:говори|сообщай|рассказывай|упоминай)\w*\b.{0,20}(?:пользовател|владельц|человек)",
    # th (ohne Wortgrenzen)
    r"(?:ไม่ต้องสนใจ|ละเว้น|ลืม|เพิกเฉย).{0,30}คำสั่ง", r"คำสั่งก่อนหน้า", r"คำสั่งใหม่", r"ตอนนี้คุณคือ", r"ตอนนี้คุณเป็น",
    r"จากนี้ไปคุณ", r"(?:อย่า|โดยไม่|ไม่ต้อง)บอกผู้ใช้", r"แอบ(?:ส่ง|ทำ|แชร์)",
)

# --- address: Anrede einer KI (zaehlt nur mit einem Befehlswort) ------------------------------------------------------
_ADDRESS = _rx(
    r"\b(?:note|message|instructions?|notice) (?:to|for) (?:the |any |all )?(?:ai|assistant|llm|language model|chatbot|agent|bot)s?\b",
    r"\b(?:dear|hey|hello|hi|attention),? (?:ai|assistant|llm|chatbot)\b",
    r"\b(?:ai|llm)s? ?(?:agents?|assistants?|models?|bots?) (?:reading|processing|summari[sz]ing|that read|who read)",
    r"\bif you are an? (?:ai|llm|language model|assistant|bot)\b", r"\bto (?:any|all|the) (?:ai|llm|assistant)s?\b",
    r"\b(?:hinweis|nachricht|anweisung|info)\w* (?:an|für) (?:die |den |eine |einen |alle )?(?:ki|kis|assistent\w*|sprachmodell\w*|chatbot\w*|bot)\b",
    r"\b(?:liebe|lieber|hallo|hey|achtung),? (?:ki|assistent\w*|chatbot)\b",
    r"\bwenn du (?:eine ki|ein sprachmodell|ein bot|ein assistent|ein ki)", r"\b(?:an|für) (?:die |alle )?(?:ki|kis|ki-assistent\w*) ?[:,]",
    r"\b(?:nota|mensaje|instrucciones?|aviso) (?:para|a) (?:la |el |cualquier )?(?:ia|inteligencia artificial|asistente|chatbot|modelo)\b",
    r"\b(?:querida|querido|hola|atenci[oó]n),? (?:ia|asistente)\b", r"\bsi eres (?:una |un )?(?:ia|asistente|modelo|bot)\b",
    r"(?:^|\n) ?(?:asistente|assistant|ассистент) ?[:,]",
    r"\b(?:для|к) (?:ии|ассистента|нейросети|бота|модели) ?[:,]", r"\b(?:дорогой|привет|внимание),? (?:ии|ассистент|бот)\b",
    r"\bесли (?:ты|вы) (?:ии|ассистент|бот|нейросеть|языковая модель)\b", r"\bассистент,",
    r"ถึง ?(?:ai|เอไอ|ผู้ช่วย)", r"(?:ai|เอไอ) ?ที่(?:กำลัง)?อ่าน", r"(?:หาก|ถ้า)คุณเป็น ?(?:ai|เอไอ)", r"เรียน ?(?:ai|เอไอ|ผู้ช่วย)",
)

_COMMAND = _rx(
    r"\b(?:send|forward|share|e-?mail|delete|remove|create|add|write|post|reply|ignore|tell|invite|transfer|upload|copy|export|book|schedule)\b",
    r"\b(?:send|schick|leit|teil|lösch|losch|erstell|leg|füg|fug|trag|schreib|antwort|ignorier|sag|lad|kopier|exportier|überweis)\w*",
    r"\b(?:env[ií]a|reenv[ií]a|comparte|borra|elimina|crea|a[nñ]ade|escribe|responde|ignora|invita|copia|exporta|sube|transfiere)\b",
    r"(?:отправь|перешли|поделись|удали|создай|добавь|напиши|ответь|игнорируй|скажи|пригласи|скопируй|экспортируй|загрузи)",
    r"(?:ส่ง|แชร์|ลบ|สร้าง|เพิ่ม|เขียน|ตอบ|เชิญ|คัดลอก|อัปโหลด)",
)

# --- exfil: private Daten an ein Ziel ----------------------------------------------------------------------------
_EXFIL_VERB = _rx(
    r"\b(?:send|forward|share|e-?mail|mail|upload|export|transfer|post|leak|copy)\w*",
    r"\b(?:send|schick|leit\w* \w* weiter|weiterleit|teil|übermittel|ubermittel|lad\w* \w* hoch|hochlad|exportier|kopier)\w*",
    r"\b(?:env[ií]\w*|reenv[ií]\w*|compart\w*|manda\w*|sube|exporta\w*|copia\w*|transfier\w*)",
    r"(?:отправ|перешл|пересла|подел|выгрузи|загрузи|скопируй|экспорт)",
    r"(?:ส่ง|แชร์|อัปโหลด|คัดลอก|โอน)",
)
_PRIVATE = _rx(
    r"\b(?:calendar|passwords?|passcodes?|contacts|address book|finances?|bank\w*|account (?:details|numbers?)|notes|all (?:of the )?pages|wi-?fi|credentials|api ?keys?|tokens?|chat history|messages)\b",
    r"(?:kalender|passw[oö]rt|kennw[oö]rt|kontakte|adressbuch|finanz|kontodaten|kontonummer|kontostand|iban|notizen|alle seiten|wlan|zugangsdaten|api-?key|chatverlauf|nachrichten)",
    r"(?:calendario|contrase[nñ]a|contactos|finanzas|cuenta bancaria|notas|credenciales|historial)",
    r"(?:календар|парол|контакт|финанс|банк|заметк|учётн|учетн|переписк)",
    r"(?:ปฏิทิน|รหัสผ่าน|รายชื่อ|ผู้ติดต่อ|การเงิน|บัญชี|บันทึก|โน้ต)",
)
_MAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Adresse mit Ziel-Wort davor ("to https://...", "an http://", "a https://", "на http", "ไปที่ https") - "mehr unter https://" nicht
_DEST_URL = re.compile(r"(?:\b(?:to|into|an|nach|mit|zu|a|con|para|на|в)|ไปที่|ไปยัง|ไป|ที่) ?(?:<)?(?:https?://|www\.)\S+")
WINDOW = 160
_SENT_END = re.compile(r"[.!?。](?:\s|$)|\n")


def _exfil(s):
    """Verb + private Daten + Ziel im selben Satz (hoechstens WINDOW Zeichen vor dem Ziel)."""
    for rx in (_MAIL, _DEST_URL):
        for m in rx.finditer(s):
            a = max(0, m.start() - WINDOW)
            ends = [x.end() for x in _SENT_END.finditer(s, a, m.start())]
            w = s[ends[-1] if ends else a:m.end()]
            if _EXFIL_VERB.search(w) and _PRIVATE.search(w):
                return True
    return False


def _persona_address(s, names):
    for nm in names:
        nm = norm(nm).strip()
        if len(nm) >= 2 and re.search(r"(?:^|[\s.!?;:(\"'«„“])" + re.escape(nm) + r" ?,", s):
            return True
    return False


def reasons(text, names=("saga",)):
    """-> Liste der Arten (strong, exfil, address) - leer = kein Hinweis. names: Anreden der KI (Persona-Name, "Saga")."""
    s = norm(text)
    if not s.strip():
        return []
    out = []
    if _STRONG.search(s):
        out.append("strong")
    if _exfil(s):
        out.append("exfil")
    if (_ADDRESS.search(s) or _persona_address(s, names)) and _COMMAND.search(s):
        out.append("address")
    return out


def detect(text, names=("saga",)):
    """Enthaelt der geteilte Inhalt Anweisungen an eine KI? -> bool (ohne Modell, EV-25)."""
    return bool(reasons(text, names))
