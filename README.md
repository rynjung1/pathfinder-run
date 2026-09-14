# Pathfinder Run

A running app that generates scenic, non-repeating loop routes based on a target distance — prioritizing sidewalks and park paths instead of just shortest-path directions.

## Why

Most running apps either give turn-by-turn point-to-point directions or make you manually draw your own loop. Pathfinder Run generates the loop for you: give it a distance, and it builds a route that favors sidewalks/parks, avoids doubling back over the same segment repeatedly, and stays aware of closures.

## Status

v1 and v2 core scope (see [`docs/running-app-architecture.md`](docs/running-app-architecture.md) §0 for the phased plan this was built from) are both done:

- Self-hosted routing engine (GraphHopper) with a custom pedestrian profile (`graphhopper/pathfinder_foot.json`) — path-type weighting plus greenness/park-proximity, the latter via a small Java extension (`graphhopper-ext/`) baking a static "greenspace" encoded value into the graph at import time rather than evaluating it per request — covering the full province of Ontario, not just a single city (`graphhopper/config-ontario.yml`).
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

### App Store readiness

Beyond backend deployment, submitting to the App Store/Play Store needs:
- **Apple Developer Program enrollment** ($99/year) and an App Store Connect account — not something code can do, needs to happen directly.
- **A reachable production backend** — a release build can't point at `localhost`; this is the same VPS/domain blocker as backend deployment above.
- **Privacy policy**: drafted and published — https://claude.ai/code/artifact/b1f491f0-5c0c-4454-8c93-17ca88adf517 — describes the app's actual data handling (location, run history, device-scoped sync, deletion), with a real contact email now filled in. Needs to be pasted into App Store Connect's/Play Console's privacy policy URL fields at submission time.
- **Export compliance** (`mobile/app.json`'s `ios.infoPlist.ITSAppUsesNonExemptEncryption: false`): without this, every App Store Connect build submission stalls on a manual export-compliance question. This app only makes standard HTTPS requests (no custom encryption), so it's accurately exempt — declared once in code instead of answered by hand on every future build.
- **Privacy Manifest** (`mobile/app.json`'s `ios.privacyManifests`): declares precise-location collection accurately — was previously the stock Expo default declaring zero collected data types despite the app collecting and syncing location, now fixed.
- **App identity**: real name ("Pathfinder Run", was "mobile") and a real icon (`mobile/assets/generate_icons.py` — was the unmodified Expo template default) — both fixed.
- **Delete-my-data**: a real, working "Delete All My Data" button (Past Runs screen) clearing both local and server-synced data — required by app store review and by the privacy policy above; was missing entirely, now built.
- **Android permissions**: the generated manifest was requesting `SYSTEM_ALERT_WINDOW` (a sensitive, Play-Store-scrutinized permission — "display over other apps"), plus legacy `READ_EXTERNAL_STORAGE`/`WRITE_EXTERNAL_STORAGE`/`VIBRATE`, none of which this app uses (confirmed via grep — no vibration/haptics/overlay code anywhere) — inherited from transitive Expo/React Native dependencies (`expo-file-system`, RN's own template), not requested by anything this app does. Blocked via `mobile/app.json`'s `android.blockedPermissions` (verified in a real `expo prebuild`: the generated manifest now carries `tools:node="remove"` on all four, which strips them from the built APK/AAB). The release manifest now only requests what's actually used: location + internet.
- **Google Play Console's Data Safety form**: like Apple's Privacy Manifest, this is filled out in the console, not code — has to accurately declare: precise location (collected, linked to a device identifier, used for app functionality, not shared, user can request deletion), and that no data is sold or used for advertising. Should mirror the privacy policy above.
- **`eas.json`** (`mobile/eas.json`): build profiles set up — `development` (internal distribution, dev client, since the app's native modules — `react-native-maps`/`expo-sqlite`/`expo-location` — go beyond what Expo Go supports), `preview` (internal distribution, for testing a release-shaped build before submission), and `production` (store-ready, `autoIncrement` so build numbers don't need manual bumping). Each references an EAS-side `environment` rather than committing `EXPO_PUBLIC_PATHFINDER_API_KEY` — that var only exists locally via the gitignored `mobile/.env`, and a cloud build has no access to that file. `submit.production` is deliberately left empty (no `ascAppId`/`appleId`/Android `serviceAccountKeyPath`) since there's no App Store Connect/Play Console account yet to get real values from — `eas submit` will prompt for them interactively once those exist, rather than shipping placeholder credentials now.
- **EAS project**: linked for real — https://expo.dev/accounts/rynjung/projects/pathfinder-run (`mobile/app.json`'s `extra.eas.projectId`) — and `EXPO_PUBLIC_PATHFINDER_API_KEY` is set as a `sensitive` EAS env var across all three build environments, so a cloud build can actually authenticate against the backend once it's deployed.
- **End-to-end native verification**: beyond config generation (`expo prebuild`), the app was actually compiled with Xcode and run on an iOS Simulator (`expo run:ios`) — it installed, launched, called the real local `route_api.py`/GraphHopper backend, and rendered three genuine generated route candidates on the map, confirming the whole pipeline (native build → backend → routing engine → UI) works, not just that each piece works in isolation.
- **Apple vs. Google enrollment cost**: Apple Developer Program is $99/year; Google Play Console is a one-time $25 registration fee — different from what's implied above if only targeting Android.

## License

All rights reserved. No open-source license is granted — this is the
legal default with no `LICENSE` file present, stated explicitly here
rather than left as an unresolved placeholder.
