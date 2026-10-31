# Chats archivieren und löschen – Vertrag für die App-Teams (2.1)

Ergänzung zur Chat-Schnittstelle (Format und übrige Endpunkte: Kopf von `saga/chat.py`, Anleitung „Chats“). Alle
Endpunkte wie gewohnt mit Sitzung oder Geräte-Schlüssel (`Authorization: Bearer hkd_…`) und `X-Saga: 1`. Alles ist
rückwärtskompatibel: ältere Apps sehen archivierte Chats einfach weiter in der Liste, unbekannte Felder und Ereignisse
ignorieren. `MIN_APP` ändert sich dadurch nicht.

## 1. Neue Felder in der Chatliste

`GET /api/chats`, `GET /api/chats/{cid}`, `POST /api/chats` und `PATCH /api/chats/{cid}` liefern je Chat zusätzlich:

| Feld | Bedeutung |
|---|---|
| `archived` | `true` = von mir archiviert. Gehört in einen Bereich „Archiviert“ (Web: aufklappbar unten in der Liste). |
| `archived_at` | Zeitpunkt (Unix-Sekunden) oder `null`. |
| `cleared_seq` | „Verlauf gelöscht bis“: Nachrichten mit `seq <= cleared_seq` liefert der Server nicht mehr aus. `> 0` → am Anfang des Verlaufs „Der bisherige Verlauf wurde gelöscht.“ zeigen statt „nicht lesbar“. |
| `delete_for_all` | Was ich **für alle** löschen darf: `"chat"` (Chat samt Verlauf weg), `"history"` (nur Verlauf, Chat bleibt) oder `null` (nicht erlaubt – Option ausgegraut mit Begründung zeigen). |

Regeln für `delete_for_all`: Direkt-Chat – beide Personen (`chat`); freie Gruppe – Chat-Admins (`chat`, `role` =
`admin`); Gruppen-Chat (Saga-Gruppe) – Saga-Admins (`history`); Seiten-Chat – Eigentümer der Seite (`history`).

- Das Feld `unread` der Liste (Summe) und `GET /api/chats/unread` zählen **archivierte** (wie stummgeschaltete) Chats
  **nicht** mit; der Wert `unread` je Chat bleibt (für einen Zähler am Bereich „Archiviert“).
- „Für mich gelöschte“ **Direkt-Chats** fehlen in `GET /api/chats`, bis eine neue Nachricht kommt (dann erscheinen sie
  leer bis auf das Neue) oder die Person den Chat bewusst wieder öffnet (`POST /api/chats {kind:'dm', user}`).
- `GET /api/chats/{cid}` funktioniert für Mitglieder immer, auch für ausgeblendete oder archivierte Chats.

## 2. Archivieren

`PATCH /api/chats/{cid}` mit `{"archived": true|false}` (nur für mich; lässt sich mit `muted` kombinieren) →
`{"chat": {…}}`. Ein anderer Wert als `true`/`false` → 400.

- Eine **neue Nachricht** (auch ein Anruf-Eintrag) holt den Chat für alle nicht stummgeschalteten Mitglieder und für den
  Absender automatisch aus dem Archiv. **Stummgeschaltet + archiviert** bleibt archiviert.
- Benachrichtigungen/Push: wie bisher (nur stummgeschaltete Chats sind still) – da der Chat bei einer neuen Nachricht
  ohnehin aus dem Archiv kommt.

## 3. Löschen

`DELETE /api/chats/{cid}?scope=me|all` (ohne `scope` = `me`). Nicht-Mitglied → 404, unbekannter `scope` → 400.

### `scope=me` – „Nur für mich“ (jede Chat-Art)

```json
{"ok": true, "scope": "me", "removed": false, "cleared_seq": 812, "archived": true, "hidden": false}
```

- Grenze `cleared_seq` = neueste Nachricht des Chats. Der Server **löscht die für mich versiegelten Inhaltsschlüssel**
  dieser Nachrichten (alle meine Geräte und `backup:<uid>`): Sie sind auch mit dem Wiederherstellungsschlüssel nicht
  mehr zu öffnen. Nachrichten, Anhänge (`GET /api/chat/files/{id}` → 404) und Anruf-Einträge bis zur Grenze liefert der
  Server mir nicht mehr aus. Die anderen Mitglieder behalten alles.
- Der verschlüsselte **Name einer freien Gruppe** (Nachricht `kind='title'`) bleibt lesbar.
- Direkt-Chat: verschwindet aus der Liste (`hidden: true`). Andere Arten: werden archiviert (`archived: true`).
- Meine Lesestände springen ans Ende, Benachrichtigungen dieses Chats verschwinden (`notification.read`).
- **Apps müssen ihren lokalen Zwischenspeicher** (entschlüsselte Nachrichten, Vorschaubilder, Anhänge) für diesen Chat
  bis `cleared_seq` **löschen** – auch bei `chat.cleared` von einem anderen eigenen Gerät.
