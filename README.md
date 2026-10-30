# strava-dawarich-sync

Automatically import your Strava activities into your self-hosted [Dawarich](https://github.com/Freika/dawarich) instance. No duplicates, no manual exports.

🇩🇪 [Deutsche Anleitung](README.de.md)

- Imports your **entire Strava history** on the first run, then checks for new activities every hour
- Every activity with GPS: runs, rides, hikes, swims, …. Activities without GPS (treadmill, strength) are skipped
- **Virtual rides and runs (Zwift, ROUVY, MyWhoosh, …) are skipped**, so no fake trips to Watopia end up in your location history
- **Never imports an activity twice**, even if you lose the local state
- **Optional: replaces overlapping points from OwnTracks & co.** with the Strava track, so a trip doesn't show up twice on the map
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
| `REPLACE_OVERLAPPING_POINTS` | `false` | `true` deletes points from other sources in the time span of a Strava track (see below) |
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

## Overlapping tracks (OwnTracks & co.)

If your phone also sends your location to Dawarich, an activity gets recorded twice: by the watch and by the phone. The two never measure exactly the same place, so Dawarich keeps both and you see two tracks on top of each other.

With `REPLACE_OVERLAPPING_POINTS=true` the Strava track wins:

- Each run deletes points from every other source in the time span of a Strava track. That includes other imports, not just live tracking
- Pauses longer than 5 minutes split the time span, so a coffee stop recorded by your phone stays
- It only covers activities in the `LOOKBACK_DAYS` window, so phone data that arrives late is handled too, but **older years stay untouched** until you run the cleanup below. Virtual activities never replace anything
- Exception: without `data/state.json` (fresh install, deleted `data/`) a run covers your whole history, including older years, once their imports are processed
- It waits until Dawarich has finished processing the Strava import, so it can take a run or two after the upload

**⚠️ This deletes points from Dawarich.** The position and time of each one are first saved to `data/overlap_deleted.jsonl`, but there is no restore command, and that file can't bring back details such as altitude or a point's link to its import. A wrong clock on your watch would remove points from the wrong time, so do one test run first. It uploads and deletes nothing and logs what would be replaced:

```sh
docker compose pull
docker compose run --rm -e DRY_RUN=true -e REPLACE_OVERLAPPING_POINTS=true strava-sync python sync.py
```

**To clean up your whole history, including past years:**

1. Wait for the first import to finish, back up Dawarich, and get the latest image in your `strava-dawarich-sync` folder with `docker compose pull`
2. Run a preview that changes nothing:

   ```sh
   docker compose run --rm strava-sync python cleanup_overlaps.py
   ```

3. Check the list. It shows the number of points per activity, so look closely at activities with unusually many. Everything in the time span of a Strava track that isn't from a `strava_*` import would go, including older imports such as Google Timeline data or GPX files you uploaded by hand
4. Delete those points:

   ```sh
   docker compose run --rm strava-sync python cleanup_overlaps.py --delete
   ```

✅ Success: `Done: N points deleted from M Strava activities.`

With hundreds of activities this takes a while and can put noticeable load on your Dawarich instance. It uses very few Strava requests.

**To keep it clean from now on**, add `REPLACE_OVERLAPPING_POINTS=true` to `.env` (older `.env` files don't have this line yet), then apply it and check the log:

```sh
docker compose pull && docker compose up -d
docker logs -f strava-sync
```

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
