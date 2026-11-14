# Saga – öffentliche API, Webhooks und MCP-Server

Ab Saga 2.5 können Skripte, Automationen (n8n, Node-RED, Home Assistant …) und KI-Werkzeuge mit einem **API-Token** auf
Seiten, Datenbanken, Aufgaben, Kommentare und Dateien zugreifen. Drei Wege:

| Weg | Adresse | Wofür |
|---|---|---|
| REST-API | `/api/v1/…` | Skripte und Automationen |
| Webhooks | eigene Adresse, Saga sendet `POST` | „Sag mir Bescheid, wenn sich etwas ändert“ |
| MCP-Server | `/mcp` | KI-Werkzeuge wie Claude (Model Context Protocol) |

Ob ein Server das kann: `GET /api/version` → `features` enthält `api1`, `webhook1` und `mcp1`.

Die internen Endpunkte unter `/api/…` (ohne `v1`) gehören der Weboberfläche und den Apps; sie können sich jederzeit
ändern und nehmen **keine** API-Tokens an.

## Tokens

Ein Token entsteht unter **Einstellungen → API** (Web). Vorher fragt Saga zur Sicherheit noch einmal nach dem Passwort
(bzw. einem Code oder Passkey; SSO-Konten: Anmeldung höchstens 5 Minuten alt). Der Token (`sga_…`) wird **genau einmal**
angezeigt; Saga speichert nur eine Prüfsumme.

- **Bereiche:** `read:pages`, `write:pages`, `read:db`, `write:db`, `read:tasks`, `write:tasks`, `read:files`,
  `write:files`, `read:comments`, `write:comments`, `webhooks`, `mcp`. `write:x` schließt `read:x` ein.
- **Seitenbäume (optional):** Mit „Nur diese Seitenbäume“ sieht der Token nur die angegebenen Seiten und alles darunter.
  Alles andere ist für ihn „nicht gefunden“ (404) – auch in der Suche.
- **Rechte:** Ein Token kann nie mehr als das Konto. Geteilte Seiten gehen mit der Rolle, die das Konto hat (Lesen,
  Kommentieren, Bearbeiten).
- **Ablauf:** 30 Tage bis 1 Jahr (Standard 90 Tage; Höchstwert und „ohne Ablauf“ legt der Admin fest).
- **Ungültig** bei Widerruf, Ablauf, gesperrtem oder gelöschtem Konto, „Überall abmelden“, Passwortwechsel mit
  Abmelden und wenn der Admin die API abschaltet.
- Höchstens 10 Tokens je Konto (Admin einstellbar).

Anfragen tragen den Token im Kopf:

```
Authorization: Bearer sga_…
```

Keine Cookies, kein CORS: die API ist für Server und Skripte gedacht, nicht für Webseiten fremder Domains.

### Ratenbegrenzung

Je Token 120 Anfragen und 30 Schreibzugriffe pro Minute, je Konto (alle Tokens zusammen) das Dreifache (Admin
einstellbar). Darüber: `429` mit `Retry-After` (Sekunden).

### Fehler

```json
{"error": "Dem Token fehlt der Bereich „write:pages“.", "code": "insufficient_scope"}
```

| Status | `code` | Bedeutung |
|---|---|---|
| 400 | `bad_request` | Eingabe ungültig (Meldung sagt, was) |
| 401 | `unauthorized`, `expired` | Token fehlt, ist falsch, widerrufen oder abgelaufen |
| 403 | `insufficient_scope`, `forbidden`, `api_disabled` | Bereich fehlt, Rolle reicht nicht, API für das Konto aus |
| 404 | `not_found` | gibt es nicht oder nicht für diesen Token |
| 409 | `conflict` | Seite wurde inzwischen geändert (`rev`) bzw. Höchstzahl erreicht |
| 413 | `too_large` | Inhalt oder Datei zu groß |
| 423 | `locked` | Seite ist gesperrt |
| 429 | `rate_limited` | zu viele Anfragen (`Retry-After`) |

## Endpunkte

Alle Antworten sind JSON. IDs sind Zahlen. **Personen** erscheinen nur als Handle (`~abcd…`, lokale Konten) bzw.
`@name@host` (andere Saga-Instanzen) mit Anzeigename – nie mit Anmeldenamen.