- Freie Gruppe zusätzlich verlassen: danach wie bisher `DELETE /api/chats/{cid}/members/{ich}`.

### `scope=all` – „Für alle“

Ohne Recht (siehe `delete_for_all`) → 403.

- `delete_for_all = "chat"` (Direkt-Chat, freie Gruppe): Chat samt Nachrichten, Schlüsseln, Anhängen (auch
  Vorschaubildern), Lesestand, KI-Freigabe und -Belegen (`chat_ai`, `chat_ai_reply`), Anrufen und Benachrichtigungen
  ist weg → `{"ok": true, "scope": "all", "removed": true}`. Ein neuer Direkt-Chat mit derselben Person bekommt eine
  neue Kennung.
- `delete_for_all = "history"` (Gruppen-/Seiten-Chat): alle Nachrichten samt Anhängen, KI-Belegen und beendeten
  Anrufen sind weg, der Chat und seine Mitglieder bleiben (sie ergeben sich aus Gruppe bzw. Freigaben); für alle
  Mitglieder wird `cleared_seq` gesetzt → `{"ok": true, "scope": "all", "removed": false, "deleted": 42}`.
- Push-Mitteilungen dazu gibt es nicht.

## 4. Live-Ereignisse (`/api/events`, ohne Inhalt)

| Ereignis | Daten | An | Bedeutung |
|---|---|---|---|
| `chat.changed` | `{chat_id, archived}` | mich | archiviert / aus dem Archiv geholt (andere Geräte: Liste neu laden) |
| `chat.cleared` | `{chat_id, scope: "me", cleared_seq}` | mich | Verlauf für mich gelöscht (anderes eigenes Gerät) – Zwischenspeicher leeren, Direkt-Chat schließen |
| `chat.cleared` | `{chat_id, scope: "all", by: {username, name}}` | alle Mitglieder | Verlauf für alle gelöscht – Verlauf leeren, Hinweis „<Name> hat den Verlauf gelöscht.“ |
| `chat.removed` | `{chat_id, kind, by: {username, name}}` | alle Mitglieder | Chat für alle gelöscht – Chat schließen, aus Liste und Zwischenspeicher entfernen, Hinweis „<Name> hat den Chat gelöscht.“ (nicht zeigen, wenn `by.username` ich bin) |

## 5. Speicher, Kontolöschung

Der Stand je Person liegt in `chat_pref` (archiviert seit, `cleared_seq`, ausgeblendet) – eigene Tabelle, keine
Schema-Migration. Wird der Chat gelöscht, verschwindet sie mit (`ON DELETE CASCADE`); die Kontolöschung entfernt die
Einträge des Kontos. Aufbewahrungsfristen (`retention_s`, Admin-Höchstwert) gelten unverändert.

## 6. Gruppeninfo und Admins freier Gruppen (2.5, `chatadm1`)

Server meldet `chatadm1` in `GET /api/version` → `features` (und in den Föderations-Fähigkeiten). Ohne die Meldung:
bisheriges Verhalten (nur `role`, keine Admin-Vergabe). Ältere Apps ignorieren die neuen Felder.

**Felder** (`GET /api/chats`, `GET/POST/PATCH /api/chats/{cid}`, `POST/PATCH …/members`):

| Feld | Bedeutung |
|---|---|
| `members[].admin` | `true` = Admin der freien Gruppe; Gruppen-, Seiten- und Direkt-Chats immer `false` |
| `me_admin` | ich bin Admin (`role == "admin"` in einer freien Gruppe) |
| `can_manage` | Mitglieder hinzufügen/entfernen erlaubt (= freie Gruppe und `me_admin`; schreibgeschützte Gruppe über Servergrenzen: `false`) |
| `can_admin` | Admin vergeben/entziehen erlaubt (= `can_manage`, zusätzlich `false`, wenn das Heim einer Gruppe über Servergrenzen `chatadm1` nicht kann) |
| `can_rename` | Name setzen (Nachricht `kind: 'title'`) erlaubt – jedes Mitglied einer freien Gruppe (schreibgeschützt: `false`) |

Anzeigen: Name (bearbeitbar bei `can_rename`), Mitglieder mit Avatar, `name`, Handle (`handle` bzw. bei Chats v1 `ref`) –
**nie** den Anmeldenamen anderer; Kennzeichen „Admin“ (`admin`) und „Du“ (`me`). `group`: nur lesend mit Hinweis
„Mitglieder ergeben sich aus der Benutzergruppe – dort verwalten.“ `page`: siehe 6a.

