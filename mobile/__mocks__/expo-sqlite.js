// Manual mock for expo-sqlite, picked up automatically by Jest (same
// mechanism as the other manual mocks in this directory). Not a fake SQL
// engine -- expo-sqlite's real native module genuinely can't load under
// Jest at all (confirmed directly: `require('expo-sqlite')` fails with
// "Cannot find module 'expo-asset'" deep in its own dependency chain,
// before even reaching the usual TurboModuleRegistry crash) -- so this
// backs db.js's actual queries with a REAL SQLite engine
// (better-sqlite3, a Node-native addon, dev-dependency-only) rather than
// hand-rolling a SQL interpreter that could quietly diverge from real
// SQLite's behavior (subtle things like ORDER BY collation, ALTER TABLE
// semantics, or PRAGMA table_info's exact output shape would be very
// easy to get wrong by hand).
//
// Opens a fresh :memory: database regardless of the name db.js passes
// in, UNLESS a test has queued a specific raw database via
// __useDatabaseForNextOpen -- used to test schema migration against a
// pre-seeded old-schema database (see db.test.js's migration tests),
// since db.js has no way to accept an externally-created connection
// itself. Tests get isolation via jest.resetModules() (see db.test.js)
// forcing db.js's own module-level dbPromise cache to reset between
// tests, not via this mock tracking multiple named databases.
const Database = require('better-sqlite3');

let pendingRawDb = null;

function __useDatabaseForNextOpen(rawDb) {
  pendingRawDb = rawDb;
}

function wrapDatabase(rawDb) {
  return {
    execAsync: async (sql) => {
      rawDb.exec(sql);
    },
    runAsync: async (sql, ...params) => {
      const info = rawDb.prepare(sql).run(...params);
      // expo-sqlite's real casing (lastInsertRowId, capital ID) --
      // better-sqlite3's own result uses lastInsertRowid (lowercase d).
      return { lastInsertRowId: info.lastInsertRowid, changes: info.changes };
    },
    getAllAsync: async (sql, ...params) => {
      // better-sqlite3 disallows preparing PRAGMA statements directly
      // (they have to go through .pragma()) -- db.js's schema-migration
      // checks (PRAGMA table_info(...)) are the only PRAGMA usage here.
      const trimmed = sql.trim();
      if (/^PRAGMA/i.test(trimmed)) {
        const pragmaExpr = trimmed.replace(/^PRAGMA\s+/i, '').replace(/;$/, '');
        return rawDb.pragma(pragmaExpr);
      }
      return rawDb.prepare(sql).all(...params);
    },
    getFirstAsync: async (sql, ...params) => {
      const row = rawDb.prepare(sql).get(...params);
      return row === undefined ? null : row;
    },
  };
}

async function openDatabaseAsync(_name) {
  const rawDb = pendingRawDb || new Database(':memory:');
  pendingRawDb = null;
  return wrapDatabase(rawDb);
}

module.exports = { openDatabaseAsync, __useDatabaseForNextOpen, __RawDatabase: Database };
