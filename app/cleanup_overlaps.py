"""Remove points from other sources (OwnTracks & co.) that overlap imported Strava activities.

Preview (changes nothing):   python cleanup_overlaps.py
Delete those points:         python cleanup_overlaps.py --delete

The regular sync only checks recent activities (REPLACE_OVERLAPPING_POINTS). This covers
your whole history. Deleted points are written to data/overlap_deleted.jsonl first.
"""
import sys

from common import dawarich_strava_imports, env, get_access_token, log
from overlap import delete_foreign_points, find_foreign_points
from sync import is_virtual, list_activities


def main(argv):
    delete = "--delete" in argv
    base_url = env("DAWARICH_URL").rstrip("/")

    imports = dawarich_strava_imports(base_url)
    # A virtual track is not where you really were, so it must not replace anything
    virtual = {a["id"] for a in list_activities(get_access_token(), 0) if is_virtual(a)}
    targets = [imp for aid, imp in imports.items() if aid not in virtual]
    foreign = find_foreign_points(base_url, targets, list(imports.values()))

    if not foreign:
        log("No overlapping points from other sources found. Nothing to do.")
        return 0

    count = sum(len(points) for points in foreign.values())
    log(f"Found {count} overlapping points in {len(foreign)} Strava activities:")
    for name, points in foreign.items():
        log(f"  {name}: {len(points)} points")

    if not delete:
        log("Preview only, nothing changed. To delete these points run: "
            "python cleanup_overlaps.py --delete")
        return 0

    total = delete_foreign_points(base_url, foreign)
    log(f"Done: {total} points deleted from {len(foreign)} Strava activities.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
