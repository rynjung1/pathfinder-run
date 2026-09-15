package run.pathfinder.graphhopper;

import com.graphhopper.GraphHopperConfig;
import com.graphhopper.config.CHProfile;
import com.graphhopper.config.Profile;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * graphhopper-ext/README.md's "Tests" section named PathfinderImporter as
 * a whole as untestable without a real import -- true of main() (which
 * drives an actual GraphHopper import end to end), but not of
 * loadGraphHopperConfig, the one piece of this class that's a pure
 * function (a YAML file in, a GraphHopperConfig out) with no GraphHopper
 * import/side effects at all. Made package-private specifically so this
 * test file could reach it. Fixture below mirrors config-ontario.yml's
 * actual real shape (checked directly, not guessed) rather than an
 * invented one, so a real future change to that file's structure is what
 * this would actually catch a regression against.
 */
class PathfinderImporterTest {

    private static final String REALISTIC_CONFIG_YAML = """
            graphhopper:
              datareader.file: ../data/raw/ontario-260910.osm.pbf
              graph.location: graph-cache-ontario
              graph.encoded_values: foot_access, foot_average_speed, road_class, osm_way_id
              routing.timeout_ms: 5000
              profiles:
               - name: foot
                 custom_model_files: [pathfinder_foot.json]
              profiles_ch:
                - profile: foot
              profiles_lm: []
            """;

    private Path writeConfig(Path tempDir, String yaml) throws IOException {
        Path file = tempDir.resolve("config-test.yml");
        Files.writeString(file, yaml);
        return file;
    }

    @Test
    void loadsScalarConfigValuesIntoTheFlatConfig(@TempDir Path tempDir) throws Exception {
        Path configFile = writeConfig(tempDir, REALISTIC_CONFIG_YAML);

        GraphHopperConfig config = PathfinderImporter.loadGraphHopperConfig(configFile.toString());

        assertEquals("../data/raw/ontario-260910.osm.pbf", config.getString("datareader.file", null));
        assertEquals("graph-cache-ontario", config.getString("graph.location", null));
        // graph.encoded_values specifically -- not just any scalar value --
        // because it's the one PathfinderImporter's own main() actually
        // reads via getString (to append "greenspace" to it), so this is
        // the real usage this codebase depends on, not just any string
        // that happens to round-trip. (routing.timeout_ms was tried here
        // first and doesn't work with getString: it's an unquoted YAML
        // integer, not a string, and PMap.getString doesn't stringify a
        // non-string value -- a real property of PMap worth knowing, but
        // not a bug in loadGraphHopperConfig, and not what production code
        // actually does with that key.)
        assertEquals("foot_access, foot_average_speed, road_class, osm_way_id",
                config.getString("graph.encoded_values", null));
    }

    @Test
    void loadsProfilesWithTheirNames(@TempDir Path tempDir) throws Exception {
        Path configFile = writeConfig(tempDir, REALISTIC_CONFIG_YAML);

        GraphHopperConfig config = PathfinderImporter.loadGraphHopperConfig(configFile.toString());

        List<Profile> profiles = config.getProfiles();
        assertEquals(1, profiles.size());
        assertEquals("foot", profiles.get(0).getName());
    }

    @Test
    void loadsChProfilesReferencingARealProfileName(@TempDir Path tempDir) throws Exception {
        Path configFile = writeConfig(tempDir, REALISTIC_CONFIG_YAML);

        GraphHopperConfig config = PathfinderImporter.loadGraphHopperConfig(configFile.toString());

        List<CHProfile> chProfiles = config.getCHProfiles();
        assertEquals(1, chProfiles.size());
        assertEquals("foot", chProfiles.get(0).getProfile());
    }

    @Test
    void anEmptyProfilesLmListDoesNotErrorAndProducesAnEmptyList(@TempDir Path tempDir) throws Exception {
        Path configFile = writeConfig(tempDir, REALISTIC_CONFIG_YAML);

        GraphHopperConfig config = PathfinderImporter.loadGraphHopperConfig(configFile.toString());

        assertTrue(config.getLMProfiles().isEmpty());
    }

    @Test
    void aConfigWithNoTopLevelGraphhopperKeyThrowsAClearError(@TempDir Path tempDir) throws Exception {
        Path configFile = writeConfig(tempDir, "not_graphhopper:\n  foo: bar\n");

        Exception ex = assertThrows(IllegalArgumentException.class,
                () -> PathfinderImporter.loadGraphHopperConfig(configFile.toString()));

        assertTrue(ex.getMessage().contains("no top-level 'graphhopper:' key"),
                "expected a message naming the actual problem, got: " + ex.getMessage());
    }

    @Test
    void aConfigWithNoProfilesSectionAtAllIsHandledWithoutErroring(@TempDir Path tempDir) throws Exception {
        // profiles/profiles_ch/profiles_lm are all read via Map.remove,
        // which returns null rather than throwing when the key is
        // absent -- confirms loadGraphHopperConfig's null-checks around
        // each actually cover a config that omits them entirely, not
        // just one that sets them to an empty list.
        Path configFile = writeConfig(tempDir, "graphhopper:\n  graph.location: graph-cache-test\n");

        GraphHopperConfig config = PathfinderImporter.loadGraphHopperConfig(configFile.toString());

        assertTrue(config.getProfiles().isEmpty());
        assertTrue(config.getCHProfiles().isEmpty());
        assertTrue(config.getLMProfiles().isEmpty());
    }
}
