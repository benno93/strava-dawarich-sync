"""Remove virtual activities (Zwift, ROUVY, ...) that were already imported into Dawarich.

Preview (changes nothing):   python cleanup_virtual.py
Delete their points:         python cleanup_virtual.py --delete

How it works: reads your Strava history to find virtual activities, matches them to
the strava_<id>.gpx imports in Dawarich, and deletes the points of those imports via
the Dawarich API. The import entries themselves stay (with 0 points) so they are never
imported again; you can delete them in the Dawarich UI if you like.
"""
import sys

import requests

from common import env, get_access_token, log
from sync import NAME_RE, dawarich_headers, is_virtual, list_activities

BULK_LIMIT = 5000


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


def point_ids_of_import(base_url, import_id):
    ids, page = [], 1
    while True:
        r = requests.get(
            f"{base_url}/api/v1/points", headers=dawarich_headers(),
            params={"import_id": import_id, "include_anomalies": "true",
                    "per_page": 10000, "page": page},
            timeout=120,
        )
        r.raise_for_status()
        ids.extend(p["id"] for p in r.json())
        if page >= int(r.headers.get("X-Total-Pages", page) or page):
            return ids
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


def main(argv):
    delete = "--delete" in argv
    base_url = env("DAWARICH_URL").rstrip("/")

    imports = dawarich_strava_imports(base_url)
    token = get_access_token()
    virtual = [a for a in list_activities(token, 0) if is_virtual(a) and a["id"] in imports]

    if not virtual:
        log("No imported virtual activities found. Nothing to do.")
        return 0

    log(f"Found {len(virtual)} imported virtual activities:")
    for a in virtual:
        log(f"  {a['start_date'][:10]}  {a.get('sport_type', '')}  {a.get('name', '')}  ({a['id']})")

    if not delete:
        log("Preview only, nothing changed. To delete their points run: "
            "python cleanup_virtual.py --delete")
        return 0

    total = 0
    for a in virtual:
        imp = imports[a["id"]]
        ids = point_ids_of_import(base_url, imp["id"])
        total += delete_points(base_url, ids) if ids else 0
        log(f"Deleted {len(ids)} points of {imp['name']}")
    log(f"Done: {total} points deleted from {len(virtual)} virtual activities.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