### Konto

`GET /api/v1/me` (jeder gültige Token) → `{handle, name, token: {id, name, prefix}, scopes, roots, expires}`

### Seiten (`read:pages` / `write:pages`)

| Methode | Pfad | |
|---|---|---|
| `GET` | `/api/v1/search?q=…&limit=50` | Volltextsuche (Titel, Inhalt, Schlagworte) → `{results: [{id, title, icon, kind, parent_id, updated, snippet, role}]}` |
| `GET` | `/api/v1/pages/{id}` | Seite mit Inhalt als Editor.js-Blöcke (`blocks`) |
| `GET` | `/api/v1/pages/{id}?format=markdown` | … als Markdown (`markdown`) |
| `POST` | `/api/v1/pages` | neue Seite `{title, parent_id?, markdown? \| blocks?, icon?, tags?, kind?: "database"}` → `201 {id}` |
| `PATCH` | `/api/v1/pages/{id}` | ändern: `title`, `icon`, `tags`, `markdown` bzw. `blocks` (ersetzt den Inhalt), `append_markdown` (anfügen), `rev` |
| `DELETE` | `/api/v1/pages/{id}` | in den Papierkorb (nur der Eigentümer, mit Unterseiten) |
| `POST` | `/api/v1/pages/{id}/blocks` | Block/Abschnitt gezielt ändern `{block_id \| heading, markdown, mode?, rev?}` (wie MCP `update_block`) → `{rev, mode, target, ids}` |
| `POST` | `/api/v1/pages/{id}/checkbox` | Kästchen setzen `{block_id?, item \| text, checked, rev?}` (wie MCP `set_checkbox`) → `{rev, block_id, item, checked, changed}` |
| `POST` | `/api/v1/pages/{id}/archive` | archivieren `{archived: true}` (Standard) bzw. zurückholen `{archived: false}` (nur der Eigentümer, mit Unterseiten) |

Lesen liefert `{id, title, icon, kind, parent_id, created, updated, rev, tags, archived, locked, role, owner, updated_by,
blocks | markdown}`, bei Datenbank-Einträgen zusätzlich `properties` (Werte nach Eigenschafts-ID) und bei Datenbanken
`schema`.

**Konflikte:** Wer `rev` mitschickt (aus dem letzten Lesen), überschreibt keine Änderung, die inzwischen jemand anderes
gemacht hat – dann kommt `409`. `append_markdown` prüft das immer.

**Blöcke gezielt ändern** (`/blocks`, MCP `update_block`): Ziel ist `block_id` (auch in Toggles, Spalten und
synchronisierten Originalen) **oder** `heading` (Text einer Überschrift – ohne Formatierung, Leerraum und Groß/Klein
egal; mehrdeutig oder nicht gefunden → `400`/`404` mit `candidates: [{id, level, text}]`). `mode`:
- `replace_block` (Standard bei `block_id`): nur dieser Block; der erste neue Block übernimmt seine ID.
- `replace_section` (Standard bei `heading`): Überschrift + alle folgenden Blöcke bis zur nächsten Überschrift gleicher
  oder höherer Ebene. Die Überschrift bleibt – beginnt das Markdown mit einer eigenen Überschrift, ersetzt diese sie
  (ID bleibt). Leeres Markdown leert den Abschnitt. Bei einer Toggle-Überschrift: ihr Inhalt.
- `insert_after`: nach dem Block (`block_id`) bzw. am Ende des Abschnitts (`heading`).
- `delete`: den Block entfernen; per `heading` den ganzen Abschnitt.

Alle anderen Blöcke bleiben unverändert (dieselben IDs – Kommentare an Blöcken bleiben hängen); neue Blöcke bekommen
neue IDs. Ohne `rev` gilt der eben gelesene Stand – schreibt jemand dazwischen, kommt `409` statt eines stillen
Überschreibens. Kopien synchronisierter Blöcke lassen sich hier nicht ändern (`400` mit Hinweis auf das Original),
gesperrte Seiten → `423`, Text-Notizen → `400` (nur ganzer Inhalt per `PATCH`).
**Kästchen** (`/checkbox`): Checklisten (`list` mit `style: checklist`, auch verschachtelt, und ältere `checklist`-Blöcke).
`item` = Nummer des Punkts (`"2"`, verschachtelt `"2.1"`) zusammen mit `block_id`, oder `text` (eindeutig; ohne
`block_id` in allen Checklisten der Seite). Ist das Kästchen schon so gesetzt: `changed: false`, keine neue Revision.
Kontrollkästchen-Eigenschaften von Datenbank-Einträgen gehen über `PATCH /api/v1/rows/{id}`.

