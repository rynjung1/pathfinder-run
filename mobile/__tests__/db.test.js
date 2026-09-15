/**
 * Tests for db.js's real SQLite logic -- found with zero direct coverage
 * anywhere on a follow-up sweep: App.test.js mocks `../db` entirely
 * (`jest.mock('../db', ...)`), and geometry.test.js never touches it, so
 * db.js's own schema/migration/query logic had never actually run under
 * a test. expo-sqlite genuinely can't load under Jest at all (confirmed
 * directly -- fails on a missing `expo-asset` deep in its own dependency
 * chain, not just the usual native-module crash), so this needs
 * __mocks__/expo-sqlite.js (backed by better-sqlite3, a real SQLite
 * engine, not a hand-rolled fake) -- see that file's own header.
 *
 * jest.resetModules() before every test, re-requiring db.js fresh each
 * time: db.js caches its connection in a module-level `dbPromise`
 * variable, so without this every test after the first would silently
 * reuse the first test's in-memory database instead of getting its own
 * isolated one -- the mock's openDatabaseAsync only creates a fresh
 * :memory: database on an actual call, and re-requiring the module is
 * what makes that call happen again.
 */

beforeEach(() => {
  jest.resetModules();
});

function loadDb() {
  return require('../db');
}

describe('getOrCreateDeviceId', () => {
  test('creates a device id on first call', async () => {
    const { getOrCreateDeviceId } = loadDb();
    const id = await getOrCreateDeviceId();
    expect(typeof id).toBe('string');
    expect(id.length).toBeGreaterThan(0);
  });

  test('returns the same id on subsequent calls, not a new one each time', async () => {
    const { getOrCreateDeviceId } = loadDb();
    const first = await getOrCreateDeviceId();
    const second = await getOrCreateDeviceId();
    expect(second).toBe(first);
  });
});

describe('saveRun / getRuns', () => {
  function realRun(overrides = {}) {
    return {
      startedAt: '2026-01-01T00:00:00.000Z',
      targetDistanceM: 5000,
      actualDistanceM: 4820.3,
      durationMs: 1620000,
      trace: [{ latitude: 43.4643, longitude: -80.5204, timestamp: 1000 }],
      runUuid: 'run-uuid-1',
      mapSnapshotUri: 'file:///snapshots/run-uuid-1.png',
      ...overrides,
    };
  }

  test('saveRun returns a real row id, and getRuns round-trips every field correctly', async () => {
    const { saveRun, getRuns } = loadDb();
    const rowId = await saveRun(realRun());

    expect(typeof rowId).toBe('number');
    const runs = await getRuns();
    expect(runs).toHaveLength(1);
    expect(runs[0]).toMatchObject({
      id: rowId,
      startedAt: '2026-01-01T00:00:00.000Z',
      targetDistanceM: 5000,
      actualDistanceM: 4820.3,
      durationMs: 1620000,
      runUuid: 'run-uuid-1',
      mapSnapshotUri: 'file:///snapshots/run-uuid-1.png',
    });
    // The full GPS trace round-trips as real parsed objects, not the raw
    // JSON string it's stored as -- getRuns' own contract (see its
    // comment: "trace comes back already parsed").
    expect(runs[0].trace).toEqual([{ latitude: 43.4643, longitude: -80.5204, timestamp: 1000 }]);
  });

  test('a run saved without a mapSnapshotUri stores NULL, not the string "undefined"', async () => {
    const { saveRun, getRuns } = loadDb();
    await saveRun(realRun({ mapSnapshotUri: undefined }));
    const runs = await getRuns();
    expect(runs[0].mapSnapshotUri).toBeNull();
  });

  test('getRuns returns most-recent-first, regardless of insertion order', async () => {
    const { saveRun, getRuns } = loadDb();
    await saveRun(realRun({ runUuid: 'older', startedAt: '2026-01-01T00:00:00.000Z' }));
    await saveRun(realRun({ runUuid: 'newer', startedAt: '2026-02-01T00:00:00.000Z' }));

    const runs = await getRuns();
    expect(runs.map((r) => r.runUuid)).toEqual(['newer', 'older']);
  });

  test('getRuns on an empty database is an empty array, not an error', async () => {
    const { getRuns } = loadDb();
    expect(await getRuns()).toEqual([]);
  });
});

describe('deleteAllRuns', () => {
  test('actually removes every row, a real delete not a soft flag', async () => {
    const { saveRun, getRuns, deleteAllRuns } = loadDb();
    await saveRun({
      startedAt: '2026-01-01T00:00:00.000Z',
      targetDistanceM: 5000,
      actualDistanceM: 4900,
      durationMs: 1000,
      trace: [],
      runUuid: 'u1',
    });

    await deleteAllRuns();

    expect(await getRuns()).toEqual([]);
  });

  test('is a harmless no-op when there is nothing to delete', async () => {
    const { deleteAllRuns, getRuns } = loadDb();
    await deleteAllRuns();
    expect(await getRuns()).toEqual([]);
  });
});