### 6a. Seiten-Chats: Mitglieder = Freigaben der Seite (2.5, `pagechatmembers1`)

Server meldet `pagechatmembers1` in `features`. Ohne die Meldung: Hinzufügen/Entfernen im Seiten-Chat → `400` (wie
bisher). Hinzufügen und Entfernen laufen über dieselben Endpunkte wie bei freien Gruppen und werden auf die Freigaben der
Seite abgebildet – vorhandene UI funktioniert ohne neue Aufrufe.

**Felder** (Seiten-Chat, `kind: 'page'`):

| Feld | Bedeutung |
|---|---|
| `can_manage` | ich darf die Seite teilen (= Eigentümer, Teilen eingeschaltet, Seite nicht archiviert/Vorlage) – **auch bei einer noch nicht geteilten Seite** (Chat mit nur mir). Kopie eines Seiten-Chats eines anderen Servers: `false` |
| `can_rename`, `can_admin` | immer `false` (Name = Seitentitel) |
| `members[].via` | nur bei `can_manage`: `'owner'` (gehört die Seite) \| `'share'` (direkte Freigabe dieser Seite) \| `'group'` (über eine Gruppe) \| `'parent'` (über eine Seite darüber) \| `null`. Bei mehreren Wegen der erste in dieser Reihenfolge (share, group, parent) |
| `members[].removable` | nur bei `can_manage`: `true` = Entfernen geht (direkte Freigabe dieser Seite und kein anderer Weg) |

Ohne `can_manage` fehlen `via`/`removable` (andere sollen nicht erfahren, über welche Gruppe/Seite jemand Zugriff hat).

**Hinzufügen:** `POST /api/chats/{cid}/members` mit `{"user": …}` (Konto hier: Handle `~x` bzw. `members[].username`
wie bei freien Gruppen) oder `{"handle": "~x@host"}` (eigener Server wie `user`; anderer Server: Freigabe an diese
Person dort, wie Teilen im Web), optional `"role": "view"|"comment"|"edit"` (Standard `view`).

- Legt die Freigabe der Seite an (gleiche Prüfungen wie `PUT /api/notes/{nid}/shares`, Mitteilung „Seite geteilt“,
  Föderation). Hat die Person schon eine höhere direkte Freigabe, bleibt diese.
- Antwort `200 {"chat": …}` mit neuer Liste; `chat.changed` an alle Mitglieder. Personen anderer Server erscheinen, sobald
  ihr Server den Handle bestätigt (im Hintergrund, wenige Sekunden).
- Fehler: `403` nicht Eigentümer („Personen hinzufügen kann nur, wem die Seite gehört …“) bzw. Teilen-Regeln (z. B. nur
  gemeinsame Gruppen); `400` `role` ungültig bzw. Ziel ungültig; `404` Konto unbekannt; `409` schon Mitglied (auch über
  eine Gruppe), höchstens 50 Mitglieder bzw. 200 Freigaben; anderer Server: `400`/`403`/`502` wie beim Teilen.

**Entfernen:** `DELETE /api/chats/{cid}/members/{who}` (`who` wie oben).

- Direkte Freigabe dieser Seite und kein anderer Weg → Freigabe wird entzogen (wie im Teilen-Dialog), die Person ist
  nicht mehr im Chat. Antwort `200 {"ok": true}`, `chat.changed` an alle bisherigen Mitglieder.
- `409` mit Text, woher der Zugriff kommt: „{name} hat Zugriff über die Gruppe „{g}“ – …“ bzw. „… über die übergeordnete
  Seite „{title}“ – …“; Eigentümer: „Wem die Seite gehört, bleibt immer im Seiten-Chat.“ (Die Texte bekommt nur der
  Eigentümer – er kennt Gruppen und Seiten darüber.)
- `who` = ich → `400` (nicht verlassbar, nur stummschalten); nicht Eigentümer → `403`; unbekannt → `404`.
- `group`-Chats bleiben unverändert (`400`).

**Admin vergeben/entziehen:** `PATCH /api/chats/{cid}/members/{who}` mit `{"admin": true|false}`.

- `who` wie bei `DELETE …/members/{who}`: `members[].username` (Chats v1: Benutzername; Format v2/Stufe 3: Handle
  `~x@host` bzw. bei Personen anderer Server deren Handle), der Such-Handle `~x` oder der eigene Benutzername.
