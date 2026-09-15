# App Store Connect — App Privacy answers

Copy-paste reference for App Store Connect's "App Privacy" section (the
"nutrition label" shown on the app's store page) — derived directly from
what this app's code actually collects, not a guess. Cross-check against
`mobile/App.js`/`scripts/route_api.py`/`scripts/runs.py` if the app's data
handling ever changes; this file goes stale the moment it does.

## Does this app collect data?

**Yes.**

## Data types collected

### Location → Precise Location
- **Collected:** Yes (`expo-location`'s `getCurrentPositionAsync`/
  `watchPositionAsync` — see `mobile/App.js`)
- **Linked to the user's identity:** Yes — synced to the server keyed by
  a device-scoped id (`getOrCreateDeviceId`, `db.js`)
- **Used for tracking:** No
- **Purposes:** App Functionality (generating a route from the user's
  position, live tracking during a run, deviation detection)

### Health & Fitness → Fitness
- Covers the run's distance/duration/pace/route — all derived from the
  location data above, not a separate collection point, but Apple's
  taxonomy treats fitness/exercise data as its own category regardless
  of source.
- **Collected:** Yes
- **Linked to the user's identity:** Yes (same device-scoped id)
- **Used for tracking:** No
- **Purposes:** App Functionality

### Identifiers → Device ID
- The random device-scoped id in `db.js` (`getOrCreateDeviceId`) — not
  IDFV/IDFA, an app-generated value used only for this app's own sync,
  but Apple's taxonomy is about the identifier's *role* (identifying a
  device across sessions), which this fits.
- **Collected:** Yes
- **Linked to the user's identity:** Yes
- **Used for tracking:** No
- **Purposes:** App Functionality

### Everything else in Apple's list: **Not collected**
Contact Info, Health (medical records), Financial Info, Sensitive Info,
Contacts, User Content, Browsing History, Search History, Purchases,
Usage Data, Diagnostics, Other Data — none of these are collected. In
particular:
- **User Content / Photos**: the run-map snapshot (`captureRunSnapshot`,
  App.js) is generated and stored **entirely on-device**
  (`expo-file-system`) — never uploaded anywhere. Apple's privacy labels
  are about data the app or its partners *collect* (i.e., receive off
  the device); a file that never leaves the phone isn't "collected"
  under that definition, so this is correctly left off the label, not
  an oversight.
- **Diagnostics/Usage Data**: no crash reporting or analytics SDK exists
  in this app at all (see README's own stack description) — nothing to
  disclose here because nothing is being sent.

## "Data Used to Track You" — separate question, answer: No

This is what gates the App Tracking Transparency (ATT) prompt. This app
does not track users across other companies' apps or websites for
advertising or share data with data brokers — answer **No** across the
board. No ATT prompt is needed as a result (matches
`docs/running-app-architecture.md` §3: "App Tracking Transparency only
applies if you're tracking across other apps/websites for ads").

---

## Google Play Console — Data Safety form (same underlying facts)

Play's form asks the same substance in a different shape. Same three
real data types (Location, Fitness data, Device/other IDs):

- **Data collected:** Yes
- **Location (Approximate/Precise location)**: collected, shared: No,
  linked to a user identifier: Yes, purpose: App functionality
- **App activity → Other actions / Fitness info**: collected (run
  distance/duration/pace/route), shared: No, linked: Yes, purpose: App
  functionality
- **Device or other IDs**: collected (the device-scoped sync id),
  shared: No, linked: Yes, purpose: App functionality
- **Is data encrypted in transit?** Yes (HTTPS, once the production
  backend is behind Caddy — see `deploy/Caddyfile`; the local dev server
  is plain HTTP, irrelevant to what ships)
- **Can users request data deletion?** Yes — "Delete All My Data" in the
  app is real and working (`deleteAllData`, App.js); also true that
  uninstalling the app deletes the local copy, and the device-scoped
  sync id means there's no separate "account" to delete beyond that.
- **Data collected for ads or sold to third parties?** No, to both.

---

## What still has to happen in-console, not here

This file gives you the *answers*; App Store Connect's and Play
Console's own privacy questionnaires still have to be filled out by
hand by whoever has access to those accounts (they're tied to the
Apple Developer Program / Play Console enrollment, not something
committed to this repo) — this doc exists so that's a five-minute
copy-paste job instead of a research task when you get there.
