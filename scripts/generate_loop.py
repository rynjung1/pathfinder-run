#!/usr/bin/env python3
"""
Pathfinder Run -- v1 loop generation, §5 points 2-4: several out-and-back
candidates (one per bearing), each with the edge-reuse penalty applied to
its return leg, scored on distance accuracy + edge-reuse + shape and the
best one kept.

Scope, deliberately narrow:
- Candidates vary only by outbound bearing, spread evenly starting from
  --bearing. No park-proximity/"greenness" scoring (§5 point 1's second
  bullet) -- deferred, pending the custom_areas park-polygon extraction
  work, along with the closures layer (both correctly v2+ per the doc).
- Keeps and reports the top --top-n candidates by score (default 3, per §5
  point 4's "return 2-3 alternatives"), not just the single best. This
  script only prints the comparison and writes them as a GeoJSON
  FeatureCollection -- actually serving alternatives to a caller is a
  mobile-client-facing concern for later.
- Each bearing's far-point radius is refined, not fixed: starting from
  target_distance / 2, it's rescaled by (target / actual) after each
  attempt and re-requested, up to --max-radius-iterations times or until
  within --distance-tolerance-pct of the target. Straight-line radius vs.
  actual walked distance isn't linear (street detour varies by direction
  and local street grid), so this is a simple iterative correction, not a
  closed-form fix -- it can still land outside tolerance within the
  iteration cap, particularly on a bearing that hits a real detour-forcing
  obstacle (river, highway, dead-end trail). The rescale step is clamped
  to [0.4x, 2.5x] per iteration, and any attempt landing outside
  [--min-distance-fraction, --max-distance-fraction] of target is rejected
  as degenerate rather than kept as a bad-but-real candidate -- see
  build_candidate's docstring; found via testing near the clipped region's
  boundary (New Hamburg, Elmira), where GraphHopper can snap both ends of
  a route to the same node (a 200 OK with near-zero distance) or force a
  huge real detour in a sparse rural network (a 200 OK many times over
  target), neither of which is a usable "loop."
- Scoring includes a shape term (compactness -- see compactness_score's
  docstring) alongside distance accuracy and edge-reuse, specifically
  because the first two alone let a real sawtooth zigzag numerically
  outscore a genuinely clean loop (confirmed on the Uptown Waterloo 3km
  case in the validation session -- see score_candidate's docstring for
  the weight and reasoning).
- The penalty is applied at the OSM-way level (see
  docs/running-app-architecture.md §5 and the investigation earlier in this
  session): the outbound leg is requested with `details=osm_way_id`, and
  every distinct way it touches is penalized on the return leg via a
  per-request custom_model OR-chain (`osm_way_id == X || osm_way_id == Y || ...`),
  merged on top of the profile's base pathfinder_foot.json weighting (a
  per-request custom_model merges with the base model, verified empirically
  against the running server -- it does not replace it).
- This requires two separate GraphHopper requests (outbound, then return),
  not one 3-point request as in the previous version of this script --
  each leg needs a different custom_model, and GraphHopper only accepts one
  per request.
- Penalizing a per-request custom_model requires disabling CH (speed mode)
  for that request (`ch.disable: true`) -- GraphHopper rejects a custom_model
  on a CH-prepared profile outright. Confirmed this measures at ~single-digit
  to tens of milliseconds of extra latency at this graph's size, not a
  meaningful cost yet.

Known, unsolved-by-design risk (flagged, not fixed here): penalizing by
osm_way_id blanket-penalizes the ENTIRE way, not just the segment actually
used. A handful of long trail/rail-trail ways in this extract exceed 1km
(worst case ~6.9km) -- see the way-length audit in this session's notes.
Touching 200m of a 6.9km trail would currently make the whole trail
expensive for the return leg. Not addressed by this script.

Usage:
    python3 scripts/generate_loop.py --lat 43.4643 --lon -80.5204 --distance 5000
    python3 scripts/generate_loop.py --lat 43.4643 --lon -80.5204 --distance 5000 \
        --candidates 8 --bearing 0 --output loop.geojson
"""
import argparse
import json
import math
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

EARTH_RADIUS_M = 6371000
# The full-Ontario instance's own audit (see audit_long_ways.py), not
# Waterloo Region's data/long_ways.json -- kept as this module's default
# so the CLI's standalone usage (no --graphhopper-url override) stays
# internally consistent with DEFAULT_GRAPHHOPPER_URL below, which now
# points at the same Ontario instance, not a leftover Waterloo-only
# default. route_api.py doesn't use this default at all -- it always
# resolves and passes the correct path via cities.py.
LONG_WAYS_PATH = Path(__file__).parent.parent / "data" / "long_ways_ontario.json"
DEFAULT_WAY_BUFFER_WIDTH_M = 15  # ad hoc, revisit -- see build_way_buffer_polygon's docstring
# Port 8995, the full-Ontario production instance (§0) -- NOT 8989, which
# was Waterloo Region's dedicated port before that instance was
# decommissioned in favor of the single province-wide graph. Nothing
# listens on 8989 anymore.
DEFAULT_GRAPHHOPPER_URL = "http://localhost:8995"
DEFAULT_REUSE_PENALTY_MULTIPLIER = 0.01  # how much cheaper an unused way is vs a reused one
DEFAULT_MAX_RADIUS_ITERATIONS = 4
DEFAULT_DISTANCE_TOLERANCE_PCT = 5.0  # matches docs/running-app-architecture.md §5 point 3
DEFAULT_MIN_DISTANCE_FRACTION = 0.30  # below this fraction of target, treat as a broken/degenerate
                                       # route (e.g. GraphHopper snapped both ends to the same node
                                       # near a graph boundary, or a single-way straight there-and-back
                                       # with no real loop shape at all), not just a bad-but-real
                                       # candidate. 0.25 was tried first and was too lenient -- a real
                                       # observed case (New Hamburg, single-way 829m for a 3000m
                                       # target = 27.6%) slipped through it; 0.30 catches it.
