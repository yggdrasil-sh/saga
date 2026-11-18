# Saga-Siegel

Mitgelieferte Seitensymbole (2.5): 36 Motive in drei Farben (Weinrot, Smaragd, Elfenbein) – runde Siegel mit goldenem
Rand. Herkunft: Saga-Projekt, eigens fuer Saga gestaltet. Lizenz wie Saga (AGPL-3.0, siehe `LICENSE` im Hauptverzeichnis).

| Datei | Inhalt |
|---|---|
| `<farbe>/<motiv>.svg` | Vorlage (viewBox 64), nur Formen und Verlaeufe; ids je Datei mit Praefix `s<f>-<motiv>-` (eindeutig, auch wenn mehrere Siegel inline im DOM stehen) |
| `<farbe>/<motiv>.png` | 256 px, aus dem SVG mit Chromium gerendert – fuer Apps ohne SVG-Anzeige; 64/128 px verkleinert der Server daraus |

Als Seitensymbol gilt genau der Pfad `/siegel/<farbe>/<motiv>.svg`; `?w=<px>` liefert das PNG (siehe `docs/APP-API.md`).
Farben, Motive und Namen: `saga/siegel.py`. Pflege: `tools/siegel.py` (`import`, `png`, `check`).

Motive: yggdrasil, rabe, drachenboot, horn, rune, buch, feder, schriftrolle, kerze, laterne, mond, sonne, nordlicht, berg,
wald, welle, kompass, karte, schluessel, schild, hammer, axt, krone, kristall, trank, feuer, stern, herz, brief, sanduhr,
zahnrad, truhe, server, terminal, katze, teetasse.
