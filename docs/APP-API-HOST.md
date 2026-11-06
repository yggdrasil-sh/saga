# Desktop-Hülle ↔ Web – Brückenvertrag `window.sagaHost` (2.5, P1)

Die Desktop-Apps werden zur Web-Oberfläche in einer dünnen nativen Hülle: **macOS** Swift mit `WKWebView`,
**Windows/Linux** Tauri 2 (WebView2 bzw. WebKitGTK). Alles, was Saga ist, kommt aus dem Web; die Hülle liefert nur, was
ein Browser-Tab nicht kann (Dock-/Tray-Zahl, Widgets, System-Mitteilungen, Systembrowser, Deep-Links, Anmeldung mit
Schlüssel im System-Tresor). Dieses Dokument ist der Vertrag zwischen beiden Seiten.

Web-Seite: `static/host.js` (auf allen Seiten mit `common.js`, direkt danach geladen). Test: `tests/browser_host.js`.
Server-API und `MIN_APP` ändern sich dadurch nicht; ältere Apps sind nicht betroffen.

## 1. Erkennung

Das Web sieht eine Hülle nur, wenn **beides** zutrifft:

1. **User-Agent** enthält `SagaApp/<version>` (ältere Apps: `HlidskjalfApp/<version>`).
   - Mac: `WKWebViewConfiguration.applicationNameForUserAgent = "SagaApp/<version> (macOS)"` – `WKWebView` hat von sich
     aus keinen `Safari/…`-Teil (P0).
   - Tauri: `userAgent` im Fenster-Builder bzw. `tauri.conf.json` (Standard-UA + ` SagaApp/<version> (windows|linux)`).
2. **Nachrichtenweg** vorhanden:
   - Mac: `window.webkit.messageHandlers.hk` (Name `hk`, wie die heutige App in `ios/App/WebView.swift`).
   - Tauri: `window.__TAURI_INTERNALS__.invoke` (Kommando `saga_host`, siehe 3.2).

**Meldung der Fähigkeiten** – die Hülle meldet, was sie kann; das Web nutzt nur Gemeldetes:

- vor dem Laden, als Skript zum Dokumentbeginn (Mac `WKUserScript(…, injectionTime: .atDocumentStart, forMainFrameOnly: true)`,
  Tauri `initialization_script`):
  ```js
  window.sagaHostInfo = { kind: 'mac', version: '3.0.0', caps: ['badge', 'notify', 'widgets', 'openExternal', 'settings', 'token'] };
  ```
- oder später: `sagaHost.receive({ type: 'hello', kind: 'tauri', version: '0.1.0', caps: [...] })`.

| `kind` | Bedeutung |
|---|---|
| `null` | kein Hülle (normaler Browser) – alle Aufrufe sind No-ops bzw. heutiges Web-Verhalten |
| `'mac'` | Swift-Hülle (`WKWebView`) |
| `'tauri'` | Tauri-Hülle (Windows/Linux) |
| `'app'` | alte native App mit eingebetteter Web-Ansicht (UA + `hk`, aber **keine** Meldung) – nur `settings`, `token` wie bisher |

| Fähigkeit (`caps`) | Das Web … |
|---|---|
| `badge` | meldet die Ungelesen-Zahl der Glocke (`unread_groups`) |
| `widgets` | stößt nach Änderungen das Neuladen der Widgets an |
| `notify` | lässt Live-Mitteilungen (SSE `notification`) von der Hülle als System-Mitteilung zeigen – **nur** melden, wenn die Hülle keinen eigenen Push-Kanal für dieses Gerät hat (siehe 5) |
| `openExternal` | gibt fremde Links (`http(s)`, `mailto`) und `window.open` zu fremden Adressen an die Hülle (Systembrowser) |
| `settings` | zeigt im Benutzermenü „App-Einstellungen“ |
| `token` | bietet beim Anlegen eines App-Schlüssels „In dieser App verwenden“ an |

Unbekannte Fähigkeiten verwirft das Web. Neue Fähigkeiten kommen nur mit neuem Eintrag hier dazu.

## 2. Web-API (`HK.host` = `window.sagaHost`)

