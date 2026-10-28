# strava-dawarich-sync

Importiert deine Strava-Aktivitäten automatisch in deine selbst gehostete [Dawarich](https://github.com/Freika/dawarich)-Instanz. Ohne Duplikate, ohne manuelle Exporte.

🇬🇧 [English version](README.md)

- Importiert beim ersten Start deine **komplette Strava-Historie**, danach stündlich neue Aktivitäten
- Jede Aktivität mit GPS: Laufen, Rad, Wandern, Schwimmen, …. Aktivitäten ohne GPS (Laufband, Rolle, Kraft) werden übersprungen
- **Erzeugt nie Duplikate**, auch wenn der lokale Stand verloren geht
- Kommt mit Stravas Rate-Limits selbst klar: pausiert und macht weiter
- Fertiges Docker-Image für `amd64` und `arm64` (z. B. Raspberry Pi), veröffentlicht erst, wenn alle Tests grün sind

## Du brauchst

- Eine laufende Dawarich-Instanz, per HTTPS erreichbar
- Docker mit Docker Compose auf irgendeinem Rechner (der Dawarich-Server selbst geht auch)
- Einen Strava-Account

## Einrichtung (etwa 15 Minuten)

### 1. Strava-API-App anlegen

1. https://www.strava.com/settings/api öffnen
2. Formular ausfüllen. Wichtig ist nur ein Feld:
   - **Authorization Callback Domain:** `localhost`
   - Alles andere (Name, Website, Kategorie) ist egal
3. **Client ID** (eine Zahl) und **Client Secret** (Geheimschlüssel) notieren

„Zugriffstoken“ und „Aktualisierungstoken“ auf der Seite brauchst du **nicht**. Sie können keine Aktivitäten lesen. Die richtigen erzeugt Schritt 4.

### 2. Dawarich-API-Key kopieren

Dawarich → **Account-Einstellungen** → **API-Key** kopieren.

### 3. Herunterladen und konfigurieren

```sh
mkdir strava-dawarich-sync && cd strava-dawarich-sync
curl -O https://raw.githubusercontent.com/benno93/strava-dawarich-sync/main/compose.yaml
curl -o .env https://raw.githubusercontent.com/benno93/strava-dawarich-sync/main/.env.example
nano .env
```

Mehr brauchst du nicht: `compose.yaml` lädt das fertige Image von `ghcr.io`.

Diese vier Zeilen ausfüllen, den Rest so lassen:

```
STRAVA_CLIENT_ID=123456
STRAVA_CLIENT_SECRET=dein-geheimschluessel
DAWARICH_URL=https://dawarich.example.com
DAWARICH_API_KEY=dein-dawarich-api-key
```

Speichern in `nano`: `Strg+O`, `Enter`, beenden mit `Strg+X`.

### 4. Strava freigeben (einmalig)

```sh
docker compose run --rm strava-sync python authorize.py
```

1. Angezeigten Link im Browser öffnen, **Autorisieren** klicken. Alle Haken gesetzt lassen
2. Der Browser zeigt eine Fehlerseite unter `http://localhost/exchange_token?...`. **Das ist richtig so**
3. Die **komplette Adresse** aus der Adresszeile kopieren, ins Terminal einfügen, Enter

✅ Erfolgreich: `Done. Tokens saved to /data/tokens.json.`

### 5. Starten

```sh
docker compose up -d
docker logs -f strava-sync
```

✅ So sieht Erfolg aus:

```
2026-10-28 15:00:00 Start: sport types all, after epoch 0
2026-10-28 15:00:01 OK  2019-05-12  Morgenlauf  (1234567890)
...
2026-10-28 15:04:10 Done: 722 imported, 147 without GPS, 0 errors
```

Log verlassen mit `Strg+C`. Der Container läuft weiter. Die Uhrzeit im Log ist UTC.

### 6. In Dawarich prüfen

Dawarich → **Imports**: Dort stehen Einträge `strava_<id>.gpx`. Dawarich verarbeitet sie im Hintergrund (inklusive Reverse-Geocoding). Bei Hunderten Aktivitäten dauert es 15 bis 60 Minuten, bis alles auf der Karte ist.

Fertig. Ab jetzt landen neue Aktivitäten innerhalb einer Stunde in Dawarich.

## So funktioniert es

**Erster Lauf:** Es gibt noch keinen Stand, also fragt das Skript Strava nach *allen* Aktivitäten und importiert deine komplette Historie. Ein separater Bulk-Upload ist nicht nötig. Lade vorher keine Strava-Exporte manuell hoch: Sie können minimal von den API-Daten abweichen und fast doppelte Punkte erzeugen.

**Spätere Läufe:** Das Skript fragt nur Aktivitäten ab, die nach *neueste bekannte Aktivität minus `LOOKBACK_DAYS`* (Standard 14) gestartet sind.

**Wozu der Rückblick?** Strava filtert nach dem *Start*zeitpunkt einer Aktivität, nicht nach dem Upload. Beispiel ohne Rückblick:

1. Montag: Radtour, die Uhr synchronisiert nicht
2. Mittwoch: Lauf, sofort hochgeladen und importiert. Das Skript merkt sich „Mittwoch“
3. Freitag: Die Uhr lädt die Montags-Tour nach. Ihr Start liegt vor Mittwoch, sie würde nie gefunden

Mit 14 Tagen Rückblick prüft jeder Lauf die letzten zwei Wochen mit und findet die Tour.

**Doppelter Duplikatschutz:**

1. `data/state.json` listet jede importierte Strava-ID
2. Vor jedem Lauf liest das Skript die Import-Namen in Dawarich (`strava_<id>.gpx`). Selbst wenn `data/` verloren geht, wird nichts doppelt importiert

Ein fehlgeschlagener Upload wird nicht als erledigt markiert und beim nächsten Lauf wiederholt.

### Strava-Rate-Limits

Jede Aktivität braucht eine Anfrage an Strava. Stravas Standardlimit für Apps liegt bei etwa 100 Lese-Anfragen pro 15 Minuten und 1.000 pro Tag, kann je App aber abweichen.

| Aktivitäten in deiner Historie | Erster Lauf dauert |
|---|---|
| ~100 | wenige Minuten |
| ~500 | etwa eine Stunde |
| über 1.000 | verteilt auf mehr als einen Tag; jeder Lauf macht dort weiter, wo der letzte aufgehört hat |

`Strava 15-minute rate limit reached, waiting 900 s` im Log ist normal.

## Konfiguration

Alle Einstellungen stehen in `.env`. Nach einer Änderung: `docker compose up -d`.

| Variable | Standard | Bedeutung |
|---|---|---|
| `STRAVA_CLIENT_ID` | – | Von strava.com/settings/api |
| `STRAVA_CLIENT_SECRET` | – | Von strava.com/settings/api |
| `DAWARICH_URL` | – | Deine Dawarich-Adresse, z. B. `https://dawarich.example.com` |
| `DAWARICH_API_KEY` | – | Aus den Dawarich-Account-Einstellungen |
| `SPORT_TYPES` | `all` | `all` oder kommagetrennte [Strava-Sportarten](https://developers.strava.com/docs/reference/#api-models-SportType), z. B. `Run,TrailRun,Ride` |
| `SYNC_INTERVAL_MINUTES` | `60` | Abstand zwischen zwei Läufen |
| `LOOKBACK_DAYS` | `14` | Tage, die jeder Lauf erneut prüft (siehe oben) |
| `DRY_RUN` | `false` | `true` schreibt GPX-Dateien nach `data/gpx/` und lädt nichts hoch |

## Häufige Aufgaben

| Aufgabe | Befehl |
|---|---|
| Log anzeigen | `docker logs -f strava-sync` |
| Sofort einmal laufen lassen | `docker compose restart strava-sync` |
| Aktualisieren | `docker compose pull && docker compose up -d` |
| Stoppen | `docker compose down` |
| Alles neu importieren | In Dawarich die `strava_*`-Imports löschen, dann `rm data/state.json` und `docker compose restart strava-sync` |

## Fehlerbehebung

**`ERROR: Dawarich unreachable or API key wrong`**
`DAWARICH_URL` (mit `https://`, ohne Pfad dahinter) und `DAWARICH_API_KEY` prüfen. Test: `curl -H "Authorization: Bearer DEIN_KEY" https://dawarich.example.com/api/v1/imports`

**`Dawarich returned 403: ... write_api_restricted`**
Dein Dawarich-Tarif erlaubt keine Uploads per API. Das betrifft Dawarich-Cloud-Tarife ohne Schreibzugriff; selbst gehostete Instanzen sind nicht eingeschränkt.

**`ERROR: Strava token request failed (400/401)`**
Die Freigabe ist abgelaufen oder wurde widerrufen (z. B. in Strava → Einstellungen → Meine Apps). Schritt 4 wiederholen.

**Strava meldet `redirect_uri invalid` bei der Freigabe**
Die **Authorization Callback Domain** deiner Strava-App muss genau `localhost` sein.

**Eine Aktivität fehlt**
Sie hat kein GPS (manueller Eintrag, Laufband) oder ist durch `SPORT_TYPES` ausgeschlossen. Übersprungene IDs stehen unter `skipped` in `data/state.json`.

## Datenschutz & Sicherheit

- `data/tokens.json` gibt Lesezugriff auf deine Strava-Aktivitäten. `data/` privat halten und nicht ins Git (steht in `.gitignore`)
- Alles läuft auf deinem eigenen Rechner. Daten fließen nur Strava → dein Server → dein Dawarich
- Jeder Nutzer legt seine eigene Strava-API-App an. Das Tool ist für den Import **deiner eigenen** Daten gedacht

## Selbst bauen

Lieber ohne fertiges Image? Repo klonen und in `compose.yaml` die Zeile `image:` durch `build: .` ersetzen. Dann `docker compose up -d --build`.

## Entwicklung

```sh
pip install -r requirements.txt pytest
pytest
```

Die Tests laufen gegen nachgebaute Strava- und Dawarich-APIs; keine Accounts nötig. Jeder Push auf `main` lässt die Tests laufen und veröffentlicht nur bei Erfolg `ghcr.io/benno93/strava-dawarich-sync:latest`. Tags wie `v1.2.0` veröffentlichen zusätzlich `:1.2.0` und `:1`.

## Lizenz

MIT, siehe [LICENSE](LICENSE). Keine Verbindung zu Strava oder Dawarich.
