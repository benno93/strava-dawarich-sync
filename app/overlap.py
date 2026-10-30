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
PROGRESS_EVERY = 25


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
    for done, imp in enumerate(targets, 1):
        track = fetch_points(base_url, import_id=imp["id"])
        track_ids = {p["id"] for p in track}
        # Most points in a window are the track itself; keeping them would fill the memory
        for start, end in split_windows(int(p["timestamp"]) for p in track):
            for p in fetch_points(base_url, start_at=start, end_at=end):
                if p["id"] not in track_ids:
                    candidates.setdefault(p["id"], (imp["name"], p))
        if done % PROGRESS_EVERY == 0:
            log(f"Checked {done}/{len(targets)} activities ...")
    if len(targets) >= PROGRESS_EVERY:
        log(f"Comparing with the points of {len(own_imports)} Strava imports ...")

    # Read the own points last: a point Dawarich adds in between is then in this list if it
    # is in the candidates, so a running import can never be mistaken for a foreign source.
    for imp in own_imports:
        if not candidates:
            break
        for p in fetch_points(base_url, import_id=imp["id"]):
            candidates.pop(p["id"], None)

    foreign = {}
    for name, point in candidates.values():
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
