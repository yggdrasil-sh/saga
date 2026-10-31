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
