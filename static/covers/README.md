# Titelbild-Galerie

Standard-Galerie fuer Seiten-Titelbilder: gemeinfreie Kunstwerke (Tabelle unten) und KI-generierte Bilder der
Saga-Elfe (IDs `saga-*`, Abschnitt am Ende). Die Kunstwerke sind gemeinfreie Werke aus der
[Open-Access-Sammlung des Metropolitan Museum of Art](https://www.metmuseum.org/about-the-met/policies-and-documents/open-access)
und dort unter [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) freigegeben.
Fuer Saga wurden sie auf 1600 px Breite verkleinert (JPEG).

`covers.json` beschreibt jedes Bild (`id`, Titel de/en, Kuenstler, Datierung, Objektnummer und Link
der Met, Gruppe, `pos` = empfohlene senkrechte Bildposition 0–100). Saga liefert die Bilder unter
`/covers/<id>.jpg` aus; als Titelbild einer Seite gilt genau dieser Pfad.

| Gruppe | ID | Werk | Kuenstler | Datierung | Met |
|---|---|---|---|---|---|
| Nordisch | `balke-nordkap` | Das Nordkap im Mondlicht | Peder Balke | 1848 | [441379](https://www.metmuseum.org/art/collection/search/441379) |
| Nordisch | `dahl-wasserfall` | Zwei Männer vor einem Wasserfall bei Sonnenuntergang | Johan Christian Dahl | 1823 | [439344](https://www.metmuseum.org/art/collection/search/439344) |
| Nordisch | `dahl-kopenhagen` | Kopenhagener Hafen im Mondlicht | Johan Christian Dahl | 1846 | [439343](https://www.metmuseum.org/art/collection/search/439343) |
| Nordisch | `dahl-vesuv` | Ausbruch des Vesuvs | Johan Christian Dahl | 1824 | [438159](https://www.metmuseum.org/art/collection/search/438159) |
| Nordisch | `roerbye-zitadelle` | Blick von den Wällen der Zitadelle in Kopenhagen bei Mondlicht | Martinus Rørbye | 1839 | [438952](https://www.metmuseum.org/art/collection/search/438952) |
| Nordisch | `thaulow-picquigny` | Picquigny | Frits Thaulow | 1899 | [852668](https://www.metmuseum.org/art/collection/search/852668) |
| Romantik | `friedrich-mond` | Zwei Männer betrachten den Mond | Caspar David Friedrich | ca. 1825–30 | [438417](https://www.metmuseum.org/art/collection/search/438417) |
| Romantik | `turner-walfaenger` | Walfänger | J. M. W. Turner | ca. 1845 | [437854](https://www.metmuseum.org/art/collection/search/437854) |
| Romantik | `carus-milkel` | Schloss Milkel im Mondlicht | Carl Gustav Carus | ca. 1833–35 | [788905](https://www.metmuseum.org/art/collection/search/788905) |
| Romantik | `carus-oybin` | Gotische Fenster in der Klosterruine Oybin | Carl Gustav Carus | ca. 1828 | [439122](https://www.metmuseum.org/art/collection/search/439122) |
| Holzschnitt | `hokusai-welle` | Die große Welle vor Kanagawa | Katsushika Hokusai | ca. 1830–32 | [45434](https://www.metmuseum.org/art/collection/search/45434) |
| Holzschnitt | `hokusai-roter-fuji` | Roter Fuji | Katsushika Hokusai | ca. 1830–32 | [36490](https://www.metmuseum.org/art/collection/search/36490) |
| Holzschnitt | `hokusai-gewitter` | Gewitter unterhalb des Fuji | Katsushika Hokusai | ca. 1830–32 | [36492](https://www.metmuseum.org/art/collection/search/36492) |
| Holzschnitt | `hiroshige-kanbara` | Abendschnee in Kanbara | Utagawa Hiroshige | ca. 1833–34 | [56915](https://www.metmuseum.org/art/collection/search/56915) |
| Holzschnitt | `hiroshige-hira` | Abendschnee am Hira | Utagawa Hiroshige | ca. 1835 | [36533](https://www.metmuseum.org/art/collection/search/36533) |
| Holzschnitt | `hiroshige-uchikawa` | Abendschnee bei Uchikawa | Utagawa Hiroshige | ca. 1836 | [56890](https://www.metmuseum.org/art/collection/search/56890) |
| Holzschnitt | `hiroshige-gion` | Der Gion-Schrein im Schnee | Utagawa Hiroshige | ca. 1834 | [56889](https://www.metmuseum.org/art/collection/search/56889) |
| Landschaft | `bierstadt-landers-peak` | Die Rocky Mountains, Lander’s Peak | Albert Bierstadt | 1863 | [10154](https://www.metmuseum.org/art/collection/search/10154) |
| Landschaft | `bierstadt-lake-louise` | Kanadische Rockies (Lake Louise) | Albert Bierstadt | ca. 1889 | [10149](https://www.metmuseum.org/art/collection/search/10149) |
| Landschaft | `cole-oxbow` | The Oxbow | Thomas Cole | 1836 | [10497](https://www.metmuseum.org/art/collection/search/10497) |
| Landschaft | `church-anden` | Herz der Anden | Frederic Edwin Church | 1859 | [10481](https://www.metmuseum.org/art/collection/search/10481) |
| Landschaft | `vangogh-zypressen` | Weizenfeld mit Zypressen | Vincent van Gogh | 1889 | [436535](https://www.metmuseum.org/art/collection/search/436535) |

## Saga-Bilder (KI-generiert)

Bilder der Saga-Elfe, fuer Saga mit einem Bildgenerator erstellt (Angabe `source` je Bild: „KI-generiert fuer Saga“,
ohne Kuenstler und Link). Auf 1600 px Breite verkleinert (JPEG). Admins koennen einzelne Bilder oder ganze Gruppen unter
Admin → Titelbilder ausblenden.

| Gruppe | Anzahl | IDs |
|---|---|---|
| Saga · Stube (`saga-stube`) | 8 | `saga-am-kaminfeuer`, `saga-warme-decke-heisser-tee`, `saga-muede-am-abend`, `saga-gemuetliche-nacht`, `saga-ruhiger-abend`, `saga-erinnerungen-am-kamin`, `saga-morgentee`, `saga-harfe-am-abend` |
| Saga · Schreibstube (`saga-schreibstube`) | 18 | `saga-schreiben-unter-dem-nordlicht`, `saga-lesen-am-kamin`, `saga-einen-brief-siegeln`, `saga-bei-kerzenlicht`, `saga-das-kassenbuch`, `saga-planen-und-messen`, `saga-am-rechner`, `saga-nachdenken-in-der-schreibstube`, `saga-wochenplan`, `saga-lesen-am-fenster`, `saga-zeichnen`, `saga-die-erinnerungswand`, `saga-ueber-der-karte`, `saga-der-brief`, `saga-abend-in-der-bibliothek`, `saga-pause-beim-lernen`, `saga-ein-gutes-buch`, `saga-nachtschicht` |
| Saga · Garten & Küche (`saga-garten`) | 10 | `saga-im-gewaechshaus`, `saga-pflanzen-setzen`, `saga-in-der-kueche`, `saga-eintopf-kochen`, `saga-kraeutergarten`, `saga-kraeuter-binden`, `saga-lavendel-pfluecken`, `saga-kraeuter-am-bach`, `saga-blumen-im-gewaechshaus`, `saga-giessen` |
| Saga · Natur (`saga-natur`) | 16 | `saga-sonnenuntergang-am-see`, `saga-am-hafen`, `saga-kirschbluete`, `saga-am-wasserfall`, `saga-der-wegweiser`, `saga-auf-dem-gipfel`, `saga-herbst-im-tal`, `saga-auf-dem-langschiff`, `saga-herbstwind`, `saga-morgennebel-am-see`, `saga-die-haengebruecke`, `saga-regen-am-fjord`, `saga-ein-brief-im-regen`, `saga-lichtung-mit-wasserfall`, `saga-blick-ueber-den-fjord`, `saga-sonnenaufgang-ueber-den-wolken` |
| Saga · Winter & Nordlicht (`saga-winter`) | 12 | `saga-mondnacht-am-weltenbaum`, `saga-nordlicht-ueber-der-kueste`, `saga-der-weltenbaum-im-schnee`, `saga-nebel-am-fjord`, `saga-klarer-wintertag`, `saga-nordlicht-im-fruehling`, `saga-aufbruch-im-schnee`, `saga-abend-vor-dem-haus`, `saga-wintermorgen-am-see`, `saga-abendrot-im-winter`, `saga-sehnsucht-am-hafen`, `saga-nordlicht-ueber-yggdrasil` |
| Saga · Kriegerin (`saga-kriegerin`) | 21 | `saga-im-schneesturm`, `saga-gewitter-ueber-dem-fjord`, `saga-sturm-am-fjord`, `saga-ueber-der-festung`, `saga-ruf-des-donners`, `saga-mit-der-axt`, `saga-wache-im-schnee`, `saga-kampfbereit`, `saga-die-burg-im-schnee`, `saga-ueber-den-wolken`, `saga-durch-den-sturm`, `saga-der-drache`, `saga-mit-der-schar`, `saga-dunkle-wolken`, `saga-vor-der-festung`, `saga-nachtwache`, `saga-nach-der-schlacht`, `saga-auf-dem-uebungsplatz`, `saga-schwert-im-wind`, `saga-schwertuebung`, `saga-die-gefaehrtinnen` |
| Saga · Magie (`saga-magie`) | 5 | `saga-herrin-des-gewitters`, `saga-nordlichtzauber`, `saga-runen-der-daemmerung`, `saga-der-runenkreis`, `saga-rauchzauber` |
| Saga · Gefährten (`saga-gefaehrten`) | 10 | `saga-der-hund-mit-dem-wikingerhelm`, `saga-lesen-mit-dem-wolf`, `saga-auf-dem-markt`, `saga-umarmung-mit-dem-wolf`, `saga-bootsfahrt-auf-dem-fjord`, `saga-ein-arm-voll-blumen`, `saga-gang-ueber-den-markt`, `saga-schlaf-mit-dem-wolf`, `saga-blumen-fuer-dich`, `saga-trost-beim-wolf` |
