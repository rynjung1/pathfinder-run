#!/usr/bin/env python3
"""
Pathfinder Run -- coverage registry, §0's "expand to additional cities."

History: this started as a genuine multi-instance dispatcher -- one
GraphHopper process per city (Waterloo Region on 8989, Guelph on 8991),
resolved by point-in-polygon against each city's own boundary. That was
the right call at the time (two small city extracts), but it doesn't
scale to "add a city" as a recurring operation: every new city meant
standing up, and forever running, another whole GraphHopper process.

Investigated before rebuilding this (see the full-Ontario feasibility
commits): one big instance covering the entire province is not just
possible but comfortably so on ordinary hardware -- a real, full-Ontario
import (137M raw nodes) completed in ~8 minutes and ~4GB peak memory,
with the real province-wide greenness data loaded, and the resulting
routes are byte-identical to what the old per-city instances produced
(confirmed directly: the Uptown Waterloo and Guelph regression cases both
came back exact matches from the single Ontario instance). So this module
is now a single GraphHopper URL plus ONE coverage check, not a dispatcher
choosing between several.

The coverage check itself didn't go away, and shouldn't: a request from
clearly outside Ontario (say, a user testing from another province) needs
a clean "no coverage here" 400, not a nonsense route computed against a
graph that was never built to cover that location, and not a confusing
low-level GraphHopper error. `resolve_city()` keeps the exact same
point-in-polygon shape as before -- now checked against
data/boundaries/ontario.geojson instead of a per-city boundary -- so
route_api.py needed zero changes beyond this file's own contents.

Adding a real second GraphHopper region again (a different country, say)
would mean going back to a real CITIES list with more than one entry --
this module still supports that shape, it just currently holds one.
"""
import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"

# A single entry today (Ontario), but still a list -- this module's shape
# already supports more than one region if that's ever needed again (e.g.
# a genuinely separate country/province graph), it's just not needed for
# "another city" anymore now that one instance covers the whole province.
CITIES = [
    {
        "name": "ontario",
        "boundary_path": DATA_DIR / "boundaries" / "ontario.geojson",
        "base_url": "http://localhost:8995",
        "long_ways_path": DATA_DIR / "long_ways_ontario.json",
    },
]


def _point_in_ring(x, y, ring):
    """Standard ray-casting point-in-polygon test -- the same technique
    (independently reimplemented, same short form) already used this
    session for the greenspace and long-way-buffer geometry work. Not
    worth a geometry library dependency for a coverage check against one
    (or a handful of) region polygon(s)."""
    n = len(ring)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-15) + xi):
            inside = not inside
        j = i
    return inside


def _point_in_geometry(lon, lat, geometry):
    """Handles both Polygon (rings, first is exterior) and MultiPolygon (a
    list of such ring-lists) -- Ontario's boundary happens to be a single
    Polygon (confirmed: 26,262-point outer ring, no holes), but this
    doesn't assume that stays true forever. Interior holes (a ring after
    the first) aren't checked -- Ontario's boundary doesn't have one."""
    if geometry["type"] == "Polygon":
        return _point_in_ring(lon, lat, geometry["coordinates"][0])
    if geometry["type"] == "MultiPolygon":
        return any(_point_in_ring(lon, lat, polygon[0]) for polygon in geometry["coordinates"])
    raise ValueError(f"unsupported boundary geometry type: {geometry['type']}")


_LOADED_CITIES = None


def _load_cities():
    """Reads each region's boundary GeoJSON once per process and caches
    the parsed geometry alongside its config -- these files don't change
    at runtime, no reason to re-parse per request. (Ontario's boundary is
    700KB of coordinates -- parsed once at first use, not per request.)"""
    global _LOADED_CITIES
    if _LOADED_CITIES is None:
        loaded = []
        for city in CITIES:
            with open(city["boundary_path"]) as f:
                boundary = json.load(f)
            geometry = boundary["features"][0]["geometry"]
            loaded.append({**city, "geometry": geometry})
        _LOADED_CITIES = loaded
    return _LOADED_CITIES


def resolve_city(lat, lon):
    """Which region's GraphHopper instance a (lat, lon) belongs to.
    Returns the matching entry ({"name", "boundary_path", "base_url",
    "long_ways_path"}) or None if the point falls outside every known
    region's boundary -- the caller should surface that as "no coverage
    here," not silently fall back to a default instance (a point outside
    every boundary has no graph that actually covers it; guessing one
    would just produce a confusing wrong-region routing failure instead
    of a clear one)."""
    for city in _load_cities():
        if _point_in_geometry(lon, lat, city["geometry"]):
            return city
    return None
