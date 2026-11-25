"""Hilfen fuer Reihen, die eigene Wegwerf-Konten anlegen (Hilfsmodul, keine Testreihe).

ohne_einfuehrung: Frage "Moechtest du eine kurze Einfuehrung?" (Erste Schritte, saga/firststeps.py) fuer frische Konten als
mit "Nein" beantwortet speichern - sonst liegt der Dialog (.hk-ask) in Browser-Laeufen ueber der Oberflaeche und die Reihe
bricht nach 30 s ab (Bugs-DB 2428). Derselbe Eintrag wie tools/testumgebung/konten.py; eine schon vorhandene Antwort bleibt.
Den Dialog selbst pruefen tests/erste_schritte.py und tests/browser_leere_zustaende.js (mit frischem Konto, ohne diese Hilfe).
Im Browser (ohne DB-Zugriff): tests/lib/erste_schritte.js."""
import json

ANTWORT_NEIN = json.dumps({"state": "no"})
SQL = "INSERT INTO user_kv (uid, k, v) VALUES (?, 'first_steps', ?) ON CONFLICT (uid, k) DO NOTHING"


def ohne_einfuehrung(c, *uids):
    """c: offene Verbindung (der Aufrufer schliesst die Transaktion ab) oder None (eigene Verbindung und Transaktion).
    uids: Kontennamen (auch Listen davon) -> Anzahl der Konten."""
    flat = [u for x in uids for u in ((x,) if isinstance(x, str) else x)]
    if c is None:
        from db import connect
        with connect() as c2, c2:
            return ohne_einfuehrung(c2, *flat)
    for u in flat:
        c.execute(SQL, (u, ANTWORT_NEIN))
    return len(flat)
