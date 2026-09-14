import * as SQLite from 'expo-sqlite';

// Pathfinder Run -- local run-history storage, §2's "cached routes, run
// history" local store. Now with an optional server-side sync copy (see
// getOrCreateDeviceId below and App.js's syncRunToServer) -- still
// entirely usable local-only if sync fails or is never attempted; the
// local copy here is always the authoritative one, sync is best-effort
// on top of it, not a replacement for it.
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
//
// run_uuid: a client-generated id assigned per run (see saveRun), used
// as the sync idempotency key against the server (scripts/runs.py's
// store_run) -- generated locally, before a run is even known to sync
// successfully, so a retried sync after a flaky connection doesn't
// create a duplicate server-side row. Migrated in via ALTER TABLE for
// installs that already have the runs table from before this existed
// (see ensureRunUuidColumn) rather than assuming a fresh CREATE TABLE
// always runs -- this app has been through enough schema changes this
// session (closures.py's resolve_token column being the precedent) that
// "assume every install is fresh" is already known to be wrong.
//
// device table: exactly one row (id fixed to 1 via CHECK), holding the
// random device id used for server-side sync -- see getOrCreateDeviceId
// and runs.py's module docstring for the identity model and its honest
// limits (does not survive an app reinstall).

const DB_NAME = 'pathfinder_run.db';

let dbPromise = null;

async function ensureRunUuidColumn(db) {
  const columns = await db.getAllAsync('PRAGMA table_info(runs)');
  const hasRunUuid = columns.some((c) => c.name === 'run_uuid');
  if (!hasRunUuid) {
    await db.execAsync('ALTER TABLE runs ADD COLUMN run_uuid TEXT');
  }
}

// §2's "offline map tiles for the last route" -- see App.js's
// captureRunSnapshot for the actual mechanism (a static MapView snapshot,
// not real tile caching) and why. Just a file path (or NULL for runs saved
// before this existed, or where the snapshot capture itself failed) --
// same ALTER-TABLE-for-existing-installs pattern as run_uuid above, for
// the same reason.
async function ensureMapSnapshotUriColumn(db) {
  const columns = await db.getAllAsync('PRAGMA table_info(runs)');
  const hasColumn = columns.some((c) => c.name === 'map_snapshot_uri');
  if (!hasColumn) {
    await db.execAsync('ALTER TABLE runs ADD COLUMN map_snapshot_uri TEXT');
  }
}

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
        CREATE TABLE IF NOT EXISTS device (
          id INTEGER PRIMARY KEY CHECK (id = 1),
          device_id TEXT NOT NULL
        );
      `);
      await ensureRunUuidColumn(db);
      await ensureMapSnapshotUriColumn(db);
      return db;
    });
  }
  return dbPromise;
}

// A device id sufficiently unique for its actual purpose (identifying
// which device's runs are which for sync, nothing security-sensitive --
// unlike closures.py's resolve_token, which genuinely needs to be
// unguessable), generated with Math.random() rather than crypto.randomUUID()
// specifically to avoid depending on Hermes's current level of Web Crypto
// support -- not worth checking version-specific availability for an id
// with this low a quality bar. Timestamp prefix + two random chunks is
// comfortably collision-free at this app's actual scale.
function generateDeviceId() {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
}

// Returns this install's device id, creating and persisting one on first
// call if none exists yet. See this file's header and runs.py's module
// docstring: this does NOT survive an app uninstall/reinstall (it lives
// in the same local database as the run history it's meant to help sync)
// -- a known, accepted limitation, not an oversight.
export async function getOrCreateDeviceId() {
  const db = await getDb();
  const existing = await db.getFirstAsync('SELECT device_id FROM device WHERE id = 1');
  if (existing) return existing.device_id;

  const deviceId = generateDeviceId();
  await db.runAsync('INSERT INTO device (id, device_id) VALUES (1, ?)', deviceId);
  return deviceId;
}

// Insert one completed run. Returns the new row's id. Callers should
// generate runUuid themselves (see App.js) before calling this, the same
// value used for the server sync attempt right after -- both need to
// agree on it for sync idempotency to mean anything.
export async function saveRun({ startedAt, targetDistanceM, actualDistanceM, durationMs, trace, runUuid, mapSnapshotUri }) {
  const db = await getDb();
  const result = await db.runAsync(
    'INSERT INTO runs (started_at, target_distance_m, actual_distance_m, duration_ms, trace, run_uuid, map_snapshot_uri) VALUES (?, ?, ?, ?, ?, ?, ?)',
    startedAt,
    targetDistanceM,
    actualDistanceM,
    durationMs,
    JSON.stringify(trace),
    runUuid,
    mapSnapshotUri || null
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
    runUuid: row.run_uuid,
    mapSnapshotUri: row.map_snapshot_uri,
  }));
}

// "Delete my data" -- the local half. See App.js's deleteAllData for the
// full picture (this alone only clears the local copy; the server-side
// synced copy, if any, needs its own DELETE /runs call, since they're two
// separate stores by design -- local is always authoritative, server is
// a best-effort backup on top, per this file's own header). A real
// DELETE, not a soft flag -- same reasoning as scripts/runs.py's
// delete_runs_for_device: the point is that the data stops existing.
export async function deleteAllRuns() {
  const db = await getDb();
  await db.runAsync('DELETE FROM runs');
}
