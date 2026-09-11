# Scenic loop running app — architecture

## 0. v1 scope — what to actually build first

The full architecture below is the end state. Build toward it in this order, not all at once:

**v1 — prove the core idea works, one city, self-hosted, no fees**
- Self-hosted GraphHopper (or Valhalla/OSRM) with a custom pedestrian profile, running against a single city's OSM extract
- Loop generation with the edge-reuse penalty (§5) — this is the actual product bet, so it's the only thing worth perfecting before anything else
- Mobile client: request a route, show it on a map, no live tracking yet
- **No background location.** `WhenInUse` only.
- **No closures layer yet.** Ship without it; a wrong route occasionally is a v1 tradeoff, not a blocker.
- Run this on your own machine while tuning the cost function, then a small/free-tier VPS once you want a beta with real users.

**v2 — once the loop quality is validated by actually running the output**
- Live GPS tracking during a run + deviation detection
- Crowdsourced closure reporting, matched to OSM edges, feeding the cost function
- Expand to additional cities

**v3 — once you have a real feature that needs it**
- Background/`Always` location, gated behind a specific opt-in feature (auto-pause on trail exit, live location share) — not turned on by default

**Realistic costs at this stage:** the software stack (OSM, GraphHopper, PostGIS) is free/open source — you're paying for compute, not licenses. Local machine while developing = $0. Early beta on a small or free-tier VPS = roughly $0–20/month. The one fixed cost that's unavoidable regardless of how cheap the backend stays is the Apple Developer Program, $99/year, required to publish to the App Store.

---

## 1. What makes this different from Strava/NRC/Komoot

Existing apps solve "record what I ran" or "let me manually draw a route." Nobody solves the actual input you want:

> distance → generated loop → prefers sidewalks/park paths → minimizes repeated road segments → stays current with closures

That's a **route generation problem with a live data feed**, not a navigation problem. The architecture below treats it that way.

---

## 2. Mobile client architecture

Structure the app in four layers so location handling, UI, and networking don't tangle together:

```
┌─────────────────────────────────────┐
│ UI layer (SwiftUI / Jetpack Compose  │
│ or React Native if cross-platform)   │
├─────────────────────────────────────┤
│ Run session controller               │
│ - state machine: idle → generating → │
│   ready → running → paused → done    │
├─────────────────────────────────────┤
│ Location manager (native, per-OS)    │
│ - foreground GPS stream              │
│ - background significant-location    │
│ - deviation/reroute detection        │
├─────────────────────────────────────┤
│ Local store (SQLite / Core Data /    │
│ Room) — cached routes, run history,  │
│ offline map tiles for the last route │
└─────────────────────────────────────┘
```

**Why native location managers, not a cross-platform location library as the primary source:** iOS and Android have fundamentally different background execution models, and getting this wrong is the #1 reason fitness apps drain battery or get killed by the OS mid-run. Use a cross-platform framework (React Native / Flutter) for UI if you want one codebase, but bridge to native location APIs directly:

- **iOS**: `CLLocationManager`. Use `startUpdatingLocation()` at high accuracy only while a run is actively recording. Use `startMonitoringSignificantLocationChanges()` (low-power, cell-tower-based) for anything that isn't an active run — this is what would let you say "notify me of a nearby route" without draining battery. Background execution requires the `Always` authorization plus the `location` background mode in `Info.plist`, but you should request `WhenInUse` first and only escalate to `Always` when the user does something that clearly needs it (e.g. turns on "auto-pause my run if I leave the trail" or "find routes as I walk around").
- **Android**: `FusedLocationProviderClient` for GPS + network fusion. Active run tracking needs a **foreground service** with a persistent notification (required since Android 8, enforced harder since Android 10+) — you cannot reliably get high-frequency location in the background otherwise. `ACCESS_BACKGROUND_LOCATION` is a separate permission from `ACCESS_FINE_LOCATION` on Android 10+ and Google Play reviews apps requesting it fairly strictly, so only request it if you actually ship a background feature (e.g. live location sharing with a friend).

