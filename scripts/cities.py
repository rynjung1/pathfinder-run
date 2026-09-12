#!/usr/bin/env python3
"""
Pathfinder Run -- multi-city registry, §0's "expand to additional cities."

Investigated before implementing (see the Guelph-expansion commit's
message): one GraphHopper process holds exactly one imported graph, so
supporting a second city means a second, wholly separate GraphHopper
instance (config-guelph.yml, its own port, its own graph-cache) running
alongside the first -- not a multi-graph trick inside one process.

That leaves route_api.py needing to know which instance to query for a
given request. This resolves that by point-in-polygon against each city's
real administrative boundary (already on hand from the boundary-fetch step
that produced data/boundaries/*.geojson), not an explicit city parameter
from the client. Reasoning: the mobile client's contract today is "send
your current lat/lon, get a route back" -- no city concept exists on the
client side, and adding one would mean UI work (a picker, or the client
doing its own boundary math) to solve a problem the server can solve
transparently instead. A third city added later needs one new entry in
CITIES below and zero client changes.

Known, deliberately out-of-scope-for-now limitation: closures.py's
get_active_closure_way_ids() returns every active closure regardless of
which city reported it, and every one gets folded into the OR-chain
condition for every request, including ones for a different city's
GraphHopper instance. This is NOT a correctness bug -- OSM way ids are
globally unique, so a Waterloo closure's way_id will simply never match
any edge in Guelph's own graph, the condition is just always-false noise
for that instance -- but it doesn't scale cleanly: the OR-chain grows
with total closures across every city, not just the relevant one. Not
fixed here (deliberately) since it isn't a real problem yet at this
scale; the fix, if it's ever needed, is a `city` column on the closures
table, filtered by resolve_city()'s result.
"""
import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"

# Adding a city: append one entry here (boundary file, the GraphHopper
# instance serving it, and its own long-way audit -- see
# generate_loop.py's get_long_way_ids/audit_long_ways.py). Nothing else in
# this module needs to change.
CITIES = [
    {
        "name": "waterloo-region",
        "boundary_path": DATA_DIR / "boundaries" / "region-of-waterloo.geojson",
        "base_url": "http://localhost:8989",
        "long_ways_path": DATA_DIR / "long_ways.json",
    },
    {
        "name": "guelph",
        "boundary_path": DATA_DIR / "boundaries" / "guelph.geojson",
        "base_url": "http://localhost:8991",
        "long_ways_path": DATA_DIR / "long_ways_guelph.json",
    },
]


def _point_in_ring(x, y, ring):
    """Standard ray-casting point-in-polygon test -- the same technique
    (independently reimplemented, same short form) already used this
    session for the greenspace and long-way-buffer geometry work. Not
    worth a geometry library dependency for point checks against a
    handful of city polygons."""
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
    """Handles both Polygon (rings, first is exterior) and MultiPolygon
    (a list of such ring-lists) -- Guelph's boundary happens to be a
    single Polygon, Waterloo Region's too, but this doesn't assume that
    stays true for every future city. Interior holes (a ring after the
    first) aren't checked -- no city boundary used here has one."""
    if geometry["type"] == "Polygon":
        return _point_in_ring(lon, lat, geometry["coordinates"][0])
    if geometry["type"] == "MultiPolygon":
        return any(_point_in_ring(lon, lat, polygon[0]) for polygon in geometry["coordinates"])
    raise ValueError(f"unsupported boundary geometry type: {geometry['type']}")


_LOADED_CITIES = None


def _load_cities():
    """Reads each city's boundary GeoJSON once per process and caches the
    parsed geometry alongside its config -- these files don't change at
    runtime, no reason to re-parse per request."""
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
    """Which city's GraphHopper instance a (lat, lon) belongs to. Returns
    the matching city dict ({"name", "boundary_path", "base_url", ...})
    or None if the point falls outside every known city's boundary -- the
    caller should surface that as "no coverage here," not silently fall
    back to a default instance (a point outside every boundary has no
    graph that actually covers it; guessing one would just produce a
    confusing wrong-city routing failure instead of a clear one)."""
    for city in _load_cities():
        if _point_in_geometry(lon, lat, city["geometry"]):
            return city
    return None
