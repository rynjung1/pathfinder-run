"""Unit tests for generate_loop.py's candidate scoring (score_candidate)
and its API-response formatting (candidate_to_feature/
candidates_to_geojson) -- found untested anywhere on a backend sweep.
Both are pure, zero-I/O functions (no network/GraphHopper/DB needed,
same category as test_geometry.py), but score_candidate is the actual
ranking logic that decides which candidate a user sees as "best," and
candidates_to_geojson is the literal shape of every /route response the
mobile client parses -- a bug in either would only otherwise have
surfaced via test_route_regression.py's live-server case (skipped in CI
without a real GraphHopper instance) or a real device.
"""
import pytest

from generate_loop import candidate_to_feature, candidates_to_geojson, score_candidate


def _fake_candidate(total_distance=5000.0, outbound_ways=None, reused=None, coords=None,
                     bearing=0.0, total_time=3000000, radius_iterations=1):
    # A minimal square loop -- real enough for compactness_score to
    # return a sensible value, not a degenerate line.
    if coords is None:
        coords = [[-80.52, 43.46], [-80.52, 43.47], [-80.51, 43.47], [-80.51, 43.46], [-80.52, 43.46]]
    return {
        "total_distance": total_distance,
        "total_time": total_time,
        "outbound_ways": outbound_ways if outbound_ways is not None else [1, 2, 3, 4],
        "reused": reused if reused is not None else [],
        "coords": coords,
        "bearing": bearing,
        "radius_iterations": radius_iterations,
    }


# --- score_candidate ----------------------------------------------------

def test_score_candidate_exact_distance_and_no_reuse_scores_only_the_shape_penalty():
    candidate = _fake_candidate(total_distance=5000.0)
    score, distance_error_pct, reuse_pct, compactness = score_candidate(candidate, target_distance=5000.0)

    assert distance_error_pct == 0.0
    assert reuse_pct == 0.0
    # Score is purely the shape penalty here (both other terms are zero)
    assert score == (1 - compactness) * 20  # DEFAULT_COMPACTNESS_WEIGHT


def test_score_candidate_distance_error_pct_is_computed_correctly():
    candidate = _fake_candidate(total_distance=5500.0)
    _, distance_error_pct, _, _ = score_candidate(candidate, target_distance=5000.0)
    # |5500 - 5000| / 5000 * 100 = 10%
    assert distance_error_pct == pytest.approx(10.0)


def test_score_candidate_reuse_pct_is_reused_over_outbound_ways():
    candidate = _fake_candidate(outbound_ways=[1, 2, 3, 4], reused=[1, 2])
    _, _, reuse_pct, _ = score_candidate(candidate, target_distance=5000.0)
    # 2 reused / 4 outbound = 50%
    assert reuse_pct == pytest.approx(50.0)


def test_score_candidate_empty_outbound_ways_does_not_divide_by_zero():
    candidate = _fake_candidate(outbound_ways=[], reused=[])
    # Must not raise ZeroDivisionError -- a degenerate candidate
    # (build_candidate would normally reject this before it ever reaches
    # scoring, but this function shouldn't crash if it somehow does).
    _, _, reuse_pct, _ = score_candidate(candidate, target_distance=5000.0)
    assert reuse_pct == 0


def test_score_candidate_lower_reuse_scores_better_than_higher_reuse():
    # Same distance/shape, only reuse differs -- confirms the ordering
    # this whole scoring system exists for: a route that doubles back on
    # itself more should score worse (score_candidate's docstring: "lower
    # is better").
    clean = _fake_candidate(outbound_ways=[1, 2, 3, 4], reused=[])
    zigzag = _fake_candidate(outbound_ways=[1, 2, 3, 4], reused=[1, 2, 3])
    clean_score, _, _, _ = score_candidate(clean, target_distance=5000.0)
    zigzag_score, _, _, _ = score_candidate(zigzag, target_distance=5000.0)
    assert clean_score < zigzag_score


# --- candidate_to_feature / candidates_to_geojson ------------------------

def test_candidate_to_feature_shape_is_real_geojson():
    candidate = _fake_candidate()
    candidate["score"] = 12.5
    candidate["compactness"] = 0.8

    feature = candidate_to_feature(candidate, start_lat=43.4643, start_lon=-80.5204,
                                    target_distance_m=5000.0, rank=1)

    assert feature["type"] == "Feature"
    assert feature["geometry"]["type"] == "LineString"
    assert feature["geometry"]["coordinates"] == candidate["coords"]
    assert feature["properties"]["rank"] == 1
    assert feature["properties"]["start"] == [-80.5204, 43.4643]  # [lon, lat], GeoJSON order
    assert feature["properties"]["actual_distance_m"] == candidate["total_distance"]
    assert feature["properties"]["target_distance_m"] == 5000.0
    assert feature["properties"]["outbound_way_count"] == len(candidate["outbound_ways"])
    assert feature["properties"]["reused_way_count"] == len(candidate["reused"])


def test_candidates_to_geojson_is_a_feature_collection_ranked_in_input_order():
    # candidates_to_geojson trusts its input is already sorted best-first
    # (score_candidate/generate_candidates' own contract -- see that
    # function's docstring) and just numbers them 1..N; this confirms it
    # does exactly that and nothing more (no re-sorting of its own that
    # could silently mask a caller passing unsorted candidates).
    best = _fake_candidate(total_distance=5000.0)
    best["score"] = 1.0
    best["compactness"] = 0.9
    worst = _fake_candidate(total_distance=5800.0)
    worst["score"] = 50.0
    worst["compactness"] = 0.1

    geojson = candidates_to_geojson([best, worst], start_lat=43.4643, start_lon=-80.5204,
                                     target_distance_m=5000.0)

    assert geojson["type"] == "FeatureCollection"
    assert len(geojson["features"]) == 2
    assert geojson["features"][0]["properties"]["rank"] == 1
    assert geojson["features"][0]["properties"]["actual_distance_m"] == 5000.0
    assert geojson["features"][1]["properties"]["rank"] == 2
    assert geojson["features"][1]["properties"]["actual_distance_m"] == 5800.0


def test_candidates_to_geojson_empty_list_is_a_valid_empty_feature_collection():
    geojson = candidates_to_geojson([], start_lat=43.4643, start_lon=-80.5204, target_distance_m=5000.0)
    assert geojson == {"type": "FeatureCollection", "features": []}
