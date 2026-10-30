"""Replace points from other sources (OwnTracks & co.) with the Strava track.

While an activity runs, phone and watch record the same trip twice. They never measure
exactly the same place, so Dawarich keeps both tracks. Here Strava wins: points of other
sources inside the time span of a Strava track are deleted. Pauses longer than PAUSE_GAP
split that span, so a coffee stop recorded by the phone stays.

Every check starts from what is in Dawarich right now, so it does not matter which source
arrives first. An import that is still being processed has no points yet and is skipped
until a later run.
"""
import json
import time

from common import data_dir, delete_points, fetch_points, log

PAUSE_GAP = 300


def journal_file():
    return data_dir() / "overlap_deleted.jsonl"


def split_windows(timestamps, gap=PAUSE_GAP):
    """[(start, end), ...] of the track, split wherever it has a gap longer than `gap` seconds."""
    windows = []
    for ts in sorted(timestamps):
        if windows and ts - windows[-1][1] <= gap:
            windows[-1][1] = ts
        else:
            windows.append([ts, ts])
    return [tuple(w) for w in windows]


def find_foreign_points(base_url, targets, own_imports):
    """{import name: [points from other sources]} inside the tracks of the target imports.
    `own_imports` must cover every Strava import that could overlap with a target."""
    candidates = {}
    for imp in targets:
        timestamps = [int(p["timestamp"]) for p in fetch_points(base_url, import_id=imp["id"])]
        for start, end in split_windows(timestamps):
            for p in fetch_points(base_url, start_at=start, end_at=end):
                candidates.setdefault(p["id"], (imp["name"], p))

    # Read the own points last: a point Dawarich adds in between is then in this list if it
    # is in the candidates, so a running import can never be mistaken for a foreign source.
    own = set()
    for imp in own_imports:
        own.update(p["id"] for p in fetch_points(base_url, import_id=imp["id"]))

    foreign = {}
    for point_id, (name, point) in candidates.items():
        if point_id not in own:
            foreign.setdefault(name, []).append(point)
    return foreign


def delete_foreign_points(base_url, foreign):
    """Delete the points, each batch only after it is safely in the journal."""
    total = 0
    for name, points in foreign.items():
        with journal_file().open("a") as f:
            for p in points:
                f.write(json.dumps({"at": int(time.time()), "strava_import": name, "point": p}) + "\n")
        total += delete_points(base_url, [p["id"] for p in points])
        log(f"Replaced {len(points)} points from other sources with {name}")
    return total