- Antwort `200 {"chat": …}` (vollständiges Chat-Objekt wie `GET`, mit neuer Liste). Ist die Rolle schon so: ebenfalls `200`.
- Fehler: `400` keine freie Gruppe (`group`/`page`/`dm`) bzw. `admin` kein Boolean; `403` ich bin kein Admin; `404` Chat
  bzw. Mitglied unbekannt; `409` letzter Admin („Das ist der letzte Admin der Gruppe – erst jemand anderen zum Admin
  machen.“) – sich selbst entziehen geht, solange ein anderer Admin bleibt; über Servergrenzen zusätzlich `409` „Der
  Server der Gruppe ({host}) ist zu alt, um Admins zu ändern.“, `502` Heim nicht erreichbar, `403` schreibgeschützt.
- Danach `chat.changed {chat_id}` an alle Mitglieder (wie Hinzufügen/Entfernen); Push gibt es dafür nicht.
- **Systemzeile** (nur Format v2/Stufe 3 und über Servergrenzen, `kind: 'sys'`, wie `members`):
  `{"type":"admin","target":{name,host,handle},"on":true|false,"by":{name,host,handle}|null}`. Texte: „{by} hat {target}
  zum Admin gemacht.“ / „{by} hat {target} die Admin-Rolle entzogen.“; `by == target`: „… ist jetzt Admin.“ / „… ist kein
  Admin mehr.“ Chats v1 bekommen (wie beim Hinzufügen/Entfernen) keine Systemzeile.
- Geht der letzte Admin (verlassen/entfernt), wird wie bisher automatisch ein anderes Mitglied Admin.

## 7. Kontakte (2.5, `contacts1`)

Server meldet `contacts1` in `GET /api/version` → `features`. Kontakte sind die Personen, mit denen ich in einem
**Direkt-Chat oder einer freien Gruppe** war – lokal und auf anderen Saga-Servern. Sie **bleiben**, wenn der Chat
gelöscht oder verlassen wird. Benutzergruppen- und Seiten-Chats ergeben keine Kontakte. Nie ein Anmeldename: lokale
Personen erscheinen mit dem Such-Handle `~…` (wie `GET /api/chat/people` → `users[].username`), Personen anderer Server
mit ihrem Handle `~x@host` (wie `members[].handle`). Kein Orakel: es gibt nur Einträge aus eigenen Chats.

**Wann eingetragen** (gegenseitig, ein ausgeblendeter Kontakt wird dabei wieder sichtbar): Chat anlegen (`dm`, `free`,
auch mit `handles`), Mitglied hinzufügen (neues Mitglied ↔ alle anderen), Direkt-Chat bzw. Gruppe eines anderen Servers
annehmen (eine offene **Nachrichtenanfrage** macht noch niemanden zum Kontakt), Mitgliederliste vom Heim einer Gruppe.
Bestehende Chats wurden beim Update einmalig übernommen (Migration 28). Neue Nachrichten sortieren den Kontakt nach oben.

**Liste:** `GET /api/chat/contacts?q=&limit=50` (höchstens 200; 60 Anfragen je Minute wie die Personensuche, sonst 429)

```json
{"contacts": [{"ref": "~k3…", "name": "Anna Berg", "sub": "", "avatar": "/api/avatar/…", "remote": false,
               "ready": true, "gone": false, "last_at": 1793460000.5},
              {"ref": "~bslp…@b.example", "name": "Bert Gast", "sub": "b.example", "host": "b.example", "avatar": null,
               "remote": true, "ready": true, "gone": false, "last_at": 1793450000.0}]}
```

| Feld | Bedeutung |
|---|---|
| `ref` | Kennung. Lokal: `POST /api/chats {kind:'dm', user: ref}` bzw. `members: [ref]`, `POST …/members {user: ref}`. Fremd: `{kind:'dm', handle: ref}` bzw. `handles: [ref]`, `POST …/members {handle: ref}` |
| `name` | Anzeigename (lokal: Anzeige-/öffentlicher Name, nie Anmeldename; fremd: zuletzt bekannter Name) |
| `sub` | Unterzeile: lokal wie `/api/chat/people` (unterscheidet Gleichnamige), fremd der Server |
| `host` | nur bei `remote: true` – Server der Person |
| `avatar` | Profilbild oder `null` (Initialen) |
| `ready` | Chat möglich (lokal: hat Chats eingerichtet; fremd: Server verbunden, Chats erlaubt) |
| `gone` | nicht anschreibbar: lokales Konto deaktiviert bzw. Verbindung zum Server getrennt – ausgegraut zeigen |
| `last_at` | Unix-Sekunden, Sortierung „zuletzt zuerst“ |

`q` filtert als Teilwort in `name`/`sub`, **jede Länge** (die Liste ist ohnehin die eigene).

**Aus Kontakten entfernen:** `DELETE /api/chat/contacts/{ref}` (URL-kodiert; `~x@host` darf `@` enthalten) → `200
{"ok": true}`; unbekannt oder schon entfernt → `404`. Der Kontakt ist ausgeblendet, bis ein neuer gemeinsamer Chat
entsteht. Live-Ereignis an meine anderen Geräte: `chat.contacts {ref, hidden: true}` – Liste neu laden.

