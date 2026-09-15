"""Tests for route_api.py's own Flask/HTTP layer -- auth, input
validation, and /health. Found genuinely missing entirely on a backend
sweep: test_route_regression.py exercises the real generated-route
behavior end to end against a live GraphHopper instance, and
test_runs.py/test_cities.py cover the storage/coverage modules route_api
calls into, but nothing anywhere tested route_api.py's own endpoint
layer -- the actual auth check, the actual input validation, the actual
/health response -- in isolation.

PATHFINDER_API_KEY is set here, before importing route_api, rather than
relying on a local scripts/.env: route_api.py raises at import time if
it's unset (deliberately, see that file's own comment), and CI (see
.github/workflows/test.yml) has no .env and no such secret -- a test file
that only worked because the developer's own machine happens to have a
real key would pass locally and fail in CI the first time this runs
there. A fixed test-only value, not whatever's really configured, also
keeps these tests fully independent of local dev state.

DB access (closures.db/runs.db) is not mocked -- route_api.py calls
ensure_schema()/ensure_runs_schema() as import-time side effects using
their real default paths (both idempotent CREATE TABLE IF NOT EXISTS,
so this is harmless, if not ideal -- a real architectural constraint of
route_api.py's current module-level app object, not something this test
file works around). What IS mocked, per test, is the specific thing that
test needs: urllib.request.urlopen for the two /health cases. Every
/route test below only exercises the auth/validation layer, which runs
and returns before the view function ever reaches a real GraphHopper or
DB call -- so those need no mocking to stay fast and hermetic.
"""
import os
import urllib.error

os.environ["PATHFINDER_API_KEY"] = "test-key-for-route-api-tests"

import pytest

import route_api

API_KEY = "test-key-for-route-api-tests"


@pytest.fixture
def client():
    route_api.app.config["TESTING"] = True
    return route_api.app.test_client()


def test_health_reports_ok_when_graphhopper_is_reachable(client, monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(route_api.urllib.request, "urlopen", lambda *a, **kw: FakeResponse())

    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_health_reports_degraded_with_503_when_graphhopper_is_unreachable(client, monkeypatch):
    def raise_unreachable(*args, **kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(route_api.urllib.request, "urlopen", raise_unreachable)

    response = client.get("/health")
    assert response.status_code == 503
    body = response.get_json()
    assert body["status"] == "degraded"
    assert "ontario" in body["unreachable_graphhopper_regions"]


def test_route_without_api_key_is_rejected(client):
    response = client.post("/route", json={"lat": 43.4643, "lon": -80.5204, "distance": 5000})
    assert response.status_code == 401


def test_route_with_wrong_api_key_is_rejected(client):
    response = client.post(
        "/route",
        json={"lat": 43.4643, "lon": -80.5204, "distance": 5000},
        headers={"X-API-Key": "not-the-real-key"},
    )
    assert response.status_code == 401


def test_route_with_missing_fields_is_a_400_not_a_500(client):
    response = client.post("/route", json={"lat": 43.4643}, headers={"X-API-Key": API_KEY})
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_route_with_distance_over_the_cap_is_rejected(client):
    response = client.post(
        "/route",
        json={"lat": 43.4643, "lon": -80.5204, "distance": route_api.MAX_DISTANCE_M + 1},
        headers={"X-API-Key": API_KEY},
    )
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_route_with_non_positive_distance_is_rejected(client):
    response = client.post(
        "/route",
        json={"lat": 43.4643, "lon": -80.5204, "distance": 0},
        headers={"X-API-Key": API_KEY},
    )
    assert response.status_code == 400


# Everything below covers /closures and /runs -- only /route and /health
# had any coverage until now, found on a follow-up sweep of this same
# file. All of these hit real auth/validation code that returns before
# ever touching a database or GraphHopper (confirmed by reading each
# view function itself, same as /route's cases above), so none need the
# DB/GraphHopper mocking a success-path test for these would.


def test_closures_post_without_api_key_is_rejected(client):
    response = client.post("/closures", json={"lat": 43.4643, "lon": -80.5204})
    assert response.status_code == 401


def test_closures_post_with_missing_fields_is_a_400(client):
    response = client.post("/closures", json={"lat": 43.4643}, headers={"X-API-Key": API_KEY})
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_closures_patch_without_api_key_is_rejected(client):
    response = client.patch("/closures/1", json={"resolve_token": "abc"})
    assert response.status_code == 401


def test_closures_patch_without_resolve_token_is_a_400(client):
    response = client.patch("/closures/1", json={}, headers={"X-API-Key": API_KEY})
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_runs_post_without_api_key_is_rejected(client):
    response = client.post("/runs", json={"deviceId": "d", "runUuid": "u"})
    assert response.status_code == 401


def test_runs_post_with_missing_device_id_is_a_400(client):
    response = client.post("/runs", json={"runUuid": "u"}, headers={"X-API-Key": API_KEY})
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_runs_post_with_device_id_over_the_length_cap_is_rejected(client):
    response = client.post(
        "/runs",
        json={"deviceId": "d" * (route_api.MAX_DEVICE_ID_LEN + 1), "runUuid": "u"},
        headers={"X-API-Key": API_KEY},
    )
    assert response.status_code == 400


def test_runs_post_with_too_many_trace_points_is_rejected(client):
    response = client.post(
        "/runs",
        json={
            "deviceId": "d",
            "runUuid": "u",
            "startedAt": "2026-01-01T00:00:00Z",
            "targetDistanceM": 5000,
            "actualDistanceM": 4900,
            "durationMs": 1000,
            "trace": [{"latitude": 0, "longitude": 0}] * (route_api.MAX_TRACE_POINTS + 1),
        },
        headers={"X-API-Key": API_KEY},
    )
    assert response.status_code == 400


def test_runs_get_without_api_key_is_rejected(client):
    response = client.get("/runs?deviceId=d")
    assert response.status_code == 401


def test_runs_get_without_device_id_is_a_400(client):
    response = client.get("/runs", headers={"X-API-Key": API_KEY})
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_runs_delete_without_api_key_is_rejected(client):
    response = client.delete("/runs", json={"deviceId": "d"})
    assert response.status_code == 401


def test_runs_delete_without_device_id_is_a_400(client):
    response = client.delete("/runs", json={}, headers={"X-API-Key": API_KEY})
    assert response.status_code == 400
    assert "error" in response.get_json()
