"""Tests against fake Strava and Dawarich APIs. Run with: pytest"""
import json
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import authorize  # noqa: E402
import cleanup_overlaps  # noqa: E402
import cleanup_virtual  # noqa: E402
import common  # noqa: E402
import overlap  # noqa: E402
import sync  # noqa: E402

ACTIVITIES = [
    {"id": 1, "sport_type": "Run", "start_date": "2026-10-01T06:00:00Z",
     "name": "Morning run & <test>", "start_latlng": [53.1, 8.2]},
    {"id": 2, "sport_type": "Ride", "start_date": "2026-10-02T06:00:00Z",
     "name": "Ride", "start_latlng": [53.1, 8.2]},
    {"id": 3, "sport_type": "Run", "start_date": "2026-10-03T06:00:00Z",
     "name": "Treadmill", "start_latlng": []},
    {"id": 4, "sport_type": "TrailRun", "start_date": "2026-10-04T06:00:00Z",
     "name": "Trail", "start_latlng": [53.2, 8.1]},
]
STREAMS = {
    "latlng": {"data": [[53.1, 8.2], [53.11, 8.21]]},
    "time": {"data": [0, 30]},
    "altitude": {"data": [5.0, 6.0]},
}


class FakeResponse:
    def __init__(self, status_code, data, headers=None):
        self.status_code = status_code
        self._data = data
        self.headers = headers or {}
        self.text = json.dumps(data)

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise sync.requests.HTTPError(str(self.status_code))


VIRTUAL = [
    {"id": 10, "sport_type": "VirtualRide", "start_date": "2026-10-05T18:00:00Z",
     "name": "Zwift - Watopia", "start_latlng": [-11.6, 166.9], "trainer": False},
    {"id": 11, "sport_type": "Ride", "start_date": "2026-10-06T18:00:00Z",
     "name": "ROUVY route", "start_latlng": [47.1, 11.4], "trainer": True},
]


class FakeServers:
    def __init__(self, existing_imports=(), activities=None):
        self.activities = ACTIVITIES if activities is None else activities
        self.imports = []
        for n in existing_imports:
            self._add_import(n)
        self.uploads = []
        self.upload_status = 201
        self.points = {}  # import_id -> [point ids]
        self.timestamps = {}  # point id -> epoch, for all points including foreign ones
        self.deleted = []
        self.processing = False  # True: uploads stay unprocessed, like a busy Dawarich
        self.after_window_read = None

    def _add_import(self, name):
        imp_id = len(self.imports) + 1
        self.imports.append({"id": imp_id, "name": name})
        return imp_id

    def add_foreign(self, point_id, timestamp):
        self.timestamps[point_id] = timestamp

    def add_own(self, import_id, point_id, timestamp):
        self.points.setdefault(import_id, []).append(point_id)
        self.timestamps[point_id] = timestamp

    def import_id(self, name):
        return next(imp["id"] for imp in self.imports if imp["name"] == name)

    def _points_response(self, params):
        if params["page"] > 1:
            return FakeResponse(200, [], {"X-Total-Pages": "0"})
        if "import_id" in params:
            ids = self.points.get(params["import_id"], [])
        else:
            ids = [i for i, ts in self.timestamps.items()
                   if params["start_at"] <= ts <= params["end_at"]]
            if self.after_window_read:
                self.after_window_read()
        return FakeResponse(200, [{"id": i, "timestamp": self.timestamps[i]} for i in ids],
                            {"X-Total-Pages": "1"})

    def get(self, url, headers=None, params=None, timeout=None):
        if url.endswith("/athlete/activities"):
            if params["page"] > 1:
                return FakeResponse(200, [])
            return FakeResponse(200, [a for a in self.activities
                                      if sync.parse_start(a).timestamp() > params["after"]])
        if "/streams" in url:
            return FakeResponse(200, STREAMS)
        if url.endswith("/api/v1/imports"):
            return FakeResponse(200, self.imports, {"X-Total-Pages": "1"})
        if url.endswith("/api/v1/points"):
            return self._points_response(params)
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url, headers=None, files=None, timeout=None, data=None):
        name, content, _ = files["file"]
        if self.upload_status != 201:
            return FakeResponse(self.upload_status, {"error": "boom"})
        self.uploads.append((name, content.decode()))
        imp_id = self._add_import(name)
        if not self.processing:
            for i, ts in enumerate(gpx_timestamps(content.decode()), 1):
                self.add_own(imp_id, imp_id * 100 + i, ts)
        return FakeResponse(201, {})

    def delete(self, url, headers=None, json=None, timeout=None):
        assert url.endswith("/api/v1/points/bulk_destroy")
        self.deleted.extend(json["point_ids"])
        for i in json["point_ids"]:
            self.timestamps.pop(i, None)
            for ids in self.points.values():
                if i in ids:
                    ids.remove(i)
        return FakeResponse(200, {"count": len(json["point_ids"])})