DEFAULT_MAX_DISTANCE_FRACTION = 2.0    # above this multiple of target, also treat as degenerate --
                                       # symmetric to DEFAULT_MIN_DISTANCE_FRACTION. Confirmed real:
                                       # a sparse rural network (New Hamburg @ 8km) let the radius
                                       # rescale diverge to candidates at 23281m/36397m for an 8000m
                                       # target instead of converging.
DEFAULT_COMPACTNESS_WEIGHT = 20  # ad hoc weight, revisit later -- see score_candidate's docstring
DEFAULT_CLOSURE_MULTIPLIER = 0  # hard exclusion, NOT the edge-reuse's 0.01 -- see fetch_return_leg's
                                 # docstring. Verified empirically against the running server: 0.01
                                 # only discourages (a way can still get used if the detour around it
                                 # costs more than the 99%-discounted-but-nonzero penalty -- observed
                                 # directly on a real way that stayed in the route at multiply_by=0.01
                                 # but was fully routed around once dropped to 0). A closure is a fact
                                 # ("this is not passable"), not a preference, so it needs the stronger
                                 # guarantee: multiply_by=0 makes the edge's weight infinite, so
                                 # GraphHopper only ever uses it if literally no other path exists --
                                 # in which case the request fails with no route rather than silently
                                 # routing a runner through a closure.


def destination_point(lat, lon, bearing_deg, distance_m):
    """Given a start point, bearing, and distance, return the destination
    point using the standard spherical-earth destination formula. Good
    enough for picking a rough waypoint -- not used for anything precise."""
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    theta = math.radians(bearing_deg)
    delta = distance_m / EARTH_RADIUS_M

    lat2 = math.asin(
        math.sin(lat1) * math.cos(delta)
        + math.cos(lat1) * math.sin(delta) * math.cos(theta)
    )
    lon2 = lon1 + math.atan2(
        math.sin(theta) * math.sin(delta) * math.cos(lat1),
        math.cos(delta) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), math.degrees(lon2)


def _request(url, body=None, method="GET"):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        parsed = json.loads(resp.read())
    if "paths" not in parsed:
        raise RuntimeError(f"GraphHopper returned no path: {json.dumps(parsed)}")
    return parsed["paths"][0]


def fetch_outbound_leg(base_url, start_lat, start_lon, far_lat, far_lon, profile,
                        closed_way_ids=None, closure_multiplier=DEFAULT_CLOSURE_MULTIPLIER):
    """Route out, no edge-reuse penalty (there's nothing to reuse yet) --
    also requests osm_way_id details so we know which ways to penalize on
    the way back.

    Closures DO apply here, unlike edge-reuse: a real closure blocks a way
    in both directions, so the outbound leg needs to avoid it too, not just
    the return leg. When closed_way_ids is empty (the common case -- no
    active closures), this stays the original plain CH-speed-mode GET, so
    the no-closures path pays no extra latency. Only switches to a
    ch.disable POST with a custom_model when there's actually something to
    avoid."""
    if not closed_way_ids:
        params = [
            ("point", f"{start_lat},{start_lon}"),
            ("point", f"{far_lat},{far_lon}"),
            ("profile", profile),
            ("points_encoded", "false"),
            ("details", "osm_way_id"),
        ]
        url = f"{base_url}/route?" + urllib.parse.urlencode(params)
        return _request(url)

    condition = " || ".join(f"osm_way_id == {way_id}" for way_id in closed_way_ids)
    body = {
        "points": [[start_lon, start_lat], [far_lon, far_lat]],
        "profile": profile,
        "points_encoded": False,
        "details": ["osm_way_id"],
        "ch.disable": True,
        "custom_model": {
            "priority": [{"if": condition, "multiply_by": str(closure_multiplier)}]
        },
    }
    return _request(f"{base_url}/route", body=body, method="POST")


def used_way_ids(path):
    """Distinct OSM way IDs traversed by a path, from its osm_way_id path detail."""
    return sorted({way_id for _start, _end, way_id in path["details"]["osm_way_id"]})


