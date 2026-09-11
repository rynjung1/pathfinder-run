#!/usr/bin/env python3
"""
Pathfinder Run -- v1 loop generation, step 1 (docs/running-app-architecture.md §7).

The simplest possible version: given a start point and a target distance,
picks a single "far point" roughly half the target distance away along a
bearing, and asks GraphHopper for one out-and-back route through it
(start -> far point -> start).

Deliberately NOT here yet (later steps in §5):
- No edge-reuse penalty -- the return leg will very likely retrace the
  outbound leg almost exactly, since nothing here penalizes that.
- No candidate generation or scoring -- one bearing in, one route out.
- No alternatives.

This script exists only to prove the request/response round trip against a
running GraphHopper server works end to end.

Usage:
    python3 scripts/generate_loop.py --lat 43.4643 --lon -80.5204 --distance 5000
    python3 scripts/generate_loop.py --lat 43.4643 --lon -80.5204 --distance 5000 \
        --bearing 45 --output loop.geojson
"""
import argparse
import json
import math
import sys
import urllib.parse
import urllib.request

EARTH_RADIUS_M = 6371000
DEFAULT_GRAPHHOPPER_URL = "http://localhost:8989"


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


def fetch_loop_route(base_url, start_lat, start_lon, far_lat, far_lon, profile="foot"):
    """Ask GraphHopper for a single route through start -> far point -> start
    (an out-and-back loop) and return the parsed response."""
    params = [
        ("point", f"{start_lat},{start_lon}"),
        ("point", f"{far_lat},{far_lon}"),
        ("point", f"{start_lat},{start_lon}"),
        ("profile", profile),
        ("points_encoded", "false"),
    ]
    url = f"{base_url}/route?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as resp:
        body = json.loads(resp.read())

    if "paths" not in body:
        raise RuntimeError(f"GraphHopper returned no path: {json.dumps(body)}")
    return body["paths"][0]


def path_to_geojson(path, start_lat, start_lon, target_distance_m):
    coords = path["points"]["coordinates"]  # already [lon, lat]
    return {
        "type": "Feature",
        "properties": {
            "start": [start_lon, start_lat],
            "target_distance_m": target_distance_m,
            "actual_distance_m": path["distance"],
            "time_ms": path["time"],
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
    parser.add_argument("--graphhopper-url", default=DEFAULT_GRAPHHOPPER_URL, help=f"GraphHopper server base URL (default: {DEFAULT_GRAPHHOPPER_URL})")
    parser.add_argument("--output", help="Write the loop as a GeoJSON Feature to this file")
    args = parser.parse_args()

    # Naive half-target straight-line offset for the far point. The real
    # walked distance to and from it will differ once GraphHopper routes it
    # onto the actual street/path network -- that's expected and fine here.
    far_lat, far_lon = destination_point(args.lat, args.lon, args.bearing, args.distance / 2)

    try:
        path = fetch_loop_route(args.graphhopper_url, args.lat, args.lon, far_lat, far_lon, args.profile)
    except urllib.error.URLError as e:
        print(f"error: could not reach GraphHopper at {args.graphhopper_url}: {e}", file=sys.stderr)
        sys.exit(1)

    actual_m = path["distance"]
    pct_off = 100 * (actual_m - args.distance) / args.distance

    print(f"start:            {args.lat}, {args.lon}")
    print(f"far point:        {far_lat:.6f}, {far_lon:.6f}  (bearing {args.bearing}°)")
    print(f"target distance:  {args.distance:.0f} m")
    print(f"actual distance:  {actual_m:.1f} m  ({pct_off:+.1f}%)")
    print(f"time:             {path['time'] / 1000:.0f} s")
    print(f"geometry points:  {len(path['points']['coordinates'])}")

    if args.output:
        feature = path_to_geojson(path, args.lat, args.lon, args.distance)
        with open(args.output, "w") as f:
            json.dump(feature, f, indent=2)
        print(f"wrote GeoJSON to {args.output}")


if __name__ == "__main__":
    main()
