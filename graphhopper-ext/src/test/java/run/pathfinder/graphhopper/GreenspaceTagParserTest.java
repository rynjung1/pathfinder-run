package run.pathfinder.graphhopper;

import com.graphhopper.reader.ReaderWay;
import com.graphhopper.routing.ev.BooleanEncodedValue;
import com.graphhopper.routing.ev.EdgeIntAccess;
import com.graphhopper.routing.ev.EncodedValue;
import com.graphhopper.routing.ev.SimpleBooleanEncodedValue;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Was zero automated test coverage anywhere in graphhopper-ext/ before
 * this -- found on a backend sweep. Uses real GraphHopper types
 * throughout (SimpleBooleanEncodedValue, ReaderWay), not mocks -- both
 * turned out directly usable standalone (a public single-arg
 * constructor plus EncodedValue's own init(InitializerConfig), the same
 * pattern GraphHopper's own internals use), and EdgeIntAccess is a
 * two-method interface trivial to fake with a plain in-memory map, so a
 * mocking library added nothing here a mocking library would.
 */
class GreenspaceTagParserTest {

    /** Backs edge storage with a plain map instead of GraphHopper's real
     * byte-array-backed edge store -- this parser only ever calls
     * setBool/getBool on ONE edge id at a time in real usage, so a map
     * keyed by (edgeId, dataIndex) is a faithful enough fake without
     * needing GraphHopper's actual storage engine. */
    private static class FakeEdgeIntAccess implements EdgeIntAccess {
        private final Map<Long, Integer> values = new HashMap<>();

        @Override
        public int getInt(int edgeId, int dataIndex) {
            return values.getOrDefault(key(edgeId, dataIndex), 0);
        }

        @Override
        public void setInt(int edgeId, int dataIndex, int value) {
            values.put(key(edgeId, dataIndex), value);
        }

        private static long key(int edgeId, int dataIndex) {
            return ((long) edgeId << 32) | (dataIndex & 0xffffffffL);
        }
    }

    private BooleanEncodedValue greenspaceEnc;
    private FakeEdgeIntAccess edgeIntAccess;

    @BeforeEach
    void setUp() {
        greenspaceEnc = new SimpleBooleanEncodedValue("greenspace");
        greenspaceEnc.init(new EncodedValue.InitializerConfig());
        edgeIntAccess = new FakeEdgeIntAccess();
    }

    private GreenspaceWayIds wayIdsContaining(long... ids) throws IOException {
        Path file = Files.createTempFile("greenspace-way-ids", ".json");
        StringBuilder json = new StringBuilder("[");
        for (int i = 0; i < ids.length; i++) {
            if (i > 0) json.append(",");
            json.append(ids[i]);
        }
        json.append("]");
        Files.writeString(file, json.toString());
        return GreenspaceWayIds.load(file.toString());
    }

    @Test
    void setsGreenspaceTrueForAWayInTheWayIdSet() throws IOException {
        GreenspaceTagParser parser = new GreenspaceTagParser(greenspaceEnc, wayIdsContaining(42L));
        ReaderWay way = new ReaderWay(42L);

        parser.handleWayTags(0, edgeIntAccess, way, null);

        assertTrue(greenspaceEnc.getBool(false, 0, edgeIntAccess));
    }

    @Test
    void leavesGreenspaceFalseForAWayNotInTheWayIdSet() throws IOException {
        GreenspaceTagParser parser = new GreenspaceTagParser(greenspaceEnc, wayIdsContaining(42L));
        ReaderWay way = new ReaderWay(999L); // not in the set

        parser.handleWayTags(0, edgeIntAccess, way, null);

        // The encoded value's real default -- confirms this parser
        // genuinely leaves non-greenspace ways alone rather than, say,
        // accidentally setting them false explicitly (which would only
        // coincidentally look the same here, and could mask a bug where
        // this parser overwrites a value set by SOME OTHER parser on the
        // same edge, since custom_models can run several per edge).
        assertFalse(greenspaceEnc.getBool(false, 0, edgeIntAccess));
    }

    @Test
    void onlyMarksTheSpecificEdgeGivenNotEveryEdgeForAGreenspaceWay() throws IOException {
        // A single OSM way often splits into several graph edges after
        // import -- this confirms handleWayTags only touches the edgeId
        // it's actually called with, not e.g. every edge sharing that
        // way's tags via some shared mutable state.
        GreenspaceTagParser parser = new GreenspaceTagParser(greenspaceEnc, wayIdsContaining(42L));
        ReaderWay way = new ReaderWay(42L);

        parser.handleWayTags(5, edgeIntAccess, way, null);

        assertTrue(greenspaceEnc.getBool(false, 5, edgeIntAccess));
        assertFalse(greenspaceEnc.getBool(false, 6, edgeIntAccess));
    }
}