def used_way_segments(path):
    """Like used_way_ids, but keeps the actual [lon, lat] coordinates each way
    was traversed at, not just its bare id -- {way_id: [[lon, lat], ...]}.
    This is what makes the long-way fix possible for edge-reuse: the exact
    touched stretch is already sitting right here in the outbound leg's own
    response, for free, no extra request or stored data needed.

    If a way appears in more than one non-contiguous index range (rare, but
    possible -- e.g. a route crosses the same way twice), all its points are
    concatenated under one key. build_way_buffer_polygon (below) buffers
    each consecutive pair, so a gap between two disjoint ranges just
    produces one extra (harmless, if slightly too generous) connecting
    segment rather than wrong output -- not worth the extra complexity of
    tracking ranges separately for how rare this is."""
    segments = {}
    coords = path["points"]["coordinates"]
    for start, end, way_id in path["details"]["osm_way_id"]:
        segments.setdefault(way_id, []).extend(coords[start:end + 1])
    return segments


def _load_long_way_ids(path):
    """way_id -> length_m for every way the offline audit (audit_long_ways.py)
    found at or above its length threshold, for a given clipped extract.

    Missing file is NOT an error: it just means the audit hasn't been run
    (e.g. a fresh checkout before `python3 scripts/audit_long_ways.py`) --
    every way is then treated as "not long," which is exactly today's
    pre-fix behavior (the safe default), not a crash."""
    path = Path(path)
    if not path.exists():
        return {}
    with open(path) as f:
        raw = json.load(f)
    return {int(k): v for k, v in raw.items()}


# Keyed by path (as a string), not a single global -- multi-city (§0), each
# city has its own audit file (data/long_ways.json for Waterloo Region,
# data/long_ways_guelph.json for Guelph, etc.). A single unkeyed cache was
# the original (v1, one-city) shape of this function; keeping it unkeyed
# after Guelph was added would have silently used Waterloo's audit for
# every city's requests -- way ids are globally unique, so a genuinely
# long Guelph way just never appears in Waterloo's list, and the long-way
# fix would silently never engage for it, re-introducing the exact
# blanket-penalty bug it exists to fix, just for the second city. Caught
# during Guelph's full validation pass, not left in.
_LONG_WAY_IDS_CACHE = {}


def get_long_way_ids(path=LONG_WAYS_PATH):
    key = str(path)
    if key not in _LONG_WAY_IDS_CACHE:
        _LONG_WAY_IDS_CACHE[key] = _load_long_way_ids(path)
    return _LONG_WAY_IDS_CACHE[key]


def build_way_buffer_polygon(coords, width_m=DEFAULT_WAY_BUFFER_WIDTH_M):
    """Build a GeoJSON MultiPolygon buffering just the given [lon, lat]
    coordinate sequence -- the mechanism behind the long-way fix (§5's
    osm_way_id-blanket-penalty investigation): instead of matching a long
    way's whole osm_way_id (which penalizes the entire way, including
    stretches nowhere near what was actually walked), wrap only the
    actually-touched stretch in a narrow polygon and reference it via
    custom_model's `areas` + `in_<id>` condition -- confirmed live against
    the running server that this genuinely restricts the penalty to just
    that geography, not the whole way (a hard exclusion zone around a
    ~250m stretch of the real 6.9km Cambridge-to-Paris Rail Trail left the
    remaining ~6.6km fully usable in the same request).

    Emits one narrow rectangle per consecutive coordinate pair rather than
    one polygon that follows the whole polyline's outline -- simpler to
    get right (no self-intersection/mitring logic at bends) at the cost of
    a small gap or overlap at each joint, which doesn't matter here: the
    buffer only needs to reliably COVER the touched stretch, not have a
    precise outline, and a `custom_model` `areas` FeatureCollection is
    allowed to hold multiple polygons under one id (they union together
    for `in_<id>` matching).

    width_m=15 is ad hoc, like this codebase's other tunables: wide enough
    to comfortably cover a trail/path's own width plus a bit of GPS/graph
    snapping slack, narrow enough not to spill onto a parallel street a
    real rail-trail or trailway commonly runs beside."""
    lat0 = coords[0][1]
    m_per_deg_lat, m_per_deg_lon = _local_meters_per_degree(lat0)
    half_width = width_m / 2
    polygons = []
    for (lon1, lat1), (lon2, lat2) in zip(coords, coords[1:]):
        x1, y1 = lon1 * m_per_deg_lon, lat1 * m_per_deg_lat
        x2, y2 = lon2 * m_per_deg_lon, lat2 * m_per_deg_lat
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length == 0:
            continue  # duplicate consecutive point -- no segment to buffer
        # unit vector perpendicular to the segment, in meters-space
        nx, ny = -dy / length, dx / length
        ox, oy = nx * half_width, ny * half_width
        corners_m = [(x1 + ox, y1 + oy), (x2 + ox, y2 + oy), (x2 - ox, y2 - oy), (x1 - ox, y1 - oy)]
        ring = [[cx / m_per_deg_lon, cy / m_per_deg_lat] for cx, cy in corners_m]
        ring.append(ring[0])  # GeoJSON polygons must close their ring
        polygons.append([ring])
    return {"type": "MultiPolygon", "coordinates": polygons}