| Aufruf | Ohne Hülle / ohne Fähigkeit | Mit Hülle |
|---|---|---|
| `kind`, `version`, `caps`, `has(cap)` | `null`, `null`, `[]`, `false` | wie gemeldet |
| `badge(n)` | `false` | `{action:'badge', n}` – nur bei Änderung; die letzte Zahl wird nach einem späten `hello` nachgereicht |
| `widgetsReload()` | `false` | `{action:'widgets.reload'}` – höchstens alle 10 s (erster Aufruf sofort, weitere am Fensterende zusammengefasst) |
| `notify(title, body, url, {id, kind, tag})` | `false` (Mitteilungen weiter über Web-Push/Service Worker) | `{action:'notify', …}` |
| `openExternal(url)` | `window.open(url, '_blank', 'noopener')`, Rückgabe `false` | `{action:'openExternal', url}`; nur `http(s)`/`mailto` fremder Origins |
| `post(msg)` | `false` | rohe Nachricht (für `settings`/`token`, Rückwärtskompatibilität) |
| `on(event, fn)` → `off()` | registriert, wird aber nie gerufen | Ereignisse der Hülle (Abschnitt 4) |
| `receive(evt)` | `false` (außer `hello`, das nur mit Nachrichtenweg greift) | Einstieg für die Hülle |
| `route(link)` | Deep-Link → Web-Pfad oder `null` (reine Funktion, Abschnitt 6) | dto. |
| `open(linkOrPath)` | navigiert (nur eigene Origin) | dto. |

**Wo das Web die Brücke nutzt**

- `static/live.js`: Zähler der Glocke → `badge(n)` (nach dem ersten Zählen, bei jeder Änderung); neue ungelesene
  Benachrichtigung über SSE → `notify(…)` nur mit `notify`; Ereignis `active` → Zähler frisch holen.
- `static/common.js` (`HK.api`): nach jedem erfolgreichen Schreiben auf Aufgaben, Termine, Seiten/Einträge, Papierkorb,
  Vorlage verwenden, Import → `widgetsReload()`. Nicht: Suche, Kommentare, Formel-/Widget-Vorschau, Versionen-Merker,
  Veröffentlichen, Freigaben, Tages-Zusammenfassung, Chat, Konto.
- `static/host.js`: Seiteninhalt geht im Live-Modus über `WS /api/collab` statt `HK.api` – nach Eingaben stößt das Web
  beim Seitenwechsel (`hk-page-open`) und beim Wechsel in den Hintergrund an.
- `static/host.js`: Klick auf fremde Links (ohne `download`, wenn kein anderer Handler `preventDefault` gerufen hat) und
  `window.open` zu fremden Adressen → `openExternal`. Eigene Adressen (`window.open('/chats…', 'hk-chats')`) bleiben bei
  der Hülle (Mac: `createWebViewWith` lädt eigene Adressen im selben Fenster, wie `WebView.swift` heute).
- `static/push.js`: in einer Hülle (`mac`/`tauri`) kein Browser-Abo, Hinweis „In der Saga-App kommen Mitteilungen über die
  App selbst“ – so gibt es keine doppelten Mitteilungen (WKWebView hat ohnehin kein `PushManager`, WebView2 lehnt ab).
- `static/settings.js`: „App-Einstellungen“ (`settings`) und „In dieser App verwenden“ (`token`) über `has()`/`post()`.
- iframes derselben Origin (Peek, Chat-Panel) reichen alles an das Hauptfenster weiter; die Hülle bekommt Nachrichten
  nur aus dem Hauptfenster.

## 3. Nachrichten Web → Hülle

### 3.1 Format

Immer ein JSON-Objekt mit `action` (Text) und `v: 1` (außer den alten `settings`/`token`). Weitere Felder können
dazukommen – unbekannte Felder und Aktionen ignorieren.

