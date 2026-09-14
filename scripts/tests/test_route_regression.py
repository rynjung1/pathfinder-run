"""Formalizes the Uptown Waterloo regression case that's been manually
re-run by hand, repeatedly, throughout this project's sessions (most
recently to confirm the full-Ontario migration and the in_greenspace
removal each reproduced the same known-good route) -- an actual real
request against a live GraphHopper instance, not a mock.

Requires DEFAULT_GRAPHHOPPER_URL (localhost:8995) to be reachable. This
is the one test in the suite that isn't zero-I/O by design -- it's
formalizing a real end-to-end check, not a unit test -- so it's isolated
in its own file and skips cleanly (not a confusing failure/hang) when no
server is up, e.g. a fresh checkout or CI with no GraphHopper instance
provisioned.
"""
import urllib.error
import urllib.request

import pytest

from generate_loop import (
    DEFAULT_GRAPHHOPPER_URL,
    DEFAULT_REUSE_PENALTY_MULTIPLIER,
    build_candidate,
    get_long_way_ids,
)

# Uptown Waterloo, bearing 120 degrees, 3km target -- the exact case used
# throughout this project's manual regression checks (most recently: the
# full-Ontario migration, the in_greenspace removal, and now greenness's
# restoration via graphhopper-ext/'s static encoded value -- each
# confirmed against this same request). The expected distance has changed
# twice, legitimately, not as a regression each time:
#   3059.0299999999997 -- original baseline, live in_greenspace active
#   2988.2780000000002 -- after in_greenspace was removed (root cause of
#                          /route being unusably slow in flexible mode
#                          at province scale; see 4c8681d)
#   3068.4449999999997 -- current: greenness restored via a static
#                          "greenspace" encoded value baked in at import
#                          time (graphhopper-ext/), costing nothing per
#                          query -- verified CH speed mode stays engaged.
# Each change legitimately altered which path is cheapest; this isn't the
# same number recurring, it's the real effect of each change confirmed
# against the live server before updating the test.
START_LAT, START_LON = 43.4643, -80.5204
BEARING_DEG = 120.0
TARGET_DISTANCE_M = 3000
PROFILE = "foot"
EXPECTED_TOTAL_DISTANCE_M = 3068.4449999999997

# Short and separate from generate_loop.py's own request timeout
# (30s, sized for a real slow query) -- this is just a reachability
# probe, so it should fail fast on a clean checkout with nothing
# listening on the port, not hang for 30s per test collection.
HEALTH_CHECK_TIMEOUT_S = 2


def _graphhopper_is_reachable():
    try:
        with urllib.request.urlopen(
            f"{DEFAULT_GRAPHHOPPER_URL}/health", timeout=HEALTH_CHECK_TIMEOUT_S
        ) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


@pytest.fixture(scope="module")
def require_graphhopper():
    if not _graphhopper_is_reachable():
        pytest.skip(
            f"GraphHopper not reachable at {DEFAULT_GRAPHHOPPER_URL} -- "
            "start it with graphhopper/run-graphhopper.sh to run this test."
        )


def test_uptown_waterloo_bearing_120_3km_regression(require_graphhopper):
    long_way_ids = get_long_way_ids()
    candidate = build_candidate(
        DEFAULT_GRAPHHOPPER_URL,
        START_LAT,
        START_LON,
        BEARING_DEG,
        TARGET_DISTANCE_M,
        PROFILE,
        DEFAULT_REUSE_PENALTY_MULTIPLIER,
        long_way_ids=long_way_ids,
    )
    assert candidate is not None, "expected a candidate, got a rejected/degenerate result"
    assert candidate["total_distance"] == pytest.approx(EXPECTED_TOTAL_DISTANCE_M, abs=1e-6)