def fetch_return_leg(base_url, far_lat, far_lon, start_lat, start_lon, profile,
                      avoid_way_ids, penalty_multiplier,
                      closed_way_ids=None, closure_multiplier=DEFAULT_CLOSURE_MULTIPLIER,
                      long_way_ids=None):
    """Route back, penalizing every way used on the outbound leg, AND
    excluding any currently-closed way. Two independent `priority` if-blocks
    in one custom_model, one per concern -- merges with (does not replace)
    the profile's base custom model, and the two blocks combine
    multiplicatively rather than one overriding the other. Both confirmed
    empirically against the running server, not assumed:
    a way penalized by two independent same-condition if-blocks at 0.97
    each behaved identically (same route, same avoidance threshold) to a
    single block at 0.9409 (=0.97*0.97), in both block orders -- i.e. this
    really is a product of independent factors, not last-write-wins or
    first-match-wins.

    Deliberately different multipliers for the two concerns: edge-reuse
    uses a small-but-nonzero discourage (penalty_multiplier, default 0.01)
    because a repeated way is merely undesirable -- it's fine as a last
    resort if every other option is worse. A closure uses a hard 0
    (closure_multiplier / DEFAULT_CLOSURE_MULTIPLIER) because it's not a
    preference, it's a fact: the way cannot be used at all if any other
    path exists. If a way is BOTH reused and closed, the two blocks still
    apply independently (0.01 * 0 = 0) -- closure wins, correctly, since
    it's the more restrictive of the two regardless of which block is
    listed first.

    Long-way fix (§5's osm_way_id-blanket-penalty investigation), edge-reuse
    only: avoid_way_ids may now be either a plain iterable of way ids
    (legacy/direct-call form -- always whole-way OR-chain, unchanged) or a
    dict {way_id: [[lon, lat], ...]} as produced by used_way_segments(),
    which is what _fetch_leg_pair actually passes. For a dict, each way is
    checked against the offline long-way audit -- long_way_ids, if given
    (the resolved dict from get_long_way_ids(path) for whichever city's
    extract this request is against; multi-city callers like route_api.py
    must pass this explicitly, not rely on the default), else
    get_long_way_ids() with its default (Waterloo Region's) path -- an
    ordinary (short) way still goes into the flat OR-chain exactly as
    before -- zero behavior change, confirmed byte-identical against the
    pre-fix Uptown Waterloo validation case. A LONG way instead gets its
    own custom_areas buffer polygon (build_way_buffer_polygon), covering
    only the coordinates actually touched, referenced by its own
    `in_<id>` priority block at the same penalty_multiplier -- so touching
    200m of a 6.9km trail on the outbound leg now costs the return leg
    only that 200m, not the whole trail.

    Deliberately NOT extended to closed_way_ids/closures: a closure is
    resolved from a lat/lon down to a bare osm_way_id and nothing else
    (closures.py's schema has no lat/lon column at all, on purpose --
    see its module docstring's §3 privacy reasoning), so there is no
    stored coordinate to buffer a polygon around. A closure on a long way
    still blanket-excludes the whole way, exactly as before -- a real,
    known, explicitly out-of-scope gap (a deliberate choice, not an
    oversight -- extending it would mean storing more location data than
    the privacy design currently allows, which is its own decision to
    make on purpose, not a side effect of this fix)."""
    body = {
        "points": [[far_lon, far_lat], [start_lon, start_lat]],
        "profile": profile,
        "points_encoded": False,
        "details": ["osm_way_id"],
    }
    priority_blocks = []
    areas = {}

    if avoid_way_ids:
        if isinstance(avoid_way_ids, dict):
            if long_way_ids is None:
                long_way_ids = get_long_way_ids()  # default: Waterloo Region's audit -- see get_long_way_ids' docstring
            short_way_ids = []
            for way_id, coords in avoid_way_ids.items():
                if way_id in long_way_ids and len(coords) >= 2:
                    area_id = f"reuse_{way_id}"
                    areas[area_id] = build_way_buffer_polygon(coords)
                    priority_blocks.append({"if": f"in_{area_id}", "multiply_by": str(penalty_multiplier)})
                else:
                    short_way_ids.append(way_id)
        else:
            short_way_ids = list(avoid_way_ids)
        if short_way_ids:
            condition = " || ".join(f"osm_way_id == {way_id}" for way_id in short_way_ids)
            priority_blocks.append({"if": condition, "multiply_by": str(penalty_multiplier)})

    if closed_way_ids:
        condition = " || ".join(f"osm_way_id == {way_id}" for way_id in closed_way_ids)
        priority_blocks.append({"if": condition, "multiply_by": str(closure_multiplier)})

    if priority_blocks:
        body["ch.disable"] = True
        custom_model = {"priority": priority_blocks}
        if areas:
            custom_model["areas"] = {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "id": area_id, "properties": {}, "geometry": geometry}
                    for area_id, geometry in areas.items()
                ],
            }
        body["custom_model"] = custom_model
    # else: nothing to avoid or close (shouldn't happen in practice for
    # avoid_way_ids -- the outbound leg always touches at least one way) --
    # fall through to a plain CH request with no penalty.
    return _request(f"{base_url}/route", body=body, method="POST")


def combine_legs(outbound, return_leg):
    """Concatenate outbound + return coordinates into one loop LineString."""
    out_coords = outbound["points"]["coordinates"]
    back_coords = return_leg["points"]["coordinates"]
    # avoid duplicating the shared far-point vertex
    return out_coords + back_coords[1:]


