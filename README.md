# Pathfinder Run

A running app that generates scenic, non-repeating loop routes based on a target distance — prioritizing sidewalks and park paths instead of just shortest-path directions.

## Why

Most running apps either give turn-by-turn point-to-point directions or make you manually draw your own loop. Pathfinder Run generates the loop for you: give it a distance, and it builds a route that favors sidewalks/parks, avoids doubling back over the same segment repeatedly, and stays aware of closures.

## Status

v1 and v2 core scope (see [`docs/running-app-architecture.md`](docs/running-app-architecture.md) §0 for the phased plan this was built from) are both done:

- Self-hosted routing engine (GraphHopper) with a custom pedestrian profile (`graphhopper/pathfinder_foot.json`), covering the full province of Ontario, not just a single city (`graphhopper/config-ontario.yml`).
- Loop generation with an edge-reuse penalty and a compactness score (`scripts/generate_loop.py`).
- Mobile client: request a route, choose between the 2-3 generated alternatives, view the chosen one on a map (`mobile/App.js`).
- Live GPS tracking during a run, with on-device deviation detection.
- Crowdsourced closure reporting, matched to OSM edges and fed into the routing cost (`scripts/closures.py`).
- Run history: local SQLite storage, replay of a past run's actual recorded GPS trace on a map, and a best-effort device-scoped sync to the server for durability (`mobile/db.js`, `scripts/runs.py`) — no user accounts, so this doesn't survive an app reinstall; see `runs.py`'s module docstring for the honest scope.

Also done: a pre-deployment hardening pass (API-key auth, per-IP rate limiting, input caps, a production WSGI server — `scripts/route_api.py`, `scripts/serve.py`). Actual VPS deployment hasn't happened yet — it's blocked on a domain (HTTPS needs one; see [`deploy/README.md`](deploy/README.md) for the concrete, ready-to-run plan). v3 (background/`Always` location) is a deliberate not-yet, not an oversight — see the file header of `mobile/App.js` for why.

## Stack

- **Routing engine:** [GraphHopper](https://www.graphhopper.com/) (self-hosted) with a custom pedestrian profile, against raw OSM data.
- **Storage:** SQLite, not PostGIS — deliberate, not a placeholder. See the reasoning in [`scripts/closures.py`](scripts/closures.py)'s module docstring (search for "Storage: SQLite, not PostGIS").
- **Backend API:** Python/Flask (`scripts/route_api.py`), served via [waitress](https://github.com/Pylons/waitress) in production (`scripts/serve.py`).
- **Mobile:** React Native via [Expo](https://expo.dev/) (`mobile/`), with local run history in SQLite via `expo-sqlite` (`mobile/db.js`), synced to the server (`scripts/runs.py`, device-scoped, no accounts) on a best-effort basis.

## Setup

### 1. Routing engine (GraphHopper)

Requires Java 17+.

You'll need an Ontario OSM extract (e.g. from [Geofabrik](https://download.geofabrik.de/north-america/canada.html)) placed under `data/raw/` — it's gitignored (too large to commit), so this isn't fetched for you. If your filename doesn't match `graphhopper/config-ontario.yml`'s `datareader.file`, update that line.

```bash
cd graphhopper
./run-graphhopper.sh   # defaults to config-ontario.yml; first run imports the graph (~1 min)
```

Starts on `localhost:8995` (API) / `8996` (admin). See the script's own header comment for why it pins JDK 17 and sets `-Xmx10g`.

### 2. Backend API (`scripts/route_api.py`)

```bash
cd scripts
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in a real PATHFINDER_API_KEY -- .env.example has the exact command to generate one
venv/bin/python3 route_api.py   # local dev: binds 0.0.0.0:5001, so the iOS Simulator/a physical device on the LAN can reach it
```

For a production-style run instead of the Flask dev server, use `venv/bin/python3 serve.py` (waitress; binds `127.0.0.1` by default — see that file's docstring).

### 3. Mobile client (`mobile/`)

```bash
cd mobile
npm install
cp .env.example .env   # EXPO_PUBLIC_PATHFINDER_API_KEY must match scripts/.env's PATHFINDER_API_KEY
npx expo start --ios   # or --android
```

See `mobile/App.js`'s file header for the `API_BASE_URL` addressing notes (Simulator vs. physical device vs. Android emulator all need different values).

`npm audit` reports 10 moderate-severity findings here — a known, accepted issue, not an unaddressed gap. All 10 trace to exactly one advisory ([GHSA-w5hq-g745-h8pq](https://github.com/advisories/GHSA-w5hq-g745-h8pq), a `uuid` buffer-bounds issue), reached only through Expo's own build-time tooling (`@expo/cli` → `@expo/config-plugins` → `xcode` → `uuid`) — code that runs on the dev machine during `expo start`/`expo prebuild`, never inside the JS bundle shipped to a device, so it's not reachable by any network input. The only fix `npm audit fix --force` offers is downgrading `expo` to `46.0.21` — an 11-major-version regression that would almost certainly break `expo-sqlite`/`expo-location`/`react-native-maps` compatibility — to patch build tooling that isn't exposed at runtime. Confirmed via `npm audit --json` that this really is one advisory propagated across 10 package entries, not 10 distinct issues. Not worth that cost for this risk; revisit if Expo ships a patched `uuid`/`xcode` without requiring the downgrade.

### Deploying for real

Not done yet — see [`deploy/README.md`](deploy/README.md) for the concrete plan (systemd units, layout, what's blocked and on what).

## License

All rights reserved. No open-source license is granted — this is the
legal default with no `LICENSE` file present, stated explicitly here
rather than left as an unresolved placeholder.