def gpx_timestamps(gpx):
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    return [int(datetime.fromisoformat(t.text.replace("Z", "+00:00")).timestamp())
            for t in ET.fromstring(gpx).findall(".//g:trkpt/g:time", ns)]


def epoch(day, hour=6, minute=0, second=0):
    return int(datetime(2026, 10, day, hour, minute, second, tzinfo=timezone.utc).timestamp())


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("STRAVA_CLIENT_ID", "1")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secret")
    monkeypatch.setenv("DAWARICH_URL", "https://dawarich.example.com/")
    monkeypatch.setenv("DAWARICH_API_KEY", "key")
    monkeypatch.delenv("SPORT_TYPES", raising=False)
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.delenv("SKIP_VIRTUAL", raising=False)
    monkeypatch.delenv("REPLACE_OVERLAPPING_POINTS", raising=False)
    common.save_json(common.tokens_file(), {
        "access_token": "a", "refresh_token": "r", "expires_at": time.time() + 9999})
    return tmp_path


def patch(monkeypatch, servers):
    monkeypatch.setattr(sync.requests, "get", servers.get)
    monkeypatch.setattr(sync.requests, "post", servers.post)
    monkeypatch.setattr(sync.requests, "delete", servers.delete)


def run(monkeypatch, servers):
    patch(monkeypatch, servers)
    with pytest.raises(SystemExit) as exc:
        sync.main()
    return exc.value.code


def state(tmp_path):
    return json.loads((tmp_path / "state.json").read_text())


def test_imports_all_gps_activities_and_skips_the_rest(env, monkeypatch):
    servers = FakeServers()
    assert run(monkeypatch, servers) == 0
    assert [u[0] for u in servers.uploads] == ["strava_1.gpx", "strava_2.gpx", "strava_4.gpx"]
    assert state(env)["skipped"] == [3]


def test_second_run_creates_no_duplicates(env, monkeypatch):
    servers = FakeServers()
    run(monkeypatch, servers)
    first = len(servers.uploads)
    run(monkeypatch, servers)
    assert len(servers.uploads) == first


def test_existing_dawarich_import_is_respected_without_state(env, monkeypatch):
    servers = FakeServers(existing_imports=["strava_4.gpx", "other.gpx"])
    run(monkeypatch, servers)
    assert "strava_4.gpx" not in [u[0] for u in servers.uploads]


def test_sport_type_filter(env, monkeypatch):
    monkeypatch.setenv("SPORT_TYPES", "Run,TrailRun")
    servers = FakeServers()
    run(monkeypatch, servers)
    assert [u[0] for u in servers.uploads] == ["strava_1.gpx", "strava_4.gpx"]


def test_failed_upload_is_retried_next_run(env, monkeypatch):
    servers = FakeServers()
    servers.upload_status = 500
    assert run(monkeypatch, servers) == 1
    assert state(env)["imported"] == []
    assert state(env)["newest_start"] == 0  # time window not moved after incomplete run
    servers.upload_status = 201
    assert run(monkeypatch, servers) == 0
    assert len(servers.uploads) == 3