def _local_meters_per_degree(lat0):
    """Equirectangular local projection scale factors at a given latitude --
    fine at the scale of a single loop or a short way segment, not for
    anything spanning enough latitude for the small-angle approximation to
    break down. Shared by polygon_area_m2 (below) and
    build_way_buffer_polygon (§5's long-way over-penalization fix)."""
    m_per_deg_lat = EARTH_RADIUS_M * math.pi / 180
    m_per_deg_lon = m_per_deg_lat * math.cos(math.radians(lat0))
    return m_per_deg_lat, m_per_deg_lon


def polygon_area_m2(coords):
    """Shoelace formula in a local equirectangular projection (fine at the
    scale of a single loop). A self-overlapping ring -- e.g. a return leg
    that retraces the outbound leg almost exactly -- partially cancels its
    own area here, which is a feature for compactness_score below: a
    there-and-back that barely diverges should read as enclosing ~zero
    area, not need separate overlap-detection logic."""
    lat0 = coords[0][1]
    m_per_deg_lat, m_per_deg_lon = _local_meters_per_degree(lat0)
    pts = [(lon * m_per_deg_lon, lat * m_per_deg_lat) for lon, lat in coords]
    area = 0
    for i in range(len(pts) - 1):
        x1, y1 = pts[i]
        x2, y2 = pts[i + 1]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2


def compactness_score(coords, perimeter_m):
    """Polsby-Popper compactness: 4*pi*Area / Perimeter^2. Standard measure
    of "how circle-like is this shape" (1.0 = perfect circle, ->0 = a
    degenerate sliver or line) borrowed from geography/redistricting.

    Why this, not turning angle: cumulative turning was the first thing
    tried and it does NOT distinguish a real winding path from a zigzag --
    tested on the Uptown Waterloo case below, the visually-cleanest loop
    (bearing 300) had the HIGHEST total turning of all six candidates
    (5198 degrees), just from following a legitimately winding street.
    Compactness does distinguish them: a zigzag or a near-total-overlap
    there-and-back encloses almost no area relative to its length,
    regardless of how much it turns; a real loop encloses meaningful
    territory. Confirmed on that same case: compactness ranked all six
    candidates in the same order as visual judgment (0.294, 0.200, 0.157
    for the three good-looking loops; 0.012, 0.006, 0.003 for the
    near-overlap, thin-sliver, and zigzag shapes respectively)."""
    if perimeter_m <= 0:
        return 0
    area = polygon_area_m2(coords)
    return 4 * math.pi * area / (perimeter_m ** 2)


def candidate_to_feature(candidate, start_lat, start_lon, target_distance_m, rank):
    return {
        "type": "Feature",
        "properties": {
            "rank": rank,
            "start": [start_lon, start_lat],
            "bearing_deg": candidate["bearing"],
            "target_distance_m": target_distance_m,
            "actual_distance_m": candidate["total_distance"],
            "time_ms": candidate["total_time"],
            "outbound_way_count": len(candidate["outbound_ways"]),
            "reused_way_count": len(candidate["reused"]),
            "radius_iterations": candidate["radius_iterations"],
            "compactness": candidate["compactness"],
            "score": candidate["score"],
        },
        "geometry": {
            "type": "LineString",
            "coordinates": candidate["coords"],
        },
    }


def candidates_to_geojson(candidates, start_lat, start_lon, target_distance_m):
    """A FeatureCollection of the top candidates, ranked 1..N -- §5 point 4's
    "return 2-3 alternatives." Rank 1 is the lowest-scoring (best)."""
    return {
        "type": "FeatureCollection",
        "features": [
            candidate_to_feature(c, start_lat, start_lon, target_distance_m, rank)
            for rank, c in enumerate(candidates, start=1)
        ],
    }


def _fetch_leg_pair(base_url, lat, lon, far_lat, far_lon, profile, penalty_multiplier,
                     closed_way_ids=None, long_way_ids=None):
    """One outbound + penalized-return leg pair at a given far point. Raises
    on failure -- caller decides how to handle it."""
    outbound = fetch_outbound_leg(base_url, lat, lon, far_lat, far_lon, profile, closed_way_ids)
    # A dict (way_id -> touched coords), not just a list of ids -- lets
    # fetch_return_leg apply the long-way buffer-polygon fix instead of a
    # whole-way OR-chain where it's actually needed. See used_way_segments
    # and fetch_return_leg's docstrings.
    outbound_segments = used_way_segments(outbound)
    outbound_ways = sorted(outbound_segments.keys())
    return_leg = fetch_return_leg(
        base_url, far_lat, far_lon, lat, lon, profile, outbound_segments, penalty_multiplier,
        closed_way_ids, long_way_ids=long_way_ids,
    )
    return_ways = used_way_ids(return_leg)
    reused = set(outbound_ways) & set(return_ways)
    total_distance = outbound["distance"] + return_leg["distance"]
    total_time = outbound["time"] + return_leg["time"]
    return {
        "far_point": (far_lat, far_lon),
        "outbound": outbound,
        "return_leg": return_leg,
        "outbound_ways": outbound_ways,
        "return_ways": return_ways,
        "reused": reused,
        "total_distance": total_distance,
        "total_time": total_time,
        "coords": combine_legs(outbound, return_leg),
    }