**Markdown** (rein): Überschriften, Absätze, **fett**, *kursiv*, ~~durch~~, `Code`, Links, Listen (auch verschachtelt
und Aufgaben `- [ ]`), Zitate, Callouts (`> [!note]`), Codeblöcke, Tabellen, Trennlinien. Seitenverweise:
`[Text](/notes#123)` oder `[Text](page:123)`. Unsichere Links (`javascript:` …) werden zu Text. Markdown (raus):
dieselbe Umwandlung wie beim Export. Markdown-Eingaben (`markdown`, `append_markdown`) haben höchstens
100 000 Zeichen (sonst `413`).

### Datenbanken (`read:db` / `write:db`)

| Methode | Pfad | |
|---|---|---|
| `GET` | `/api/v1/databases?q=…` | Datenbanken, die der Token sehen darf |
| `GET` | `/api/v1/databases/{id}` | Schema: `{properties: [{id, name, type, options?, target?, rtype?}], views, title_name}` |
| `POST` | `/api/v1/databases/{id}/query` | Einträge abfragen |
| `POST` | `/api/v1/databases/{id}/rows` | neuer Eintrag `{title, properties?, markdown?}` → `201 {id}` |
| `PATCH` | `/api/v1/rows/{id}` | Eintrag ändern `{title?, properties?, markdown?}` → `{ok, rev, calc?}` |

**Eigenschaften per Name oder ID:** `{"Status": "Erledigt", "Punkte": 3, "Fällig": "2026-12-24", "Wer": ["~abcd…"]}`.
Auswahl-Werte gehen per Optionsname oder Options-ID. Formeln, Rollups, „Erstellt“ und „Bearbeitet“ rechnet Saga –
gesendete Werte dafür werden ignoriert. Personen nur, wenn die Person Zugriff auf die Datenbank hat.

**Abfrage** (`query`), alles optional:

```json
{
  "filters": [{"property": "Status", "op": "is", "value": "Erledigt"}],
  "sorts": [{"property": "Fällig", "dir": "asc"}],
  "view": "<ID einer gespeicherten Ansicht>",
  "q": "Volltext",
  "tz": "Europe/Berlin",
  "limit": 50,
  "offset": 0
}
```

Filter und Sortierung arbeiten genau wie im Web (gleiche Operatoren: `is`, `is_not`, `contains`, `not_contains`,
`gt`, `lt`, `before`, `after`, `is_empty`, `not_empty`, `checked`, `unchecked`, `today`, `this_week`, `overdue`, `me` …). `limit` höchstens
100. Antwort:

```json
{"rows": [{"id": 12, "title": "…", "icon": "", "created": 0, "updated": 0,
           "values": {"status": "done", "pkt": 3}, "properties": {"Status": "Erledigt", "Punkte": 3}}],
 "total": 120, "matched": 7, "offset": 0, "limit": 50, "people": {"~abcd…": {"name": "Anna", "remote": false}}}
```

`values` = Rohwerte nach Eigenschafts-ID, `properties` = lesbar nach Name (Auswahl als Optionsname).

### Aufgaben (`read:tasks` / `write:tasks`)

`GET /api/v1/tasks` (offen; `?done=1` die letzten erledigten), `POST /api/v1/tasks {text, due?, prio?, repeat?}` → `201 {id}`,
`PATCH /api/v1/tasks/{id} {text?, due?, prio?, repeat?, done?}`.

### Kommentare (`read:comments` / `write:comments`)

`GET /api/v1/pages/{id}/comments` → `{threads: [{id, block_id, resolved, comments: [{id, author: {handle, name}, text, created, edited}]}]}`,
`POST /api/v1/pages/{id}/comments {text, thread_id?, block_id?}` → `201 {id, thread_id}` (braucht die Rolle „Kommentieren“).

