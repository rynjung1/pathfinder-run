"""Unit tests for generate_loop.py's pure, zero-I/O geometry/math functions.

Scope, deliberately: only functions with no network/DB dependency --
everything here runs offline, no GraphHopper instance required. See
test_route_regression.py for the one live-server case. Expected values
are hand-derived from the same well-known spherical/equirectangular
geodesy facts these functions implement (see each test's comment for the
derivation), not reverse-engineered from the implementation, except
where noted.
"""
import math

import pytest

from generate_loop import (
    EARTH_RADIUS_M,
    _local_meters_per_degree,
    build_way_buffer_polygon,
    combine_legs,
    compactness_score,
    destination_point,
    polygon_area_m2,
    used_way_ids,
    used_way_segments,
)


# --- destination_point ------------------------------------------------

def test_destination_point_zero_distance_returns_same_point():
    # For distance_m=0, delta=0 regardless of bearing: sin(delta)=0,
    # cos(delta)=1, which algebraically collapses the formula back to
    # (lat1, lon1) for any bearing (away from the poles) -- not just the
    # cardinal ones below, so this uses a deliberately non-cardinal
    # bearing (37 degrees) to confirm that.
    lat, lon = 43.4643, -80.5204
    result_lat, result_lon = destination_point(lat, lon, 37.0, 0.0)
    assert result_lat == pytest.approx(lat, abs=1e-9)
    assert result_lon == pytest.approx(lon, abs=1e-9)


def test_destination_point_north_from_equator():
    # Due north (bearing 0) from the equator: the general spherical
    # formula collapses exactly to lat2 = lat1 + delta, lon2 = lon1,
    # where delta = distance_m / EARTH_RADIUS_M (in radians) -- no small-
    # distance approximation needed, this is exact.
    distance_m = 10_000.0
    delta_deg = math.degrees(distance_m / EARTH_RADIUS_M)
    lat, lon = destination_point(0.0, 0.0, 0.0, distance_m)
    assert lat == pytest.approx(delta_deg, abs=1e-9)
    assert lon == pytest.approx(0.0, abs=1e-9)


def test_destination_point_east_from_equator():
    # Due east (bearing 90) from the equator is itself a great circle
    # (the equator), so this also collapses exactly: lat2 = lat1 = 0,
    # lon2 = lon1 + delta.
    distance_m = 10_000.0
    delta_deg = math.degrees(distance_m / EARTH_RADIUS_M)
    lat, lon = destination_point(0.0, 0.0, 90.0, distance_m)
    assert lat == pytest.approx(0.0, abs=1e-9)
    assert lon == pytest.approx(delta_deg, abs=1e-9)


# --- _local_meters_per_degree ------------------------------------------

def test_local_meters_per_degree_equal_at_equator():
    # cos(0) == 1, so the longitude scale factor equals the latitude one
    # exactly at the equator.
    m_per_deg_lat, m_per_deg_lon = _local_meters_per_degree(0.0)
    assert m_per_deg_lon == pytest.approx(m_per_deg_lat, abs=1e-9)
    assert m_per_deg_lat == pytest.approx(EARTH_RADIUS_M * math.pi / 180, abs=1e-6)


def test_local_meters_per_degree_scales_by_cosine_at_60():
    # cos(60 degrees) == 0.5 exactly -- a clean, hand-checkable ratio.
    m_per_deg_lat, m_per_deg_lon = _local_meters_per_degree(60.0)
    assert m_per_deg_lon == pytest.approx(m_per_deg_lat * 0.5, rel=1e-9)


# --- polygon_area_m2 -----------------------------------------------------

def test_polygon_area_m2_unit_square_at_equator():
    # A square in lon/lat space at the equator, where the equirectangular
    # projection's lat and lon scale factors are identical (cos(0)=1), so
    # the area is just (side_deg * m_per_deg)^2 -- computed here from the
    # same well-known "meters per degree of latitude on a sphere" fact
    # (arc length = radius * angle), independent of calling the module's
    # own _local_meters_per_degree helper.
    side_deg = 0.01
    m_per_deg = EARTH_RADIUS_M * math.pi / 180
    side_m = side_deg * m_per_deg
    ring = [[0, 0], [side_deg, 0], [side_deg, side_deg], [0, side_deg], [0, 0]]
    assert polygon_area_m2(ring) == pytest.approx(side_m ** 2, rel=1e-6)


def test_polygon_area_m2_self_retraced_line_is_zero():
    # An out-and-back "ring" -- e.g. a return leg that exactly retraces
    # the outbound leg -- encloses exactly zero area under the shoelace
    # formula (every cross term cancels), not just approximately.
    ring = [[0, 0], [0, 0.01], [0, 0]]
    assert polygon_area_m2(ring) == 0


# --- compactness_score ---------------------------------------------------

def _circle_ring(center_lat, center_lon, radius_m, n=360):
    """A regular n-gon inscribed in a circle of the given radius, in
    lon/lat space -- independently derived (not via the module's own
    helpers) from the same equirectangular relation they use, to build a
    known geometric shape rather than reuse the implementation under
    test."""
    m_per_deg_lat = EARTH_RADIUS_M * math.pi / 180
    m_per_deg_lon = m_per_deg_lat * math.cos(math.radians(center_lat))
    points = []
    for i in range(n + 1):  # +1 to close the ring
        angle = 2 * math.pi * i / n
        lat = center_lat + (radius_m * math.sin(angle)) / m_per_deg_lat
        lon = center_lon + (radius_m * math.cos(angle)) / m_per_deg_lon
        points.append([lon, lat])
    return points


