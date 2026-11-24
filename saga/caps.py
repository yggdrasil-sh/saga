"""Faehigkeiten (Bugs-DB 2261, Konzept 2.6 K18): ein Rechtemechanismus fuer alles, was die KI darf - der Server prueft
jede Faehigkeit selbst (can), Gruppen stellt der Admin ein (Admin -> KI-Assistent). Clients bekommen die Liste in
GET /api/account ("caps") und blenden danach Einstiege ein - entscheiden tut immer der Server.

  ai        Assistent nutzen (wie bisher: KI eingeschaltet + Gruppe "Nur fuer diese Gruppen")
  ai_act    Auftraege nach aussen (Nachrichten in deinem Namen) und Websuche - Gruppe ohne ai_act = eingeschraenkt
            (z. B. Kinder): Saga schlaegt nur vor, keine Aussen-Schritte, keine Websuche
  ai_ideas  Ideen von sich aus (die Person schaltet sie selbst ein; der Admin kann sie hier begrenzen)

Jede Faehigkeit ausser "ai" setzt "ai" voraus. Leere Gruppenliste = alle, die "ai" haben (Standard - so aendert sich bei
bestehenden Servern nichts). Reserviert fuer 3.0 (Telefonie), heute immer False: tel, tel_out, tel_in, tel_intl,
tel_mailbox. Einstellung: setting "ai" -> {"caps": {"ai_act": [gruppen], "ai_ideas": [gruppen]}}.
Tests: tests/faehigkeiten.py
"""
CAPS = ("ai", "ai_act", "ai_ideas")
GROUP_CAPS = ("ai_act", "ai_ideas")                  # mit eigener Gruppenliste im Admin-Bereich
RESERVED = ("tel", "tel_out", "tel_in", "tel_intl", "tel_mailbox")


def _want(cfg, cap):
    return {str(g).lower() for g in ((cfg.get("caps") or {}).get(cap) or []) if g}


def can(uid, cap, conn=None, cfg=None):
    """Darf uid die Faehigkeit cap? Unbekannte und reservierte Faehigkeiten: nie."""
    if cap not in CAPS or not uid:
        return False
    from saga import ai
    c = cfg or ai.cfg()
    if not ai.allowed(uid, c, conn):
        return False
    if cap == "ai":
        return True
    want = _want(c, cap)
    if not want:
        return True
    from saga.sharing import groups_of
    if conn is not None:
        return bool({g.lower() for g in groups_of(conn, uid)} & want)
    from db import connect
    with connect() as cc:
        return bool({g.lower() for g in groups_of(cc, uid)} & want)


def caps_of(uid, conn=None):
    """Alle Faehigkeiten von uid (sortiert wie CAPS) - fuer /api/account."""
    from saga import ai
    c = ai.cfg()
    if conn is None:
        from db import connect
        with connect() as cc:
            return [x for x in CAPS if can(uid, x, cc, c)]
    return [x for x in CAPS if can(uid, x, conn, c)]