### Dateien (`read:files` / `write:files`)

- `POST /api/v1/files?page={id}` – `multipart/form-data`, Feld `file` → `201 {url, name, size}`. Mit `page` gehört die Datei
  dem Eigentümer dieser Seite (Rolle „Bearbeiten“ nötig); Tokens mit Seitenbäumen müssen `page` angeben. Die `url`
  (`/files/…`) dann in einen Block oder eine Datei-Eigenschaft eintragen.
- `GET /api/v1/files/{id}` – eigene Dateien; fremde (bzw. bei Tokens mit Seitenbäumen) mit `?page={id}` einer Seite, in
  der die Datei steht.

## Webhooks

Saga meldet Änderungen per `POST` an deine Adresse. Anlegen unter **Einstellungen → API → Webhooks** oder per API
(Bereich `webhooks`):

| Methode | Pfad | |
|---|---|---|
| `GET` | `/api/v1/webhooks` | eigene Webhooks |
| `POST` | `/api/v1/webhooks` | `{url, events, name?}` → `201 {id, secret}` (Geheimnis nur jetzt) |
| `DELETE` | `/api/v1/webhooks/{id}` | löschen |
| `GET` | `/api/v1/webhooks/{id}/deliveries` | Protokoll der letzten 50 Zustellungen |
| `POST` | `/api/v1/webhooks/{id}/test` | Test-Ereignis `ping` |

Ein über einen Token angelegter Webhook sieht nur, was der Token sehen darf (Bereiche und Seitenbäume) und wird
abgeschaltet, wenn der Token widerrufen wird.

**Ereignisse:** `page.created`, `page.updated`, `page.trashed`, `page.restored`, `row.created`, `row.updated`,
`row.deleted`, `row.assigned`, `comment.created`, `task.created`, `task.completed` (und `ping`).

**Nutzlast** (absichtlich dünn – Inhalte holt der Empfänger per API):

```json
{"id": "evt_3f…", "type": "row.updated", "created": 1793140000.5,
 "data": {"note_id": 12, "db_id": 7, "title": "Angebot Müller", "changed": ["status", "title"],
          "actor": {"handle": "~abcd…", "name": "Anna"}}}
```

`changed` = IDs der geänderten Eigenschaften (`title`, `body` für Titel/Inhalt). Speichert jemand in kurzer Folge
(Autosave), fasst Saga die Meldungen derselben Seite zusammen (etwa 10 Sekunden).

**Kopfzeilen:** `Saga-Event`, `Saga-Delivery` (= `id`, zum Erkennen von Doppelten) und

```
Saga-Signature: t=1793140001,v1=<hex HMAC-SHA256(geheimnis, t + "." + body)>
```

Prüfen (Python):

```python
import hmac, hashlib, time
def ok(secret, header, body: bytes):
    p = dict(x.split("=", 1) for x in header.split(","))
    mac = hmac.new(secret.encode(), p["t"].encode() + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, p["v1"]) and abs(time.time() - int(p["t"])) < 300
```

**Zustellung:** Antwort `2xx` = zugestellt. Zeitlimit 5 Sekunden; Weiterleitungen (`3xx`) werden **nicht** verfolgt.
Sonst neuer Versuch nach 1 min, 5 min, 30 min, 2 h, 6 h, 12 h und 24 h, danach „fehlgeschlagen“. Nach 20 Fehlschlägen in
Folge schaltet Saga den Webhook ab und schickt eine Mitteilung. Rechte prüft Saga bei **jeder** Zustellung neu: hat das
Konto die Seite inzwischen nicht mehr, geht nichts raus.

**Erlaubte Ziele:** öffentliche `https://`-Adressen auf Port 443. Interne Adressen (Heimnetz, `localhost`) nur, wenn der
Admin sie unter „Freigegebene interne Hosts“ einträgt; `http://` nur mit Freigabe. Die Adresse wird beim Anlegen und
bei jeder Zustellung geprüft (Schutz gegen DNS-Rebinding).

## MCP-Server (KI-Werkzeuge)

Saga bringt einen MCP-Server mit (Model Context Protocol, Transport „Streamable HTTP“): `POST https://saga.example.org/mcp`.
Nötig ist ein Token mit dem Bereich **`mcp`** und den Datenbereichen, die die KI nutzen soll.

