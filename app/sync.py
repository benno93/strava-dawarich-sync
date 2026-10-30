"""Fetch new Strava activities and import them into Dawarich as GPX.

Two layers of duplicate protection:
  1. state.json remembers every Strava activity ID that was imported.
  2. Before each run, the import names in Dawarich (strava_<id>.gpx) are read,
     so no duplicates appear even if state.json is lost.
"""
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

import requests

from common import (
    data_dir, dawarich_headers, dawarich_strava_imports, env, get_access_token, load_json, log,
    save_json, state_file,
)
from overlap import delete_foreign_points, find_foreign_points

STRAVA_API = "https://www.strava.com/api/v3"
OWN_MARGIN_DAYS = 7


class DailyLimitReached(Exception):
    pass


# ---------- Strava ----------

def seconds_to_next_quarter_hour(now=None):
    now = now or datetime.now(timezone.utc)
    nxt = now.replace(second=0, microsecond=0) + timedelta(minutes=15 - now.minute % 15)
    return int((nxt - now).total_seconds()) + 15


def daily_limit_hit(headers):
    """Strava headers look like 'usage_15min,usage_daily' / 'limit_15min,limit_daily'."""
    for prefix in ("X-ReadRateLimit", "X-RateLimit"):
        try:
            usage = int(headers.get(f"{prefix}-Usage", "").split(",")[1])
            limit = int(headers.get(f"{prefix}-Limit", "").split(",")[1])
        except (IndexError, ValueError):
            continue
        if usage >= limit:
            return True
    return False


def strava_get(path, token, params=None, max_waits=8):
    for _ in range(max_waits + 1):
        r = requests.get(
            f"{STRAVA_API}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=60,
        )
        if r.status_code != 429:
            return r
        if daily_limit_hit(r.headers):
            raise DailyLimitReached()
        wait = seconds_to_next_quarter_hour()
        log(f"Strava 15-minute rate limit reached, waiting {wait} s ...")
        time.sleep(wait)
    raise DailyLimitReached()