**GPS sampling strategy during a run:**
- 1–3 second interval while actively running (not the fastest possible — that just burns battery for marginal accuracy gain)
- Switch to a coarser interval automatically when the accelerometer/motion API shows no movement (paused at a light, tying a shoe)
- Buffer points locally and batch-sync to the backend every 15–30s rather than streaming every point — this matters for both battery and your server bill

---

## 3. Location & privacy — doing it right

You said "access their current location at all times" — worth being precise about what that should actually mean, because "always-on" and "good privacy" aren't opposites if you scope it correctly.

### Permission tiers (ask for the minimum that satisfies the feature)

| Tier | When needed | iOS | Android |
|---|---|---|---|
| One-time / while using | Generating a route from current position | `WhenInUse` | `ACCESS_FINE_LOCATION` (foreground) |
| Active session | Tracking a run in progress, live rerouting | `WhenInUse` (foreground service keeps it alive) | Foreground service + `ACCESS_FINE_LOCATION` |
| Always / background | Auto-pause when leaving a trail, live share with a contact, proactive "there's a good route nearby" notification | `Always` | `ACCESS_BACKGROUND_LOCATION` |

Only the third tier is genuinely "always." This is a v3 feature (see §0) — don't build toward it until you have a specific feature that needs it, gated behind an explicit named toggle in settings, never requested at first launch. Apps that ask for `Always` location on install get rejected or flagged by both app stores, and users bounce off the permission prompt anyway.

### Data minimization

