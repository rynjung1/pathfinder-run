#!/usr/bin/env python3
"""
Pathfinder Run -- crowdsourced closure reporting, §6/§7 step 5.

Scope: reporting (store_closure), reading active closures for the routing
cost function (get_active_closure_way_ids, wired into generate_loop.py),
resolving a report by hand (resolve_closure), and now automatic
decay/expiry (expire_stale_closures) per §6 point 1's exact wording:
"a decay/expiry (auto-clear after N days unless re-confirmed) so stale
reports don't permanently block a segment."

Expiry design, reasoning:
- No scheduler/cron infrastructure exists anywhere in this project (§0
  discipline: don't stand up infrastructure before something needs it),
  so this can't be "a job runs nightly and flips old rows." Instead
  expire_stale_closures() runs lazily, called at the top of
  get_active_closure_way_ids() -- the ONE place in the whole system that
  actually reads "what's currently closed" (both route_api.py's /route
  and generate_loop.py's CLI go through it). That makes the check
  self-healing on every read with no separate process to run or forget to
  run, at the cost of a small per-request table scan over just the
  currently-active rows (fine at this write volume -- same reasoning
  already used to justify SQLite over PostGIS above).
- A third status value, 'expired', distinct from 'resolved' -- keeps the
  audit trail meaningful (matches the reasoning for using UPDATE, not
  DELETE, in resolve_closure): "somebody confirmed this is clear" and
  "nobody re-confirmed it within N days" are different facts about a
  report and shouldn't collapse into the same value.
- "Unless re-confirmed" needed no new mechanism: store_closure() already
  inserts a new, independent row per report rather than upserting one row
  per way (confirmed by the multiple-reports-per-way design already
  covered by get_active_closure_way_ids' DISTINCT). So a fresh report on
  a way that has an old, now-expired report is just another 'active' row
  with its own reported_at -- the way reads as closed again as soon as
  ANY of its reports is both active and fresh, with zero extra code.
- Age is computed in Python (datetime.fromisoformat), not in SQL. Checked
  empirically first: SQLite's julianday() does parse this exact
  isoformat()-produced string (with its '+00:00' offset and microseconds)
  correctly in this environment's sqlite3 build, but that offset-suffix
  support is a relatively recent SQLite addition (3.42+) and isn't
  guaranteed on every deployment target, whereas Python's
  datetime.fromisoformat() is guaranteed to round-trip exactly what
  datetime.isoformat() produced -- the same string store_closure already
  writes. Doing the comparison in Python trades a small amount of "let
  the database do it" for not depending on the runtime's SQLite version.
- DEFAULT_CLOSURE_MAX_AGE_DAYS = 7 is an ad hoc default, not sourced from
  the doc (which specifies the *mechanism*, "auto-clear after N days,"
  but not a value for N) -- same status as this codebase's other tunable
  constants (DEFAULT_REUSE_PENALTY_MULTIPLIER, DEFAULT_COMPACTNESS_WEIGHT):
  a reasonable starting point, explicitly open to revision once real
  closure reports show whether 7 days is too eager or too lax for
  construction/event-style closures.

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
import secrets
import sqlite3
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

from generate_loop import destination_point, fetch_outbound_leg

DEFAULT_DB_PATH = str(Path(__file__).parent / "closures.db")
DEFAULT_CLOSURE_MAX_AGE_DAYS = 7  # ad hoc, revisit -- see module docstring


def ensure_schema(db_path=DEFAULT_DB_PATH):
    """Create the closures table/indexes if they don't exist, and migrate
    older databases forward. Idempotent -- safe to call on every server
    startup.

    resolve_token (added for the pre-deployment hardening pass, §4/§7):
    PATCH /closures/<id> originally had no ownership check at all -- any
    caller could resolve any report by walking sequential ids. Rather than
    building real user accounts (out of scope for a small beta), each
    report gets its own random, unguessable token at creation time
    (store_closure, below); resolving requires presenting that exact
    token back. An existing database from before this column existed gets
    it added via ALTER TABLE, defaulting to NULL for old rows -- those
    rows simply can't be resolved via a token anymore (there was never a
    token issued for them to prove), which is the correct, safe default,
    not a bug: nothing legitimate should already be holding a token for a
    report the schema never generated one for."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS closures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                osm_way_id INTEGER NOT NULL,
                reported_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                resolve_token TEXT
            )
            """
        )
        existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(closures)").fetchall()}
        if "resolve_token" not in existing_columns:
            conn.execute("ALTER TABLE closures ADD COLUMN resolve_token TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_closures_way_id ON closures(osm_way_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_closures_reported_at ON closures(reported_at)")
        conn.commit()
    finally:
        conn.close()


def store_closure(osm_way_id, db_path=DEFAULT_DB_PATH):
    """Insert one closure report. Opens and closes its own connection rather
    than sharing a long-lived one -- avoids any assumption about Flask's dev
    server being single-threaded, at the cost of a trivial per-request file
    open.

    Generates a random resolve_token (secrets.token_urlsafe -- 24 bytes,
    not guessable by brute force) and returns it alongside the new row's
    id: (closure_id, resolve_token). The caller (route_api.py) hands the
    token back to whoever reported the closure; resolve_closure() requires
    it later. Returned once, at creation -- not retrievable afterward by
    id alone, on purpose (that's the whole point: knowing the id shouldn't
    be enough)."""
    reported_at = datetime.now(timezone.utc).isoformat()
    resolve_token = secrets.token_urlsafe(24)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO closures (osm_way_id, reported_at, status, resolve_token) VALUES (?, ?, 'active', ?)",
            (osm_way_id, reported_at, resolve_token),
        )
        conn.commit()
        return cur.lastrowid, resolve_token
    finally:
        conn.close()


def expire_stale_closures(max_age_days=DEFAULT_CLOSURE_MAX_AGE_DAYS, db_path=DEFAULT_DB_PATH):
    """§6 point 1's decay/expiry: flip any 'active' report older than
    max_age_days to 'expired'. See module docstring for why this runs
    lazily (called from get_active_closure_way_ids, below) instead of a
    background job, why 'expired' is a separate status from 'resolved',
    and why the age check is done in Python rather than in SQL.

    Only ever touches rows currently 'active' -- a 'resolved' row already
    reflects a real outcome (someone confirmed it's clear) and shouldn't
    be reclassified as merely having aged out.

    Returns the list of ids that were just expired (empty if none) --
    mainly for tests/logging visibility, not required by any caller."""
    conn = sqlite3.connect(db_path)
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        rows = conn.execute(
            "SELECT id, reported_at FROM closures WHERE status = 'active'"
        ).fetchall()
        stale_ids = [
            row_id for row_id, reported_at in rows
            if datetime.fromisoformat(reported_at) < cutoff
        ]
        if stale_ids:
            placeholders = ",".join("?" * len(stale_ids))
            conn.execute(
                f"UPDATE closures SET status = 'expired' WHERE id IN ({placeholders})",
                stale_ids,
            )
            conn.commit()
        return stale_ids
    finally:
        conn.close()


def get_active_closure_way_ids(db_path=DEFAULT_DB_PATH):
    """Distinct osm_way_ids currently reported closed, for the routing cost
    function (§4, §5 point 1's third bullet). Expires stale reports first
    (see expire_stale_closures) so 'active' here always means "reported
    and still within the decay window," not just "never explicitly
    resolved."

    Returns a plain list of ints (empty if none), not a cursor/generator --
    the caller (generate_loop.py) needs to check truthiness and pass this
    into two separate GraphHopper requests (outbound + return leg), so a
    fully-materialized list is simpler than re-querying or holding a
    connection open across both."""
    expire_stale_closures(db_path=db_path)
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT osm_way_id FROM closures WHERE status = 'active'"
        ).fetchall()
        return [row[0] for row in rows]
    finally:
        conn.close()


def resolve_closure(closure_id, resolve_token, db_path=DEFAULT_DB_PATH):
    """Mark one closure report resolved -- the basic clear mechanism that
    was missing when closures were first wired into routing (see module
    docstring), now requiring the token issued at creation (see
    store_closure) rather than just the id -- ids are sequential and
    trivially guessable, the token isn't. Not time-based decay/expiry, and
    not scoped to a way_id (a way can have multiple independent reports;
    this resolves the one report, not "everything on this way").

    Returns one of three strings, not a bool -- the caller (route_api.py)
    needs to tell "doesn't exist" (404) apart from "exists, wrong token"
    (403), not collapse both into one failure case:
      "resolved"       -- updated (or already resolved with this same
                           correct token -- idempotent).
      "not_found"       -- no row with this id.
      "invalid_token"   -- the row exists but resolve_token doesn't match
                           (including rows from before this column
                           existed, where it's NULL -- compare_digest
                           against a real token never matches NULL).
    Uses secrets.compare_digest for the comparison -- a plain == is
    vulnerable to a timing attack that could let an attacker recover the
    token byte-by-byte from response-time differences; compare_digest
    runs in constant time regardless of where the strings first differ."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT resolve_token FROM closures WHERE id = ?", (closure_id,)
        ).fetchone()
        if row is None:
            return "not_found"
        stored_token = row[0]
        if not stored_token or not isinstance(resolve_token, str) or not secrets.compare_digest(stored_token, resolve_token):
            return "invalid_token"
        conn.execute(
            "UPDATE closures SET status = 'resolved' WHERE id = ?",
            (closure_id,),
        )
        conn.commit()
        return "resolved"
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
