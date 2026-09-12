#!/usr/bin/env python3
"""
Pathfinder Run -- v1 mobile client backbone, §7 step 3.

A minimal HTTP wrapper around generate_loop.py's candidate generation, so a
mobile client has something to call over the network instead of shelling
out to the CLI script. Deliberately NOT the API gateway from §4 -- no auth,
no rate limiting, no persistence, no request logging beyond Flask's default.
Just enough for the React Native side to request a route and get GeoJSON
back. Auth/rate-limiting/etc. are real requirements before this is anything
but a local dev server -- not addressed here.

Multi-city (§0's "expand to additional cities"): this single Flask app
fronts multiple GraphHopper instances, one per city (cities.py), and
resolves which one to query per-request via point-in-polygon on the
request's own lat/lon against each city's real boundary -- see
cities.py's module docstring for the investigation this was based on.
The client never specifies a city; a location outside every known city's
boundary gets a plain 400, not a guess.

Endpoints:
    POST /route
    body: {"lat": 43.4643, "lon": -80.5204, "distance": 5000}
    optional: "bearing" (default 0), "candidates" (default 6),
              "top_n" (default 3), "profile" (default "foot")
    -> 200: GeoJSON FeatureCollection of the top-scoring candidates
             (candidates_to_geojson from generate_loop.py)
    -> 400: missing/invalid lat, lon, or distance
    -> 502: GraphHopper unreachable, or no candidate succeeded

    POST /closures -- §6/§7 step 5. Wired into /route's routing cost as a
    hard exclusion (see generate_loop.py's fetch_return_leg/fetch_outbound_leg
    docstrings and closures.py's get_active_closure_way_ids) -- a route
    request made after a closure report will avoid that way on both legs.
    body: {"lat": 43.4643, "lon": -80.5204}
    optional: "profile" (default "foot")
    -> 201: {"id", "osm_way_id", "status"} -- the stored report
    -> 400: missing/invalid lat/lon, or no routable way near that point
    -> 502: GraphHopper unreachable

    PATCH /closures/<id> -- mark one report resolved (basic clear
    mechanism; no time-based decay/expiry yet -- see closures.py).
    -> 200: {"id", "status": "resolved"}
    -> 404: no closure with that id

Usage:
    python3 scripts/route_api.py
    curl -X POST http://localhost:5001/route \
        -H "Content-Type: application/json" \
        -d '{"lat": 43.4643, "lon": -80.5204, "distance": 5000}'
    curl -X POST http://localhost:5001/closures \
        -H "Content-Type: application/json" \
        -d '{"lat": 43.4643, "lon": -80.5204}'
    curl -X PATCH http://localhost:5001/closures/1
"""
import sys

from flask import Flask, jsonify, request

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

app = Flask(__name__)
ensure_schema()


@app.route("/route", methods=["POST"])
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

    # Multi-city dispatch (§0's "expand to additional cities") -- see
    # cities.py's module docstring for the investigation/reasoning. Point-in-
    # polygon against each city's real boundary, not a client-supplied city
    # name: the client's contract stays "send lat/lon, get a route," exactly
    # as before Guelph existed.
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

    closure_id = store_closure(way_id)
    return jsonify({"id": closure_id, "osm_way_id": way_id, "status": "active"}), 201


@app.route("/closures/<int:closure_id>", methods=["PATCH"])
def resolve_closure_route(closure_id):
    if not resolve_closure(closure_id):
        return jsonify({"error": f"no closure with id {closure_id}"}), 404
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
