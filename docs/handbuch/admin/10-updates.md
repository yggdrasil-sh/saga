# A10 Updates und Release-Kanal

**Gilt für:** Web (Admin-Bereich) · Server

[← Übersicht](../README.md)

---

## Typische Aufgaben

- **Eine neue Version einspielen.** Frieda sieht im Admin-Bereich „Saga 2.5.1 ist erschienen“, liest „Was ist neu?“
  und aktualisiert am Samstagmorgen.
- **Neues früh ausprobieren.** Anna lässt ihr Saga auf dem Standard `2`: Bis 3.0 bringt jedes Update den neuesten
  Stand von `main`, also neue Funktionen, sobald sie fertig sind.
- **Ruhe im Verein.** Frieda nagelt den Vereinsserver auf `2.5` fest – dann kommen nur Releases 2.5.x, keine
  Testversionen dazwischen.
- **Apps auf Stand halten.** Nach einem Server-Update erinnert Frieda die Mitglieder, auch die Apps zu aktualisieren.

## Wofür ist das?

Saga entwickelt sich weiter. Neue Versionen bringen Fehlerbehebungen, neue Funktionen und Sicherheitsupdates. Der
Admin-Bereich sagt dir Bescheid, wenn es eine neue Version gibt. Das Aktualisieren selbst machst du auf dem Server –
mit zwei Befehlen.

Die Versionsnummern folgen dem Schema **Major.Minor.Patch**, zum Beispiel 2.5.1:

- **Patch** (2.5.**1**) – nur Fehlerbehebungen.
- **Minor** (2.**5**.0) – neue Funktionen.
- **Major** (**3**.0.0) – kann Handarbeit brauchen. Kommt nur, wenn du es ausdrücklich einstellst.

