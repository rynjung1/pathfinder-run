#!/usr/bin/env python3
"""
Pathfinder Run -- v1 mobile client backbone, §7 step 3.

A minimal HTTP wrapper around generate_loop.py's candidate generation, so a
mobile client has something to call over the network instead of shelling
out to the CLI script. Still deliberately NOT the full API gateway from
§4 -- no user accounts, no persistence beyond closures.db, no request
logging beyond the WSGI server's default -- but as of the pre-deployment
hardening pass, no longer wide open either: a static shared API key
(require_api_key), per-IP rate limits (flask-limiter), input caps on
/route, and per-report resolve tokens on /closures are all real now. See
that hardening review's own notes for what's covered and what's
explicitly still deferred (real user accounts, per-city closure
filtering, etc.).

All endpoints below except /health require an `X-API-Key` header
matching the PATHFINDER_API_KEY environment variable (see .env.example).
Requests are also rate-limited per source IP.

Coverage (§0's "expand to additional cities"): fronts one GraphHopper
instance covering the full province of Ontario (cities.py) -- this
started as a genuine per-city dispatcher (a separate instance each for
Waterloo Region and Guelph) but a real feasibility check found one
instance can cover the whole province comfortably (~8 minute import,
~4GB peak memory), so that's what actually runs now; see cities.py's
module docstring for the history and the investigation this was based
on. A point-in-polygon coverage check against Ontario's real boundary
still runs on every request -- a location outside Ontario gets a plain
400, not a guess or a nonsense route. The client never specifies a
region; this was true before Guelph existed and stays true now.

Endpoints:
    POST /route  (rate limit: 30/min per IP)
    body: {"lat": 43.4643, "lon": -80.5204, "distance": 5000}
    optional: "bearing" (default 0), "candidates" (default 6, max 12),
              "top_n" (default 3, max 10), "profile" (default "foot")
    -> 200: GeoJSON FeatureCollection of the top-scoring candidates
             (candidates_to_geojson from generate_loop.py)
    -> 400: missing/invalid lat, lon, or distance; distance over 30km;
             candidates/top_n over their max
    -> 401: missing/invalid X-API-Key
    -> 429: rate limit exceeded
    -> 502: GraphHopper unreachable, or no candidate succeeded

    POST /closures  (rate limit: 20/min per IP) -- §6/§7 step 5. Wired
    into /route's routing cost as a hard exclusion (see generate_loop.py's
    fetch_return_leg/fetch_outbound_leg docstrings and closures.py's
    get_active_closure_way_ids) -- a route request made after a closure
    report will avoid that way on both legs.
    body: {"lat": 43.4643, "lon": -80.5204}
    optional: "profile" (default "foot")
    -> 201: {"id", "osm_way_id", "status", "resolve_token"} -- the stored
             report. resolve_token is returned ONCE, here -- hold onto it
             to resolve this report later; there's no way to recover it
             afterward by id alone (see closures.py's store_closure).
    -> 400: missing/invalid lat/lon, or no routable way near that point
    -> 401: missing/invalid X-API-Key
    -> 429: rate limit exceeded
    -> 502: GraphHopper unreachable

    PATCH /closures/<id>  (rate limit: 20/min per IP) -- mark one report
    resolved. Requires the resolve_token issued when that report was
    created -- ids are sequential and guessable, the token isn't (basic
    clear mechanism; no time-based decay/expiry yet -- see closures.py).
    body: {"resolve_token": "..."}
    -> 200: {"id", "status": "resolved"}
    -> 400: missing/invalid resolve_token in the body
    -> 401: missing/invalid X-API-Key
    -> 403: resolve_token doesn't match this closure
    -> 404: no closure with that id
    -> 429: rate limit exceeded

Usage:
    python3 scripts/route_api.py
    curl -X POST http://localhost:5001/route \
        -H "Content-Type: application/json" -H "X-API-Key: $PATHFINDER_API_KEY" \
        -d '{"lat": 43.4643, "lon": -80.5204, "distance": 5000}'
    curl -X POST http://localhost:5001/closures \
        -H "Content-Type: application/json" -H "X-API-Key: $PATHFINDER_API_KEY" \
        -d '{"lat": 43.4643, "lon": -80.5204}'
    curl -X PATCH http://localhost:5001/closures/1 \
        -H "Content-Type: application/json" -H "X-API-Key: $PATHFINDER_API_KEY" \
        -d '{"resolve_token": "..."}'
"""
import functools
import os
import secrets
import sys

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from generate_loop import (
    DEFAULT_DISTANCE_TOLERANCE_PCT,
    DEFAULT_MAX_RADIUS_ITERATIONS,
    DEFAULT_REUSE_PENALTY_MULTIPLIER,
    candidates_to_geojson,
    generate_candidates,
    get_long_way_ids,
)
from closures import (
    ensure_schema,
    get_active_closure_way_ids,
    resolve_closure,
    snap_to_way_id,
    store_closure,
)
from cities import resolve_city

