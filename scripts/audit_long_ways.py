#!/usr/bin/env python3
"""
Pathfinder Run -- one-time audit: which OSM ways in the clipped extract are
"long" (§5, the osm_way_id-blanket-penalty investigation).

Why this exists: the edge-reuse penalty (generate_loop.py's fetch_return_leg)
and the closures exclusion both identify a way to avoid by its whole
osm_way_id, because that's the only identity GraphHopper's custom_model DSL
can actually condition on (verified empirically -- the finer per-edge
`edge_id`/`edge_key` path details exist and are requestable, but are
Path-level artifacts, not registered EncodedValues, so a custom_model `if`
condition referencing them is rejected outright: "'edge_id' not
available"). For an ordinary short way that's a non-issue -- the whole way
IS the segment used. For a handful of very long ways (rail trails,
regional trailways), touching a short stretch currently blanket-penalizes
the entire remaining length.

The fix (see fetch_return_leg) is to use geometry (a small buffer polygon
around the ACTUALLY-touched coordinate range, via a custom_model `areas`
FeatureCollection + `in_<id>` condition) instead of whole-way ID matching,
but only for the minority of ways long enough for that gap to matter --
for every other way, the existing OR-chain is simpler, cheaper, and
already produces the identical result. This script produces the lookup
table that draws that line: data/long_ways.json, {way_id: length_m} for
every routable way over LONG_WAY_THRESHOLD_M in the current clipped
extract.

Deliberately a one-time offline step, not a live query: there's no
Overpass dependency at request time (fragile, network-dependent, and
Overpass was never part of the runtime pipeline -- it was only used
once, interactively, during the region-boundary fetch). `osmium` is
already a hard dependency of this project (used for the region clip), so
this reuses that instead of adding pyosmium or another geometry library.

Usage:
    python3 scripts/audit_long_ways.py
    python3 scripts/audit_long_ways.py --threshold-m 500 --output /tmp/test.json
"""
import argparse
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

EARTH_RADIUS_M = 6371000
DEFAULT_EXTRACT_PATH = str(Path(__file__).parent.parent / "data" / "raw" / "waterloo-region-clipped.osm.pbf")
DEFAULT_OUTPUT_PATH = str(Path(__file__).parent.parent / "data" / "long_ways.json")
LONG_WAY_THRESHOLD_M = 1000  # matches the threshold implied by the original audit
                              # ("153 ways over 1km") referenced throughout this
                              # session's comments -- ad hoc, like this codebase's
                              # other tunables, open to revision.


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def line_length_m(coords):
    """coords: list of [lon, lat] pairs."""
    total = 0.0
    for (lon1, lat1), (lon2, lat2) in zip(coords, coords[1:]):
        total += haversine_m(lat1, lon1, lat2, lon2)
    return total


def audit_long_ways(extract_path=DEFAULT_EXTRACT_PATH, threshold_m=LONG_WAY_THRESHOLD_M):
    """Runs osmium tags-filter (restrict to routable highways) + osmium export
    (linestrings with their original OSM id) against the extract, then sums
    each way's real-world length via haversine over its full geometry.
    Returns {way_id: length_m} for every way at or above threshold_m.

    Uses two temp files rather than piping osmium's two subcommands
    together -- tags-filter's output is a binary PBF, export needs a real
    file (not point-blank stdin support confirmed), and both are small
    enough here (this extract's highway-tagged subset is a few MB) that
    the extra disk round-trip isn't worth avoiding."""
    with tempfile.TemporaryDirectory() as tmp:
        highways_pbf = str(Path(tmp) / "highways.osm.pbf")
        geojsonseq_path = str(Path(tmp) / "highways.geojsonseq")

        subprocess.run(
            ["osmium", "tags-filter", extract_path, "w/highway", "-o", highways_pbf, "--overwrite"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["osmium", "export", highways_pbf, "-u", "type_id", "--geometry-types", "linestring",
             "-f", "geojsonseq", "-o", geojsonseq_path, "--overwrite"],
            check=True, capture_output=True,
        )

        long_ways = {}
        with open(geojsonseq_path) as f:
            for line in f:
                line = line.strip().lstrip("\x1e")  # osmium's geojsonseq uses RS (0x1e) record separators
                if not line:
                    continue
                feature = json.loads(line)
                feature_id = feature.get("id", "")
                if not feature_id.startswith("w"):
                    continue  # skip anything unexpected; "w" prefix = a way-derived linestring
                way_id = int(feature_id[1:])
                coords = feature["geometry"]["coordinates"]
                length = line_length_m(coords)
                if length >= threshold_m:
                    # A single OSM way can be split into multiple linestring features
                    # by osmium export (e.g. if it self-intersects) -- sum rather than
                    # overwrite, so the recorded length reflects the whole way.
                    long_ways[way_id] = long_ways.get(way_id, 0.0) + length

        return long_ways


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--extract", default=DEFAULT_EXTRACT_PATH, help="Path to the clipped .osm.pbf extract")
    parser.add_argument("--threshold-m", type=float, default=LONG_WAY_THRESHOLD_M,
                         help=f"Minimum way length in meters to include (default: {LONG_WAY_THRESHOLD_M})")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_PATH, help="Where to write the JSON lookup")
    args = parser.parse_args()

    print(f"auditing {args.extract} for ways >= {args.threshold_m:.0f}m...", file=sys.stderr)
    long_ways = audit_long_ways(args.extract, args.threshold_m)
    print(f"found {len(long_ways)} long way(s)", file=sys.stderr)

    worst = sorted(long_ways.items(), key=lambda kv: -kv[1])[:5]
    for way_id, length in worst:
        print(f"  way {way_id}: {length:.0f}m", file=sys.stderr)

    with open(args.output, "w") as f:
        json.dump({str(k): v for k, v in sorted(long_ways.items())}, f, indent=2)
    print(f"wrote {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
