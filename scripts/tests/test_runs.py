"""Tests for runs.py's SQLite-backed run-history sync. Unlike
test_geometry.py's pure functions, this genuinely touches a database --
each test gets its own isolated temp file (pytest's tmp_path) rather than
scripts/runs.db, so these are safe to run anywhere, any time, with no
risk to real data and no ordering dependence between tests.
"""
from runs import delete_runs_for_device, ensure_schema, get_runs_for_device, store_run


def _fresh_db(tmp_path):
    db_path = str(tmp_path / "test_runs.db")
    ensure_schema(db_path)
    return db_path


def test_store_run_inserts_a_new_row(tmp_path):
    db_path = _fresh_db(tmp_path)
    inserted = store_run(
        "device-a", "run-uuid-1", "2026-09-14T00:00:00Z",
        5000.0, 4820.3, 1620000,
        [{"latitude": 43.4643, "longitude": -80.5204, "timestamp": 1000}],
        db_path=db_path,
    )
    assert inserted is True


def test_store_run_is_idempotent_on_run_uuid(tmp_path):
    # Same rationale as the endpoint docstring: a retried sync request
    # after a flaky connection should be a no-op, not a duplicate row or
    # an error.
    db_path = _fresh_db(tmp_path)
    first = store_run("device-a", "run-uuid-1", "2026-09-14T00:00:00Z",
                       5000.0, 4820.3, 1620000, [], db_path=db_path)
    second = store_run("device-a", "run-uuid-1", "2026-09-14T00:00:00Z",
                        5000.0, 4820.3, 1620000, [], db_path=db_path)
    assert first is True
    assert second is False
    assert len(get_runs_for_device("device-a", db_path=db_path)) == 1


def test_get_runs_for_device_only_returns_matching_device(tmp_path):
    db_path = _fresh_db(tmp_path)
    store_run("device-a", "run-uuid-1", "2026-09-14T00:00:00Z",
              5000.0, 4820.3, 1620000, [], db_path=db_path)
    store_run("device-b", "run-uuid-2", "2026-09-14T00:05:00Z",
              3000.0, 3010.0, 900000, [], db_path=db_path)

    device_a_runs = get_runs_for_device("device-a", db_path=db_path)
    assert len(device_a_runs) == 1
    assert device_a_runs[0]["runUuid"] == "run-uuid-1"


def test_get_runs_for_device_unknown_device_is_empty(tmp_path):
    db_path = _fresh_db(tmp_path)
    assert get_runs_for_device("no-such-device", db_path=db_path) == []


def test_get_runs_for_device_orders_most_recent_first(tmp_path):
    db_path = _fresh_db(tmp_path)
    store_run("device-a", "run-uuid-older", "2026-09-14T00:00:00Z",
              5000.0, 4820.3, 1620000, [], db_path=db_path)
    store_run("device-a", "run-uuid-newer", "2026-09-14T01:00:00Z",
              5000.0, 4820.3, 1620000, [], db_path=db_path)

    runs = get_runs_for_device("device-a", db_path=db_path)
    assert [r["runUuid"] for r in runs] == ["run-uuid-newer", "run-uuid-older"]


def test_get_runs_for_device_round_trips_trace_as_parsed_list(tmp_path):
    db_path = _fresh_db(tmp_path)
    trace = [
        {"latitude": 43.4643, "longitude": -80.5204, "timestamp": 1000},
        {"latitude": 43.4650, "longitude": -80.5190, "timestamp": 3000},
    ]
    store_run("device-a", "run-uuid-1", "2026-09-14T00:00:00Z",
              5000.0, 4820.3, 1620000, trace, db_path=db_path)

    result = get_runs_for_device("device-a", db_path=db_path)[0]
    assert result["trace"] == trace  # parsed back, not a JSON string


def test_delete_runs_for_device_removes_only_that_devices_rows(tmp_path):
    db_path = _fresh_db(tmp_path)
    store_run("device-a", "run-uuid-1", "2026-09-14T00:00:00Z",
              5000.0, 4820.3, 1620000, [], db_path=db_path)
    store_run("device-a", "run-uuid-2", "2026-09-14T01:00:00Z",
              5000.0, 4820.3, 1620000, [], db_path=db_path)
    store_run("device-b", "run-uuid-3", "2026-09-14T02:00:00Z",
              5000.0, 4820.3, 1620000, [], db_path=db_path)

    deleted = delete_runs_for_device("device-a", db_path=db_path)

    assert deleted == 2
    assert get_runs_for_device("device-a", db_path=db_path) == []
    assert len(get_runs_for_device("device-b", db_path=db_path)) == 1


def test_delete_runs_for_device_with_no_runs_is_a_harmless_no_op(tmp_path):
    db_path = _fresh_db(tmp_path)
    assert delete_runs_for_device("no-such-device", db_path=db_path) == 0
