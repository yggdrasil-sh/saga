# Seitenbaum live – Vertrag für die App-Teams (2.2)

Neues Live-Ereignis `notes.tree` über `GET /api/events` (Server-Sent Events, Anmeldung wie gewohnt: Sitzung oder
Geräte-Schlüssel `Authorization: Bearer hkd_…`). Es meldet, **dass** sich der Seitenbaum geändert hat – nur IDs, keine
Titel oder Inhalte. Damit kann eine App mit verbundenem Live-Kanal ihren Abgleich-Takt von 60 s auf 5 min senken.
Rückwärtskompatibel: ältere Apps ignorieren unbekannte Ereignisse; `MIN_APP` ändert sich nicht.

## Ereignis

```
event: notes.tree
id: <boot>-<n>
data: {"ids":[812,815],"op":"updated","ops":["updated"],"full":false}
```

| Feld | Bedeutung |
|---|---|
| `ids` | Betroffene Seiten (auch Datenbank-Einträge). Leer, wenn `full` gesetzt ist. |
| `op` | `created` · `updated` (Titel, Symbol, Titelbild, Tags, Favorit, Vorlage, Sperre) · `moved` (verschoben oder umsortiert) · `trashed` · `restored` (aus Papierkorb oder Archiv) · `archived` · `deleted` (endgültig, auch durch die 30-Tage-Frist) · `shared` (Freigabe dieser Seite geändert – Symbol „geteilt“) · `mixed` (mehrere Arten gesammelt, siehe `ops`) |
| `ops` | Alle gesammelten Arten (sortiert). |
| `full` | `true`: zu viele Seiten (> 500) – den ganzen Baum neu laden. |

Unbekannte `op`-Werte wie eine Änderung behandeln (neu laden). Weitere Felder können dazukommen.

## Wer bekommt es

- **Alle Live-Verbindungen des Eigentümers** – außer der Stelle, die die Änderung selbst ausgelöst hat (die kennt das
  Ergebnis ihrer Anfrage schon). Kommt ein Bündel aus mehreren Quellen, geht es an alle.
  - **Apps:** ausgeschlossen wird das Gerät (Geräte-Schlüssel bzw. App-Sitzung).
  - **Web:** ausgeschlossen wird nur der **Tab**, der die Änderung ausgelöst hat. Jeder Tab erzeugt beim Laden eine
    zufällige Kennung und schickt sie bei Anfragen als Kopf `X-Saga-Tab` und beim Live-Kanal als `?tab=` mit
    (8–40 Zeichen `A–Z a–z 0–9 _ -`). Andere Tabs derselben Browser-Sitzung bekommen das Ereignis. Anfragen ohne
    Tab-Kennung (ältere Seitenstände, Clients ohne den Kopf) schließen wie bisher die ganze Browser-Sitzung aus.
- **Personen mit Freigabe** auf einer betroffenen Seite oder einer Seite darüber (Person oder Gruppe) – nur die IDs, die
  sie jetzt sehen dürfen. Bei `trashed`, `archived`, `deleted` die IDs, die ihnen bis eben geteilt waren.
- **Verschieben** (`moved`, auch `PATCH parent_id` und Wiederherstellen auf die oberste Ebene): Die Kette wird vor
  **und** nach dem Verschieben bestimmt. Wer nur über die alte Kette Zugriff hatte (Seite aus einem geteilten Teilbaum
  heraus verschoben), bekommt die IDs wie bei `trashed`; wer über die neue Kette Zugriff bekommt, die IDs wie bei
  `created`. Beide bekommen zusätzlich `shared.changed`.
- **Gruppen-Mitgliedschaft geändert** (Admin-Bereich, LDAP/OIDC-Abgleich bei der Anmeldung, Gruppe gelöscht) und
  mindestens eine betroffene Gruppe ist Ziel einer Freigabe: das Konto bekommt `shared.changed` und
  `notes.tree` mit `{"ids": [], "op": "shared", "full": true}` – ganzen Baum und „Mit mir geteilt“ neu laden.
  Entprellt: höchstens eines je 2 s und Konto.
