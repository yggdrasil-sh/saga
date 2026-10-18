# KI in den Apps – Vertrag für die App-Teams (2.1)

Kurzbeschreibung der Schnittstellen rund um die KI: Persona (Name, Profilbild) und – im zweiten Abschnitt – die KI im
Chat. Alle Endpunkte wie gewohnt mit Sitzung oder Geräte-Schlüssel (`Authorization: Bearer hkd_…`) und `X-Saga: 1`.
Alles ist rückwärtskompatibel: unbekannte Felder und Ereignisse ignorieren. `MIN_APP` ändert sich dadurch nicht.

## 1. Persona

### `GET /api/ai/persona`

```json
{"name": "Marin", "custom_name": true, "avatar": "/api/ai/avatar?v=3", "avatar_etag": "1f0c…", "enabled": true}
```

| Feld | Bedeutung |
|---|---|
| `name` | Anzeigename der KI. Ohne eigenen Namen der Standard **in der Sprache der Anfrage** (`Accept-Language` bzw. Konto-Sprache): „Saga KI“ / „Saga AI“ / „Saga IA“. |
| `custom_name` | `false` = Standardname – die App darf dann ihren eigenen, lokalisierten Standard zeigen. |
| `avatar` | Relativer Pfad zum Profilbild oder `null` = kein Bild: **neutrales Symbol** zeigen (Web: ✦ auf Akzentfarbe). Der Wert ändert sich (`?v=`), sobald ein neues Bild hochgeladen wird. |
| `avatar_etag` | Kennung des Bildinhalts – zum Vergleich mit einem Zwischenspeicher. |
| `enabled` | Ist der Assistent für **dieses Konto** eingeschaltet (Admin-Schalter + Gruppen)? `false` = keine KI-Funktionen anbieten. |

Dasselbe Objekt (ohne `enabled`) steckt auch in `GET /api/ai/status` unter `persona`, solange der Assistent für das
Konto an ist.

### `GET /api/ai/avatar?v=…&s=64|128|256`

Wie `/api/avatar/<schlüssel>`: nur angemeldet (Sitzung oder `hkd_`-Schlüssel), sonst 404. Größe `s` (nächstgrößere Stufe),
WebP mit `Accept: image/webp` (oder `&fmt=webp`), sonst PNG/JPEG. `ETag` + `If-None-Match` → 304. Mit passendem `v`
ist das Bild einen Tag cachebar. 404 = kein Bild (Symbol zeigen).

### Live-Ereignis `ai.persona.changed`

Über `/api/events` an **alle** verbundenen Konten, wenn der Admin Name/Bild ändert oder die KI bzw. „KI in Chats“
ein- oder ausschaltet:

```
event: ai.persona.changed
data: {"name": "Marin", "custom_name": true, "avatar": "/api/ai/avatar?v=4", "avatar_etag": "…"}
```

Ohne `enabled` (das ist je Person verschieden): Apps fragen danach `GET /api/ai/persona` (oder `/api/ai/status`) neu ab.
Nach einer Wiederverbindung ohne Nachholen (`reset`) ebenfalls neu abfragen.

### Beschriftung

„Frage {name}“ / „Ask {name}“ / „Pregunta a {name}“ für den Einstieg, KI-Antworten mit kleinem Profilbild und Namen.

### Tonfall

Der Admin kann eine kurze Persönlichkeit/Tonfall-Beschreibung hinterlegen. Sie geht nur serverseitig in den
System-Prompt; Apps sehen sie nicht und müssen nichts tun. Antwortformate (z. B. Aufgaben-Vorschläge `done.tasks`)
bleiben unverändert.