| `action` | Felder | Hülle tut |
|---|---|---|
| `ready` | `path` (Pfad+Query+Hash) | Web geladen (Ladeanzeige/Offline-Seite weg, Zustand merken) |
| `badge` | `n` (0…99999) | Dock-Zahl (`NSApp.dockTile.badgeLabel`, 0 = leer) bzw. Tray-/Taskleisten-Overlay |
| `widgets.reload` | – | Mac `WidgetCenter.shared.reloadAllTimelines()`; Windows Markierung für den Widget-Provider (Konzept 4.2) |
| `notify` | `title` (≤120), `body` (≤300), `url` (eigener Pfad, beginnt mit `/`), `tag` (≤64), `kind`, `id` | System-Mitteilung; gleicher `tag` ersetzt; Klick → `notificationClick` (4) bzw. Navigation zu `url`. Mehrere Fenster derselben Hülle senden dieselbe Mitteilung → nach `id` entdoppeln. Ist das Fenster vorn und aktiv, darf die Hülle sie weglassen (Glocke klingelt ohnehin) |
| `openExternal` | `url` (`http(s)`/`mailto`, ≤4000) | Systembrowser (`NSWorkspace.open`, Tauri `opener`); Schema in der Hülle erneut prüfen |
| `settings` (alt) | – | eigene Einstellungen der Hülle (Server, Konto, Updates) |
| `token` (alt) | `token` (`hk_…`) | als Widget-/App-Schlüssel ablegen (Mac: Schlüsselbund der App-Group) |
| `retry` | – | nur von der Offline-Seite: Server-Adresse neu laden (5) |

### 3.2 Transport

- **Mac:** `window.webkit.messageHandlers.hk.postMessage(msg)` → `WKScriptMessageHandler`, `message.body` ist ein
  `[String: Any]`. Annahme **nur**, wenn `message.frameInfo.isMainFrame` und `securityOrigin` (Schema, Host, Port) gleich
  der konfigurierten Server-Origin ist (wie `WebView.swift`). Ausnahme: `retry` von der lokalen Offline-Seite (eigenes
  Schema der Hülle).
- **Tauri:** `window.__TAURI_INTERNALS__.invoke('saga_host', { msg })` → Rust-Kommando
  `#[tauri::command] fn saga_host(webview: tauri::Webview, msg: serde_json::Value)`. Freigabe über eine Capability mit
  `remote: { urls: ["https://<server>/*"] }` nur für die Server-Origin und nur für dieses Kommando; im Kommando die URL
  des aufrufenden Webviews nochmals gegen die Server-Origin prüfen. `withGlobalTauri` ist nicht nötig. **[ungeprüft:
  genaue Capability-Syntax für Remote-Origins in der eingesetzten Tauri-Version]**

## 4. Ereignisse Hülle → Web

Die Hülle ruft `window.sagaHost.receive(evt)` auf (Mac `evaluateJavaScript`, Tauri `webview.eval`), `evt` als Objekt
oder JSON-Text. Nur im Hauptfenster; ohne Nachrichtenweg ignoriert das Web alles (auch `hello`).

| `type` | Felder | Web tut |
|---|---|---|
| `hello` | `kind`, `version`, `caps` | Fähigkeiten setzen/ersetzen, Ereignis `hello`, letzte Badge-Zahl nachreichen |
| `open` | `link` (`saga://…`/`hlidskjalf://…`) **oder** `path` (eigene Origin) | Ereignis `open` (ein Handler kann mit `return false` selbst übernehmen), sonst Abbildung (6) und Navigation; fremde Adressen, `auth`, `pair` → nichts |
| `notificationClick` | `url` | zur eigenen Adresse navigieren |
| `active` | `active` (bool) | Fenster vorn/hinten – Web holt Zähler frisch (Timer liefen gedrosselt, P0) |
| `online` / `offline` | – | nur Ereignis (für spätere Offline-Stufen) |

Navigation: gleiche Seite mit anderem Hash → nur `location.hash` (Seiten-Ansicht reagiert auf `hashchange`);
`/notes?settings=<bereich>` → Einstellungen ohne Neuladen; sonst `location.assign`.

## 5. Mitteilungen, Hintergrund, Offline

- **Kein Web-Push in der Hülle:** `WKWebView` hat kein `PushManager`, WebView2 lehnt die Berechtigung automatisch ab (P0,
  Konzept 5). Das Web fragt in der Hülle keine Browser-Mitteilungen an (`push.js`).
- **Mac:** Mitteilungen wie heute über **APNs** (Abo-Art `apns`, an die Geräte-Anmeldung gebunden; Bereiche, Ruhezeiten,
  Stummschalten prüft der Server). Die Mac-Hülle meldet deshalb **kein** `notify` – sonst käme jede Mitteilung doppelt.
  `badge` und `widgets` meldet sie.