def build_candidate(base_url, lat, lon, bearing, target_distance, profile, penalty_multiplier,
                     max_iterations=DEFAULT_MAX_RADIUS_ITERATIONS,
                     tolerance_pct=DEFAULT_DISTANCE_TOLERANCE_PCT,
                     min_distance_fraction=DEFAULT_MIN_DISTANCE_FRACTION,
                     max_distance_fraction=DEFAULT_MAX_DISTANCE_FRACTION,
                     closed_way_ids=None, long_way_ids=None):
    """Fetch a full out-and-back candidate for a single bearing, refining the
    far-point radius rather than accepting whatever the fixed
    target_distance/2 straight-line guess produces.

    Starts at radius = target_distance / 2, then after each attempt rescales
    by (target / actual) and retries -- e.g. if a radius produced a walked
    loop 20% longer than target, the next attempt shrinks the radius by
    ~17% (1 / 1.20). This is a simple proportional correction, not a
    real optimizer: it assumes the relationship between straight-line radius
    and walked distance is roughly linear near the current guess, which is
    good enough in practice for a handful of iterations but isn't
    guaranteed to converge (a bearing that crosses a river or highway can
    make small radius changes produce large, non-monotonic distance jumps
    as the route is forced around the obstacle differently).

    Keeps the closest-to-target attempt seen across all iterations, not
    just the last one, in case a later rescale overshoots past a better
    earlier attempt. Returns None (with a message printed) instead of
    raising, so one bad bearing doesn't abort the whole batch -- e.g. a far
    point that lands somewhere ungraphed.

    A degenerate result -- total_distance below min_distance_fraction of
    target, OR above max_distance_fraction of target -- is treated as a
    FAILED attempt, not a real-but-bad candidate. The low side is confirmed
    by testing near the clipped region's boundary (New Hamburg, Elmira):
    GraphHopper can snap the start and far point to the same graph node
    when the far point falls near/past the graph's edge, returning a 200 OK
    with a near-zero-length path. The high side is confirmed by the same
    boundary testing in a sparse rural network (New Hamburg @ 8km): a
    radius that happens to force a huge real detour can produce a 23000m+
    result for an 8000m target, and -- without this ceiling -- that result
    was locked in as `best` on iteration 1 before a later iteration's
    rescale collapsed to a degenerate route the floor then correctly
    rejected, leaving the over-target result as the only one recorded.
    Neither is retried with a rescaled radius: for the low side, the normal
    rescale formula assumes the real route came in short due to street
    detours, which doesn't apply to a collapsed single-node route; for the
    high side, traced empirically (see the validation session's notes) --
    the rescale ratio computed off a near-zero or wildly-over distance can
    itself be pathological (a traced case computed a 367x multiplier),
    so retrying risks compounding the problem rather than correcting it.
    Either way, retrying is unlikely to recover a fundamentally bad
    bearing. The rescale step for a normal (non-degenerate) attempt is
    itself clamped to [0.4x, 2.5x] per iteration for the same reason --
    bounding the correction at its source, not just filtering the result
    after the fact."""
    radius = target_distance / 2
    best = None
    iterations_used = 0
    min_distance = min_distance_fraction * target_distance
    max_distance = max_distance_fraction * target_distance

    for iteration in range(1, max_iterations + 1):
        far_lat, far_lon = destination_point(lat, lon, bearing, radius)
        try:
            attempt = _fetch_leg_pair(base_url, lat, lon, far_lat, far_lon, profile, penalty_multiplier,
                                       closed_way_ids, long_way_ids)
        except (urllib.error.URLError, RuntimeError) as e:
            print(f"  bearing {bearing:>5.0f}° iteration {iteration}: skipped ({e})", file=sys.stderr)
            break

        iterations_used = iteration
        if attempt["total_distance"] < min_distance:
            print(f"  bearing {bearing:>5.0f}° iteration {iteration}: rejected -- degenerate route "
                  f"({attempt['total_distance']:.1f}m, target {target_distance:.0f}m)", file=sys.stderr)
            break
        if attempt["total_distance"] > max_distance:
            print(f"  bearing {bearing:>5.0f}° iteration {iteration}: rejected -- wildly over target "
                  f"({attempt['total_distance']:.1f}m, target {target_distance:.0f}m)", file=sys.stderr)
            break

        distance_error_pct = 100 * abs(attempt["total_distance"] - target_distance) / target_distance
        if best is None or distance_error_pct < best["_distance_error_pct"]:
            attempt["_distance_error_pct"] = distance_error_pct
            best = attempt

        if distance_error_pct <= tolerance_pct:
            break
        ratio = max(0.4, min(target_distance / attempt["total_distance"], 2.5))
        radius *= ratio

    if best is None:
        return None

    best["bearing"] = bearing
    best["radius_iterations"] = iterations_used
    del best["_distance_error_pct"]
    return best


