#!/usr/bin/env python3
"""
Pathfinder Run -- crowdsourced closure reporting, §6/§7 step 5.

Scope, deliberately narrow: this is the *reporting* pipeline only -- a
report lands in storage, matched to a real osm_way_id. It is NOT wired
into the routing cost function yet (a separate, later step once reporting
actually works), and it does NOT implement decay/expiry -- the schema just
avoids precluding it (see `status` and `reported_at` below).

Storage: SQLite, not PostGIS, deliberately. Reasoning (see conversation
notes from the storage investigation before this was built):
- The nearest-way spatial lookup is already solved by GraphHopper, not a
  database -- see snap_to_way_id below. What's left to store is a plain
  relational record (way_id, timestamp, status), not geometry, so
  PostGIS's actual value-add (spatial indexing/queries) doesn't apply
  here.
- Matches this project's own incremental-build discipline (§0): stand up
  the full architecture-doc stack when something actually needs it, not
  before. A single low-write-volume reporting endpoint doesn't need
  Postgres yet.
- The schema translates 1:1 to a future Postgres table if/when write
  volume or a multi-instance deployment actually demands it.

Privacy: no lat/lon column, on purpose, per the architecture doc's §3
data-minimization guidance verbatim -- "snap the report to the nearest OSM
way/segment ID and drop the precise coordinate once you've done that
matching." The reported position exists only transiently, inside
snap_to_way_id, long enough to resolve a way_id; it is never written to
disk.
"""
import sqlite3
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from generate_loop import destination_point, fetch_outbound_leg

DEFAULT_DB_PATH = str(Path(__file__).parent / "closures.db")


def ensure_schema(db_path=DEFAULT_DB_PATH):
    """Create the closures table/indexes if they don't exist. Idempotent --
    safe to call on every server startup."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS closures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                osm_way_id INTEGER NOT NULL,
                reported_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_closures_way_id ON closures(osm_way_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_closures_reported_at ON closures(reported_at)")
        conn.commit()
    finally:
        conn.close()


def store_closure(osm_way_id, db_path=DEFAULT_DB_PATH):
    """Insert one closure report. Opens and closes its own connection rather
    than sharing a long-lived one -- avoids any assumption about Flask's dev
    server being single-threaded, at the cost of a trivial per-request file
    open. Returns the new row's id."""
    reported_at = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO closures (osm_way_id, reported_at, status) VALUES (?, ?, 'active')",
            (osm_way_id, reported_at),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_active_closure_way_ids(db_path=DEFAULT_DB_PATH):
    """Distinct osm_way_ids currently reported closed, for the routing cost
    function (§4, §5 point 1's third bullet). Deliberately just `status =
    'active'` -- no decay/expiry logic exists yet (see module docstring),
    so an active report stays active until something else marks it
    otherwise.

    Returns a plain list of ints (empty if none), not a cursor/generator --
    the caller (generate_loop.py) needs to check truthiness and pass this
    into two separate GraphHopper requests (outbound + return leg), so a
    fully-materialized list is simpler than re-querying or holding a
    connection open across both."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT osm_way_id FROM closures WHERE status = 'active'"
        ).fetchall()
        return [row[0] for row in rows]
    finally:
        conn.close()


def snap_to_way_id(base_url, lat, lon, profile="foot"):
    """Resolve a raw (lat, lon) to the nearest routable OSM way.

    GraphHopper's own /nearest endpoint only returns a snapped coordinate
    and a distance -- confirmed empirically, no way metadata at all:
        GET /nearest?point=43.4643,-80.5204
        -> {"coordinates": [...], "distance": 0.58, "type": "Point"}

    So instead this reuses the exact mechanism already built for the
    edge-reuse penalty (generate_loop.py's osm_way_id path detail): request
    a trivial ~2m route from the point to a point just north of it, and
    read the way_id of the first segment -- i.e. the way at the reported
    point itself, not just "some way this tiny path happened to touch."

    Returns None if the point isn't near any routable way -- either because
    the resulting path carries no osm_way_id detail, or because GraphHopper
    itself rejects the point outright (PointNotFoundException /
    PointOutOfBoundsException, both surfaced as an HTTP 400). That 400 means
    "point isn't usable," the same thing an empty detail list means, NOT a
    connectivity failure -- confirmed by testing a point far outside the
    clipped extract: GraphHopper responds just fine, it just can't route
    from there. Only a genuine reachability failure (connection refused,
    timeout -- a plain URLError, not an HTTPError) propagates up to the
    caller as an exception."""
    near_lat, near_lon = destination_point(lat, lon, 0, 2)
    try:
        path = fetch_outbound_leg(base_url, lat, lon, near_lat, near_lon, profile)
    except urllib.error.HTTPError:
        return None
    details = path.get("details", {}).get("osm_way_id", [])
    if not details:
        return None
    return details[0][2]
