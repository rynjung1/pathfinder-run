#!/usr/bin/env python3
"""
Pathfinder Run -- server-side run-history sync (§2/§4's "run history"
local store, now with an optional server-side copy).

Scope and identity model, deliberately narrow: this app has no user
accounts or login of any kind -- auth today is one shared API key for the
whole app (route_api.py's require_api_key), not per-user. Building real
account-based sync on top of that would mean designing and building a
whole identity system first, which nobody has asked for. Instead, this
uses a device-scoped id: the mobile client generates a random id once
(see mobile/db.js's getOrCreateDeviceId) and tags every synced run with
it. No login, no signup.

Honest about what this does and doesn't solve, not overselling it:
- DOES protect against local data loss that isn't an app uninstall --
  bugs, local SQLite corruption, the phone being lost/damaged after a
  run but before the app is reinstalled elsewhere, etc. -- and lays a
  foundation for real accounts later (an account system could "claim" an
  existing device id's history).
- Does NOT reliably survive an app uninstall/reinstall on its own: the
  device id itself is stored in the same local SQLite database as the
  run history, using the same storage this feature is meant to back up
  -- reinstalling the app wipes both together, so there's no id left to
  ask the server for that device's history back. Making the id survive
  a reinstall would need Keychain (iOS) / equivalent (no clean Android
  parallel without extra native modules) -- a real, separate piece of
  work, not implemented here. A new phone or a fresh install always gets
  a new, unrelated device id.

Storage: SQLite, not PostGIS -- same reasoning as closures.py (a plain
relational record, no geometry, this project's own incremental-build
discipline). A separate runs.db, not folded into closures.db -- unrelated
concerns, no reason to share a file.

Trace data privacy: unlike closures.py's closures (which deliberately
drop the precise coordinate after snapping to a way, per §3), a run's GPS
trace IS the actual product -- storing it is the point, the same as it
already is in the mobile client's own local SQLite (mobile/db.js). No
new privacy tradeoff is introduced by syncing it to the server that
wasn't already true of the local copy.
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = str(Path(__file__).parent / "runs.db")

# Input caps -- same "independent of rate limiting" reasoning as
# route_api.py's MAX_DISTANCE_M/MAX_CANDIDATES: a single oversized
# request (a trace with, say, a million fabricated points) is a
# per-request resource-exhaustion vector that rate limiting alone
# (which throttles request *volume*) doesn't protect against. A real
# run's trace, even hours long at the app's own GPS sampling interval
# (WATCH_OPTIONS: 2s / 5m in mobile/App.js), is nowhere near this size --
# generous headroom, not a tight realistic estimate.
MAX_TRACE_POINTS = 20000
MAX_DEVICE_ID_LEN = 128


def ensure_schema(db_path=DEFAULT_DB_PATH):
    """Create the runs table/indexes if they don't exist. Idempotent --
    safe to call on every server startup, same pattern as
    closures.ensure_schema."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                run_uuid TEXT NOT NULL UNIQUE,
                started_at TEXT NOT NULL,
                target_distance_m REAL NOT NULL,
                actual_distance_m REAL NOT NULL,
                duration_ms INTEGER NOT NULL,
                trace TEXT NOT NULL,
                synced_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_device_id ON runs(device_id)")
        conn.commit()
    finally:
        conn.close()


def store_run(device_id, run_uuid, started_at, target_distance_m, actual_distance_m,
              duration_ms, trace, db_path=DEFAULT_DB_PATH):
    """Insert one synced run. run_uuid is a client-generated identifier
    (the mobile client assigns one per run at creation time, before it's
    even saved locally -- see mobile/db.js), used as the sync idempotency
    key: syncing the same run twice (e.g. a retried request after a
    flaky connection) is a no-op via INSERT OR IGNORE on the UNIQUE
    constraint, not a duplicate row. Returns True if a new row was
    actually inserted, False if this run_uuid was already synced."""
    synced_at = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO runs
                (device_id, run_uuid, started_at, target_distance_m,
                 actual_distance_m, duration_ms, trace, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (device_id, run_uuid, started_at, target_distance_m,
             actual_distance_m, duration_ms, json.dumps(trace), synced_at),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_runs_for_device(device_id, db_path=DEFAULT_DB_PATH):
    """All synced runs for one device_id, most recent first -- what the
    mobile client's Past Runs screen merges with its own local list.
    trace comes back already parsed (a list of {latitude, longitude,
    timestamp}), not a raw JSON string, matching mobile/db.js's own
    getRuns() shape so the client can treat local and server-sourced runs
    identically."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT run_uuid, started_at, target_distance_m, actual_distance_m,
                   duration_ms, trace
            FROM runs WHERE device_id = ? ORDER BY started_at DESC
            """,
            (device_id,),
        ).fetchall()
        return [
            {
                "runUuid": row[0],
                "startedAt": row[1],
                "targetDistanceM": row[2],
                "actualDistanceM": row[3],
                "durationMs": row[4],
                "trace": json.loads(row[5]),
            }
            for row in rows
        ]
    finally:
        conn.close()