**Personensuche:** `GET /api/chat/people?q=` stellt passende, anschreibbare Kontakte **vorne** in `users` (lokal) bzw.
`remote` (fremd) – mit `contact: true`, auch bei 1–2 Zeichen als Teilwort und auch außerhalb von „nur gemeinsame Gruppen“
(man hat schon miteinander geschrieben; ein Direkt-Chat bzw. Hinzufügen ist dann erlaubt). Keine Dubletten mit den
übrigen Treffern. Ein Kontakt gilt nur in **eine** Richtung (wer mich ausgeblendet hat, umgeht für mich nichts).

**Oberfläche:** Dialog „Neue Unterhaltung“ und „Mitglied hinzufügen“ ohne Eingabe: Überschrift „Kontakte“ und die Liste
(zuletzt zuerst, ~30, scrollbar; in „Mitglied hinzufügen“ ohne die schon dabei sind und fremde nur in Gruppen über
Servergrenzen); mit Eingabe die Suche (Kontakte zuerst). Je Kontakt Menü/Wischen „Aus Kontakten entfernen“.

**Weg ist ein Kontakt,** wenn das Konto (meins oder das der Person) gelöscht wird, die Person auf ihrem Server gelöscht
wird (`person.gone`) oder der Admin die getrennte Instanz ganz entfernt. Persönliches Backup: `daten.json` →
`chat_contacts` `[{ref, name, remote, first_at, last_at, hidden}]`; Einspielen mit `{contacts: true}` (lokale nur, mit
denen ein Chat erlaubt ist).

## 8. Gruppen: Bild, Beschreibung, Rechte, Admin-Löschen, Anheften, Sperren, Melden (2.5)

Server meldet in `GET /api/version` → `features`: `groupinfo2` (8a, 8b), `moddel1` (8c), `msgpin1` (8d), `block2` (8e),
`report1` (8f) – dieselben Namen auch als Föderations-Fähigkeiten. Grundsatz: Ende-zu-Ende bleibt Ende-zu-Ende; der
Server sieht keinen neuen Klartext. Ohne die Meldung: Felder fehlen, Endpunkte antworten `404`, Apps zeigen nichts davon.

**Felder im Chat-Objekt** (`GET /api/chats`, `GET/POST/PATCH /api/chats/{cid}` und alle Antworten mit `chat`):

| Feld | Bedeutung |
|---|---|
| `perm_info` | `'all'` \| `'admins'` – wer Name, Bild, Beschreibung ändert und anheftet (freie Gruppe; sonst immer `'all'`) |
| `perm_send` | `'all'` \| `'admins'` – wer Nachrichten senden darf (freie Gruppe; sonst `'all'`) |
| `can_send` | ich darf jetzt schreiben (`false`: Eingabefeld ausblenden, Hinweis „In dieser Gruppe dürfen nur Admins schreiben.“; Direkt-Chat mit einer von mir gesperrten Person ebenfalls `false`, dazu `blocked: true`) |
| `can_edit_info` | Name/Bild/Beschreibung ändern erlaubt; `can_rename` folgt diesem Wert |
| `can_info2` | Bild und Beschreibung möglich (wie `can_edit_info`, zusätzlich muss das Heim einer Gruppe über Servergrenzen `groupinfo2` können) |
| `can_perms` | Rechte-Schalter ändern erlaubt (Admin einer freien Gruppe, Heim mit `groupinfo2`) |
| `can_pin` | anheften/lösen erlaubt |
| `can_moderate` | fremde Nachrichten für alle löschen erlaubt (8c) |
| `pins` | `[msg_id, …]` angeheftete Nachrichten (älteste zuerst, höchstens 3) |
| `desc_msg`, `avatar_msg` | aktuelle Steuer-Nachricht mit Beschreibung bzw. Bild (wie `title_msg`: verschlüsselt, mit `key`/`key_for`) – fehlt, wenn es keine gibt |
| `members[].blocked` | `true` = ich habe diese Person gesperrt |

### 8a. Gruppenbild und Beschreibung (`groupinfo2`)

Wie der Name einer freien Gruppe (`kind: 'title'`) als **verschlüsselte Steuer-Nachricht** über
`POST /api/chats/{cid}/messages` (v1 und Format v2, Signatur wie jede Nachricht):

- `kind: 'desc'`, Inhalt `{"v":1,"type":"desc","desc":"…"}` (höchstens 500 Zeichen, Client kürzt).
- `kind: 'avatar'`, Inhalt `{"v":1,"type":"avatar","file":{id,k,m,n,s,w,h}}` – das Bild ist ein verschlüsselter Anhang
  wie bei Bild-Nachrichten (vorher `POST /api/chats/{cid}/files`, Kennung in `files:[id]` mitsenden). Empfehlung:
  quadratisch zugeschnitten, 256 px, JPEG. `"file": null` = Bild entfernt.