- **Tauri (Windows/Linux):** empfohlen ist die vorhandene **eigene Push-Verbindung** `GET /api/push/stream`
  (WebSocket, `Authorization: Bearer hkd_…`, Abo-Art `stream`, Vertrag in `APP-API.md` „Eigene Push-Verbindung“) nativ in
  der Hülle – läuft ohne Fenster (Tray/Autostart), ohne JS-Timer, mit allen Regeln des Servers. Dann **kein** `notify`
  melden. Nur solange das fehlt, meldet die Hülle `notify`: das Web zeigt neue Benachrichtigungen aus dem Live-Kanal und
  wendet dabei die Mitteilungs-Einstellungen an (`GET /api/push`: Server an, Bereiche, Ruhezeiten, „Namen zeigen“; 5 min
  gemerkt). Das läuft nur, solange das Fenster lebt.
- **Gedrosselte Timer:** Ein verdecktes oder unsichtbares `WKWebView` drosselt JS-Timer bzw. setzt sie aus (P0). Nichts,
  was im Hintergrund zuverlässig laufen muss (Mitteilungen, Klingeln, Widget-Takt), darf am Web-JS hängen – das macht die
  Hülle nativ. Nach dem Zurückkommen `active` senden.
- **Offline O0:** `static/offline.html` ist eine eigenständige Seite (Stil und Skript inline, per Hash in der CSP
  erlaubt, keine weiteren Dateien; Texte de/en/es/ru/th nach Browsersprache oder `?lang=xx`; Hell/Dunkel nach System
  bzw. `localStorage['hk-theme']`). Die Hülle legt eine Kopie ins App-Paket und zeigt sie, wenn die Server-Adresse nicht
  lädt (Mac `didFailProvisionalNavigation` mit Netzfehler; Tauri `on_page_load`/Navigationsfehler). „Erneut versuchen“
  und das Ereignis `online` senden `{action:'retry'}`; ohne Hülle lädt die Seite neu.
  Am Mac per `WKURLSchemeHandler` bzw. `loadHTMLString(_, baseURL: nil)` – **kein** Service Worker auf eigenem Schema (P0).
- **Service Worker (`/sw.js`)**: hält die Offline-Seite im Cache und zeigt sie, wenn eine Seiten-Navigation (GET, eigene
  Origin) gar keine Antwort bekommt; HTTP-Fehler des Servers bleiben, wie sie sind. Gilt nur, wo der Service Worker
  registriert ist (heute: nach Einschalten von Web-Push oder dem ersten Video im Chat; in der Hülle allgemein erst mit O1).
  In Chromium gehen Navigationen dafür über den Service Worker (Static Routing: Navigationen → fetch-Handler,
  `/_m/` → fetch-Handler, Rest direkt ans Netz).

## 6. Deep-Links → Web-Routen

Gilt für `saga://` und `hlidskjalf://` (gleich behandelt). `sagaHost.route(link)` liefert den Pfad; die Hülle kann beim
Kaltstart dieselbe Tabelle nativ anwenden (Server-Adresse + Pfad laden) oder erst `/notes` laden und dann `open` senden.

| Link | Herkunft heute | Web-Route |
|---|---|---|
| `saga://today`, `saga://` | Widgets (Heute, Rückfall), Spotlight-Suchanbieter (Linux), Bildschirmfotos | `/` |
| `saga://page/<id>` | Widgets (Seite, Seitenkarte), AppIntent „Seite öffnen“, Spotlight, GNOME-Suche | `/notes#<id>` |
| `saga://tasks` | Today-Widget (Aufgaben), AppIntent „Aufgaben öffnen“ | `/notes?v=tasks` |
| `saga://events` | Today-Widget (nächster Termin) | `/notes?v=events` |
| `saga://new-task[?src=<id>]` | Widget-/Dock-Schnellaktion | `/notes?v=tasks&new=task` – Aufgaben-Ansicht, Eingabe „Neue Aufgabe“ fokussiert (**neu**). `src` (Aufgabenquelle) wertet das Web noch nicht aus → Inbox |
| `saga://new-page` | (neu, Menüleiste/Tray) | `/notes?new=blank` |
| `saga://new?parent=<id>` | Seiten-Widget „+“ | `/notes#<id>` (das Web legt per Link nichts an) |
| `saga://new?db=<id>&view=<v>&k=<geheim>` | Datenbank-Widget „+“ | `/notes#<id>`. Anlegen macht die Hülle **nativ** nach Prüfung von `k` (`AppConfig.linkSecret`, wie `RootView.createFromLink`) über die API mit `hk_…`, dann `open` mit `saga://page/<neu>` |
| `saga://settings[/<bereich>]` | (neu, ⌘, bzw. Tray) | `/notes?settings=<bereich>` (`account`, `security`, `push`/`notify`, `pages`, `calendar`, `bookmarks`, `immich`, `nextcloud`, `apps`, `api`, `backup`, `help`, `lang`); ohne gültigen Bereich `account` |
| `saga://chats` | Linux | `/chats` |
| `saga://auth?code=…` | Rückkehr der Browser-Anmeldung | **nur Hülle** (`/app/exchange`) → `null` |
| `saga://pair?u=…&c=…` | QR-Kopplung | **nur Hülle**, immer mit Rückfrage → `null` |
| sonst | – | `null` (nichts tun) |

