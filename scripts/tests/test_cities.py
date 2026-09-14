"""Unit tests for cities.py's pure, zero-I/O point-in-polygon functions
(_point_in_ring, _point_in_geometry) -- the coverage check behind
resolve_city. Not testing resolve_city/_load_cities themselves here:
both do file I/O (reading data/boundaries/*.geojson), which is out of
scope for this pure-math suite.
"""
import pytest

from cities import _point_in_geometry, _point_in_ring

UNIT_SQUARE = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]


def test_point_in_ring_inside():
    assert _point_in_ring(5, 5, UNIT_SQUARE) is True


def test_point_in_ring_outside():
    assert _point_in_ring(15, 15, UNIT_SQUARE) is False
    assert _point_in_ring(-5, 5, UNIT_SQUARE) is False


def test_point_in_geometry_polygon():
    geometry = {"type": "Polygon", "coordinates": [UNIT_SQUARE]}
    assert _point_in_geometry(5, 5, geometry) is True
    assert _point_in_geometry(50, 50, geometry) is False


def test_point_in_geometry_multipolygon_checks_every_polygon():
    # A point inside the SECOND polygon of a MultiPolygon must still
    # match -- this is what `any(...)` in _point_in_geometry is for, and
    # a bug that only checked the first polygon would silently pass a
    # single-Polygon test but fail this one.
    far_square = [[100, 100], [110, 100], [110, 110], [100, 110], [100, 100]]
    geometry = {"type": "MultiPolygon", "coordinates": [[UNIT_SQUARE], [far_square]]}
    assert _point_in_geometry(105, 105, geometry) is True
    assert _point_in_geometry(5, 5, geometry) is True
    assert _point_in_geometry(50, 50, geometry) is False


def test_point_in_geometry_rejects_unsupported_type():
    geometry = {"type": "Point", "coordinates": [0, 0]}
    with pytest.raises(ValueError):
        _point_in_geometry(0, 0, geometry)