# Pre-deployment hardening (§4/§7, "is this safe to expose on the public
# internet" review) -- see that review's own notes for the reasoning
# behind each piece below, not just the mechanics.

load_dotenv()  # reads .env in this directory if present; a no-op if it's
                # not (e.g. env vars set some other way, like systemd's
                # EnvironmentFile) -- never overrides an already-set
                # real environment variable.

# Required, not optional with a silent bypass: an earlier draft of this
# considered "skip the check if the env var isn't set" for local-dev
# convenience, but that's exactly the kind of default that gets deployed
# by accident with no auth at all. Fails at import time instead, loudly,
# in every environment including local dev -- a .env with a real
# (dev-only) key is the intended way to satisfy this locally, not a
# bypass path.
API_KEY = os.environ.get("PATHFINDER_API_KEY")
if not API_KEY:
    raise RuntimeError(
        "PATHFINDER_API_KEY is not set. Set it in scripts/.env (see .env.example) "
        "or in the real environment before starting route_api.py -- there is no "
        "unauthenticated mode."
    )

app = Flask(__name__)
ensure_schema()

# In-memory storage is fine at this scale (a single-process small beta,
# not a fleet behind a load balancer) -- see the hardening review. Limits
# are ad hoc starting points, like this codebase's other tunables
# (DEFAULT_REUSE_PENALTY_MULTIPLIER, DEFAULT_COMPACTNESS_WEIGHT, etc.):
# generous enough for a real user tapping "regenerate" a few times, tight
# enough to blunt casual scripted abuse. Revisit once real usage shows
# whether they're too tight or too loose.
limiter = Limiter(key_func=get_remote_address, app=app, storage_uri="memory://")

# Input caps for /route -- see require_api_key's neighboring comment on
# why these exist independent of rate limiting: a single oversized
# request (e.g. distance=500000, candidates=10000) can tie up the server
# without needing repeated requests at all. Ad hoc, like the rate limits
# above -- 30km comfortably covers any real run distance this app is
# meant for; 12 candidates is already more than generate_candidates' own
# default (6) and more alternatives than §5 point 4 asks for (2-3).
MAX_DISTANCE_M = 30000
MAX_CANDIDATES = 12
MAX_TOP_N = 10


