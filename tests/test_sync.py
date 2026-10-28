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
import cleanup_virtual  # noqa: E402
import common  # noqa: E402
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
        self.deleted = []

    def _add_import(self, name):
        imp_id = len(self.imports) + 1
        self.imports.append({"id": imp_id, "name": name})
        return imp_id

    def get(self, url, headers=None, params=None, timeout=None):
        if url.endswith("/athlete/activities"):
            return FakeResponse(200, self.activities if params["page"] == 1 else [])
        if "/streams" in url:
            return FakeResponse(200, STREAMS)
        if url.endswith("/api/v1/imports"):
            return FakeResponse(200, self.imports, {"X-Total-Pages": "1"})
        if url.endswith("/api/v1/points"):
            ids = self.points.get(params["import_id"], [])
            return FakeResponse(200, [{"id": i} for i in ids],
                                {"X-Total-Pages": "1" if ids else "0"})
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url, headers=None, files=None, timeout=None, data=None):
        name, content, _ = files["file"]
        if self.upload_status != 201:
            return FakeResponse(self.upload_status, {"error": "boom"})
        self.uploads.append((name, content.decode()))
        imp_id = self._add_import(name)
        self.points[imp_id] = [imp_id * 100 + 1, imp_id * 100 + 2]
        return FakeResponse(201, {})

    def delete(self, url, headers=None, json=None, timeout=None):
        assert url.endswith("/api/v1/points/bulk_destroy")
        self.deleted.extend(json["point_ids"])
        return FakeResponse(200, {"count": len(json["point_ids"])})


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("STRAVA_CLIENT_ID", "1")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secret")
    monkeypatch.setenv("DAWARICH_URL", "https://dawarich.example.com/")
    monkeypatch.setenv("DAWARICH_API_KEY", "key")
    monkeypatch.delenv("SPORT_TYPES", raising=False)
    monkeypatch.delenv("DRY_RUN", raising=False)
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
