package run.pathfinder.graphhopper;

import com.graphhopper.reader.ReaderWay;
import com.graphhopper.routing.ev.BooleanEncodedValue;
import com.graphhopper.routing.ev.EdgeIntAccess;
import com.graphhopper.routing.util.parsers.TagParser;
import com.graphhopper.storage.IntsRef;

/**
 * Sets the "greenspace" boolean encoded value per way, at import time,
 * from the offline-precomputed way-id set (GreenspaceWayIds) -- the
 * actual point-in-polygon spatial join already happened once, in Python
 * (scripts/compute_greenspace_way_ids.py), so this is a trivial O(1)
 * lookup per way, not geometry work. See that script's own header for
 * why: GraphHopper's TagParser callback only exposes a ReaderWay's tags
 * and raw OSM node ids, not resolved coordinates, so doing the spatial
 * test here directly wasn't an available option without adding
 * geometry-resolution machinery this project's existing Python/osmium
 * tooling already does better.
 */
final class GreenspaceTagParser implements TagParser {
    private final BooleanEncodedValue greenspaceEnc;
    private final GreenspaceWayIds wayIds;

    GreenspaceTagParser(BooleanEncodedValue greenspaceEnc, GreenspaceWayIds wayIds) {
        this.greenspaceEnc = greenspaceEnc;
        this.wayIds = wayIds;
    }

    @Override
    public void handleWayTags(int edgeId, EdgeIntAccess edgeIntAccess, ReaderWay way, IntsRef relationFlags) {
        if (wayIds.contains(way.getId())) {
            greenspaceEnc.setBool(false, edgeId, edgeIntAccess, true);
        }
    }
}
