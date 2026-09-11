#!/usr/bin/env python3
"""
Pathfinder Run -- v1 loop generation, §5 point 2: a single out-and-back
candidate with the edge-reuse penalty applied to the return leg.

Scope, deliberately narrow:
- One bearing in, one route out -- no candidate generation across multiple
  headings, no scoring/selection between alternatives (§5 points 3-4).
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
        --bearing 45 --output loop.geojson
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


def path_to_geojson(coords, start_lat, start_lon, target_distance_m, total_distance_m,
                     total_time_ms, overlap_way_count, outbound_way_count):
    return {
        "type": "Feature",
        "properties": {
            "start": [start_lon, start_lat],
            "target_distance_m": target_distance_m,
            "actual_distance_m": total_distance_m,
            "time_ms": total_time_ms,
            "outbound_way_count": outbound_way_count,
            "reused_way_count": overlap_way_count,
        },
        "geometry": {
            "type": "LineString",
            "coordinates": coords,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lat", type=float, required=True, help="Start latitude")
    parser.add_argument("--lon", type=float, required=True, help="Start longitude")
    parser.add_argument("--distance", type=float, required=True, help="Target loop distance in meters")
    parser.add_argument("--bearing", type=float, default=45.0, help="Outbound bearing in degrees (0=N, 90=E); default 45")
    parser.add_argument("--profile", default="foot", help="GraphHopper profile name (default: foot)")
    parser.add_argument("--penalty-multiplier", type=float, default=DEFAULT_REUSE_PENALTY_MULTIPLIER,
                         help=f"Priority multiplier applied to ways reused from the outbound leg (default: {DEFAULT_REUSE_PENALTY_MULTIPLIER})")
    parser.add_argument("--graphhopper-url", default=DEFAULT_GRAPHHOPPER_URL, help=f"GraphHopper server base URL (default: {DEFAULT_GRAPHHOPPER_URL})")
    parser.add_argument("--output", help="Write the loop as a GeoJSON Feature to this file")
    args = parser.parse_args()

    far_lat, far_lon = destination_point(args.lat, args.lon, args.bearing, args.distance / 2)

    try:
        outbound = fetch_outbound_leg(args.graphhopper_url, args.lat, args.lon, far_lat, far_lon, args.profile)
        outbound_ways = used_way_ids(outbound)
        return_leg = fetch_return_leg(
            args.graphhopper_url, far_lat, far_lon, args.lat, args.lon,
            args.profile, outbound_ways, args.penalty_multiplier,
        )
    except urllib.error.URLError as e:
        print(f"error: could not reach GraphHopper at {args.graphhopper_url}: {e}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    return_ways = used_way_ids(return_leg)
    reused = set(outbound_ways) & set(return_ways)

    total_distance = outbound["distance"] + return_leg["distance"]
    total_time = outbound["time"] + return_leg["time"]
    pct_off = 100 * (total_distance - args.distance) / args.distance

    print(f"start:              {args.lat}, {args.lon}")
    print(f"far point:          {far_lat:.6f}, {far_lon:.6f}  (bearing {args.bearing}°)")
    print(f"target distance:    {args.distance:.0f} m")
    print(f"actual distance:    {total_distance:.1f} m  ({pct_off:+.1f}%)")
    print(f"  outbound leg:     {outbound['distance']:.1f} m, {len(outbound_ways)} distinct ways")
    print(f"  return leg:       {return_leg['distance']:.1f} m, {len(return_ways)} distinct ways")
    print(f"reused ways:        {len(reused)} / {len(outbound_ways)} outbound ways also used on return")
    print(f"time:               {total_time / 1000:.0f} s")

    if args.output:
        coords = combine_legs(outbound, return_leg)
        feature = path_to_geojson(
            coords, args.lat, args.lon, args.distance, total_distance, total_time,
            len(reused), len(outbound_ways),
        )
        with open(args.output, "w") as f:
            json.dump(feature, f, indent=2)
        print(f"wrote GeoJSON to {args.output}")


if __name__ == "__main__":
    main()