def score_candidate(candidate, target_distance, compactness_weight=DEFAULT_COMPACTNESS_WEIGHT):
    """Lower is better. Three terms:
    - distance error: |actual - target| / target * 100
    - edge-reuse: reused ways / outbound ways * 100
    - shape penalty: (1 - compactness) * compactness_weight -- see
      compactness_score's docstring for why compactness (not turning angle)
      is the signal used here.

    The first two are an unweighted sum (ad hoc, not a tuned formula --
    a placeholder good enough to rank a handful of candidates against each
    other, not a claim that a 1-point distance-error move should always
    trade evenly against a 1-point reuse move). compactness_weight=20 is
    ALSO an ad hoc weight, revisit later: found empirically on the Uptown
    Waterloo 3km case (§5 validation session) as the point where a real
    zigzag (bearing 180, old score 8.35 -- the winner) drops below a clean
    wide loop (bearing 240, old score 11.4) that was already better on
    distance accuracy alone. Below weight~10 the zigzag still wins; at 20
    there's a clean gap between the three good-looking shapes and the
    three bad ones in that test. Cross-checked against Galt/Cambridge 8km,
    where the pre-existing winner (already visually verified clean) stays
    the winner -- this term demotes shapes that were quietly bad, it
    doesn't disturb an already-good result. Revisit the weighting once
    real test runs (actually walking generated routes, per
    docs/running-app-architecture.md §0/§7) show it needs adjusting.

    Greenness/park-proximity (§5 point 1's second bullet) is NOT a term
    here. It used to be baked into pathfinder_foot.json's routing cost (an
    `in_greenspace` priority multiplier) instead of being a scoring term,
    but that rule has since been removed at province scale -- it was the
    confirmed root cause of /route being unusably slow in flexible mode
    (see pathfinder_foot.json's and config-ontario.yml's comments). A
    proper fix is filed as a future item (a static encoded value baked in
    at import time via a custom Java TagParser); until then, greenness is
    simply not represented anywhere in route generation, not here and not
    in the routing cost. §5 point 3's separate "path-type score
    (sidewalk/park path percentage)" criterion is still not a distinct
    scoring term here -- the routing-cost-level path-type weighting still
    does the equivalent job for candidate generation, greenness aside.
    """
    distance_error_pct = 100 * abs(candidate["total_distance"] - target_distance) / target_distance
    reuse_pct = 100 * len(candidate["reused"]) / len(candidate["outbound_ways"]) if candidate["outbound_ways"] else 0
    compactness = compactness_score(candidate["coords"], candidate["total_distance"])
    shape_penalty = (1 - compactness) * compactness_weight
    score = distance_error_pct + reuse_pct + shape_penalty
    return score, distance_error_pct, reuse_pct, compactness


