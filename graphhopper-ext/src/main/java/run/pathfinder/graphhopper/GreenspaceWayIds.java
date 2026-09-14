package run.pathfinder.graphhopper;

import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.File;
import java.io.IOException;
import java.util.HashSet;
import java.util.Set;

/**
 * Loads the way-id set precomputed offline by
 * scripts/compute_greenspace_way_ids.py (a flat JSON array of OSM way
 * ids that intersect the greenspace polygon set) into memory once, for
 * O(1) membership checks from GreenspaceTagParser during import.
 *
 * Deliberately just a plain java.util.HashSet<Long>, not a primitive
 * long-specialized collection (e.g. HPPC's LongHashSet, which
 * graphhopper-core itself uses internally for exactly this reason) --
 * boxing overhead for a few hundred thousand entries, checked once per
 * way during a one-time offline import, isn't worth a second dependency
 * for. Revisit if this ever needs to run somewhere memory-constrained
 * enough for it to matter.
 */
final class GreenspaceWayIds {
    private final Set<Long> wayIds;

    private GreenspaceWayIds(Set<Long> wayIds) {
        this.wayIds = wayIds;
    }

    static GreenspaceWayIds load(String path) {
        try {
            ObjectMapper mapper = new ObjectMapper();
            long[] ids = mapper.readValue(new File(path), long[].class);
            Set<Long> set = new HashSet<>(ids.length * 2);
            for (long id : ids) set.add(id);
            System.out.println("GreenspaceWayIds: loaded " + set.size() + " way ids from " + path);
            return new GreenspaceWayIds(set);
        } catch (IOException e) {
            throw new RuntimeException("Could not load greenspace way-id set from " + path
                    + " -- run scripts/compute_greenspace_way_ids.py first", e);
        }
    }

    boolean contains(long wayId) {
        return wayIds.contains(wayId);
    }
}
