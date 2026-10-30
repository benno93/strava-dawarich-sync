"""Shared helpers: configuration, token handling, JSON files, Dawarich API."""
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
NAME_RE = re.compile(r"^strava_(\d+)")
BULK_LIMIT = 5000


def data_dir():
    return Path(os.environ.get("DATA_DIR", "/data"))


def tokens_file():
    return data_dir() / "tokens.json"


def state_file():
    return data_dir() / "state.json"


def env(name, default=None, required=True):
    value = os.environ.get(name, default)
    if required and not value:
        sys.exit(f"ERROR: environment variable {name} is missing (set it in .env).")
    return value


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)  # atomic: an interrupted run never leaves a half-written file


def exchange_token(payload):
    payload = {
        "client_id": env("STRAVA_CLIENT_ID"),
        "client_secret": env("STRAVA_CLIENT_SECRET"),
        **payload,
    }
    r = requests.post(STRAVA_TOKEN_URL, data=payload, timeout=30)
    if r.status_code != 200:
        sys.exit(f"ERROR: Strava token request failed ({r.status_code}): {r.text}")
    data = r.json()
    tokens = {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_at": data["expires_at"],
    }
    save_json(tokens_file(), tokens)
    return tokens


def get_access_token():
    tokens = load_json(tokens_file(), None)
    if not tokens:
        sys.exit("ERROR: tokens.json not found. Run authorize.py first.")
    # 5-minute buffer before expiry; Strava rotates refresh tokens, so always save
    if tokens["expires_at"] - 300 < time.time():
        tokens = exchange_token(
            {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]}
        )
    return tokens["access_token"]


def dawarich_headers():
    return {"Authorization": f"Bearer {env('DAWARICH_API_KEY')}"}


def dawarich_strava_imports(base_url):
    """{strava_id: dawarich_import} for every strava_<id>.gpx import."""
    found, page = {}, 1
    while True:
        r = requests.get(
            f"{base_url}/api/v1/imports", headers=dawarich_headers(),
            params={"page": page, "per_page": 100}, timeout=60,
        )
        r.raise_for_status()
        for imp in r.json():
            m = NAME_RE.match(imp.get("name", ""))
            if m:
                found[int(m.group(1))] = imp
        if page >= int(r.headers.get("X-Total-Pages", page)):
            return found
        page += 1


def fetch_points(base_url, **filters):
    """Points matching the filters. Reads until an empty page: deleting relies on a
    complete list, and X-Total-Pages comes from a cached counter."""
    points, page = [], 1
    while True:
        r = requests.get(
            f"{base_url}/api/v1/points", headers=dawarich_headers(),
            params={**filters, "include_anomalies": "true", "per_page": 10000, "page": page},
            timeout=120,
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            return points
        points.extend(batch)
        page += 1


def delete_points(base_url, ids):
    deleted = 0
    for i in range(0, len(ids), BULK_LIMIT):
        r = requests.delete(
            f"{base_url}/api/v1/points/bulk_destroy", headers=dawarich_headers(),
            json={"point_ids": ids[i:i + BULK_LIMIT]}, timeout=300,
        )
        if r.status_code != 200:
            raise RuntimeError(f"Dawarich returned {r.status_code}: {r.text[:300]}")
        deleted += r.json().get("count", 0)
    return deleted
