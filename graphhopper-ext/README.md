# graphhopper-ext

Restores greenness/park-proximity scoring (§5 point 1's second bullet) at
full-Ontario scale, after the original mechanism (`custom_areas`, a live
per-query point-in-polygon lookup) was confirmed to be the root cause of
`/route` being unusably slow in flexible mode at that scale — see git
history around that removal, and `../graphhopper/pathfinder_foot.json`'s
comment, for the full investigation.

## What this actually is

A small **standalone import tool**, not a modified web server. Confirmed
by decompiling `graphhopper-web.jar`: `GraphHopper.load()` — all the
production server ever does, since it only ever loads a pre-built
`graph-cache`, never imports (see `../deploy/README.md`) — reconstructs
its `EncodingManager` via `EncodingManager.fromProperties(...)`, entirely
independent of the `ImportRegistry` that only matters at import time. So
the stock, unmodified `graphhopper-web.jar` serves whatever this tool
produces, with zero server-side code changes. The custom registry only
needs to exist here, and only ever runs once, at import time.

## Mechanism

1. **Offline, in Python** (`../scripts/compute_greenspace_way_ids.py`):
   the actual spatial join — does a way's geometry intersect any
   greenspace polygon — computed once, with a real spatial index
   (shapely's STRtree), producing a flat JSON array of matched OSM way
   ids (`data/greenspace_way_ids_ontario.json`). This has to happen in
   Python, not Java: GraphHopper's `TagParser` callback during import
   only exposes a way's tags and raw OSM node ids, not resolved
   coordinates, so a live point-in-polygon check isn't available inside
   the Java import path without adding geometry-resolution machinery
   this project's existing `osmium` tooling already does better.
2. **At import time, in Java** (this module): `GreenspaceImportRegistry`
   wraps GraphHopper's own `DefaultImportRegistry`, adding one new
   encoded value — `greenspace`, a boolean — delegating every other name
   unchanged. `GreenspaceTagParser` sets it per way via an O(1) lookup
   against the precomputed id set (`GreenspaceWayIds`) — no geometry, no
   spatial index, in Java at all.
3. **At serve time**: nothing changes. The stock server reads
   `greenspace` back out of the graph-cache's own stored properties, the
   same as `road_class`/`foot_access`/etc. always have been.

## Rebuilding the graph (when greenness needs to be included)

From `graphhopper/` (same working-directory convention as
`run-graphhopper.sh` — `config-ontario.yml`'s `datareader.file`/
`graph.location` are relative to it):

```bash
# 1. Only if the OSM extract itself changed -- not needed for a routine
#    rebuild against the same extract, since the way-id set doesn't
#    change unless the underlying map data does.
cd ../scripts
python3 compute_greenspace_way_ids.py \
    --extract ../data/raw/ontario-260910.osm.pbf \
    --output ../data/greenspace_way_ids_ontario.json

# 2. Build this module (once, or after editing it)
cd ../graphhopper-ext
mvn package   # needs JDK 17 -- see run-import.sh's own JAVA_HOME detection

# 3. Actually rebuild the graph
cd ../graphhopper
./run-import.sh
```

`run-import.sh` mirrors `run-graphhopper.sh`'s JDK-17 detection exactly
(same `LINKED_JDK`/`BREW_JDK` fallback), but invokes
`run.pathfinder.graphhopper.PathfinderImporter` with both
`graphhopper-web.jar` and `graphhopper-ext/target/graphhopper-ext-1.0.0.jar`
on the classpath, instead of the stock jar's own entry point. It does
**not** start a server — once it finishes, `graph-cache-ontario/` is
ready, and serving it is `run-graphhopper.sh` exactly as before, unchanged.

## Why a standalone importer instead of extending the real server

Investigated first, before writing any code (see the commit history for
the full trail): `GraphHopperApplication` (the actual Dropwizard entry
point) is `final` — can't be subclassed. The next layer down,
`GraphHopperBundle`, isn't final, but its `run()` method does a fair
amount of Jersey/REST resource registration inline that would need
faithfully reimplementing to safely override just the `GraphHopper`
construction. Once the `load()` vs. `importOrLoad()` registry
independence was confirmed (see above), none of that was necessary —
this tool never touches Dropwizard, Jersey, or any web-server code at
all, which is most of what made the original "multi-day, might need a
modified server" estimate for this work as large as it was.

## Validated against

- Compiles against real `graphhopper-core:9.1` (Maven Central), matching
  the exact version embedded in `graphhopper-web.jar` (confirmed via its
  own `pom.properties`, not assumed).
- A real import, small scale (Waterloo Region) and full scale (all of
  Ontario, 55s — matching the no-greenness baseline of ~62s, nothing
  like the old live-lookup approach's ~207s subnetwork-marking blowup)
  both completed and produced a graph-cache the stock server loaded
  successfully — confirmed via the server's own log line listing
  `"greenspace"` among its loaded encoded values.
- CH speed mode stays fully engaged post-fix (`dijkstrabi|ch-routing` in
  the request log, not a flexible-mode fallback) — the actual point of
  this whole exercise.
- Real, varying effect on real routes, not a stuck value: requesting
  `details=greenspace` on a live route returns segments genuinely
  alternating `true`/`false` matching real geography.
- The offline way-id precomputation validated against a fact already
  established earlier this project: Westmount Road North (way
  `38896608`, confirmed crossing a greenspace polygon during the
  original `in_greenspace` work) is present in the computed set.
