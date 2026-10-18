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