Der Server merkt sich nur die Folgenummer (`desc_seq`, `avatar_seq`) und löscht die vorige Steuer-Nachricht samt Anhang.
Steuer-Nachrichten erscheinen nicht in `GET …/messages`, laufen nicht ab und bleiben nach „für mich löschen“ lesbar.
Clients zeigen das zuletzt gültige (Signatur geprüft) und laden bei `chat.changed` neu. Nur in freien Gruppen (sonst
`400`); ohne Recht (`perm_info: 'admins'`, kein Admin) `403`. Seiten-Chats behalten Seitensymbol/-titel.

### 8b. Gruppenrechte (`groupinfo2`)

`PATCH /api/chats/{cid}` `{"perm_info": "all"|"admins", "perm_send": "all"|"admins"}` (einzeln oder zusammen; die Felder
lassen sich mit anderen PATCH-Feldern kombinieren). Nur Admins freier Gruppen (`403`), andere Chat-Arten `400`,
ungültiger Wert `400`. Antwort `{chat}`; Live `chat.changed` an alle Mitglieder. Durchgesetzt beim Senden:
`title`/`desc`/`avatar` bei `perm_info: 'admins'` nur von Admins, normale Nachrichten bei `perm_send: 'admins'` nur von
Admins – sonst `403` mit Meldung. Im Format v2/Stufe 3 Systemzeile
`{"type":"perm","perm_info"?:…, "perm_send"?:…, "by":{name,host,handle}|null}` (nur die geänderten Felder).

### 8c. Admins löschen fremde Nachrichten (`moddel1`)

`DELETE /api/chats/{cid}/messages/{mid}` für eine **fremde** Nachricht ist erlaubt für: Admins der freien Gruppe,
Eigentümer der Seite im Seiten-Chat, Saga-Admins im Benutzergruppen-Chat (`can_moderate`). Antwort
`{"ok":true,"by_admin":true}`. Statt die Zeile zu entfernen bleibt ein **Grabstein**: `kind: 'tomb'`, kein Ciphertext,
keine Schlüssel, Anhänge/Reaktionen/Bearbeitungen/Anheftung weg. Live `chat.deleted` mit `by_admin: true` (ältere
Clients entfernen die Nachricht wie bisher). Grabsteine liefert `GET …/messages` nur mit `?tombs=1`
(`{id, seq, kind:'tomb', by_admin:true, sender, created, …}`, ohne `key`); Anzeige „Von einem Admin gelöscht“.
Mehrfach-Löschen (`POST …/messages/delete`) bleibt auf eigene Nachrichten beschränkt.

### 8d. Nachrichten anheften (`msgpin1`)

`POST /api/chats/{cid}/pins/{mid}` bzw. `DELETE …` → `{"ok":true,"pins":[…]}`. Höchstens 3 (`409` „Höchstens 3
angeheftete Nachrichten – erst eine lösen.“), nur normale Nachrichten des Chats (`404`), freie Gruppe mit
`perm_info: 'admins'` nur Admins (`403`), sonst alle Mitglieder (Direkt-Chat: beide). Idempotent. Live `chat.changed`.
Der Inhalt bleibt verschlüsselt: Clients zeigen oben eine Leiste mit der (geladenen, entschlüsselten) Nachricht.
Gelöschte bzw. abgelaufene Nachrichten fallen heraus.

### 8e. Sperren lokaler Konten (`block2`)

`POST /api/chat/blocks` `{"target": …}` – Such-Handle `~x` (aus der Personensuche / `members[].ref`), Handle `~x@host`
(auch dieses Servers, `members[].handle`) oder ganzer Server `host`. Antwort `{ok, target}` (gespeichert wird immer der
stabile Handle `~x@host`); unbekannt `404`, ungültig `400`. `GET /api/chat/blocks` liefert Konten dieses Servers mit
`local: true` und Anzeigenamen (nie den Anmeldenamen); `DELETE /api/chat/blocks?target=` nimmt `target` aus der Liste
(oder den Such-Handle). Live `chat.blocks` `{target, blocked}` an die eigenen Geräte.
Wirkung (beide Richtungen): kein neuer Direkt-Chat (`404` wie „kein Chat möglich“), kein Hinzufügen zu Gruppen, keine
Anrufe; im bestehenden Direkt-Chat Senden `403` („Du hast diese Person gesperrt …“ bzw. „… kann nicht zugestellt
werden.“). Gesperrte erfahren es nicht. In Gruppen klappt der Client ihre Nachrichten ein (`members[].blocked`).