- Niemand sonst.

## Bündelung

Je Konto höchstens **ein Ereignis je 2 s**: Das erste geht sofort raus, alles in den folgenden 2 s wird gesammelt und
danach als ein Ereignis (Vereinigung der IDs) geschickt.

## Nachholen (`Last-Event-ID`)

Verpasste Ereignisse werden wie alle anderen nachgeholt (10 Minuten); bei `reset` alles neu laden. Die Rechte werden
dabei **neu** geprüft: Enthält ein `notes.tree` IDs fremder Seiten und ist eine davon jetzt nicht mehr sichtbar
(Freigabe entzogen, verschoben, Gruppe verlassen, gelöscht), kommt das Ereignis als `{"ids": [], "full": true}` mit
derselben `id` – ein Konto ohne Zugriff erfährt so keine IDs. IDs eigener Seiten bleiben unverändert.

## Empfohlenes Verhalten der Apps

1. Live-Kanal verbunden (`hello` erhalten): Abgleich-Takt **5 min** statt 60 s. Ohne Live-Kanal (Fehler, `bye`,
   Hintergrund) wie bisher 60 s.
2. `notes.tree` mit IDs: Seitenbaum gezielt nachladen – `GET /api/notes?since=…` (eigene Seiten, siehe „Seitenbaum
   abgleichen“; ältere Server: `GET /api/notes`) bzw. `GET /api/shared` (Seiten anderer), entprellt (z. B. 300 ms).
   Betrifft eine ID die gerade offene Seite und ist `op` `trashed`, `archived` oder `deleted`, kommt zusätzlich wie
   bisher `note.changed` mit `gone: true`.
3. `full: true`, `reset` oder ein Wiederaufbau ohne Nachholen: alles neu laden.
4. Eigene Aktionen weiter wie bisher selbst nachziehen (das Ereignis kommt nicht an das auslösende Gerät zurück).
5. `shared.changed`: „Mit mir geteilt“ (`GET /api/shared`) neu laden – kommt jetzt auch beim Verschieben aus einem bzw.
   in einen geteilten Teilbaum und nach einem Gruppenwechsel.

Die Weboberfläche nutzt das Ereignis bereits: Andere Tabs (auch derselben Browser-Sitzung) und Geräte zeigen neue,
umbenannte, verschobene oder gelöschte Seiten ohne Neuladen.

## Seitenbaum abgleichen (`GET /api/notes?since=…`)

Statt nach jedem `notes.tree` (und im Sicherheitsnetz-Takt) den ganzen Baum zu laden, holt eine App nur, was sich seit
ihrem letzten Stand geändert hat. Alles opt-in: **`GET /api/notes` ohne Parameter bleibt byte-gleich** (Liste wie
bisher); ältere Server ignorieren die Parameter und liefern die Liste – daran erkennt die App, dass es keinen Abgleich
gibt (Antwort ist ein Array statt eines Objekts). `MIN_APP` ändert sich nicht.

| Parameter | Wirkung |
|---|---|
| `delta=1` | Ganzer Baum als Objekt mit Stand: `{"cursor": "…", "full": true, "notes": […], "removed": []}` |
| `since=<cursor>` | Nur Geändertes seit `cursor`: `{"cursor": "…", "full": false, "notes": [neu/geändert], "removed": [ids]}` |
| `rows=0` | ohne Datenbank-Einträge (Seiten, deren Elternseite eine Datenbank ist); `rows=only`: nur diese |
| `fields=a,b,…` | nur diese Felder je Seite (`id` immer; unbekannte Namen werden ignoriert) |

