"""Tests for closures.py's SQLite-backed crowdsourced closure reporting
-- found with zero test coverage at all on a backend sweep (runs.py, the
comparable storage module, already had test_runs.py; this one never
did). Same tmp_path-per-test isolation as test_runs.py, for the same
reason: real database operations, safe to run anywhere with no risk to
real data and no ordering dependence between tests.

snap_to_way_id (the one function here that calls out to a real
GraphHopper instance) isn't covered -- that's a live-server integration
concern like test_route_regression.py's case, not a unit test for this
file.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

from closures import (
    ensure_schema,
    expire_stale_closures,
    get_active_closure_way_ids,
    resolve_closure,
    store_closure,
)


def _fresh_db(tmp_path):
    db_path = str(tmp_path / "test_closures.db")
    ensure_schema(db_path)
    return db_path


def _insert_with_reported_at(db_path, osm_way_id, reported_at, status="active", resolve_token="tok"):
    # Bypasses store_closure to insert a row with a specific (possibly
    # backdated) reported_at -- store_closure always uses datetime.now(),
    # which a test can't control, and expire_stale_closures' whole job is
    # to treat old vs. recent reports differently.
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO closures (osm_way_id, reported_at, status, resolve_token) VALUES (?, ?, ?, ?)",
            (osm_way_id, reported_at, status, resolve_token),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _row(db_path, closure_id):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT osm_way_id, status, resolve_token FROM closures WHERE id = ?", (closure_id,)
        ).fetchone()
    finally:
        conn.close()


# --- store_closure --------------------------------------------------------

def test_store_closure_inserts_an_active_row_and_returns_id_and_token(tmp_path):
    db_path = _fresh_db(tmp_path)
    closure_id, resolve_token = store_closure(38896608, db_path=db_path)

    assert isinstance(closure_id, int)
    assert isinstance(resolve_token, str) and len(resolve_token) > 0
    way_id, status, stored_token = _row(db_path, closure_id)
    assert way_id == 38896608
    assert status == "active"
    assert stored_token == resolve_token


def test_store_closure_generates_a_different_token_each_time(tmp_path):
    # The whole point of a per-report token (see store_closure's own
    # docstring on why this exists at all) fails silently if two reports
    # ever collide -- secrets.token_urlsafe(24) should make that
    # astronomically unlikely, but this at least confirms two real calls
    # produce two different values, not e.g. a fixed/cached one.
    db_path = _fresh_db(tmp_path)
    _, token_a = store_closure(1, db_path=db_path)
    _, token_b = store_closure(2, db_path=db_path)
    assert token_a != token_b


# --- expire_stale_closures ------------------------------------------------

def test_expire_stale_closures_expires_reports_older_than_max_age(tmp_path):
    db_path = _fresh_db(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    closure_id = _insert_with_reported_at(db_path, 111, old, status="active")

    expired_ids = expire_stale_closures(max_age_days=7, db_path=db_path)

    assert expired_ids == [closure_id]
    _, status, _ = _row(db_path, closure_id)
    assert status == "expired"


def test_expire_stale_closures_leaves_recent_reports_active(tmp_path):
    db_path = _fresh_db(tmp_path)
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    closure_id = _insert_with_reported_at(db_path, 111, recent, status="active")

    expired_ids = expire_stale_closures(max_age_days=7, db_path=db_path)

    assert expired_ids == []
    _, status, _ = _row(db_path, closure_id)
    assert status == "active"


def test_expire_stale_closures_never_touches_already_resolved_reports(tmp_path):
    # A resolved report already reflects a real outcome (someone
    # confirmed it's clear) -- module docstring is explicit this should
    # never get reclassified as merely aged-out, even if it's old.
    db_path = _fresh_db(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    closure_id = _insert_with_reported_at(db_path, 111, old, status="resolved")

    expired_ids = expire_stale_closures(max_age_days=7, db_path=db_path)

    assert expired_ids == []
    _, status, _ = _row(db_path, closure_id)
    assert status == "resolved"


# --- get_active_closure_way_ids -------------------------------------------

def test_get_active_closure_way_ids_returns_only_active_way_ids(tmp_path):
    db_path = _fresh_db(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    _insert_with_reported_at(db_path, 111, now, status="active")
    _insert_with_reported_at(db_path, 222, now, status="resolved")

    assert get_active_closure_way_ids(db_path=db_path) == [111]


def test_get_active_closure_way_ids_deduplicates_multiple_reports_on_the_same_way(tmp_path):
    # store_closure inserts a new independent row per report rather than
    # upserting one row per way (module docstring) -- this confirms the
    # DISTINCT actually does its job when a way has multiple active
    # reports.
    db_path = _fresh_db(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    _insert_with_reported_at(db_path, 111, now, status="active")
    _insert_with_reported_at(db_path, 111, now, status="active")

    assert get_active_closure_way_ids(db_path=db_path) == [111]


def test_get_active_closure_way_ids_expires_stale_reports_before_reading(tmp_path):
    # "active" here has to mean "reported AND still within the decay
    # window," not just "never explicitly resolved" (module docstring) --
    # a way whose only report just aged out should not show as closed.
    db_path = _fresh_db(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    _insert_with_reported_at(db_path, 111, old, status="active")

    assert get_active_closure_way_ids(db_path=db_path) == []


def test_get_active_closure_way_ids_with_no_reports_is_an_empty_list(tmp_path):
    db_path = _fresh_db(tmp_path)
    assert get_active_closure_way_ids(db_path=db_path) == []


# --- resolve_closure -------------------------------------------------------

def test_resolve_closure_with_the_correct_token_resolves_it(tmp_path):
    db_path = _fresh_db(tmp_path)
    closure_id, token = store_closure(111, db_path=db_path)

    result = resolve_closure(closure_id, token, db_path=db_path)

    assert result == "resolved"
    _, status, _ = _row(db_path, closure_id)
    assert status == "resolved"


def test_resolve_closure_with_the_wrong_token_is_rejected_and_does_not_resolve(tmp_path):
    db_path = _fresh_db(tmp_path)
    closure_id, _real_token = store_closure(111, db_path=db_path)

    result = resolve_closure(closure_id, "not-the-real-token", db_path=db_path)

    assert result == "invalid_token"
    _, status, _ = _row(db_path, closure_id)
    assert status == "active"  # unchanged -- the wrong token must not resolve it


def test_resolve_closure_with_a_nonexistent_id_is_not_found(tmp_path):
    db_path = _fresh_db(tmp_path)
    assert resolve_closure(999, "any-token", db_path=db_path) == "not_found"


def test_resolve_closure_against_a_null_resolve_token_row_is_always_invalid(tmp_path):
    # Simulates a row from before the resolve_token column existed
    # (module docstring: these get NULL via the ALTER TABLE migration) --
    # there was never a token issued for it, so nothing should ever be
    # able to resolve it via a token, not even a real-looking guess.
    db_path = _fresh_db(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    closure_id = _insert_with_reported_at(db_path, 111, now, status="active", resolve_token=None)

    assert resolve_closure(closure_id, "some-guessed-token", db_path=db_path) == "invalid_token"


def test_resolve_closure_is_idempotent_with_the_same_correct_token(tmp_path):
    db_path = _fresh_db(tmp_path)
    closure_id, token = store_closure(111, db_path=db_path)

    first = resolve_closure(closure_id, token, db_path=db_path)
    second = resolve_closure(closure_id, token, db_path=db_path)

    assert first == "resolved"
    assert second == "resolved"