- **Process on-device where you can.** Route deviation detection ("did the user leave the planned path?") can run entirely on the phone by comparing the live GPS point to the cached route geometry — no need to ping the server every 2 seconds just to check that.
- **Don't store raw location history longer than the feature needs it.** A completed run's GPS trace is useful to keep (that's the product). A live "where are you right now" ping used only for rerouting doesn't need to be retained after the run ends.
- **Crowdsourced closure reports should be anonymized/aggregated before storage** — you want "this segment is closed," not "user X was at this exact coordinate at this exact time." Snap the report to the nearest OSM way/segment ID and drop the precise coordinate once you've done that matching.

### User-facing controls

Give people an actual settings screen, not just the OS permission dialog:
- Toggle background location independently of the app being usable at all (route generation and run tracking should work fully on `WhenInUse`)
- "Delete my run history" and "delete my account data" as real, working buttons — not a support ticket
- Make it visible when a run is being recorded (persistent notification / in-app indicator) — never track silently

### Compliance groundwork

- **iOS**: App Tracking Transparency only applies if you're tracking across other apps/websites for ads — likely not relevant unless you monetize that way. You will need a **Privacy Manifest** declaring what data types you collect and why, required for App Store submission.
- **Android**: Play Console's **Data Safety** form needs to accurately reflect location collection, or you risk rejection/removal.
- **GDPR/CCPA** if you have EU or California users: right to access and delete data, and a real basis for processing location (consent, tied to the feature, not bundled into a blanket ToS clause).

---

## 4. Backend services

| Service | Responsibility | Notes |
|---|---|---|
| **API gateway** | Auth, rate limiting, request routing | Standard — Kong, AWS API Gateway, or a thin custom layer |
| **Route generator** | Takes (lat, lon, distance, preferences) → returns a loop | The core IP of the app — see §5 |
| **Live tracking** | Ingests batched GPS points during a run, detects deviation, triggers reroute | Stateless-ish; short-lived session state in Redis |
| **Closures service** | Ingests crowdsourced + city-feed closure reports, maintains a "blocked edges" layer | Feeds directly into the route generator's cost function |
| **Data layer** | OSM road/path graph in PostGIS, routing engine (GraphHopper/Valhalla/OSRM), run history, closures table | The thing everything else reads from |

**Why PostGIS + a dedicated routing engine, not a driving-directions API:** OSM tags sidewalks (`highway=footway`, `sidewalk=*`), park paths (`leisure=park` boundaries + paths inside them), and surface type explicitly. A generic driving API doesn't expose that granularity for you to weight against. Running your own routing engine (GraphHopper is the easiest to customize) on top of your own PostGIS-hosted OSM extract gives you a controllable cost function.

---

## 5. The route generation algorithm

This is genuinely the hard part, and it's worth being clear-eyed about what "good" looks like:

1. **Build a weighted graph** from OSM data in your service area. Edge weight = a function of:
   - Path type (park path / sidewalk cheapest, unprotected road shoulder expensive, anything without pedestrian access excluded entirely)
   - "Greenness" — proximity to parks/green space, which you can derive from OSM land-use tags
   - A live penalty from the closures service (near-infinite cost on a currently-closed edge, so the router routes around it automatically)

2. **Generate candidate loops**, not a single deterministic route. Start from the user's location, and run a modified roundtrip algorithm: pick a target "far point" roughly at half the desired distance out, route out to it, then route back — but penalize any edge already used in the outbound leg so the return leg is forced to take different streets rather than mirror the first half. Repeat this a few times with slightly different waypoint headings and keep the candidate whose actual distance is closest to the target.

3. **Score candidates** and pick the best one:
   - Distance accuracy (within ~5% of requested)
   - Edge-reuse penalty (lower is better — this directly addresses "not circling the same km four times")
   - Path-type score (sidewalk/park path percentage)
   - Estimated elevation if you want to expose that later

4. **Return 2-3 alternatives**, not just one — people like a choice, and it costs you nothing extra since you already generated multiple candidates.

This is meaningfully more compute than turn-by-turn navigation (you're solving something closer to an orienteering/loop problem than shortest-path), but at 5-10k scale on a city-sized graph it's fast enough to run synchronously behind an API call — no need for a background job queue at that distance range.

---

## 6. Closures — realistic approach (v2, not v1)

There's no universal live "road closed" API, so layer it:

1. **Crowdsourced reports** (ship this first) — a simple in-app "report a closure" button during or after a run, matched to the nearest OSM edge, with a decay/expiry (auto-clear after N days unless re-confirmed) so stale reports don't permanently block a segment.
2. **City open-data feeds** where they exist — check what your target city/region publishes; coverage and format vary a lot city to city, so treat this as a bonus layer, not the foundation.
3. **OSM itself** gets updated for permanent changes but not temporary construction, so don't rely on it alone for anything time-sensitive.

---

## 7. Suggested build order

1. Backend: OSM extract → PostGIS → GraphHopper with a custom pedestrian profile. Get basic loop generation working via a test script, no app yet.
2. Add the edge-reuse penalty and candidate scoring.
3. Minimal mobile client: request route, display on map, no live tracking yet.
4. Add live GPS tracking + deviation detection during a run.
5. Add crowdsourced closure reporting and wire it into the cost function.
6. Add background location as an opt-in feature, only once the core flow is solid.

Start the algorithm work with a single city's worth of OSM data (your own city is easiest to sanity-check by actually running the routes it gives you) before worrying about scaling the data pipeline to multiple regions.

---

## 8. Repo & commit workflow

- Repo: [`pathfinder-run`](https://github.com/rynjung1/pathfinder-run)
- `.gitignore` must exclude secrets/API keys, build artifacts, and OSM data extracts (`*.osm.pbf`, GraphHopper cache) before the first commit — these files are either sensitive or too large to belong in git history.
- **Commit periodically, not on a fixed timer** — commit locally whenever a logical chunk of work is done (a working route-generation function, a working UI screen), not just at the end of a long session. Small, frequent commits make it far easier to track down when/why the loop algorithm's behavior changed.
- **Push to GitHub at least once per working session** (or daily, whichever is more frequent) so work is backed up off the local machine — don't let uncommitted work sit locally for days.
- Write commit messages that describe intent (`"Add edge-reuse penalty to loop scoring"`), not just what changed (`"fix stuff"`) — this matters most for the routing algorithm, where you'll want to remember *why* a given weighting tradeoff was made.
- Solo + early prototype stage: committing directly to `main` is fine. Once v1 is working end-to-end, branch per feature (`feature/closures-layer`, `feature/live-tracking`) so `main` stays in a runnable state.