Vorhandene Web-Adressen dazu: `/notes#<id>` (Seite), `/notes?v=tasks|events`, `/notes?new=blank`, `?settings=<bereich>`
(auf `/`, `/notes`, `/chats`), `/chats#…`.

## 7. Anmeldung in der Hülle (Server unverändert)

1. **Erstanmeldung** im Systembrowser mit PKCE: `GET /app/login?challenge=<S256>&scheme=saga` → Anmeldung/SSO →
   Rückfrage → `saga://auth?code=…` → `POST /app/exchange {code, verifier, device: "<Gerätename>", app_key: true}`
   (`Content-Type: application/json`) → `{device_token: "hkd_…", app_key: "hk_…"}`. `hkd_` in den System-Tresor (Mac
   Schlüsselbund, Windows Credential Locker, Linux libsecret), `hk_` für Widgets (Mac: Schlüsselbund der App-Group).
2. **Jeder Start / abgelaufene Sitzung:** `POST /app/resume {device_token, app_key}` – die Antwort setzt das
   Sitzungs-Cookie. Es muss im **Cookie-Speicher des WebViews** landen:
   - Mac: Anfrage mit `URLSession`, Cookie aus der Antwort per `WKWebsiteDataStore.default().httpCookieStore.setCookie`
     übernehmen, dann die Server-Adresse laden; oder die Anfrage per `evaluateJavaScript`/`callAsyncJavaScript` als
     `fetch('/app/resume', {method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, …})`
     auf einer Seite der eigenen Origin (Schlüssel dann kurz im Seiten-JS).
   - Tauri: Cookie über die Cookie-Schnittstelle des WebViews setzen (WebView2 `CookieManager`, WebKitGTK
     `CookieManager`) **[ungeprüft: Tauri-2-API zum Setzen von Cookies]**, sonst der `fetch`-Weg wie beim Mac.
   - `401` → Gerät abgemeldet: neu anmelden (1). `403` → kein Zugriff mehr.
3. **Abmelden:** Web schickt `POST /logout`; die Hülle löscht zusätzlich `hkd_`/`hk_` und alle Website-Daten
   (`WKWebsiteDataStore.removeData`, inkl. Service-Worker-Registrierungen und IndexedDB).
4. **Kopplung per QR** (`saga://pair`) wie in den Apps (immer mit Rückfrage, Zielserver zeigen).

Aufgefallen, nicht geändert: `/app/resume` setzt die Sitzung nur per Cookie – ein WebView mit getrenntem Cookie-Speicher
braucht den Weg aus 2.; ein serverseitiger Einmal-Link („Resume-URL“) würde das vereinfachen, ist aber nicht nötig.

## 8. Sicherheitsregeln (Kurzfassung)

- Hülle nimmt Nachrichten nur aus dem **Hauptfenster** der **konfigurierten Server-Origin** an (Schema, Host, Port);
  das Web sendet nur aus dem Hauptfenster (iframes reichen an `window.top` derselben Origin weiter).
- Alles, was die Hülle in das Web gibt (`open`, `notificationClick`), sind **Pfade der eigenen Origin** oder
  `saga://`-Links; das Web verwirft fremde Adressen.
- `openExternal`: Web schickt nur `http(s)`/`mailto` fremder Origins; die Hülle prüft das Schema erneut (keine
  `file:`, `javascript:`, App-Schemata wie `saga://pair`).
- Navigation im WebView auf fremde Origins: Hülle öffnet sie im Systembrowser (Anmelde-Umleitungen zu OIDC-Anbietern
  ausgenommen, wie `WebView.swift`).
- Keine Geheimnisse im Web-Speicher: `hkd_`/`hk_` bleiben im System-Tresor.