def test_compactness_score_circle_is_near_one():
    # Polsby-Popper compactness of a circle should be ~1.0 (the docstring's
    # own claim). Uses the true continuous-circle perimeter (2*pi*r) --
    # what an actual circular route's real-world distance would be, not
    # the slightly-shorter chord-summed polygon perimeter -- and a fine
    # (360-point) polygon approximation of the circle's area, which for a
    # regular n-gon inscribed in radius r is (n/2)*r^2*sin(2*pi/n): at
    # n=360 that's already >99.99% of pi*r^2, so this comes out
    # comfortably close to 1.0 without relying on the implementation to
    # get there.
    radius_m = 500.0
    ring = _circle_ring(43.4643, -80.5204, radius_m, n=360)
    perimeter_m = 2 * math.pi * radius_m
    score = compactness_score(ring, perimeter_m)
    assert score == pytest.approx(1.0, abs=0.001)


def test_compactness_score_degenerate_line_is_near_zero():
    # A there-and-back straight line encloses zero area (see
    # test_polygon_area_m2_self_retraced_line_is_zero above), so its
    # compactness is exactly 0 regardless of how long the "perimeter" is.
    ring = [[0, 0], [0, 0.01], [0, 0]]
    assert compactness_score(ring, 2000.0) == 0


def test_compactness_score_nonpositive_perimeter_is_zero():
    # The function's own explicit guard -- a zero or negative perimeter
    # can't be divided by, so it short-circuits to 0 rather than raising
    # or returning inf/nan.
    ring = [[0, 0], [0.01, 0], [0.01, 0.01], [0, 0.01], [0, 0]]
    assert compactness_score(ring, 0) == 0
    assert compactness_score(ring, -5) == 0


# --- combine_legs ----------------------------------------------------------

def test_combine_legs_dedupes_shared_far_point():
    outbound = {"points": {"coordinates": [[0, 0], [1, 1], [2, 2]]}}
    return_leg = {"points": {"coordinates": [[2, 2], [3, 3]]}}
    assert combine_legs(outbound, return_leg) == [[0, 0], [1, 1], [2, 2], [3, 3]]


# --- used_way_ids / used_way_segments ---------------------------------------

def test_used_way_ids_returns_sorted_distinct_ids():
    path = {"details": {"osm_way_id": [[0, 3, 101], [3, 5, 55], [5, 7, 101]]}}
    assert used_way_ids(path) == [55, 101]


def test_used_way_segments_groups_coords_by_way_id():
    path = {
        "points": {"coordinates": [[0, 0], [1, 1], [2, 2], [3, 3], [4, 4]]},
        "details": {"osm_way_id": [[0, 2, 101], [2, 4, 55]]},
    }
    assert used_way_segments(path) == {
        101: [[0, 0], [1, 1], [2, 2]],
        55: [[2, 2], [3, 3], [4, 4]],
    }


def test_used_way_segments_concatenates_noncontiguous_ranges():
    # Per the function's own docstring: a way touched twice, in two
    # separate (non-contiguous) index ranges, has both ranges'
    # coordinates concatenated under its one id.
    path = {
        "points": {"coordinates": [[0, 0], [1, 1], [2, 2], [3, 3], [4, 4], [5, 5]]},
        "details": {"osm_way_id": [[0, 1, 101], [1, 3, 55], [3, 5, 101]]},
    }
    result = used_way_segments(path)
    assert result[55] == [[1, 1], [2, 2], [3, 3]]
    assert result[101] == [[0, 0], [1, 1], [3, 3], [4, 4], [5, 5]]


# --- build_way_buffer_polygon -----------------------------------------------

def test_build_way_buffer_polygon_straight_segment_dimensions():
    # A single due-east segment along the equator, buffered width_m=10 --
    # hand-computable because at the equator the lon/lat meter scale
    # factors are equal (cos(0)=1), so the perpendicular offset is a pure
    # latitude shift.
    m_per_deg = EARTH_RADIUS_M * math.pi / 180
    half_width_deg = 5.0 / m_per_deg
    coords = [[0, 0], [0.01, 0]]
    result = build_way_buffer_polygon(coords, width_m=10.0)

    assert result["type"] == "MultiPolygon"
    assert len(result["coordinates"]) == 1  # one segment -> one rectangle
    ring = result["coordinates"][0][0]
    expected_ring = [
        [0, half_width_deg],
        [0.01, half_width_deg],
        [0.01, -half_width_deg],
        [0, -half_width_deg],
        [0, half_width_deg],
    ]
    assert len(ring) == len(expected_ring)
    for (actual_lon, actual_lat), (expected_lon, expected_lat) in zip(ring, expected_ring):
        assert actual_lon == pytest.approx(expected_lon, abs=1e-9)
        assert actual_lat == pytest.approx(expected_lat, abs=1e-9)


def test_build_way_buffer_polygon_skips_zero_length_segments():
    # A duplicate consecutive point has no direction to buffer -- the
    # function's own explicit skip, not an error.
    coords = [[0, 0], [0, 0], [0.01, 0]]
    result = build_way_buffer_polygon(coords, width_m=10.0)
    assert len(result["coordinates"]) == 1  # only the one real segment