describe('schema migration (ensureRunUuidColumn / ensureMapSnapshotUriColumn)', () => {
  test('a pre-migration database (missing run_uuid and map_snapshot_uri) gets both columns added, and its existing row is preserved', async () => {
    // Simulates an install from before either column existed -- exactly
    // the scenario ensureRunUuidColumn/ensureMapSnapshotUriColumn exist
    // for (see db.js's own header comment: "this app has been through
    // enough schema changes this session... 'assume every install is
    // fresh' is already known to be wrong"). Seeds the OLD schema
    // directly against the mock's real underlying SQLite connection,
    // bypassing db.js entirely, then confirms db.js's own migration
    // logic (triggered the moment it opens this same database) adds the
    // missing columns without losing the row that was already there.
    const { __useDatabaseForNextOpen, __RawDatabase } = require('expo-sqlite');
    const rawDb = new __RawDatabase(':memory:');
    rawDb.exec(`
      CREATE TABLE runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TEXT NOT NULL,
        target_distance_m REAL NOT NULL,
        actual_distance_m REAL NOT NULL,
        duration_ms INTEGER NOT NULL,
        trace TEXT NOT NULL
      );
      CREATE TABLE device (id INTEGER PRIMARY KEY CHECK (id = 1), device_id TEXT NOT NULL);
    `);
    rawDb
      .prepare(
        'INSERT INTO runs (started_at, target_distance_m, actual_distance_m, duration_ms, trace) VALUES (?, ?, ?, ?, ?)'
      )
      .run('2020-06-15T00:00:00.000Z', 5000, 4900, 1000, '[]');
    __useDatabaseForNextOpen(rawDb);

    const { getRuns, saveRun } = loadDb();
    const runs = await getRuns();

    expect(runs).toHaveLength(1);
    expect(runs[0].startedAt).toBe('2020-06-15T00:00:00.000Z'); // the pre-existing row survived
    expect(runs[0].runUuid).toBeNull(); // migrated column, no value for an old row
    expect(runs[0].mapSnapshotUri).toBeNull();

    // Confirms the migration didn't just tolerate the old row but
    // genuinely made the columns usable -- a new run saved after this
    // can actually populate them.
    await saveRun({
      startedAt: '2026-01-01T00:00:00.000Z',
      targetDistanceM: 5000,
      actualDistanceM: 4900,
      durationMs: 1000,
      trace: [],
      runUuid: 'new-run',
      mapSnapshotUri: 'file:///new.png',
    });
    const afterSave = await getRuns();
    const newRun = afterSave.find((r) => r.runUuid === 'new-run');
    expect(newRun.mapSnapshotUri).toBe('file:///new.png');
  });

  test('reopening an already-migrated database (e.g. relaunching the real app) does not error or duplicate columns', async () => {
    // getDb()'s own dbPromise caching means this never happens within
    // one running process, but it's exactly what happens across two real
    // app launches against the same on-disk database file: the SAME
    // already-migrated database gets opened again, and
    // ensureRunUuidColumn/ensureMapSnapshotUriColumn run their PRAGMA
    // table_info check again. This uses the SAME raw database object
    // across two separate db.js module loads (simulating "close the app,
    // reopen it") specifically so it can't pass by accident the way two
    // independent fresh :memory: databases would.
    const { __useDatabaseForNextOpen, __RawDatabase } = require('expo-sqlite');
    const rawDb = new __RawDatabase(':memory:');

    __useDatabaseForNextOpen(rawDb);
    await loadDb().getOrCreateDeviceId(); // first "launch" -- creates tables, adds both columns

    jest.resetModules();
    __useDatabaseForNextOpen(rawDb); // second "launch" -- the identical, already-migrated database
    await expect(loadDb().getOrCreateDeviceId()).resolves.toEqual(expect.any(String));

    // Exactly one of each column -- if the ensure-check had re-run the
    // ALTER TABLE unconditionally instead of checking first, SQLite
    // would have thrown "duplicate column name" on the second launch
    // rather than silently double-adding it, so reaching this line at
    // all is most of what this test confirms; this makes sure of it.
    const columns = rawDb.pragma('table_info(runs)').map((c) => c.name);
    expect(columns.filter((name) => name === 'run_uuid')).toHaveLength(1);
    expect(columns.filter((name) => name === 'map_snapshot_uri')).toHaveLength(1);
  });
});

describe('getDb failure recovery', () => {
  // Found on a sweep: getDb() memoizes its connection in a module-level
  // dbPromise, set the first time anything calls it and never reset --
  // if that very first open (a real native call, genuinely able to
  // reject: on-disk corruption, a full disk) failed, every later getDb()
  // call would skip the `if (!dbPromise)` check and just re-await the
  // same already-rejected promise forever, permanently breaking every DB
  // operation for the rest of the app's process lifetime with no way to
  // recover short of a full restart -- a single transient failure had a
  // permanent effect. __failNextOpen (__mocks__/expo-sqlite.js) is new,
  // added specifically to make this failure simulatable at all -- nothing
  // in the existing mock could previously make openDatabaseAsync reject.
  test('a failed open does not permanently wedge every future call on the same rejection', async () => {
    const { __failNextOpen } = require('expo-sqlite');
    __failNextOpen(new Error('disk full'));

    const { getOrCreateDeviceId } = loadDb();
    await expect(getOrCreateDeviceId()).rejects.toThrow('disk full');

    // No __failNextOpen queued this time -- a real, healthy open. Must
    // actually succeed, not just reject again with the same stale error.
    const id = await getOrCreateDeviceId();
    expect(typeof id).toBe('string');
    expect(id.length).toBeGreaterThan(0);
  });
});
