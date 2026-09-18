# Pathfinder Run

A running app that generates scenic, non-repeating loop routes based on a target distance — prioritizing sidewalks and park paths instead of just shortest-path directions.

## Why

Most running apps either give turn-by-turn point-to-point directions or make you manually draw your own loop. Pathfinder Run generates the loop for you: give it a distance, and it builds a route that favors sidewalks/parks, avoids doubling back over the same segment repeatedly, and stays aware of closures.

## Status

v1 and v2 core scope (see [`docs/running-app-architecture.md`](docs/running-app-architecture.md) §0 for the phased plan this was built from) are both done:

- Self-hosted routing engine (GraphHopper) with a custom pedestrian profile (`graphhopper/pathfinder_foot.json`) — path-type weighting plus greenness/park-proximity, the latter via a small Java extension (`graphhopper-ext/`) baking a static "greenspace" encoded value into the graph at import time rather than evaluating it per request — covering the full province of Ontario, not just a single city (`graphhopper/config-ontario.yml`).
- Loop generation with an edge-reuse penalty and a compactness score (`scripts/generate_loop.py`).
- Mobile client: pick a distance (real presets, not a fixed 5km), request a route, choose between the 2-3 generated alternatives, view the chosen one on a map, and see a real post-run summary (distance/time/pace) when you finish (`mobile/App.js`) — a real branded UI (not stock components), with actual dark mode and accessibility support (screen-reader labels/roles, real touch targets, per-device safe-area insets), not an afterthought.
- Live GPS tracking during a run, with on-device deviation detection.
- Crowdsourced closure reporting, matched to OSM edges and fed into the routing cost (`scripts/closures.py`).
- Run history: local SQLite storage, replay of a past run's actual recorded GPS trace on a map, and a best-effort device-scoped sync to the server for durability (`mobile/db.js`, `scripts/runs.py`) — no user accounts, so this doesn't survive an app reinstall; see `runs.py`'s module docstring for the honest scope.