def require_api_key(view):
    """A static shared key, not user auth -- deliberately not the full §4
    gateway. Checked with secrets.compare_digest, not `==`: a plain
    string comparison short-circuits at the first differing character,
    which leaks (via response-time differences) how many leading
    characters of a guess were correct; compare_digest runs in constant
    time regardless.

    Honest about its real limit (see the hardening review): a key baked
    into a mobile app build is extractable by anyone who decompiles it --
    this stops casual/automated abuse and drive-by scraping, not a
    targeted attacker. That's the right bar for a small beta, not a
    false promise of more."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        provided = request.headers.get("X-API-Key", "")
        if not secrets.compare_digest(provided, API_KEY):
            return jsonify({"error": "missing or invalid API key"}), 401
        return view(*args, **kwargs)
    return wrapped


@app.route("/route", methods=["POST"])
@limiter.limit("30 per minute")
@require_api_key
def route():
    body = request.get_json(silent=True) or {}

    try:
        lat = float(body["lat"])
        lon = float(body["lon"])
        distance = float(body["distance"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "lat, lon, and distance (meters) are required numeric fields"}), 400
    if distance <= 0:
        return jsonify({"error": "distance must be positive"}), 400
    if distance > MAX_DISTANCE_M:
        return jsonify({"error": f"distance must be at most {MAX_DISTANCE_M} meters"}), 400

    try:
        bearing = float(body.get("bearing", 0.0))
        num_candidates = int(body.get("candidates", 6))
        top_n = int(body.get("top_n", 3))
    except (TypeError, ValueError):
        return jsonify({"error": "bearing, candidates, and top_n must be numeric"}), 400
    profile = body.get("profile", "foot")
    if not isinstance(profile, str):
        return jsonify({"error": "profile must be a string"}), 400
    if num_candidates < 1 or top_n < 1:
        return jsonify({"error": "candidates and top_n must be at least 1"}), 400
    # Uncapped, a single request could ask for thousands of candidates at
    # an arbitrary distance -- each one is multiple GraphHopper round
    # trips, so this is a single-request resource-exhaustion vector, not
    # something rate limiting (which throttles request *volume*) protects
    # against on its own. Found during the pre-deployment hardening
    # review, fixed here rather than left for rate limiting to paper over.
    if num_candidates > MAX_CANDIDATES:
        return jsonify({"error": f"candidates must be at most {MAX_CANDIDATES}"}), 400
    if top_n > MAX_TOP_N:
        return jsonify({"error": f"top_n must be at most {MAX_TOP_N}"}), 400

    # Coverage check against Ontario's real boundary -- see cities.py's
    # module docstring for the history/reasoning. Not a client-supplied
    # region name: the client's contract stays "send lat/lon, get a
    # route," unchanged since before Guelph existed.
    city = resolve_city(lat, lon)
    if city is None:
        return jsonify({"error": "no routing coverage for this location"}), 400

    # Active closures apply to every candidate/bearing in this request, so
    # fetch once here rather than inside generate_candidates -- see that
    # function's docstring for why it doesn't query closures.py itself
    # (avoids a circular import; closures.py imports from generate_loop.py).
    # Not city-filtered (see cities.py's docstring on why that's a known,
    # deliberately deferred limitation, not a correctness bug).
    closed_way_ids = get_active_closure_way_ids()

    # This city's own long-way audit, not the module default (Waterloo
    # Region's) -- see generate_candidates' docstring on why this must be
    # passed explicitly for a non-default city.
    long_way_ids = get_long_way_ids(city["long_ways_path"])

    try:
        candidates, _bearings = generate_candidates(
            city["base_url"], lat, lon, distance, bearing, num_candidates, profile,
            DEFAULT_REUSE_PENALTY_MULTIPLIER, DEFAULT_MAX_RADIUS_ITERATIONS, DEFAULT_DISTANCE_TOLERANCE_PCT,
            closed_way_ids=closed_way_ids, long_way_ids=long_way_ids,
        )
    except Exception as e:  # GraphHopper unreachable or similar -- surface as a gateway error, not a 500
        return jsonify({"error": f"route generation failed: {e}"}), 502

    if not candidates:
        return jsonify({"error": "no viable route candidates found for this point/distance"}), 502

    top = candidates[:top_n]
    return jsonify(candidates_to_geojson(top, lat, lon, distance))


@app.route("/closures", methods=["POST"])
@limiter.limit("20 per minute")
@require_api_key
def report_closure():
    body = request.get_json(silent=True) or {}

    try:
        lat = float(body["lat"])
        lon = float(body["lon"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "lat and lon are required numeric fields"}), 400

    profile = body.get("profile", "foot")
    if not isinstance(profile, str):
        return jsonify({"error": "profile must be a string"}), 400

    city = resolve_city(lat, lon)
    if city is None:
        return jsonify({"error": "no routing coverage for this location"}), 400

    try:
        way_id = snap_to_way_id(city["base_url"], lat, lon, profile)
    except Exception as e:  # GraphHopper unreachable -- gateway error, not a 500
        return jsonify({"error": f"could not reach routing engine: {e}"}), 502

    if way_id is None:
        return jsonify({"error": "no routable way found near the reported location"}), 400

    closure_id, resolve_token = store_closure(way_id)
    # resolve_token is returned exactly once, here -- the client (or
    # whoever reported it) needs to hold onto it to resolve this specific
    # report later. There's no way to recover it afterward by id alone;
    # see closures.py's store_closure docstring for why that's the point.
    return jsonify({"id": closure_id, "osm_way_id": way_id, "status": "active", "resolve_token": resolve_token}), 201


@app.route("/closures/<int:closure_id>", methods=["PATCH"])
@limiter.limit("20 per minute")
@require_api_key
def resolve_closure_route(closure_id):
    body = request.get_json(silent=True) or {}
    resolve_token = body.get("resolve_token")
    if not isinstance(resolve_token, str) or not resolve_token:
        return jsonify({"error": "resolve_token (string) is required"}), 400

    result = resolve_closure(closure_id, resolve_token)
    if result == "not_found":
        return jsonify({"error": f"no closure with id {closure_id}"}), 404
    if result == "invalid_token":
        # 403, not 404: the id is real, the caller just can't prove they're
        # allowed to resolve it. This does let a caller distinguish "id
        # exists" from "id doesn't" by status code alone -- an accepted,
        # low-value leak for a small beta (a closure id on its own isn't
        # sensitive, unlike e.g. an account email in a login flow) traded
        # for a response a legitimate integrator can actually debug
        # against, rather than one indistinguishable failure for two very
        # different problems.
        return jsonify({"error": "invalid resolve_token for this closure"}), 403
    return jsonify({"id": closure_id, "status": "resolved"})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    # Port 5001, not Flask's default 5000 -- macOS's AirPlay Receiver squats
    # on 5000 by default and the conflict is a known source of confusion.
    #
    # host="0.0.0.0": an iOS Simulator can reach 127.0.0.1 directly (it
    # shares the host Mac's network stack), but a physical device running
    # Expo Go cannot -- it needs the dev machine's LAN IP, which only
    # resolves if this server is actually listening on all interfaces, not
    # just loopback. Binding wide costs nothing on a local dev machine and
    # avoids a second silent failure mode later.
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5001
    app.run(host="0.0.0.0", port=port)
