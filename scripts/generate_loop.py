#!/usr/bin/env python3
"""
Pathfinder Run -- v1 loop generation, §5 points 2-4: several out-and-back
candidates (one per bearing), each with the edge-reuse penalty applied to
its return leg, scored on distance accuracy + edge-reuse and the best one
kept.

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
  obstacle (river, highway, dead-end trail).
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

EARTH_RADIUS_M = 6371000
DEFAULT_GRAPHHOPPER_URL = "http://localhost:8989"
DEFAULT_REUSE_PENALTY_MULTIPLIER = 0.01  # how much cheaper an unused way is vs a reused one
DEFAULT_MAX_RADIUS_ITERATIONS = 4
DEFAULT_DISTANCE_TOLERANCE_PCT = 5.0  # matches docs/running-app-architecture.md §5 point 3


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


def fetch_outbound_leg(base_url, start_lat, start_lon, far_lat, far_lon, profile):
    """Plain route, no penalty -- also requests osm_way_id details so we know
    which ways to penalize on the way back."""
    params = [
        ("point", f"{start_lat},{start_lon}"),
        ("point", f"{far_lat},{far_lon}"),
        ("profile", profile),
        ("points_encoded", "false"),
        ("details", "osm_way_id"),
    ]
    url = f"{base_url}/route?" + urllib.parse.urlencode(params)
    return _request(url)


def used_way_ids(path):
    """Distinct OSM way IDs traversed by a path, from its osm_way_id path detail."""
    return sorted({way_id for _start, _end, way_id in path["details"]["osm_way_id"]})


def fetch_return_leg(base_url, far_lat, far_lon, start_lat, start_lon, profile,
                      avoid_way_ids, penalty_multiplier):
    """Route back, penalizing every way used on the outbound leg. Merges with
    (does not replace) the profile's base custom model -- confirmed
    empirically, not assumed.

    Known, unfixed limitation: this penalizes by whole osm_way_id, not by the
    specific segment actually walked. A way-length audit of the clipped
    extract found 153 ways over 1km (worst case: Cambridge to Paris Rail
    Trail at 6.9km, also Kissing Bridge Trailway at 4.0km) -- touching a
    short stretch of one of those blanket-penalizes the entire remaining
    length for the return leg, the same shape of problem as the power-line
    ways found during the region-clip work. Not rare for this app
    specifically: it preferentially routes onto trails, so trail ways are
    disproportionately likely to get used. A real fix needs per-segment
    identity (e.g. splitting the penalty by distance-along-way) or a
    length-threshold fallback to geometry buffering for long ways -- neither
    implemented here."""
    body = {
        "points": [[far_lon, far_lat], [start_lon, start_lat]],
        "profile": profile,
        "points_encoded": False,
        "details": ["osm_way_id"],
    }
    if avoid_way_ids:
        condition = " || ".join(f"osm_way_id == {way_id}" for way_id in avoid_way_ids)
        body["ch.disable"] = True
        body["custom_model"] = {
            "priority": [{"if": condition, "multiply_by": str(penalty_multiplier)}]
        }
    # else: nothing to avoid (shouldn't happen in practice) -- fall through to
    # a plain CH request with no penalty.
    return _request(f"{base_url}/route", body=body, method="POST")


def combine_legs(outbound, return_leg):
    """Concatenate outbound + return coordinates into one loop LineString."""
    out_coords = outbound["points"]["coordinates"]
    back_coords = return_leg["points"]["coordinates"]
    # avoid duplicating the shared far-point vertex
    return out_coords + back_coords[1:]


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


def _fetch_leg_pair(base_url, lat, lon, far_lat, far_lon, profile, penalty_multiplier):
    """One outbound + penalized-return leg pair at a given far point. Raises
    on failure -- caller decides how to handle it."""
    outbound = fetch_outbound_leg(base_url, lat, lon, far_lat, far_lon, profile)
    outbound_ways = used_way_ids(outbound)
    return_leg = fetch_return_leg(
        base_url, far_lat, far_lon, lat, lon, profile, outbound_ways, penalty_multiplier,
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
                     tolerance_pct=DEFAULT_DISTANCE_TOLERANCE_PCT):
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
    point that lands somewhere ungraphed."""
    radius = target_distance / 2
    best = None
    iterations_used = 0

    for iteration in range(1, max_iterations + 1):
        far_lat, far_lon = destination_point(lat, lon, bearing, radius)
        try:
            attempt = _fetch_leg_pair(base_url, lat, lon, far_lat, far_lon, profile, penalty_multiplier)
        except (urllib.error.URLError, RuntimeError) as e:
            print(f"  bearing {bearing:>5.0f}° iteration {iteration}: skipped ({e})", file=sys.stderr)
            break

        iterations_used = iteration
        distance_error_pct = 100 * abs(attempt["total_distance"] - target_distance) / target_distance
        if best is None or distance_error_pct < best["_distance_error_pct"]:
            attempt["_distance_error_pct"] = distance_error_pct
            best = attempt

        if distance_error_pct <= tolerance_pct or attempt["total_distance"] == 0:
            break
        radius *= target_distance / attempt["total_distance"]

    if best is None:
        return None

    best["bearing"] = bearing
    best["radius_iterations"] = iterations_used
    del best["_distance_error_pct"]
    return best