Also done: a pre-deployment hardening pass (API-key auth, per-IP rate limiting, input caps, a production WSGI server — `scripts/route_api.py`, `scripts/serve.py`). **Live in production** as of 2026-09-18: `https://api.pathfinderrun.com` (Hetzner CX23, real Let's Encrypt HTTPS via Caddy) — see [`deploy/README.md`](deploy/README.md) for the exact steps run to get there. v3 (background/`Always` location) is a deliberate not-yet, not an oversight — see the file header of `mobile/App.js` for why.

Real automated test coverage across the whole stack, not just the mobile geometry helpers it started with: Python (backend endpoints, closures, run storage, the route-scoring/GeoJSON output), the mobile client (including `db.js`'s SQLite logic, backed in tests by a real embedded SQLite engine rather than a mock, since `expo-sqlite` itself can't load under Jest), and `graphhopper-ext`'s Java extension — all three run in CI on every push ([`.github/workflows/test.yml`](.github/workflows/test.yml)), not just locally.

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

`npm audit` reports 11 moderate-severity findings here — a known, accepted issue, not an unaddressed gap. All 11 trace to exactly one advisory ([GHSA-w5hq-g745-h8pq](https://github.com/advisories/GHSA-w5hq-g745-h8pq), a `uuid` buffer-bounds issue), reached only through Expo's own build-time tooling (`@expo/cli` → `@expo/config-plugins` → `xcode` → `uuid`) — code that runs on the dev machine during `expo start`/`expo prebuild`, never inside the JS bundle shipped to a device, so it's not reachable by any network input. (Was 10 before `expo-splash-screen` was added — its config plugin pulls in the same already-accepted chain, one more package name, not a new distinct issue; confirmed via `npm audit --json` again.) The only fix `npm audit fix --force` offers is downgrading `expo` to `46.0.21` — an 11-major-version regression that would almost certainly break `expo-sqlite`/`expo-location`/`react-native-maps` compatibility — to patch build tooling that isn't exposed at runtime. Not worth that cost for this risk; revisit if Expo ships a patched `uuid`/`xcode` without requiring the downgrade.

### Deploying for real

Done — live at `https://api.pathfinderrun.com` (Hetzner CX23, Helsinki). See [`deploy/README.md`](deploy/README.md) for the exact install steps and the real gotchas hit doing it (Caddy's log directory, Oracle Cloud's free-tier capacity exhaustion).

### App Store readiness

For the actual order to do these in, not just the list of facts below, see [`docs/launch-checklist.md`](docs/launch-checklist.md).

Beyond backend deployment, submitting to the App Store/Play Store needs:
- **Apple Developer Program enrollment** ($99/year) and an App Store Connect account — not something code can do, needs to happen directly.
- **A reachable production backend** — done, see "Deploying for real" above. The only remaining step is pointing the mobile app's production build at it (`eas env:set --name EXPO_PUBLIC_API_BASE_URL --value https://api.pathfinderrun.com --environment production`), then a fresh `eas build`.
- **App Store Connect listing copy** (name, subtitle, promotional text, description, keywords, category, age rating): drafted and ready to copy-paste in [`docs/app-store-listing.md`](docs/app-store-listing.md), character/byte counts verified against Apple's real field limits, not estimated. The one thing it can't fill in: a real Support URL, since that needs to actually be reachable — that file names a concrete, honest option (the public GitHub repo) rather than leave a placeholder.
- **App Store screenshots**: two real ones exist ([`docs/app-store-screenshots/`](docs/app-store-screenshots/)) — captured from an actual running build against the real backend, at exactly Apple's required 6.9" resolution (1320×2868, verified), not mockups. Covers the main route-generation screen in both light and dark mode; a fuller set (post-run summary, past-runs replay) needs tapping through a live run on a real device or simulator, which wasn't possible in this environment.
- **Privacy policy**: drafted and published — https://claude.ai/code/artifact/b1f491f0-5c0c-4454-8c93-17ca88adf517 — describes the app's actual data handling (location, run history, device-scoped sync, deletion), with a real contact email now filled in. Needs to be pasted into App Store Connect's/Play Console's privacy policy URL fields at submission time.
- **Export compliance** (`mobile/app.json`'s `ios.infoPlist.ITSAppUsesNonExemptEncryption: false`): without this, every App Store Connect build submission stalls on a manual export-compliance question. This app only makes standard HTTPS requests (no custom encryption), so it's accurately exempt — declared once in code instead of answered by hand on every future build.
- **Privacy Manifest** (`mobile/app.json`'s `ios.privacyManifests`): declares precise-location collection accurately — was previously the stock Expo default declaring zero collected data types despite the app collecting and syncing location, now fixed.
- **Location/motion usage descriptions** (`mobile/app.json`'s `expo-location` plugin config): the shown-to-users string previously only described one-time route generation, never mentioning the continuous live tracking that actually runs for the whole duration of a run (position, distance, pace, off-route detection) — a real App Review Guideline 5.1.1 risk (the string has to describe every use, not just the first one a reviewer happens to trigger). A first attempt at this fix edited `ios.infoPlist.NSLocationWhenInUseUsageDescription` directly — verified wrong via a real `expo prebuild`: the plugin's own `locationWhenInUsePermission` option is what actually wins in the generated Info.plist, so that edit never shipped at all; the manual infoPlist key (a redundant second source of truth) has been removed entirely so this can't silently drift again. Also fixed: the same plugin unconditionally injects `NSLocationAlwaysAndWhenInUseUsageDescription`/`NSLocationAlwaysUsageDescription`/`NSMotionUsageDescription` with generic Expo placeholder text regardless of whether an app uses them — this app never requests background/"Always" location (the v3 deferral), so those two now say so plainly, and the motion one now describes its real use (adaptive GPS sampling) instead of shipping unmodified boilerplate.
- **iPad support** (`mobile/app.json`'s `ios.supportsTablet`): was `true` — the unmodified Expo template default, never a deliberate decision — despite zero iPad-specific layout or testing anywhere in the app. Left as-is, a universal binary would need separate iPad screenshots at submission (which don't exist and can't easily be produced for a phone-only design) and could ship an untested/broken layout to any iPad user who installed it. Now `false`; verified via a real `expo prebuild` that `TARGETED_DEVICE_FAMILY` changed from `"1,2"` to `"1"`.
- **App identity**: real name ("Pathfinder Run", was "mobile") and a real icon (`mobile/assets/generate_icons.py` — was the unmodified Expo template default) — both fixed.
- **Delete-my-data**: a real, working "Delete All My Data" button (Past Runs screen) clearing both local and server-synced data — required by app store review and by the privacy policy above; was missing entirely, now built.
- **Android permissions**: the generated manifest was requesting `SYSTEM_ALERT_WINDOW` (a sensitive, Play-Store-scrutinized permission — "display over other apps"), plus legacy `READ_EXTERNAL_STORAGE`/`WRITE_EXTERNAL_STORAGE`/`VIBRATE`, none of which this app uses (confirmed via grep — no vibration/haptics/overlay code anywhere) — inherited from transitive Expo/React Native dependencies (`expo-file-system`, RN's own template), not requested by anything this app does. Blocked via `mobile/app.json`'s `android.blockedPermissions` (verified in a real `expo prebuild`: the generated manifest now carries `tools:node="remove"` on all four, which strips them from the built APK/AAB). The release manifest now only requests what's actually used: location + internet.
- **App Store Connect's App Privacy / Google Play Console's Data Safety form**: both filled out in-console, not code, but the actual answers are drafted and ready to copy-paste in [`docs/app-store-privacy-labels.md`](docs/app-store-privacy-labels.md) — precise location + fitness data + a device-scoped id, all collected/linked/App-Functionality-only/never shared or sold, no tracking (no ATT prompt needed).
- **`eas.json`** (`mobile/eas.json`): build profiles set up — `development` (internal distribution, dev client, since the app's native modules — `react-native-maps`/`expo-sqlite`/`expo-location` — go beyond what Expo Go supports), `preview` (internal distribution, for testing a release-shaped build before submission), and `production` (store-ready, `autoIncrement` so build numbers don't need manual bumping). Each references an EAS-side `environment` rather than committing `EXPO_PUBLIC_PATHFINDER_API_KEY` — that var only exists locally via the gitignored `mobile/.env`, and a cloud build has no access to that file. `submit.production` is deliberately left empty (no `ascAppId`/`appleId`/Android `serviceAccountKeyPath`) since there's no App Store Connect/Play Console account yet to get real values from — `eas submit` will prompt for them interactively once those exist, rather than shipping placeholder credentials now.
- **EAS project**: linked for real — https://expo.dev/accounts/rynjung/projects/pathfinder-run (`mobile/app.json`'s `extra.eas.projectId`) — and `EXPO_PUBLIC_PATHFINDER_API_KEY` is set as a `sensitive` EAS env var across all three build environments, so a cloud build can actually authenticate against the backend once it's deployed.
- **End-to-end native verification**: beyond config generation (`expo prebuild`), the app was actually compiled with Xcode and run on an iOS Simulator (`expo run:ios`) — it installed, launched, called the real local `route_api.py`/GraphHopper backend, and rendered three genuine generated route candidates on the map, confirming the whole pipeline (native build → backend → routing engine → UI) works, not just that each piece works in isolation. Also actually ran a real EAS cloud build (`preview-simulator` profile, `ios.simulator: true` — no Apple account needed) against EAS's own servers, not just this machine: https://expo.dev/accounts/rynjung/projects/pathfinder-run/builds/14ba9b5a-b1ad-4b6f-b8cc-1a86b05281b5, succeeded.
- **`API_BASE_URL` was a hardcoded string literal** (`mobile/App.js`, defaulting to `http://localhost:5001`) — found while getting `deploy/` ready for a real domain. Every build, including the EAS cloud build above, would have silently shipped pointing at `localhost` regardless of build profile, with no way to point a production build at a real backend short of hand-editing the source before each build. Now reads from `EXPO_PUBLIC_API_BASE_URL` (same inlining mechanism already used for the API key), defaulting to the same `localhost` value when unset — so local dev is unaffected, but a real domain can now be wired in per build profile via `eas env:set` once one exists, with no code change.
- **Apple vs. Google enrollment cost**: Apple Developer Program is $99/year; Google Play Console is a one-time $25 registration fee — different from what's implied above if only targeting Android.

## License

All rights reserved. No open-source license is granted — this is the
legal default with no `LICENSE` file present, stated explicitly here
rather than left as an unresolved placeholder.
