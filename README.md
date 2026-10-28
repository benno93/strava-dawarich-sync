# strava-dawarich-sync

Automatically import your Strava activities into your self-hosted [Dawarich](https://github.com/Freika/dawarich) instance. No duplicates, no manual exports.

🇩🇪 [Deutsche Anleitung](README.de.md)

- Imports your **entire Strava history** on the first run, then checks for new activities every hour
- Every activity with GPS: runs, rides, hikes, swims, …. Activities without GPS (treadmill, strength) are skipped
- **Virtual rides and runs (Zwift, ROUVY, MyWhoosh, …) are skipped**, so no fake trips to Watopia end up in your location history
- **Never creates duplicates**, even if you lose the local state
- Handles Strava's rate limits on its own: pauses and continues
- Ready-made Docker image for `amd64` and `arm64` (e.g. Raspberry Pi), published only after all tests pass

## You need

- A running Dawarich instance, reachable via HTTPS
- Docker with Docker Compose on any machine (the Dawarich server itself is fine)
- A Strava account

## Setup (about 15 minutes)

### 1. Create a Strava API app

1. Open https://www.strava.com/settings/api
2. Fill in the form. Only one field matters:
   - **Authorization Callback Domain:** `localhost`
   - Everything else (name, website, category) can be anything
3. Write down the **Client ID** (a number) and the **Client Secret**

You do **not** need the "Access Token" and "Refresh Token" shown on that page. They can't read activities. Step 4 creates the right ones.

### 2. Copy your Dawarich API key

Dawarich → **Account settings** → copy the **API key**.

### 3. Download and configure

```sh
mkdir strava-dawarich-sync && cd strava-dawarich-sync
curl -O https://raw.githubusercontent.com/benno93/strava-dawarich-sync/main/compose.yaml
curl -o .env https://raw.githubusercontent.com/benno93/strava-dawarich-sync/main/.env.example
nano .env
```

That's all you need: `compose.yaml` pulls the ready-made image from `ghcr.io`.

Fill in these four lines, leave the rest as is:

```
STRAVA_CLIENT_ID=123456
STRAVA_CLIENT_SECRET=your-client-secret
DAWARICH_URL=https://dawarich.example.com
DAWARICH_API_KEY=your-dawarich-api-key
```

### 4. Authorize Strava (once)

```sh
docker compose run --rm strava-sync python authorize.py
```

1. Open the printed link in your browser, click **Authorize**. Leave all checkboxes ticked
2. Your browser shows an error page at `http://localhost/exchange_token?...`. **That is expected**
3. Copy the **full address** from the address bar, paste it into the terminal, press Enter

✅ Success: `Done. Tokens saved to /data/tokens.json.`

### 5. Start

```sh
docker compose up -d
docker logs -f strava-sync
```

✅ Success looks like this:

```
2026-10-28 15:00:00 Start: sport types all, after epoch 0
2026-10-28 15:00:01 OK  2019-05-12  Morning Run  (1234567890)
...
2026-10-28 15:04:10 Done: 722 imported, 147 without GPS, 0 errors
```

Leave the log with `Ctrl+C`. The container keeps running.

### 6. Check Dawarich

Dawarich → **Imports**: you see entries named `strava_<id>.gpx`. Dawarich processes them in the background (including reverse geocoding). With hundreds of activities this takes 15 to 60 minutes until everything is on the map.

Done. From now on new activities arrive within an hour.

## How it works

**First run:** There is no state yet, so the script asks Strava for *all* activities and imports your whole history. You don't need a separate bulk upload. Don't upload Strava exports manually beforehand: they can differ slightly from the API data and cause near-duplicate points.

**Later runs:** The script only asks for activities that started after *newest known activity minus `LOOKBACK_DAYS`* (default 14).

**Why look back?** Strava filters by an activity's *start* time, not its upload time. Example without look-back:

1. Monday: ride, your watch doesn't sync
2. Wednesday: run, uploaded and imported. The script now knows "Wednesday"
3. Friday: the watch uploads Monday's ride. It started before Wednesday, so it would never be seen

With 14 days of look-back, every run re-checks the last two weeks and finds the ride.

**Duplicate protection, twice:**

1. `data/state.json` lists every imported Strava ID
2. Before each run, the script reads the import names in Dawarich (`strava_<id>.gpx`). Even if `data/` is lost, nothing gets imported twice

A failed upload is not marked as done and is retried on the next run.

### Strava rate limits

Each activity needs one request to Strava. Strava's default limit for apps is roughly 100 read requests per 15 minutes and 1,000 per day, but it can differ per app.

| Activities in your history | First run takes |
|---|---|
| ~100 | a few minutes |
| ~500 | about an hour |
| 1,000+ | spread over more than one day; each run continues where the last one stopped |

`Strava 15-minute rate limit reached, waiting 900 s` in the log is normal.

## Configuration

All settings live in `.env`. After a change: `docker compose up -d`.

| Variable | Default | Meaning |
|---|---|---|
| `STRAVA_CLIENT_ID` | – | From strava.com/settings/api |
| `STRAVA_CLIENT_SECRET` | – | From strava.com/settings/api |
| `DAWARICH_URL` | – | Your Dawarich URL, e.g. `https://dawarich.example.com` |
| `DAWARICH_API_KEY` | – | From Dawarich account settings |
| `SKIP_VIRTUAL` | `true` | Skip virtual activities: sport types `VirtualRide`/`VirtualRun`/`VirtualRow` and anything marked as indoor trainer |
| `SPORT_TYPES` | `all` | `all` or a comma-separated list of [Strava sport types](https://developers.strava.com/docs/reference/#api-models-SportType), e.g. `Run,TrailRun,Ride` |
| `SYNC_INTERVAL_MINUTES` | `60` | Time between runs |
| `LOOKBACK_DAYS` | `14` | Days re-checked on each run (see above) |
| `DRY_RUN` | `false` | `true` writes GPX files to `data/gpx/` and uploads nothing |

## Virtual activities (Zwift, ROUVY, …)

Apps like Zwift or ROUVY upload rides with GPS coordinates of a virtual world or of a real route you never physically rode. They would show up in Dawarich as places you've been. `SKIP_VIRTUAL=true` (default) keeps them out.

**Already imported some with an older version?** Remove them:

```sh
docker compose run --rm strava-sync python cleanup_virtual.py
```

1. This is a **preview**: it lists the virtual activities found in Dawarich and changes nothing
2. Check the list, then delete their points:

```sh
docker compose run --rm strava-sync python cleanup_virtual.py --delete
```

✅ Success: `Done: N points deleted from M virtual activities.`

The import entries stay in Dawarich with 0 points, so they're never imported again. You can delete them in the Dawarich UI if you like.

## Common tasks

| Task | Command |
|---|---|
| Show log | `docker logs -f strava-sync` |
| Run once now | `docker compose restart strava-sync` |
| Update | `docker compose pull && docker compose up -d` |
| Stop | `docker compose down` |
| Re-import everything | In Dawarich, delete the `strava_*` imports, then `rm data/state.json` and `docker compose restart strava-sync` |

## Troubleshooting

**`ERROR: Dawarich unreachable or API key wrong`**
Check `DAWARICH_URL` (with `https://`, no trailing path) and `DAWARICH_API_KEY`. Test it: `curl -H "Authorization: Bearer YOUR_KEY" https://dawarich.example.com/api/v1/imports`

**`Dawarich returned 403: ... write_api_restricted`**
Your Dawarich plan doesn't allow uploads via the API. This affects Dawarich Cloud plans without write access; self-hosted instances are not restricted.

**`ERROR: Strava token request failed (400/401)`**
Authorization expired or was revoked (e.g. in Strava → Settings → My Apps). Run step 4 again.

**Strava says `redirect_uri invalid` during authorization**
The **Authorization Callback Domain** in your Strava app must be exactly `localhost`.

**An activity is missing**
It has no GPS (manual entry, treadmill), or it's excluded by `SPORT_TYPES`. Skipped IDs are listed under `skipped` in `data/state.json`.

## Privacy & security

- `data/tokens.json` grants read access to your Strava activities. Keep `data/` private and out of git (it's in `.gitignore`)
- Everything runs on your own machine. Data only flows Strava → your server → your Dawarich
- Each user creates their own Strava API app. This tool is meant for importing **your own** data

## Build it yourself

Prefer not to use the prebuilt image? Clone the repo, and in `compose.yaml` replace the `image:` line with `build: .`. Then use `docker compose up -d --build`.

## Development

```sh
pip install -r requirements.txt pytest
pytest
```

The tests run against fake Strava and Dawarich APIs; no accounts needed. Every push to `main` runs the tests and, only if they pass, publishes `ghcr.io/benno93/strava-dawarich-sync:latest`. Tags like `v1.2.0` additionally publish `:1.2.0` and `:1`.

## License

MIT, see [LICENSE](LICENSE). Not affiliated with Strava or Dawarich.