def list_activities(token, after_epoch):
    page = 1
    while True:
        r = strava_get(
            "/athlete/activities", token,
            {"after": after_epoch, "per_page": 100, "page": page},
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            return
        yield from batch
        page += 1


def get_streams(token, activity_id):
    r = strava_get(
        f"/activities/{activity_id}/streams", token,
        {"keys": "latlng,time,altitude", "key_by_type": "true"},
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


# ---------- GPX ----------

def parse_start(activity):
    return datetime.fromisoformat(activity["start_date"].replace("Z", "+00:00"))


def build_gpx(activity, streams):
    start = parse_start(activity)
    latlng = streams["latlng"]["data"]
    times = streams.get("time", {}).get("data") or list(range(len(latlng)))
    alts = streams.get("altitude", {}).get("data")

    points = []
    for i, (lat, lon) in enumerate(latlng):
        ts = (start + timedelta(seconds=times[i])).strftime("%Y-%m-%dT%H:%M:%SZ")
        ele = f"<ele>{alts[i]}</ele>" if alts and i < len(alts) else ""
        points.append(f'<trkpt lat="{lat}" lon="{lon}">{ele}<time>{ts}</time></trkpt>')

    name = escape(activity.get("name") or f"Strava {activity['id']}")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="strava-dawarich-sync" '
        'xmlns="http://www.topografix.com/GPX/1/1">\n'
        f"<metadata><name>{name}</name><time>{activity['start_date']}</time></metadata>\n"
        f"<trk><name>{name}</name><type>{escape(activity.get('sport_type', ''))}</type>"
        "<trkseg>\n" + "\n".join(points) + "\n</trkseg></trk>\n</gpx>\n"
    )


# ---------- Dawarich ----------

def upload_gpx(base_url, filename, gpx):
    r = requests.post(
        f"{base_url}/api/v1/imports", headers=dawarich_headers(),
        files={"file": (filename, gpx.encode("utf-8"), "application/gpx+xml")},
        timeout=120,
    )
    if r.status_code != 201:
        raise RuntimeError(f"Dawarich returned {r.status_code}: {r.text[:300]}")


# ---------- Main ----------

def is_virtual(activity):
    """Zwift, ROUVY, MyWhoosh & co.: virtual sport types or indoor trainer activities.
    Their GPS points lie in virtual worlds (or on routes you never physically rode)."""
    return str(activity.get("sport_type", "")).startswith("Virtual") or bool(activity.get("trainer"))


def env_bool(name, default):
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes")


def parse_sport_types(raw):
    """Empty or 'all' -> None (every sport type). Otherwise a set of Strava sport_type values."""
    raw = (raw or "").strip()
    if raw.lower() in ("", "all"):
        return None
    return {s.strip() for s in raw.split(",") if s.strip()}


def replace_overlaps(base_url, token, after_epoch, dry_run):
    """Replace points from other sources for activities in the lookback window. Returns the
    number of points replaced. Activities shortly before the window can still overlap one
    inside it, so their points count as own too."""
    imports = dawarich_strava_imports(base_url)
    targets, own = [], []
    for act in list_activities(token, max(0, after_epoch - OWN_MARGIN_DAYS * 86400)):
        imp = imports.get(act["id"])
        if not imp:
            continue
        own.append(imp)
        # A virtual track is not where you really were, so it must not replace anything
        if parse_start(act).timestamp() >= after_epoch and not is_virtual(act):
            targets.append(imp)

    foreign = find_foreign_points(base_url, targets, own)
    if dry_run:
        for name, points in foreign.items():
            log(f"Would replace {len(points)} points from other sources with {name}")
        return 0
    return delete_foreign_points(base_url, foreign)


def main():
    base_url = env("DAWARICH_URL").rstrip("/")
    sport_types = parse_sport_types(os.environ.get("SPORT_TYPES", "all"))
    lookback = int(env("LOOKBACK_DAYS", "14"))
    dry_run = env_bool("DRY_RUN", False)
    skip_virtual = env_bool("SKIP_VIRTUAL", True)
    replace_overlapping = env_bool("REPLACE_OVERLAPPING_POINTS", False)

    state = load_json(state_file(), {"newest_start": 0, "imported": [], "skipped": []})
    imported = set(state["imported"])
    skipped = set(state["skipped"])

    if not dry_run:
        try:
            imported |= set(dawarich_strava_imports(base_url))
        except requests.RequestException as e:
            sys.exit(f"ERROR: Dawarich unreachable or API key wrong: {e}")

    token = get_access_token()
    newest_start = state["newest_start"]
    # Look back a few days to catch activities a watch uploads late
    after_epoch = max(0, newest_start - lookback * 86400) if newest_start else 0
    counts = {"imported": 0, "no_gps": 0, "virtual": 0, "replaced": 0, "errors": 0}
    complete = True

    def persist():
        state["imported"] = sorted(imported)
        state["skipped"] = sorted(skipped)
        save_json(state_file(), state)

    log(f"Start: sport types {sorted(sport_types) if sport_types else 'all'}, "
        f"after epoch {after_epoch}")
    try:
        for act in list_activities(token, after_epoch):
            newest_start = max(newest_start, int(parse_start(act).timestamp()))
            aid = act["id"]
            if sport_types and act.get("sport_type") not in sport_types:
                continue
            if aid in imported or aid in skipped:
                continue
            if skip_virtual and is_virtual(act):
                counts["virtual"] += 1
                continue
            if not act.get("start_latlng"):
                skipped.add(aid)
                counts["no_gps"] += 1
                continue

            streams = get_streams(token, aid)
            if not streams or not streams.get("latlng", {}).get("data"):
                skipped.add(aid)
                counts["no_gps"] += 1
                continue

            filename = f"strava_{aid}.gpx"
            gpx = build_gpx(act, streams)
            try:
                if dry_run:
                    out = data_dir() / "gpx" / filename
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text(gpx)
                else:
                    upload_gpx(base_url, filename, gpx)
                    imported.add(aid)
                    persist()
                counts["imported"] += 1
                log(f"OK  {act['start_date'][:10]}  {act.get('name', '')}  ({aid})")
            except (RuntimeError, requests.RequestException) as e:
                counts["errors"] += 1
                complete = False
                log(f"ERROR on {aid}: {e}")  # not marked as done -> retried next run

        if replace_overlapping:
            try:
                counts["replaced"] = replace_overlaps(base_url, token, after_epoch, dry_run)
            except (RuntimeError, OSError, requests.RequestException) as e:
                counts["errors"] += 1
                complete = False
                log(f"ERROR replacing overlapping points: {e}")  # retried next run
    except DailyLimitReached:
        complete = False
        log("Strava daily rate limit reached. The next run continues where this one stopped.")

    if not dry_run:
        # Only move the time window forward after a complete run
        if complete:
            state["newest_start"] = newest_start
        persist()

    log(f"Done: {counts['imported']} imported, {counts['no_gps']} without GPS, "
        f"{counts['virtual']} virtual skipped, {counts['replaced']} overlapping points replaced, "
        f"{counts['errors']} errors")
    sys.exit(1 if counts["errors"] else 0)


if __name__ == "__main__":
    main()