Die Parameter lassen sich kombinieren (`?since=…&rows=0`); `rows`/`fields` ohne `delta`/`since` liefern eine Liste wie
bisher, nur gefiltert. Je Seite dieselben Felder wie bisher (`id, title, icon, parent_id, updated, tags, pinned,
template, kind, archived, locked, shared, unread`), in derselben Reihenfolge wie die Liste (Geschwister im Baum stehen
in dieser Reihenfolge; bei Gleichstand entscheidet jetzt die `id`). Seiten in einer `since`-Antwort tragen zusätzlich
**`after`**: die ID der Seite, hinter der sie in der ganzen Liste steht (`null` = ganz vorn); `notes` selbst ist in
Listen-Reihenfolge.

**Einarbeiten:** `full: true` → Liste ersetzen. `full: false` →
1. alle IDs aus `removed` **und** alle IDs aus `notes` aus der Liste nehmen (unbekannte IDs einfach ignorieren),
2. die Seiten aus `notes` der Reihe nach einsetzen, jede direkt hinter ihr `after` (bzw. an den Anfang) – steht
   `after` selbst in `notes`, ist es zu diesem Zeitpunkt schon eingesetzt,
3. `after` weglassen (gehört nicht zur Seite), den neuen `cursor` merken (undurchsichtig – nicht zerlegen).

Ergebnis: genau die Liste, die `GET /api/notes?delta=1` jetzt liefern würde. Kennt die App ein `after` nicht (Stand
weicht ab), die Seite ans Ende setzen und beim nächsten Mal `delta=1` holen. `removed` meldet: in den Papierkorb gelegt,
endgültig gelöscht, mit `rows=0` auch „ist jetzt ein Datenbank-Eintrag“. Archivierte Seiten bleiben wie bisher in der
Liste (`archived: true`).

**`full: true` statt Abgleich** (immer korrekt, nur größer), wenn der Stand nicht passt: unbekannt/kaputt, älter als
30 Tage nach dem Aufräumen gelöschter Seiten, nach einem Neustart des Servers (z. B. nach dem Zurückspielen eines
Backups), bei sehr vielen Änderungen (> 5.000 Seiten) oder auf Postgres < 13. `cursor: null` → beim nächsten Mal
wieder `delta=1`.

**Rechte:** Der Baum enthält nur **eigene** Seiten des Kontos – der Abgleich auch. Seiten anderer („Mit mir geteilt“)
kommen weiter ganz aus `GET /api/shared` (nach `shared.changed`). Eine Freigabe hinzufügen/entfernen ändert das Feld
`shared` der eigenen Seite und kommt im Abgleich mit; ebenso neue Kommentare anderer und „gelesen“ (`unread`).

Beispiel:

```
GET /api/notes?delta=1
→ {"cursor":"3f9c0a1b2c4d.88412","full":true,"removed":[],
   "notes":[{"id":812,"title":"Projekt","icon":"","parent_id":null,"updated":1760860000.1,"tags":[],"pinned":false,
             "template":false,"kind":"page","archived":false,"locked":false,"shared":true,"unread":false}, …]}
GET /api/notes?since=3f9c0a1b2c4d.88412
→ {"cursor":"3f9c0a1b2c4d.88430","full":false,"notes":[{"id":815,"title":"Neu",…,"after":812}],"removed":[790]}
```

Empfehlung: beim Start `delta=1` (oder Zwischenspeicher + `since`), nach `notes.tree` und im Takt `since`. Ein Stand
ist je Konto gültig, nicht je Gerät – nach Abmelden/Kontowechsel verwerfen. Geteilte Seiten in derselben Antwort gibt
es nicht; der Datenbank-Inhalt (Eigenschaften, Zeilen einer Ansicht) kommt weiter aus `/rows`.

## Kompression und ETag

Gepackte Antworten (`Content-Encoding: gzip`) tragen ein **schwaches** ETag (`W/"…"`), ungepackte das starke. Für
`If-None-Match` sind beide gleichwertig (304), auch gemischt.