### 8f. Melden (`report1`)

`POST /api/chat/report` `{"chat": cid, "msgs": [mid, …], "reason": "spam"|"abuse"|"illegal"|"other", "text"?: "…"}`.
`msgs` höchstens 20 fremde Nachrichten des Chats (eigene `400`, unbekannt `404`; in Gruppen mindestens eine, im
Direkt-Chat auch leer). `text` (höchstens 2000 Zeichen) ist der **Klartext, den die meldende Person freiwillig mitschickt**
– der Server kann die Nachrichten nicht lesen; Clients füllen ihn mit den gemeldeten Nachrichten vor und lassen ihn
ändern/abwählen. Gespeichert werden außerdem Kennung, Zeit und Absender (Handle, Name) der Nachrichten. Ratenbegrenzung:
5 je Stunde, 20 je Tag (`429`). Antwort `{ok, id}`. Alle Saga-Admins bekommen die Benachrichtigung `chat.report`
(Glocke + Push-Bereich „Admin“, ohne Inhalt); Admin-Bereich → „Meldungen“ (offen/erledigt, löschen). Kontolöschung der
meldenden Person: Meldung bleibt anonym.

## 9. Chat-Alltag (2.5): Stummschalten, Anheften, Ungelesen, Ausblenden, Erwähnungen, Formatierung, Linkvorschau, Medien

Features in `/api/version` → `features`: `mute2`, `chatpin1`, `markunread1`, `chathide1`, `chatmention1`, `chatfmt1`,
`chatmedia1`, dazu `unfurl1` nur, solange der Admin-Schalter „Linkvorschau in Chats“ (`chat_unfurl`, Standard an) an ist.
Ohne das Feature verhalten sich Apps wie bisher. Alles ist **nur für mich** (je Person und Chat, Tabelle `chat_pref`) und
wirkt auf allen meinen Geräten (Live `chat.changed`). Ende-zu-Ende bleibt: der Server sieht keinen neuen Klartext (einzige
Ausnahme: die Kennungen der Erwähnten, `mentions`, neben dem Chiffrat – er kennt die Mitglieder ohnehin).

### 9.1 Felder im Chat-Objekt (`GET /api/chats`, `GET/PATCH/POST /api/chats…`)

| Feld | Bedeutung |
|---|---|
| `muted` | abgeleitet: **jetzt** stumm (für ältere Apps wie bisher) |
| `muted_until` | `0` = nicht stumm, `-1` = für immer, sonst Unix-Sekunden (Ende). Abgelaufene setzt der Server selbst zurück. |
| `notify` | `"all"` \| `"mentions"` (nur Erwähnungen und Antworten auf eigene Nachrichten) \| `"none"` |
| `pinned`, `pinned_at` | angeheftet (seit, Unix-Sekunden) |
| `unread_mark` | als ungelesen markiert; `unread` ist dann mindestens `1` |
| `hidden`, `can_hide` | für mich ausgeblendet; ausblendbar sind nur Gruppen- und Seiten-Chats (nicht verlassbar) |
| `mention_seq` | `seq` der neuesten Nachricht, in der ich erwähnt wurde (`0` = nie) |
| `me_ref` | meine Kennung in Erwähnungs-Token (Stufe 3/über Servergrenzen = `my_handle`, sonst lokale Kennung `~x`) |

`GET /api/chats` sortiert **angeheftete oben** (nach `pinned_at`), dann neueste zuerst; ausgeblendete fehlen, `hidden_count`
nennt ihre Zahl, `GET /api/chats?hidden=1` liefert **nur** sie (für einen Bereich „Ausgeblendet“ bzw. die Suche).
`GET /api/chats/unread` zählt markierte Chats je 1 und ausgeblendete nicht.

### 9.2 `PATCH /api/chats/{cid}` (Felder frei kombinierbar, weitere Bereiche fügen eigene Felder hinzu)

| Body | Wirkung | Fehler |
|---|---|---|
| `{"muted_until": unix \| 0 \| -1}` | stumm bis / aus / für immer (Zeitpunkt in der Vergangenheit = aus) | 400 sonst |
| `{"muted": bool}` | alt: `true` = für immer, `false` = aus | |
| `{"notify": "all" \| "mentions" \| "none"}` | Art der Mitteilungen | 400 |
| `{"pinned": bool}` | anheften / lösen, höchstens **5** | 409 „Höchstens 5 Chats anheften.“ |
| `{"unread_mark": bool}` | als ungelesen markieren; `POST …/read` löscht es | |
| `{"hidden": bool}` | ausblenden / wieder anzeigen | 400 bei Direkt-Chat und freier Gruppe |

