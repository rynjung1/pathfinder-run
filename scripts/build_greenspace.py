#!/usr/bin/env python3
"""
Pathfinder Run -- one-time build: park/green-space polygons for GraphHopper's
static custom_areas mechanism (§5, point 1's second bullet: "Greenness" --
proximity to parks/green space, derived from OSM land-use tags).

Mirrors audit_long_ways.py's pattern exactly (same tool, same reasoning for
using it offline rather than a live Overpass call): `osmium` is already a
project dependency, so this reuses it instead of adding a geometry library.

Tag scope, checked against this specific extract (not assumed): leisure=park
(774 ways + 24 relations -- includes real, well-known parks like Waterloo
Park, confirmed by spot-check), natural=wood (2281 ways), landuse=forest
(52 ways -- a distinct tagging convention some mappers use instead of
natural=wood for the same kind of land), leisure=nature_reserve (126 ways),
landuse=recreation_ground (57 ways), leisure=garden (124 ways). Deliberately
EXCLUDES landuse=grass (2003 ways) -- checked and it's too broad a bucket
in this extract (traffic islands, individual residential lawns, verges),
not a reliable "this is parkland" signal on its own.

Output format: a single GeoJSON Feature (not one per park) with id
"greenspace" and a MultiPolygon geometry combining every matched polygon's
rings -- confirmed via a live test against the running server that
GraphHopper's custom_areas loader is fine with one Feature holding many
disjoint polygons (exactly how the closures/long-way buffer-polygon fix
already uses MultiPolygon for the same reason: one `in_<id>` condition,
many disjoint areas). Avoids any question about duplicate/unique "id"
handling across thousands of individual park features.

Usage:
    python3 scripts/build_greenspace.py
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_EXTRACT_PATH = str(Path(__file__).parent.parent / "data" / "raw" / "waterloo-region-clipped.osm.pbf")
# A dedicated subdirectory, not data/ directly: GraphHopper's custom_areas.directory
# scan requires every .geojson file it finds to have an "id" property on its
# Feature(s) (confirmed in config.yml's own scaffolded comment) -- checked
# data/boundaries/region-of-waterloo.geojson (already sitting in data/) and its
# Feature has no "id" at all, so pointing the scan at data/ directly would break
# graph import on an unrelated file. A dedicated data/greenspace/ directory avoids
# that collision outright rather than relying on GraphHopper ignoring it.
DEFAULT_OUTPUT_PATH = str(Path(__file__).parent.parent / "data" / "greenspace" / "greenspace.geojson")
GREENSPACE_TAGS = [
    "leisure=park",
    "natural=wood",
    "landuse=forest",
    "leisure=nature_reserve",
    "landuse=recreation_ground",
    "leisure=garden",
]


def build_greenspace(extract_path=DEFAULT_EXTRACT_PATH, tags=GREENSPACE_TAGS):
    """Filters the extract to just the tagged park/greenspace ways+relations,
    exports their assembled polygon geometry (osmium assembles closed ways
    and multipolygon relations into proper Polygon/MultiPolygon geometry --
    not reimplemented here), and merges every ring into one combined
    MultiPolygon. Returns the GeoJSON Feature dict (not yet wrapped in a
    FeatureCollection -- see main())."""
    with tempfile.TemporaryDirectory() as tmp:
        filtered_pbf = str(Path(tmp) / "greenspace.osm.pbf")
        geojsonseq_path = str(Path(tmp) / "greenspace.geojsonseq")

        filter_args = [f"w/{t}" for t in tags] + [f"r/{t}" for t in tags]
        subprocess.run(
            ["osmium", "tags-filter", extract_path, *filter_args, "-o", filtered_pbf, "--overwrite"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["osmium", "export", filtered_pbf, "-u", "type_id", "--geometry-types", "polygon",
             "-f", "geojsonseq", "-o", geojsonseq_path, "--overwrite", "-e"],
            check=True, capture_output=True,
        )

        all_polygons = []
        feature_count = 0
        with open(geojsonseq_path) as f:
            for line in f:
                line = line.strip().lstrip("\x1e")
                if not line:
                    continue
                feature = json.loads(line)
                feature_count += 1
                geometry = feature["geometry"]
                if geometry["type"] == "Polygon":
                    all_polygons.append(geometry["coordinates"])
                elif geometry["type"] == "MultiPolygon":
                    all_polygons.extend(geometry["coordinates"])
                # else: unexpected geometry type for a polygon export -- skip rather than guess

        print(f"merged {feature_count} park/greenspace feature(s) into "
              f"{len(all_polygons)} polygon(s)", file=sys.stderr)
        return {
            "type": "Feature",
            "id": "greenspace",
            "properties": {},
            "geometry": {"type": "MultiPolygon", "coordinates": all_polygons},
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--extract", default=DEFAULT_EXTRACT_PATH, help="Path to the clipped .osm.pbf extract")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_PATH, help="Where to write the GeoJSON FeatureCollection")
    args = parser.parse_args()

    print(f"building greenspace polygons from {args.extract}...", file=sys.stderr)
    feature = build_greenspace(args.extract)
    collection = {"type": "FeatureCollection", "features": [feature]}

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(collection, f)
    print(f"wrote {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
