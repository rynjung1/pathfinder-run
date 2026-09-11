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

Endpoint:
    POST /route
    body: {"lat": 43.4643, "lon": -80.5204, "distance": 5000}
    optional: "bearing" (default 0), "candidates" (default 6),
              "top_n" (default 3), "profile" (default "foot")
    -> 200: GeoJSON FeatureCollection of the top-scoring candidates
             (candidates_to_geojson from generate_loop.py)
    -> 400: missing/invalid lat, lon, or distance
    -> 502: GraphHopper unreachable, or no candidate succeeded

Usage:
    python3 scripts/route_api.py
    curl -X POST http://localhost:5001/route \
        -H "Content-Type: application/json" \
        -d '{"lat": 43.4643, "lon": -80.5204, "distance": 5000}'
"""
import sys

from flask import Flask, jsonify, request

from generate_loop import (
    DEFAULT_DISTANCE_TOLERANCE_PCT,
    DEFAULT_GRAPHHOPPER_URL,
    DEFAULT_MAX_RADIUS_ITERATIONS,
    DEFAULT_REUSE_PENALTY_MULTIPLIER,
    candidates_to_geojson,
    generate_candidates,
)

app = Flask(__name__)


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

    try:
        candidates, _bearings = generate_candidates(
            DEFAULT_GRAPHHOPPER_URL, lat, lon, distance, bearing, num_candidates, profile,
            DEFAULT_REUSE_PENALTY_MULTIPLIER, DEFAULT_MAX_RADIUS_ITERATIONS, DEFAULT_DISTANCE_TOLERANCE_PCT,
        )
    except Exception as e:  # GraphHopper unreachable or similar -- surface as a gateway error, not a 500
        return jsonify({"error": f"route generation failed: {e}"}), 502

    if not candidates:
        return jsonify({"error": "no viable route candidates found for this point/distance"}), 502

    top = candidates[:top_n]
    return jsonify(candidates_to_geojson(top, lat, lon, distance))


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    # Port 5001, not Flask's default 5000 -- macOS's AirPlay Receiver squats
    # on 5000 by default and the conflict is a known source of confusion.
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5001
    app.run(port=port)