Antwort wie bisher `{"chat": …}`.

**Mitteilungen (Benachrichtigung + Push) – eine Regel für alles:** stumm (`muted_until` aktiv) oder `notify: "none"` →
nichts (auch keine Erwähnung); erwähnt → `chat.mention` (auch bei `notify: "mentions"` und in ausgeblendeten Chats; der
Chat kommt dabei zurück); ausgeblendet → nichts; `notify: "mentions"` → nur Antworten auf eigene Nachrichten
(`chat.message`); sonst `chat.message`. Anrufe klingeln nicht in stummgeschalteten Chats (wie bisher). Neue Art
`chat.mention` in `/api/notifications` (abschaltbar, Bereich „chat“ beim Push), Push-Text „{name} hat dich in einem Chat
erwähnt“. Ein ausgeblendeter Chat kommt auch zurück, wenn ich ihn bewusst öffne (`POST /api/chats {kind:'group'|'page', …}`).

### 9.3 Erwähnungen (`chatmention1`)

Im (verschlüsselten) Text steht je Person das Token `<@kennung>` – Kennung = `members[].handle` (Stufe 3, über
Servergrenzen) bzw. `members[].ref` (Chat v1). Clients zeigen es als `@Anzeigename` (unbekannte Kennung: `@Unbekannt`) und
heben Nachrichten hervor, die `me_ref` enthalten. Beim Senden (`POST /api/chats/{cid}/messages`, v1 und v2) zusätzlich
unverschlüsselt `"mentions": ["kennung", …]` (höchstens 50). Der Server nimmt nur **aktuelle Mitglieder** (Rest still
ignoriert, nie der Absender); kein Array → 400. Live `chat.message` an die Mitglieder trägt dann `mentions` (Kennungen,
nie Anmeldenamen). Auswahl in der App: bei `@` die Mitglieder des Chats (Anzeigename), daneben wie bisher Seiten.

### 9.4 Formatierung (`chatfmt1`, nur Darstellung)

Markdown-light im Text: `*fett*`, `_kursiv_`, `~durch~`, `` `code` ``, ```` ```block``` ````, Zeilen `- ` = Liste,
`> ` = Zitat. Marken nur am Wortrand (`a_b_c`, `2*3*4` bleiben). **Sicher rendern:** nur Text-Elemente, nie HTML aus dem
Text, keine Links aus Markdown (Adressen erkennt der Client wie bisher, nur http/https). Ältere Clients zeigen die Zeichen.

### 9.5 Linkvorschau (`unfurl1`)

`GET /api/chat/unfurl?url=<adresse>` (Sitzung/Geräte-Schlüssel) → `{url, title, desc, site, image?: {mime:'image/jpeg', w, h,
b64}}` oder `{}` (nichts Brauchbares, nicht erreichbar, Bot-Schutz). 400 ungültige Adresse (nur http/https, Standardport,
ohne Zugangsdaten, ≤ 2000 Zeichen), 403 Admin-Schalter aus, 429 mehr als 30 je Minute. Der Server ruft nur öffentliche
Adressen ab (auch nach Weiterleitungen, höchstens 3), nur `text/html`, ≤ 512 KB, ≤ 8 s; das Bild (PNG/JPEG/GIF/WebP, ≤ 2 MB)
kommt neu kodiert als JPEG ≤ 400 px. Er speichert nichts.

Der **Absender** holt die Vorschau (z. B. 0,6 s nach der letzten Eingabe, erste Adresse im Text), zeigt sie mit „✕“ zum
Entfernen und legt sie **in den verschlüsselten Inhalt**: `body.preview = {url, title, desc, site, img?: {id, k, m, w, h, s}}`
– `img` ist ein normaler verschlüsselter Anhang (wie ein Vorschaubild; seine `id` gehört in `files` der Sende-Anfrage).
Empfänger laden **nichts** nach (nur das verschlüsselte Bild vom eigenen Server).

### 9.6 Medienübersicht (`chatmedia1`)

`GET /api/chats/{cid}/messages?has=file&before=<seq>&limit=` liefert nur Nachrichten mit Anhängen (sonst wie gewohnt).
Links kennt der Server nicht (`has=link` → 400): Clients suchen sie in geladenen und nachgeladenen Seiten. Web: Kopfmenü
bzw. Gruppeninfo → „Medien, Dateien, Links“ (Reiter Medien/Dateien/Links, „Ältere laden“).

### 9.7 Nur im Client

Entwürfe je Chat bleiben auf dem Gerät (Web: `localStorage` `saga-chat-draft:<konto>:<chat>`, beim Abmelden gelöscht), nie
auf dem Server. Emoji-Auswahl im Eingabefeld = dieselbe Auswahl wie bei Reaktionen.
