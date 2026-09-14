#!/usr/bin/env python3
"""
Pathfinder Run -- offline precomputation for the greenness-at-scale fix
(the Java TagParser work; see graphhopper-ext/).

Confirmed during that investigation: GraphHopper's custom TagParser
callback (handleWayTags) receives a ReaderWay exposing only tags and
raw OSM node IDs (getNodes()) -- no resolved lat/lon geometry -- so a
live point-in-polygon check inside the Java import path isn't available
the way it was for the old (removed) custom_areas mechanism. Rather than
add geometry-resolution machinery to the Java side, this does the actual
spatial join offline, once, in Python -- reusing this project's existing
GIS tooling discipline (osmium, already a dependency) plus shapely for
the actual polygon intersection test (a new dependency, justified: no
pure-Python geometry test this project already has covers "does a
multi-point linestring intersect any of ~300k+ disjoint polygons"
efficiently without a spatial index, and reimplementing an R-tree by
hand isn't a good use of anyone's time when a mature library exists).

Output: a flat JSON array of OSM way ids that intersect the greenspace
polygon set -- deliberately NOT the same format as
data/greenspace-ontario/greenspace.geojson (which is now unused at
runtime, kept only for whenever this script needs to regenerate its
polygon input). The Java TagParser only needs O(1) set-membership
against this list per way, at import time -- no geometry, no shapely,
no spatial index in Java at all.

"Intersects" here means the whole linestring geometry, not just
endpoints or a single representative point -- a way that clips through
the corner of a park should count, not just one whose first node happens
to land inside it.

Usage:
    python3 scripts/compute_greenspace_way_ids.py \
        --extract data/raw/waterloo-region-clipped.osm.pbf \
        --output data/greenspace_way_ids.json
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from shapely.geometry import shape
from shapely.strtree import STRtree

from build_greenspace import DEFAULT_EXTRACT_PATH, build_greenspace

DEFAULT_OUTPUT_PATH = str(Path(__file__).parent.parent / "data" / "greenspace_way_ids.json")

# Every highway=* value the routing profile (pathfinder_foot.json) actually
# assigns a priority tier to -- see that file's own priority rules. No point
# computing intersection for way types the profile doesn't weight
# differently either way (e.g. this deliberately excludes things like
# highway=steps/construction that never appear in a priority condition).
ROUTABLE_HIGHWAY_VALUES = [
    "path", "footway", "pedestrian", "living_street",
    "primary", "secondary", "trunk", "motorway",
    "residential", "tertiary", "unclassified", "service",
]


def export_routable_way_geometries(extract_path):
    """Ways with a highway=<routable value> tag, as (way_id, linestring
    coords) pairs -- osmium export with --geometry-types linestring
    resolves node ids to actual lat/lon, unlike the raw ReaderWay a Java
    TagParser sees during GraphHopper's own import."""
    filter_args = [f"w/highway={v}" for v in ROUTABLE_HIGHWAY_VALUES]
    with tempfile.TemporaryDirectory() as tmp:
        filtered_pbf = str(Path(tmp) / "routable.osm.pbf")
        geojsonseq_path = str(Path(tmp) / "routable.geojsonseq")
        subprocess.run(
            ["osmium", "tags-filter", extract_path, *filter_args, "-o", filtered_pbf, "--overwrite"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["osmium", "export", filtered_pbf, "-u", "type_id", "--geometry-types", "linestring",
             "-f", "geojsonseq", "-o", geojsonseq_path, "--overwrite", "-e"],
            check=True, capture_output=True,
        )
        ways = []
        with open(geojsonseq_path) as f:
            for line in f:
                line = line.strip().lstrip("\x1e")
                if not line:
                    continue
                feature = json.loads(line)
                # osmium's -u type_id puts the id at the top level as
                # "w<id>" (a type prefix + the numeric OSM id), e.g.
                # "w3994367" -- not under properties["@id"] (checked
                # directly against real output, not assumed from
                # build_greenspace.py's polygon export, which never
                # reads the id field at all).
                way_id = int(feature["id"].lstrip("w"))
                ways.append((way_id, feature["geometry"]))
        return ways


def compute_greenspace_way_ids(extract_path=DEFAULT_EXTRACT_PATH):
    print("building greenspace polygons...", file=sys.stderr)
    greenspace_feature = build_greenspace(extract_path)
    polygons = [shape({"type": "Polygon", "coordinates": rings})
                for rings in greenspace_feature["geometry"]["coordinates"]]
    print(f"  {len(polygons)} polygon(s)", file=sys.stderr)

    print("exporting routable way geometries...", file=sys.stderr)
    ways = export_routable_way_geometries(extract_path)
    print(f"  {len(ways)} way(s)", file=sys.stderr)

    print("building spatial index and testing intersections...", file=sys.stderr)
    tree = STRtree(polygons)
    matched_way_ids = []
    for way_id, geometry in ways:
        line = shape(geometry)
        # STRtree.query first (cheap bounding-box candidates), then a real
        # .intersects() on just those candidates -- not testing every way
        # against every polygon directly, which is what made the old live
        # per-query version of this unusably slow at province scale.
        candidate_indices = tree.query(line)
        if any(polygons[i].intersects(line) for i in candidate_indices):
            matched_way_ids.append(way_id)

    print(f"  {len(matched_way_ids)} of {len(ways)} routable ways intersect greenspace", file=sys.stderr)
    return matched_way_ids


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--extract", default=DEFAULT_EXTRACT_PATH, help="Path to the clipped .osm.pbf extract")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_PATH, help="Where to write the way-id JSON array")
    args = parser.parse_args()

    way_ids = compute_greenspace_way_ids(args.extract)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(way_ids, f)
    print(f"wrote {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