def score_candidate(candidate, target_distance):
    """Lower is better. Two terms, both expressed as percentages so they're
    comparable and the printed breakdown is self-explanatory:
    - distance error: |actual - target| / target * 100
    - edge-reuse: reused ways / outbound ways * 100

    This is an unweighted sum (distance_error_pct + reuse_pct), not a tuned
    formula -- it's a placeholder good enough to rank a handful of
    candidates against each other, not a claim that a 1-point distance-error
    move should always trade evenly against a 1-point reuse move. Revisit
    the weighting once real test runs (actually walking generated routes,
    per docs/running-app-architecture.md §0/§7) show whether reuse should
    count for more or less than distance accuracy.

    Missing entirely: path-type/"greenness" scoring, §5 point 3's third
    criterion (sidewalk/park-path percentage, proximity to green space).
    Not implemented here -- it depends on the custom_areas park-polygon
    extraction work (§5 point 1's second bullet), which hasn't been done
    yet. Once that lands, this becomes a three-term score, not two.
    """
    distance_error_pct = 100 * abs(candidate["total_distance"] - target_distance) / target_distance
    reuse_pct = 100 * len(candidate["reused"]) / len(candidate["outbound_ways"]) if candidate["outbound_ways"] else 0
    return distance_error_pct + reuse_pct, distance_error_pct, reuse_pct


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
    parser.add_argument("--graphhopper-url", default=DEFAULT_GRAPHHOPPER_URL, help=f"GraphHopper server base URL (default: {DEFAULT_GRAPHHOPPER_URL})")
    parser.add_argument("--top-n", type=int, default=3, help="Number of top-scoring candidates to keep/return, per §5 point 4's '2-3 alternatives' (default: 3)")
    parser.add_argument("--output", help="Write the top candidates as a GeoJSON FeatureCollection to this file")
    args = parser.parse_args()

    bearings = [args.bearing + i * (360 / args.candidates) for i in range(args.candidates)]

    print(f"start:            {args.lat}, {args.lon}")
    print(f"target distance:  {args.distance:.0f} m")
    print(f"candidates:       {args.candidates} (bearings: {', '.join(f'{b:.0f}°' for b in bearings)})")
    print()

    candidates = []
    for bearing in bearings:
        c = build_candidate(args.graphhopper_url, args.lat, args.lon, bearing, args.distance, args.profile,
                             args.penalty_multiplier, args.max_radius_iterations, args.distance_tolerance_pct)
        if c is not None:
            score, distance_error_pct, reuse_pct = score_candidate(c, args.distance)
            c["score"], c["distance_error_pct"], c["reuse_pct"] = score, distance_error_pct, reuse_pct
            candidates.append(c)

    if not candidates:
        print("error: no candidates succeeded", file=sys.stderr)
        sys.exit(1)

    candidates.sort(key=lambda c: c["score"])
    top = candidates[:args.top_n]
    top_set = {id(c) for c in top}

    print(f"{'bearing':>8}  {'iters':>5}  {'distance_m':>10}  {'dist_err%':>9}  {'ways_out':>8}  {'reused':>6}  {'reuse%':>7}  {'score':>7}")
    for c in candidates:
        rank = top.index(c) + 1 if id(c) in top_set else None
        marker = f" <- #{rank}" if rank else ""
        print(f"{c['bearing']:>7.0f}°  {c['radius_iterations']:>5}  {c['total_distance']:>10.1f}  {c['distance_error_pct']:>8.1f}%  "
              f"{len(c['outbound_ways']):>8}  {len(c['reused']):>6}  {c['reuse_pct']:>6.1f}%  {c['score']:>7.1f}{marker}")

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