def generate_candidates(base_url, lat, lon, target_distance, bearing=0.0, num_candidates=6,
                         profile="foot", penalty_multiplier=DEFAULT_REUSE_PENALTY_MULTIPLIER,
                         max_radius_iterations=DEFAULT_MAX_RADIUS_ITERATIONS,
                         distance_tolerance_pct=DEFAULT_DISTANCE_TOLERANCE_PCT,
                         min_distance_fraction=DEFAULT_MIN_DISTANCE_FRACTION,
                         max_distance_fraction=DEFAULT_MAX_DISTANCE_FRACTION,
                         closed_way_ids=None, long_way_ids=None):
    """Build and score every candidate for the given start point/target
    distance, one per bearing evenly spread from `bearing`. Returns
    (candidates_sorted_best_first, bearings_tried) -- candidates may be
    fewer than bearings_tried if some failed or were rejected as degenerate
    (see build_candidate).

    closed_way_ids: currently-active closures (§4/§6), fetched ONCE by the
    caller (route_api.py's /route handler, or main() below via
    --use-closures) and passed in here rather than queried per-candidate --
    it's the same closures list for every bearing in one request. Deliberately
    NOT queried from this module directly: closures.py already imports FROM
    generate_loop.py (destination_point, fetch_outbound_leg), so importing
    closures.py here too would create a cycle.

    long_way_ids: the resolved long-way audit dict (get_long_way_ids(path))
    for whichever city's extract base_url actually points at -- multi-city
    (§0) callers MUST pass this explicitly (route_api.py does, resolved
    from cities.py). Left as None, it silently defaults to Waterloo
    Region's own audit regardless of which city base_url is for -- exactly
    the bug caught during Guelph's validation pass (see get_long_way_ids'
    docstring): a real long Guelph way would never match Waterloo's way
    ids, so the long-way fix would silently never engage for it.

    This is the reusable core the CLI (main, below) and the HTTP wrapper
    (route_api.py) both call -- no argparse or printing in here."""
    bearings = [bearing + i * (360 / num_candidates) for i in range(num_candidates)]
    candidates = []
    for b in bearings:
        c = build_candidate(base_url, lat, lon, b, target_distance, profile,
                             penalty_multiplier, max_radius_iterations, distance_tolerance_pct,
                             min_distance_fraction, max_distance_fraction, closed_way_ids, long_way_ids)
        if c is not None:
            score, distance_error_pct, reuse_pct, compactness = score_candidate(c, target_distance)
            c["score"], c["distance_error_pct"], c["reuse_pct"], c["compactness"] = score, distance_error_pct, reuse_pct, compactness
            candidates.append(c)
    candidates.sort(key=lambda c: c["score"])
    return candidates, bearings


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lat", type=float, required=True, help="Start latitude")
    parser.add_argument("--lon", type=float, required=True, help="Start longitude")
    parser.add_argument("--distance", type=float, required=True, help="Target loop distance in meters")
    parser.add_argument("--bearing", type=float, default=0.0, help="Starting outbound bearing in degrees (0=N, 90=E); default 0")
    parser.add_argument("--candidates", type=int, default=6, help="Number of candidate bearings to try, evenly spread from --bearing (default: 6)")
    parser.add_argument("--profile", default="foot", help="GraphHopper profile name (default: foot)")
    parser.add_argument("--penalty-multiplier", type=float, default=DEFAULT_REUSE_PENALTY_MULTIPLIER,
                         help=f"Priority multiplier applied to ways reused from the outbound leg (default: {DEFAULT_REUSE_PENALTY_MULTIPLIER})")
    parser.add_argument("--max-radius-iterations", type=int, default=DEFAULT_MAX_RADIUS_ITERATIONS,
                         help=f"Max far-point radius corrections per bearing (default: {DEFAULT_MAX_RADIUS_ITERATIONS})")
    parser.add_argument("--distance-tolerance-pct", type=float, default=DEFAULT_DISTANCE_TOLERANCE_PCT,
                         help=f"Stop refining a bearing's radius once within this %% of target distance (default: {DEFAULT_DISTANCE_TOLERANCE_PCT})")
    parser.add_argument("--min-distance-fraction", type=float, default=DEFAULT_MIN_DISTANCE_FRACTION,
                         help=f"Reject a candidate whose distance falls below this fraction of target as degenerate (default: {DEFAULT_MIN_DISTANCE_FRACTION})")
    parser.add_argument("--max-distance-fraction", type=float, default=DEFAULT_MAX_DISTANCE_FRACTION,
                         help=f"Reject a candidate whose distance exceeds this multiple of target as degenerate (default: {DEFAULT_MAX_DISTANCE_FRACTION})")
    parser.add_argument("--graphhopper-url", default=DEFAULT_GRAPHHOPPER_URL, help=f"GraphHopper server base URL (default: {DEFAULT_GRAPHHOPPER_URL})")
    parser.add_argument("--top-n", type=int, default=3, help="Number of top-scoring candidates to keep/return, per §5 point 4's '2-3 alternatives' (default: 3)")
    parser.add_argument("--output", help="Write the top candidates as a GeoJSON FeatureCollection to this file")
    parser.add_argument("--use-closures", action="store_true",
                         help="Query scripts/closures.db for active closures and exclude those ways "
                              "from both legs (imported lazily here, not at module load, to avoid a "
                              "circular import -- closures.py imports from this module)")
    args = parser.parse_args()

    print(f"start:            {args.lat}, {args.lon}")
    print(f"target distance:  {args.distance:.0f} m")

    closed_way_ids = None
    if args.use_closures:
        from closures import get_active_closure_way_ids
        closed_way_ids = get_active_closure_way_ids()
        print(f"active closures:  {len(closed_way_ids)} way(s) {closed_way_ids}")

    candidates, bearings = generate_candidates(
        args.graphhopper_url, args.lat, args.lon, args.distance, args.bearing, args.candidates,
        args.profile, args.penalty_multiplier, args.max_radius_iterations, args.distance_tolerance_pct,
        args.min_distance_fraction, args.max_distance_fraction, closed_way_ids,
    )
    print(f"candidates:       {args.candidates} (bearings: {', '.join(f'{b:.0f}°' for b in bearings)})")
    print()

    if not candidates:
        print("error: no candidates succeeded", file=sys.stderr)
        sys.exit(1)

    top = candidates[:args.top_n]
    top_set = {id(c) for c in top}

    print(f"{'bearing':>8}  {'iters':>5}  {'distance_m':>10}  {'dist_err%':>9}  {'ways_out':>8}  {'reused':>6}  {'reuse%':>7}  {'compact':>7}  {'score':>7}")
    for c in candidates:
        rank = top.index(c) + 1 if id(c) in top_set else None
        marker = f" <- #{rank}" if rank else ""
        print(f"{c['bearing']:>7.0f}°  {c['radius_iterations']:>5}  {c['total_distance']:>10.1f}  {c['distance_error_pct']:>8.1f}%  "
              f"{len(c['outbound_ways']):>8}  {len(c['reused']):>6}  {c['reuse_pct']:>6.1f}%  {c['compactness']:>7.3f}  {c['score']:>7.1f}{marker}")

    print()
    print(f"top {len(top)} candidate(s):")
    for rank, c in enumerate(top, start=1):
        print(f"  #{rank}: bearing {c['bearing']:.0f}°, {c['total_distance']:.1f} m "
              f"({100 * (c['total_distance'] - args.distance) / args.distance:+.1f}%), "
              f"{len(c['reused'])}/{len(c['outbound_ways'])} ways reused, score {c['score']:.1f}")

    if args.output:
        collection = candidates_to_geojson(top, args.lat, args.lon, args.distance)
        with open(args.output, "w") as f:
            json.dump(collection, f, indent=2)
        print(f"wrote {len(top)} candidate(s) as GeoJSON to {args.output}")


if __name__ == "__main__":
    main()
