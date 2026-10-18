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

- **Alle Live-Verbindungen des Eigentümers** – außer der Sitzung bzw. dem Gerät, das die Änderung selbst ausgelöst hat
  (die kennt das Ergebnis ihrer Anfrage schon). Kommt ein Bündel aus mehreren Quellen, geht es an alle.
- **Personen mit Freigabe** auf einer betroffenen Seite oder einer Seite darüber (Person oder Gruppe) – nur die IDs, die
  sie jetzt sehen dürfen. Bei `trashed`, `archived`, `deleted` die IDs, die ihnen bis eben geteilt waren.
- Niemand sonst.

## Bündelung

Je Konto höchstens **ein Ereignis je 2 s**: Das erste geht sofort raus, alles in den folgenden 2 s wird gesammelt und
danach als ein Ereignis (Vereinigung der IDs) geschickt. Mit `Last-Event-ID` werden verpasste Ereignisse wie alle
anderen nachgeholt (10 Minuten); bei `reset` alles neu laden.

## Empfohlenes Verhalten der Apps

1. Live-Kanal verbunden (`hello` erhalten): Abgleich-Takt **5 min** statt 60 s. Ohne Live-Kanal (Fehler, `bye`,
   Hintergrund) wie bisher 60 s.
2. `notes.tree` mit IDs: Seitenbaum gezielt nachladen – heute `GET /api/notes` (eigene Seiten) bzw. `GET /api/shared`
   (Seiten anderer), entprellt (z. B. 300 ms). Betrifft eine ID die gerade offene Seite und ist `op` `trashed`,
   `archived` oder `deleted`, kommt zusätzlich wie bisher `note.changed` mit `gone: true`.
3. `full: true`, `reset` oder ein Wiederaufbau ohne Nachholen: alles neu laden.
4. Eigene Aktionen weiter wie bisher selbst nachziehen (das Ereignis kommt nicht an die auslösende Sitzung zurück).

Die Weboberfläche nutzt das Ereignis bereits: Andere Tabs und Geräte zeigen neue, umbenannte, verschobene oder
gelöschte Seiten ohne Neuladen.
