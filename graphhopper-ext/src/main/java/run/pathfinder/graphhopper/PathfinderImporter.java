package run.pathfinder.graphhopper;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.dataformat.yaml.YAMLFactory;
import com.graphhopper.GraphHopper;
import com.graphhopper.GraphHopperConfig;
import com.graphhopper.config.CHProfile;
import com.graphhopper.config.LMProfile;
import com.graphhopper.config.Profile;
import com.graphhopper.util.PMap;

import java.io.File;
import java.util.List;
import java.util.Map;

/**
 * Standalone import tool -- NOT a web server, deliberately. See this
 * module's pom.xml header: GraphHopper.load() (all the production
 * server ever does, per the "rsync a pre-built graph-cache" deploy plan)
 * reconstructs its EncodingManager from the graph-cache's own stored
 * properties, independent of the ImportRegistry -- confirmed by
 * decompiling graphhopper-web.jar's GraphHopper.load(), which has zero
 * references to buildEncodingManager/buildOSMParsers/importRegistry
 * anywhere in its bytecode. So the custom registry (GreenspaceImportRegistry)
 * only needs to exist here, at import time; the stock, unmodified
 * graphhopper-web.jar serves whatever this tool produces.
 *
 * Reads the real config-ontario.yml directly (not a hand-duplicated
 * subset of its settings) so this can't silently drift out of sync with
 * whatever the production config actually says.
 *
 * Usage:
 *   java -cp graphhopper-web.jar:graphhopper-ext-1.0.0.jar \
 *     run.pathfinder.graphhopper.PathfinderImporter \
 *     config-ontario.yml data/greenspace_way_ids_ontario.json
 *
 * Run from graphhopper/, same working-directory convention as
 * run-graphhopper.sh (config-ontario.yml's own datareader.file/
 * graph.location paths are relative to that).
 */
public final class PathfinderImporter {
    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            System.err.println("Usage: PathfinderImporter <config.yml> <greenspace_way_ids.json>");
            System.exit(1);
        }
        String configPath = args[0];
        String wayIdsPath = args[1];

        GraphHopperConfig ghConfig = loadGraphHopperConfig(configPath);

        // Add the new encoded value to whatever the config already lists --
        // config-ontario.yml itself doesn't need editing to add "greenspace"
        // to graph.encoded_values by hand; this tool owns that responsibility
        // since it's the one thing that actually knows this encoded value
        // exists.
        String existing = ghConfig.getString("graph.encoded_values", "");
        String combined = existing.isEmpty() ? GreenspaceImportRegistry.ENCODED_VALUE_NAME
                : existing + "," + GreenspaceImportRegistry.ENCODED_VALUE_NAME;
        ghConfig.putObject("graph.encoded_values", combined);

        GreenspaceWayIds wayIds = GreenspaceWayIds.load(wayIdsPath);

        GraphHopper gh = new GraphHopper();
        gh.setImportRegistry(new GreenspaceImportRegistry(wayIds));
        gh.init(ghConfig);

        System.out.println("Starting import (this rebuilds the graph-cache from scratch)...");
        long start = System.currentTimeMillis();
        gh.importOrLoad();
        long elapsedS = (System.currentTimeMillis() - start) / 1000;
        System.out.println("Import complete in " + elapsedS + "s. Encoded values: "
                + gh.getEncodingManager().toEncodedValuesAsString());
        gh.close();
    }

    @SuppressWarnings("unchecked")
    private static GraphHopperConfig loadGraphHopperConfig(String configPath) throws Exception {
        ObjectMapper yamlMapper = new ObjectMapper(new YAMLFactory());
        Map<String, Object> root = yamlMapper.readValue(new File(configPath), Map.class);
        Map<String, Object> gh = (Map<String, Object>) root.get("graphhopper");
        if (gh == null) {
            throw new IllegalArgumentException(configPath + " has no top-level 'graphhopper:' key");
        }

        List<Object> rawProfiles = (List<Object>) gh.remove("profiles");
        List<Object> rawChProfiles = (List<Object>) gh.remove("profiles_ch");
        List<Object> rawLmProfiles = (List<Object>) gh.remove("profiles_lm");

        // Everything left in `gh` after pulling out the structured
        // profile lists is scalar config (datareader.file, graph.location,
        // graph.encoded_values, routing.timeout_ms, etc.) -- goes straight
        // into the flat PMap GraphHopperConfig itself wraps.
        GraphHopperConfig config = new GraphHopperConfig(new PMap(gh));

        ObjectMapper plainMapper = new ObjectMapper();
        if (rawProfiles != null) {
            config.setProfiles(convertList(plainMapper, rawProfiles, Profile.class));
        }
        if (rawChProfiles != null) {
            config.setCHProfiles(convertList(plainMapper, rawChProfiles, CHProfile.class));
        }
        if (rawLmProfiles != null) {
            config.setLMProfiles(convertList(plainMapper, rawLmProfiles, LMProfile.class));
        }
        return config;
    }

    private static <T> List<T> convertList(ObjectMapper mapper, List<Object> raw, Class<T> type) {
        return raw.stream().map(item -> mapper.convertValue(item, type)).collect(java.util.stream.Collectors.toList());
    }

    private PathfinderImporter() {
    }
}