> **Hinweis:** Wie du Saga aktualisierst, sicherst und zurückspielst, steht ausführlich in der Anleitung,
> [Abschnitt 11 „Sicherung, Wiederherstellung, Updates“](../../ANLEITUNG.md#11-sicherung-wiederherstellung-updates),
> Unterabschnitt „Updates“. Hier geht es um den Hinweis im Admin-Bereich und die Kanäle.

---

## Schritt für Schritt

### 1. Der Update-Hinweis

Gibt es eine neue Version, erscheint oben im Admin-Bereich ein Hinweis: „Saga 2.5.1 ist erschienen (installiert:
2.5.0)“.

![Update-Hinweis im Admin-Bereich: neue Version verfügbar, mit „Was ist neu?“ und dem Befehl zum Aktualisieren](../bilder/a10-01-update-hinweis.png)

Was die Nummern bedeuten:

- ① Der Hinweis mit **Was ist neu?** und dem Befehl zum Aktualisieren.

1. Klick auf **Was ist neu?**. Du landest bei den Änderungen dieser Version.
2. Lies vor allem, ob etwas für dich als Admin zu tun ist.
3. Aktualisiere wie unter **Aktualisieren:** angegeben (Abschnitt 2).

Die installierte Version steht immer unten auf jeder Seite des Admin-Bereichs.

Den Hinweis schaltest du unter **Allgemein → Updates → Auf neue Versionen hinweisen** ein oder aus. Saga fragt dafür
höchstens zweimal am Tag bei GitHub nach der neuesten Versionsnummer – sonst wird nichts übertragen.

### 2. Aktualisieren

Auf dem Server, im Ordner von Saga:

```sh
docker compose pull
docker compose up -d
```

Ändert ein Update die Datenbank, legt Saga **vorher automatisch ein Backup** an. Was genau passiert, wie du ein
zusätzliches Backup machst und worauf du bei größeren Sprüngen achten musst, steht in der Anleitung, Abschnitt 11,
„Updates“.

> **Tipp:** Aktualisiere, wenn gerade niemand telefoniert – zum Beispiel morgens am Wochenende. Saga ist nach dem
> Neustart in wenigen Sekunden wieder da.

**Von 2.3 oder 2.4 auf 2.5:** Dafür gibt es eine eigene [Upgrade-Anleitung](../../UPGRADE-2.5.md). Das Wichtigste:
vorher ein Backup, danach gibt es keinen Weg zurück außer über dieses Backup; hinter einem Reverse Proxy brauchst du
nur eine Domain, Port 443 und „Websockets Support“; danach im Admin-Bereich Gjallarhorn, „Alle Anrufe über Knoten“,
moderne Chats und den Seiten-Editor prüfen.

### 3. Der Release-Kanal

Welche Versionen dein Server bekommt, bestimmt die Einstellung `SAGA_VERSION` in der `.env`:

| Wert | Bedeutung |
|---|---|
| `2` (Standard) | jeder neue Stand von `main` – Testversionen und Releases |
| `2.5` | nur Releases 2.5.x |
| `2.5.1` | genau dieses Release |
| `testing`, `rc` | derzeit gleich wie `2` – besser `2` setzen |

**Bis Saga 3.0 gibt es nur einen Kanal.** Jeder Stand von `main` ist eine Beta: Zwischen zwei Releases zeigt der
Admin-Bereich unten eine Version wie „2.6.0-rc.7“ mit dem Hinweis **Release Candidate** (Testversion). Eine
Testversion kann Fehler haben – mach regelmäßig Backups. Nach 3.0 kommt eine stabile Linie.

**Festnageln** (z. B. `2.5`) geht nur auf eine Version, die die Datenbank deines Servers schon kennt. Hat eine neuere
Version die Datenbank umgebaut, startet eine ältere nicht mehr – dann hilft nur das Backup von vor dem Update. Also
am besten festnageln, bevor du das nächste Update ziehst.

Alle Einzelheiten: Anleitung, Abschnitt 11, „Updates“ (Absätze „Ein Kanal bis 3.0“ und „Festnageln“). Willst du
Saga automatisch aktuell halten, richtet das Skript `tools/saga-testing-einrichten.sh` einen Timer ein, der alle
15 Minuten nach einem neuen Stand schaut (siehe [Upgrade-Anleitung 2.5](../../UPGRADE-2.5.md), Abschnitt 2).

### 4. Apps und Versionen

Die Apps haben eigene Versionsnummern. Sie zeigen in ihren Einstellungen ihre eigene Version und die des Servers.

- Neue Funktionen des Servers brauchen oft auch eine neue App-Version. Ältere Apps laufen weiter, nur ohne die neuen
  Funktionen.
- Ist eine App für den Server zu alt, weist sie darauf hin („Bitte die App aktualisieren“).
- Bei manchen Umstellungen (zum Beispiel den modernen Chats, siehe [A05](05-allgemein.md)) wartet der Server eine
  Weile, bis alle aktiven Geräte die neue App haben.

Welche App-Version zu welcher Server-Version passt, steht bei den Updates in der Anleitung (Punkt „Apps“) und bei den
[Saga-Apps auf GitHub](https://github.com/yggdrasil-sh/saga-apps/releases).

---

## Beispiel aus dem Alltag

**Frieda spielt 2.5.1 ein.** Am Montag sieht Frieda im Admin-Bereich: „Saga 2.5.1 ist erschienen (installiert:
2.5.0)“.

1. Sie klickt auf **Was ist neu?**: nur Fehlerbehebungen, nichts für Admins zu tun.
2. Am Samstagmorgen meldet sie sich per SSH am Server an.
3. Im Saga-Ordner führt sie `docker compose pull` und `docker compose up -d` aus.
4. Nach ein paar Sekunden öffnet sie den Admin-Bereich: unten steht 2.5.1, der Hinweis ist weg.
5. Im Vereinschat schreibt sie: „Saga ist aktualisiert. Wer die App hat: bitte auch die App aktualisieren.“

---

## Häufige Fragen

**Muss ich jedes Update sofort einspielen?**
Nein, aber bald – besonders, wenn im Changelog „Sicherheit“ steht.

**Kann ich zu einer älteren Version zurück?**
Hat das Update die Datenbank umgebaut, geht das nur über das Backup von vor dem Update. Wie, steht in der Anleitung,
Abschnitt 11.

**Der Hinweis kommt nicht, obwohl es eine neue Version gibt.**
Prüfe, ob **Auf neue Versionen hinweisen** unter Allgemein an ist und ob der Server GitHub erreichen kann. Der
Hinweis meldet nur Releases (z. B. 2.6.0) – Testversionen dazwischen kommen ohne Hinweis beim nächsten Update.

**Wann kommt Saga 3?**
Eine neue Major-Version kommt nur, wenn du `SAGA_VERSION` ausdrücklich auf `3` stellst – vorher bleibt dein Server bei
2.x (mit `testing`, `rc` oder `latest` käme sie schon vorher).

---

## Auf dem Handy und in den Apps

Den Server aktualisierst du nur auf dem Server. Die Apps aktualisierst du auf dem jeweiligen Gerät (siehe
Benutzer-Handbuch, [B17 Apps](../benutzer/17-apps.md)).

---

[← Übersicht](../README.md)
