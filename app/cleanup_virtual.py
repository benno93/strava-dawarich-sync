"""Remove virtual activities (Zwift, ROUVY, ...) that were already imported into Dawarich.

Preview (changes nothing):   python cleanup_virtual.py
Delete their points:         python cleanup_virtual.py --delete

How it works: reads your Strava history to find virtual activities, matches them to
the strava_<id>.gpx imports in Dawarich, and deletes the points of those imports via
the Dawarich API. The import entries themselves stay (with 0 points) so they are never
imported again; you can delete them in the Dawarich UI if you like.
"""
import sys

from common import dawarich_strava_imports, delete_points, env, fetch_points, get_access_token, log
from sync import is_virtual, list_activities


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
        ids = [p["id"] for p in fetch_points(base_url, import_id=imp["id"])]
        total += delete_points(base_url, ids) if ids else 0
        log(f"Deleted {len(ids)} points of {imp['name']}")
    log(f"Done: {total} points deleted from {len(virtual)} virtual activities.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
