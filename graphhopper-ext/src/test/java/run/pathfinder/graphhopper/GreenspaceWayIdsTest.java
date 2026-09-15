package run.pathfinder.graphhopper;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Was zero automated test coverage anywhere in graphhopper-ext/ before
 * this -- found on a backend sweep. GreenspaceWayIds is the most
 * self-contained piece of this extension (plain file I/O + set
 * membership, no GraphHopper types at all), so it needed no fixtures
 * beyond a real temp JSON file.
 */
class GreenspaceWayIdsTest {

    @Test
    void containsReturnsTrueForIdsInTheLoadedFile(@TempDir Path tempDir) throws IOException {
        Path file = tempDir.resolve("way_ids.json");
        Files.writeString(file, "[100, 200, 300]");

        GreenspaceWayIds wayIds = GreenspaceWayIds.load(file.toString());

        assertTrue(wayIds.contains(100L));
        assertTrue(wayIds.contains(200L));
        assertTrue(wayIds.contains(300L));
    }

    @Test
    void containsReturnsFalseForIdsNotInTheLoadedFile(@TempDir Path tempDir) throws IOException {
        Path file = tempDir.resolve("way_ids.json");
        Files.writeString(file, "[100, 200, 300]");

        GreenspaceWayIds wayIds = GreenspaceWayIds.load(file.toString());

        assertFalse(wayIds.contains(999L));
        assertFalse(wayIds.contains(0L));
    }

    @Test
    void handlesAnEmptyArray(@TempDir Path tempDir) throws IOException {
        Path file = tempDir.resolve("way_ids.json");
        Files.writeString(file, "[]");

        GreenspaceWayIds wayIds = GreenspaceWayIds.load(file.toString());

        assertFalse(wayIds.contains(1L));
    }

    @Test
    void loadWrapsAMissingFileInAClearRuntimeException() {
        RuntimeException ex = assertThrows(RuntimeException.class,
                () -> GreenspaceWayIds.load("/nonexistent/path/way_ids.json"));

        // The whole point of wrapping IOException here (see this class's
        // own comment) is a message that tells whoever hits this exactly
        // what to run next, not a bare stack trace -- assert that
        // guidance is actually present, not just that SOME exception was
        // thrown.
        assertTrue(ex.getMessage().contains("compute_greenspace_way_ids.py"),
                "expected the error to point at the script that generates this file, got: " + ex.getMessage());
    }

    @Test
    void loadHandlesLargeIdsCorrectly(@TempDir Path tempDir) throws IOException {
        // OSM way ids comfortably exceed 32-bit int range in practice
        // (current OSM way ids are in the billions) -- confirms the
        // long[] parsing path doesn't silently truncate.
        long bigId = 9_876_543_210L;
        Path file = tempDir.resolve("way_ids.json");
        Files.writeString(file, "[" + bigId + "]");

        GreenspaceWayIds wayIds = GreenspaceWayIds.load(file.toString());

        assertTrue(wayIds.contains(bigId));
        assertFalse(wayIds.contains(bigId - 1));
    }
}