**Werkzeuge** (es erscheinen nur die, die der Token darf): `search_pages`, `get_page`, `get_image`, `create_page`, `append_to_page`,
`update_page_title`, `replace_page_content`, `update_block`, `set_checkbox`, `trash_page`, `archive_page`, `list_databases`,
`get_database_schema`, `query_database`, `create_row`, `update_row`, `list_tasks`, `create_task`, `complete_task`,
`add_comment`. Eigenschaften gehen per Name. Ausgaben sind begrenzt (50 Einträge je
Abfrage, Seiten bis 100.000 Zeichen – darüber gekürzt mit Hinweis). `get_page` nennt die Bilder der Seite im Feld
`images` (`{"file": "<Datei-ID>", "alt": "…"}`); `get_image` (Bereich `read:pages`, Eingabe `file` = Datei-ID oder
`/files/<id>`-Adresse) liefert ein Bild als MCP-Inhalt `{"type": "image", "data": <base64>, "mimeType": …}` –
verkleinert auf höchstens 1568 px, ohne EXIF, nur PNG/JPEG/GIF/WebP. Rechte wie `/files/<id>` im Browser (eigene Datei
oder Datei auf einer sichtbaren Seite, mit `roots` nur innerhalb der Bäume); eigene Dateien ohne Seite nur mit
`read:files` und ohne `roots`.

**Seiteninhalt ändern** (`write:pages`): `get_page` mit `blocks: true` liefert zusätzlich die Gliederung `outline:
[{id, type, text, level?, style?, items?: [{index, text, checked?}], parent?, col?, synced?, in_copy?}]` (flach;
verschachtelte Blöcke mit `parent` = ID des Toggles/der Spalten bzw. des synchronisierten Blocks, `col` = Spalte) –
ohne den Schalter bleibt die Antwort wie bisher. `replace_page_content` ersetzt den ganzen Inhalt (`rev` aus `get_page`
ist Pflicht, Konflikt → „Seite wurde inzwischen geändert (rev …)“), `update_block` und `set_checkbox` arbeiten wie
`/api/v1/pages/{id}/blocks` bzw. `/checkbox` (oben), `trash_page` legt in den Papierkorb, `archive_page` archiviert bzw.
holt zurück (beides nur eigene Seiten). Änderungen kommen wie Web-Änderungen bei offenen Editoren an.
**Annotationen:** Schreibende Werkzeuge haben `readOnlyHint: false`; `replace_page_content`, `update_block` und
`trash_page` zusätzlich `destructiveHint: true` (Clients fragen dann ggf. nach).

**Claude Code:**

```
claude mcp add --transport http saga https://saga.example.org/mcp --header "Authorization: Bearer sga_…"
```

**Konfigurationsdatei** (z. B. `.mcp.json`, Claude Desktop und andere Clients mit HTTP-Transport):

```json
{
  "mcpServers": {
    "saga": {
      "type": "http",
      "url": "https://saga.example.org/mcp",
      "headers": {"Authorization": "Bearer sga_…"}
    }
  }
}
```

Clients, die nur OAuth können, folgen in einer späteren Version.

> **Vorsicht – Prompt-Injection:** Seiten können Text enthalten, den jemand anderes geschrieben hat (geteilte Seiten,
> Kommentare, Importe). Ein KI-Modell könnte solchen Text als Anweisung missverstehen. Saga markiert Seiteninhalte in
> jeder Antwort als Daten, aber das ist kein vollständiger Schutz. Daher: Tokens für KI auf die nötigen **Seitenbäume**
> beschränken und möglichst nur **Lese-Bereiche** geben.

## Admin

Admin-Bereich → Allgemein → „API, Webhooks und MCP“: API an/aus bzw. nur für eine Gruppe, Höchstzahl Tokens je Konto,
höchste Laufzeit, Tokens ohne Ablauf, Ratenbegrenzung, Webhooks an/aus, `http://`-Ziele, MCP an/aus. Je Konto
(Benutzer → Konto) stehen die Zahlen aktiver Tokens und Webhooks; „API-Tokens widerrufen“ widerruft alle Tokens eines
Kontos. „Überall abmelden“ widerruft sie ebenfalls.
