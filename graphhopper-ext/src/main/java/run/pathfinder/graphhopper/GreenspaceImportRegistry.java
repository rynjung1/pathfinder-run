package run.pathfinder.graphhopper;

import com.graphhopper.routing.ev.DefaultImportRegistry;
import com.graphhopper.routing.ev.ImportRegistry;
import com.graphhopper.routing.ev.ImportUnit;
import com.graphhopper.routing.ev.SimpleBooleanEncodedValue;

/**
 * Wraps GraphHopper's own DefaultImportRegistry, adding exactly one new
 * name -- "greenspace" -- and delegating everything else unchanged.
 * Confirmed via decompiling graphhopper-web.jar that no config-level or
 * OSM-tag-preprocessing path can add a new encoded value (the registry
 * is the sanctioned extension point, ImportRegistry.createImportUnit is
 * a real public interface, not an internal detail) -- this is that
 * extension point used exactly as intended, not a workaround.
 *
 * The encoded value name here ("greenspace") is what pathfinder_foot.json's
 * custom_model references directly (e.g. {"if": "greenspace", ...}) and
 * what graph.encoded_values in the GraphHopper config must list -- all
 * three have to agree, they're not independently configurable.
 */
final class GreenspaceImportRegistry implements ImportRegistry {
    static final String ENCODED_VALUE_NAME = "greenspace";

    private final DefaultImportRegistry delegate = new DefaultImportRegistry();
    private final GreenspaceWayIds wayIds;

    GreenspaceImportRegistry(GreenspaceWayIds wayIds) {
        this.wayIds = wayIds;
    }

    @Override
    public ImportUnit createImportUnit(String name) {
        if (ENCODED_VALUE_NAME.equals(name)) {
            return ImportUnit.create(
                    name,
                    properties -> new SimpleBooleanEncodedValue(ENCODED_VALUE_NAME),
                    (lookup, properties) -> new GreenspaceTagParser(
                            lookup.getBooleanEncodedValue(ENCODED_VALUE_NAME), wayIds)
            );
        }
        return delegate.createImportUnit(name);
    }
}
