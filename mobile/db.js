import * as SQLite from 'expo-sqlite';

// Pathfinder Run -- local run-history storage, §2's "cached routes, run
// history" local store. Local-only for now, deliberately -- no server
// sync yet, that's a separate later step once this proves useful.
//
// expo-sqlite over AsyncStorage+JSON: investigated before implementing,
// same as every other storage decision this session. AsyncStorage keeps
// one opaque string per key, so a growing run-history list would mean one
// ever-larger JSON blob, fully re-parsed on read and fully re-serialized
// on every single save -- even to add just one run -- with no bound as
// history grows, plus a real per-key size ceiling on some Android
// AsyncStorage implementations worth not building toward. expo-sqlite --
// confirmed via the exact versioned SDK 57 docs (per this project's own
// mobile/AGENTS.md instruction to check current docs before writing any
// code) to work in Expo Go on this SDK, no prebuild/custom dev client
// needed -- lets each run be an independent INSERT, no rewrite of prior
// history per save. Mirrors the same SQLite-over-ad-hoc-blob reasoning
// already used for the backend closures storage (scripts/closures.py).
//
// Schema: one `runs` table. The full GPS trace is stored as a
// JSON-serialized TEXT column, not a separate joined table -- the SDK 57
// docs confirm this is the normal pattern for expo-sqlite (no dedicated
// JSON column type), and a second table/join isn't earning its
// complexity yet at this scale (a handful to low hundreds of runs, each
// trace a few hundred points) -- same incremental-build discipline as
// everywhere else in this project (§0). Revisit if/when run history grows
// large enough that loading full traces just to list dates/distances
// becomes a real cost.

const DB_NAME = 'pathfinder_run.db';

let dbPromise = null;

function getDb() {
  if (!dbPromise) {
    dbPromise = SQLite.openDatabaseAsync(DB_NAME).then(async (db) => {
      await db.execAsync(`
        CREATE TABLE IF NOT EXISTS runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          started_at TEXT NOT NULL,
          target_distance_m REAL NOT NULL,
          actual_distance_m REAL NOT NULL,
          duration_ms INTEGER NOT NULL,
          trace TEXT NOT NULL
        );
      `);
      return db;
    });
  }
  return dbPromise;
}

// Insert one completed run. Returns the new row's id.
export async function saveRun({ startedAt, targetDistanceM, actualDistanceM, durationMs, trace }) {
  const db = await getDb();
  const result = await db.runAsync(
    'INSERT INTO runs (started_at, target_distance_m, actual_distance_m, duration_ms, trace) VALUES (?, ?, ?, ?, ?)',
    startedAt,
    targetDistanceM,
    actualDistanceM,
    durationMs,
    JSON.stringify(trace)
  );
  return result.lastInsertRowId;
}

// All saved runs, most recent first -- what the past-runs list screen
// needs. trace comes back already parsed (an array of {latitude,
// longitude, timestamp}), not a raw JSON string.
export async function getRuns() {
  const db = await getDb();
  const rows = await db.getAllAsync('SELECT * FROM runs ORDER BY started_at DESC');
  return rows.map((row) => ({
    id: row.id,
    startedAt: row.started_at,
    targetDistanceM: row.target_distance_m,
    actualDistanceM: row.actual_distance_m,
    durationMs: row.duration_ms,
    trace: JSON.parse(row.trace),
  }));
}
