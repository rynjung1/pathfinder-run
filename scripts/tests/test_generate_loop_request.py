"""Tests for generate_loop.py's _request -- the one function in that file
that actually talks to GraphHopper over HTTP. Everything else GraphHopper-
related is covered either by test_geometry.py (pure functions fed
hand-built "as if from GraphHopper" data, no network) or
test_route_regression.py (a real live server, skipped when none is
reachable). This file mocks urllib.request.urlopen directly -- same
monkeypatch pattern already used in test_route_api.py's /health tests
(monkeypatch.setattr(<module>.urllib.request, "urlopen", ...)) --
specifically to exercise _request's own response-parsing logic in
isolation, without needing a real GraphHopper instance running.
"""
import json

import pytest

import generate_loop
from generate_loop import _request


class FakeResponse:
    def __init__(self, body):
        self._body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


def test_request_returns_the_first_path_on_a_normal_response(monkeypatch):
    monkeypatch.setattr(
        generate_loop.urllib.request,
        "urlopen",
        lambda *a, **kw: FakeResponse({"paths": [{"distance": 123.4}, {"distance": 999}]}),
    )
    assert _request("http://example.invalid/route") == {"distance": 123.4}


def test_request_raises_a_catchable_runtime_error_when_paths_key_is_entirely_missing(monkeypatch):
    # Already worked before this sweep's fix -- confirms the missing-key
    # case (distinct from the empty-list case below) still raises the
    # RuntimeError build_candidate's `except (urllib.error.URLError,
    # RuntimeError)` is written to catch, not some other exception type.
    monkeypatch.setattr(
        generate_loop.urllib.request,
        "urlopen",
        lambda *a, **kw: FakeResponse({"message": "no route found between points"}),
    )
    with pytest.raises(RuntimeError, match="GraphHopper returned no path"):
        _request("http://example.invalid/route")


def test_request_raises_a_catchable_runtime_error_not_a_bare_indexerror_on_an_empty_paths_list(monkeypatch):
    # The actual bug found on a sweep: GraphHopper can return 200 OK with
    # {"paths": []} -- a real, already-documented behavior near
    # clipped-extract boundaries/sparse rural networks, not a
    # hypothetical malformed response. The key IS present (so the old
    # `if "paths" not in parsed` check passed it straight through), but
    # the list is empty, so `parsed["paths"][0]` used to raise a bare
    # IndexError -- neither URLError nor RuntimeError, so
    # build_candidate's own except clause never caught it, and it
    # propagated out of generate_candidates entirely, aborting every
    # bearing's candidate instead of just skipping this one. Must raise
    # the same catchable RuntimeError the missing-key case does.
    monkeypatch.setattr(generate_loop.urllib.request, "urlopen", lambda *a, **kw: FakeResponse({"paths": []}))
    with pytest.raises(RuntimeError, match="GraphHopper returned no path"):
        _request("http://example.invalid/route")