def test_dry_run_writes_files_and_uploads_nothing(env, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    servers = FakeServers()
    run(monkeypatch, servers)
    assert servers.uploads == []
    assert (env / "gpx" / "strava_1.gpx").exists()


def test_gpx_is_valid_and_escaped():
    gpx = sync.build_gpx(ACTIVITIES[0], STREAMS)
    root = ET.fromstring(gpx)
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    points = root.findall(".//g:trkpt", ns)
    assert len(points) == 2
    assert points[1].find("g:time", ns).text == "2026-10-01T06:00:30Z"
    assert root.find(".//g:trk/g:name", ns).text == "Morning run & <test>"


def test_lookback_window(env, monkeypatch):
    servers = FakeServers()
    run(monkeypatch, servers)
    newest = state(env)["newest_start"]
    assert newest == int(datetime(2026, 10, 4, 6, tzinfo=timezone.utc).timestamp())

    seen_after = []
    original_get = servers.get

    def spy(url, headers=None, params=None, timeout=None):
        if url.endswith("/athlete/activities"):
            seen_after.append(params["after"])
        return original_get(url, headers, params, timeout)

    servers.get = spy
    run(monkeypatch, servers)
    assert seen_after[0] == newest - 14 * 86400


def test_daily_limit_detection():
    assert sync.daily_limit_hit({"X-ReadRateLimit-Usage": "50,1000",
                                 "X-ReadRateLimit-Limit": "100,1000"})
    assert not sync.daily_limit_hit({"X-ReadRateLimit-Usage": "100,300",
                                     "X-ReadRateLimit-Limit": "100,1000"})
    assert not sync.daily_limit_hit({})


def test_parse_sport_types():
    assert sync.parse_sport_types("all") is None
    assert sync.parse_sport_types("") is None
    assert sync.parse_sport_types(" Run, Ride ") == {"Run", "Ride"}


def test_authorize_parses_redirect_url():
    code, scope = authorize.parse_code(
        "http://localhost/exchange_token?state=&code=abc123&scope=read,activity:read_all")
    assert code == "abc123"
    assert "activity:read_all" in scope
    assert authorize.parse_code("abc123") == ("abc123", "")


def test_virtual_activities_are_skipped_by_default(env, monkeypatch):
    servers = FakeServers(activities=ACTIVITIES + VIRTUAL)
    run(monkeypatch, servers)
    names = [u[0] for u in servers.uploads]
    assert "strava_10.gpx" not in names and "strava_11.gpx" not in names
    assert "strava_1.gpx" in names


def test_virtual_activities_can_be_included(env, monkeypatch):
    monkeypatch.setenv("SKIP_VIRTUAL", "false")
    servers = FakeServers(activities=ACTIVITIES + VIRTUAL)
    run(monkeypatch, servers)
    names = [u[0] for u in servers.uploads]
    assert "strava_10.gpx" in names and "strava_11.gpx" in names


def test_cleanup_preview_changes_nothing(env, monkeypatch):
    monkeypatch.setenv("SKIP_VIRTUAL", "false")
    servers = FakeServers(activities=ACTIVITIES + VIRTUAL)
    run(monkeypatch, servers)
    assert cleanup_virtual.main([]) == 0
    assert servers.deleted == []


def test_cleanup_deletes_only_points_of_virtual_imports(env, monkeypatch):
    monkeypatch.setenv("SKIP_VIRTUAL", "false")
    servers = FakeServers(activities=ACTIVITIES + VIRTUAL)
    run(monkeypatch, servers)
    by_name = {imp["name"]: imp["id"] for imp in servers.imports}
    expected = servers.points[by_name["strava_10.gpx"]] + servers.points[by_name["strava_11.gpx"]]
    assert cleanup_virtual.main(["--delete"]) == 0
    assert sorted(servers.deleted) == sorted(expected)


def test_is_virtual():
    assert sync.is_virtual({"sport_type": "VirtualRide"})
    assert sync.is_virtual({"sport_type": "VirtualRun"})
    assert sync.is_virtual({"sport_type": "Ride", "trainer": True})
    assert not sync.is_virtual({"sport_type": "Ride", "trainer": False})


# ---------- Overlapping points from other sources ----------

ONE_RUN = [ACTIVITIES[0]]  # own points at 06:00:00 and 06:00:30 on Oct 1


def test_split_windows():
    assert overlap.split_windows([]) == []
    assert overlap.split_windows([100]) == [(100, 100)]
    assert overlap.split_windows([100, 400, 701, 702]) == [(100, 400), (701, 702)]
    assert overlap.split_windows([702, 100]) == [(100, 100), (702, 702)]


def test_overlapping_points_stay_by_default(env, monkeypatch):
    servers = FakeServers(activities=ONE_RUN)
    servers.add_foreign(9001, epoch(1, second=15))
    assert run(monkeypatch, servers) == 0
    assert servers.deleted == []


def test_replaces_only_foreign_points_inside_the_track(env, monkeypatch):
    monkeypatch.setenv("REPLACE_OVERLAPPING_POINTS", "true")
    servers = FakeServers(activities=ONE_RUN)
    servers.add_foreign(9001, epoch(1, second=15))
    servers.add_foreign(9002, epoch(1, second=59))  # just after the track
    assert run(monkeypatch, servers) == 0
    assert servers.deleted == [9001]
    assert len(servers.points[servers.import_id("strava_1.gpx")]) == 2


def test_second_run_deletes_nothing_more(env, monkeypatch):
    monkeypatch.setenv("REPLACE_OVERLAPPING_POINTS", "true")
    servers = FakeServers(activities=ONE_RUN)
    servers.add_foreign(9001, epoch(1, second=15))
    run(monkeypatch, servers)
    run(monkeypatch, servers)
    assert servers.deleted == [9001]


def test_foreign_points_arriving_late_are_replaced(env, monkeypatch):
    monkeypatch.setenv("REPLACE_OVERLAPPING_POINTS", "true")
    servers = FakeServers(activities=ONE_RUN)
    run(monkeypatch, servers)
    servers.add_foreign(9001, epoch(1, second=15))
    run(monkeypatch, servers)
    assert servers.deleted == [9001]


def test_unprocessed_import_deletes_nothing_until_it_has_points(env, monkeypatch):
    monkeypatch.setenv("REPLACE_OVERLAPPING_POINTS", "true")
    servers = FakeServers(activities=ONE_RUN)
    servers.add_foreign(9001, epoch(1, second=15))
    servers.processing = True
    assert run(monkeypatch, servers) == 0
    assert servers.deleted == []

    imp_id = servers.import_id("strava_1.gpx")
    servers.add_own(imp_id, 101, epoch(1))
    servers.add_own(imp_id, 102, epoch(1, second=30))
    assert run(monkeypatch, servers) == 0
    assert servers.deleted == [9001]


def test_points_in_a_long_pause_stay(env, monkeypatch):
    monkeypatch.setenv("REPLACE_OVERLAPPING_POINTS", "true")
    servers = FakeServers(existing_imports=["strava_1.gpx"], activities=ONE_RUN)
    servers.add_own(1, 101, epoch(1))
    servers.add_own(1, 102, epoch(1, minute=30))
    servers.add_foreign(9001, epoch(1, minute=15))  # coffee stop
    assert run(monkeypatch, servers) == 0
    assert servers.deleted == []


def test_own_point_added_while_checking_is_not_deleted(env, monkeypatch):
    monkeypatch.setenv("REPLACE_OVERLAPPING_POINTS", "true")
    servers = FakeServers(existing_imports=["strava_1.gpx"], activities=ONE_RUN)
    servers.add_own(1, 101, epoch(1))
    servers.add_own(1, 102, epoch(1, second=30))
    servers.add_own(1, 103, epoch(1, second=15))
    # Simulates Dawarich still importing: 103 only shows up for the import after the window read
    servers.points[1].remove(103)
    servers.after_window_read = lambda: servers.points[1].append(103) if 103 not in servers.points[1] else None
    assert run(monkeypatch, servers) == 0
    assert servers.deleted == []


def test_overlapping_strava_activities_do_not_delete_each_other(env, monkeypatch):
    monkeypatch.setenv("REPLACE_OVERLAPPING_POINTS", "true")
    same_time = [ACTIVITIES[0], {**ACTIVITIES[0], "id": 5}]
    servers = FakeServers(activities=same_time)
    assert run(monkeypatch, servers) == 0
    assert servers.deleted == []


def test_activity_before_the_window_still_counts_as_own(env, monkeypatch):
    monkeypatch.setenv("REPLACE_OVERLAPPING_POINTS", "true")
    long_ride = {**ACTIVITIES[1], "id": 7, "start_date": "2026-10-01T06:00:00Z"}
    recent = {**ACTIVITIES[0], "id": 8, "start_date": "2026-10-20T06:00:00Z"}
    servers = FakeServers(existing_imports=["strava_7.gpx", "strava_8.gpx"],
                          activities=[long_ride, recent])
    common.save_json(common.state_file(), {
        "newest_start": epoch(20), "imported": [7, 8], "skipped": []})
    servers.add_own(1, 101, epoch(20))  # the ride's points lie inside the recent activity
    servers.add_own(2, 201, epoch(20))
    assert run(monkeypatch, servers) == 0
    assert servers.deleted == []


def test_virtual_activity_replaces_nothing(env, monkeypatch):
    monkeypatch.setenv("REPLACE_OVERLAPPING_POINTS", "true")
    monkeypatch.setenv("SKIP_VIRTUAL", "false")
    servers = FakeServers(activities=[VIRTUAL[0]])
    servers.add_foreign(9001, epoch(5, 18, second=15))
    assert run(monkeypatch, servers) == 0
    assert servers.deleted == []


def test_nothing_is_deleted_if_the_journal_fails(env, monkeypatch):
    monkeypatch.setenv("REPLACE_OVERLAPPING_POINTS", "true")
    servers = FakeServers(activities=ONE_RUN)
    servers.add_foreign(9001, epoch(1, second=15))
    overlap.journal_file().mkdir()  # opening a directory for writing fails
    assert run(monkeypatch, servers) == 1
    assert servers.deleted == []
    assert state(env)["newest_start"] == 0  # time window not moved, retried next run


def test_deleted_points_are_in_the_journal(env, monkeypatch):
    monkeypatch.setenv("REPLACE_OVERLAPPING_POINTS", "true")
    servers = FakeServers(activities=ONE_RUN)
    servers.add_foreign(9001, epoch(1, second=15))
    run(monkeypatch, servers)
    entry = json.loads(overlap.journal_file().read_text().splitlines()[0])
    assert entry["strava_import"] == "strava_1.gpx"
    assert entry["point"]["id"] == 9001


def test_dry_run_replaces_nothing(env, monkeypatch):
    monkeypatch.setenv("REPLACE_OVERLAPPING_POINTS", "true")
    monkeypatch.setenv("DRY_RUN", "true")
    servers = FakeServers(existing_imports=["strava_1.gpx"], activities=ONE_RUN)
    servers.add_own(1, 101, epoch(1))
    servers.add_own(1, 102, epoch(1, second=30))
    servers.add_foreign(9001, epoch(1, second=15))
    assert run(monkeypatch, servers) == 0
    assert servers.deleted == []
    assert not overlap.journal_file().exists()


def test_fetch_points_ignores_a_wrong_page_count(env, monkeypatch):
    calls = []

    def get(url, headers=None, params=None, timeout=None):
        calls.append(params["page"])
        return FakeResponse(200, [{"id": params["page"]}] if params["page"] <= 2 else [],
                            {"X-Total-Pages": "1"})

    monkeypatch.setattr(sync.requests, "get", get)
    assert [p["id"] for p in common.fetch_points("https://x", import_id=1)] == [1, 2]
    assert calls == [1, 2, 3]


def test_points_are_read_in_slim_format(env, monkeypatch):
    seen = []

    def get(url, headers=None, params=None, timeout=None):
        seen.append(params)
        return FakeResponse(200, [])

    monkeypatch.setattr(sync.requests, "get", get)
    common.fetch_points("https://x", import_id=1)
    assert seen[0]["slim"] == "true"


def test_no_second_read_without_foreign_candidates(env, monkeypatch):
    servers = FakeServers(existing_imports=["strava_1.gpx"])
    servers.add_own(1, 101, epoch(1))
    servers.add_own(1, 102, epoch(1, second=30))
    patch(monkeypatch, servers)
    calls = []
    original_get = servers.get

    def spy(url, headers=None, params=None, timeout=None):
        calls.append(params)
        return original_get(url, headers, params, timeout)

    monkeypatch.setattr(sync.requests, "get", spy)
    imp = {"id": 1, "name": "strava_1.gpx"}
    assert overlap.find_foreign_points("https://x", [imp], [imp]) == {}
    # Without foreign candidates the own points don't need a second read
    assert sum("import_id" in c for c in calls) == 2  # one page plus the empty one


def test_cleanup_overlaps_preview_changes_nothing(env, monkeypatch):
    servers = FakeServers(activities=ONE_RUN)
    run(monkeypatch, servers)
    servers.add_foreign(9001, epoch(1, second=15))
    assert cleanup_overlaps.main([]) == 0
    assert servers.deleted == []


def test_cleanup_overlaps_deletes_foreign_points_only(env, monkeypatch):
    servers = FakeServers(activities=ONE_RUN)
    run(monkeypatch, servers)
    servers.add_foreign(9001, epoch(1, second=15))
    servers.add_foreign(9002, epoch(2))
    assert cleanup_overlaps.main(["--delete"]) == 0
    assert servers.deleted == [9001]


def test_cleanup_overlaps_ignores_virtual_tracks(env, monkeypatch):
    monkeypatch.setenv("SKIP_VIRTUAL", "false")
    servers = FakeServers(activities=[VIRTUAL[0]])
    run(monkeypatch, servers)
    servers.add_foreign(9001, epoch(5, 18, second=15))
    assert cleanup_overlaps.main(["--delete"]) == 0
    assert servers.deleted == []
