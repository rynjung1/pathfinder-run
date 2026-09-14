import { useState, useEffect, useRef } from 'react';
import { StyleSheet, Text, View, Button, ActivityIndicator, Alert, Platform, FlatList, TouchableOpacity, Image } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import * as Location from 'expo-location';
import { Accelerometer } from 'expo-sensors';
import { Directory, File, Paths } from 'expo-file-system';
import MapView, { Polyline, Marker } from 'react-native-maps';
import { saveRun, getRuns, getOrCreateDeviceId, deleteAllRuns } from './db';
import {
  distanceToRouteMeters,
  formatDistance,
  formatDuration,
  formatPace,
  mergeRunHistory,
  traceDistanceMeters,
} from './geometry';

// Pathfinder Run -- v2 mobile client. §2/§3/§7 step 4: live GPS tracking
// during an active run, shown moving on the map against the generated
// route, plus on-device deviation detection (comparing the live point to
// the cached route geometry, §3's data-minimization guidance -- no server
// round trip needed for this). Run-session state machine per §2:
//   idle -> generating -> ready -> running -> paused -> done -> (idle)
//
// Also here: run history (§2's "cached routes, run history" local store)
// -- the full GPS trace is captured during an active run (not just the
// single latest liveCoord the deviation check needs), and a run record is
// saved to SQLite (db.js -- see that file for the storage investigation)
// when a session reaches 'done'. A past-runs list screen shows
// date/distance/duration for each saved run, and tapping one replays its
// actual recorded trace on a map (viewRunReplay, below) -- not the
// originally-generated route, the real path that was recorded.
//
// Local storage is always authoritative; on top of it, a completed run is
// also synced to the server on a best-effort basis (syncRunToServer,
// device-scoped via db.js's getOrCreateDeviceId -- no user accounts exist
// in this app). See runs.py's module docstring for exactly what this does
// and doesn't protect against: real durability against local data loss
// short of an app reinstall, NOT a guarantee your history survives
// reinstalling the app (the device id lives in the same local database as
// the history it's meant to back up).
//
// Also here: "Delete All My Data" (deleteAllData, on the Past Runs
// screen) -- a real, working deletion, not a support-ticket process
// (§3's user-facing-controls requirement). Clears local storage and, if
// synced, the server-side copy for this device too -- there's no
// separate "delete my account" since device-scoped sync IS the account
// data in this app's no-login model.
//
// Deliberately NOT here yet:
// - Rerouting once a deviation is detected -- detection + a UI indicator
//   only for now, no recalculation logic.
// - A retry queue for failed syncs -- syncRunToServer is attempted once,
//   right after a run ends; if it fails (no network, server down), the
//   run stays local-only until manually reconciled some other way. No
//   background retry, no "pending sync" state tracked.
// - Real user accounts / login, which would let sync survive a reinstall
//   or work across devices -- see runs.py's module docstring for why
//   device-scoped sync was the deliberately narrower thing built instead.
// - Android foreground service + persistent notification. Confirmed
//   feasible without ACCESS_BACKGROUND_LOCATION (verified directly from
//   expo-location's config-plugin source: isAndroidForegroundServiceEnabled
//   and isAndroidBackgroundLocationEnabled are independent flags) -- but
//   this is now a final decision NOT to build it, not a "revisit later"
//   item on a priority queue. See the comment on WATCH_OPTIONS/startWatching
//   below for what that means in practice.
// - iOS lock-screen survival is a DIFFERENT, harder case: locking the
//   screen backgrounds the app (applicationDidEnterBackground fires) same
//   as switching apps, and continuing location updates through that
//   requires `Always` authorization + `allowsBackgroundLocationUpdates` +
//   the `location` UIBackgroundMode -- there is no WhenInUse path to this
//   on iOS, in Expo managed workflow, bare React Native, or fully native
//   Swift; it's a hard OS-level requirement CLLocationManager enforces
//   (setting allowsBackgroundLocationUpdates=true without Always
//   authorization is documented to throw). Expo's own native module sets
//   that flag unconditionally with no prior authorization check, so this
//   isn't an Expo gap to work around -- it's Apple's platform rule.
//   Matches the doc's own §0 phasing: Always/background is v3, explicitly
//   opt-in, not default.
//
// Offline map tiles for the last route (§2's local-store box lists this
// explicitly) -- named above as a gap on the first sweep, now addressed
// via a substitution, not literally: see captureRunSnapshot's comment
// (near handleEnd) for the full reasoning on why a real tile cache would
// mean a whole different maps SDK, and what's built instead.
//
// Adaptive GPS sampling (§2's "switch to a coarser interval automatically
// when the accelerometer/motion API shows no movement") -- named above as a
// gap on the first sweep, now built: see WATCH_OPTIONS_FINE/_COARSE,
// startMotionMonitoring, and switchGpsMode below. Battery-only in effect
// (accuracy/functionality are unaffected either way) -- switches to a
// coarser watchPositionAsync profile after 4s of accelerometer-detected
// stillness, and back to fine on the very first sign of real motion again.
//
// Addressing: an iOS Simulator shares the host Mac's network stack, so
// localhost reaches a server running on the same machine directly. That is
// NOT true for a physical device (or an Android emulator, which needs
// 10.0.2.2 instead) -- those need the dev machine's LAN IP. route_api.py
// binds to 0.0.0.0 for exactly this reason.
//
// Read from EXPO_PUBLIC_API_BASE_URL, not hardcoded -- found while getting
// deploy/ ready for a real domain: this was a plain string literal until
// then, which meant the only way to point a build at anything other than
// localhost was hand-editing this line and remembering to edit it back, and
// every EAS build (including the one already run this session) would have
// silently shipped pointing at localhost regardless of profile. Same
// EXPO_PUBLIC_ inlining mechanism as API_KEY below: set
// EXPO_PUBLIC_API_BASE_URL in mobile/.env for local overrides, or per EAS
// build profile via `eas env:set --environment <profile>` once the real
// backend has a domain (deploy/README.md) -- unset (the common case today),
// this still defaults to the same localhost value it always used.
const API_BASE_URL = process.env.EXPO_PUBLIC_API_BASE_URL || 'http://localhost:5001';

// Was a single hardcoded constant with no UI to change it -- found on a
// UI/UX sweep, and a genuinely significant gap: the app's own pitch (this
// file's header, the README, docs/running-app-architecture.md §1) is
// "give it a distance, and it builds a route," but there was no actual
// way to give it one -- every route was silently exactly 5km, forever.
// route_api.py already fully supports any distance up to MAX_DISTANCE_M
// (30km, see that file) -- this was purely a missing client-side control,
// not a backend limitation. DEFAULT_TARGET_DISTANCE_M seeds targetDistanceM
// (state, below) so the existing "auto-generate a route on launch"
// convenience is unchanged; DISTANCE_PRESETS_M drives the picker shown
// once a route exists (see the 'ready' state's render) -- plain preset
// buttons, not a slider/numeric input, to match this app's existing
// all-stock-Button UI and avoid a new native dependency for this.
const DEFAULT_TARGET_DISTANCE_M = 5000;
const DISTANCE_PRESETS_M = [3000, 5000, 8000, 10000];

// route_api.py's pre-deployment hardening pass added a required X-API-Key
// header (see that commit's route_api.py docstring) but this client was
// never updated to send one -- a real gap, found while testing this file
// against the live backend, not a deliberate v1 scope cut.
//
// Read from EXPO_PUBLIC_PATHFINDER_API_KEY, not hardcoded: Expo inlines any
// env var prefixed EXPO_PUBLIC_ from a local .env file at bundle time (no
// extra plugin needed, built into Expo SDK 49+) -- put
// EXPO_PUBLIC_PATHFINDER_API_KEY=<value from scripts/.env's
// PATHFINDER_API_KEY> in mobile/.env (gitignored, see mobile/.env.example)
// to run against a real backend. This file is git-tracked, so the actual
// key must never be written here directly, even temporarily -- the fallback
// below is a placeholder for exactly that reason, not a working default.
// Same "not how a shipped app would hold a secret" caveat applies to this
// whole single-shared-key, no-per-user-auth debug-build model as to
// route_api.py's own.
const API_KEY = process.env.EXPO_PUBLIC_PATHFINDER_API_KEY || 'REPLACE_WITH_YOUR_LOCAL_PATHFINDER_API_KEY';

// Known limitation, not an oversight: this is plain watchPositionAsync, no
// foreground service. Tracking stops silently the moment the screen locks
// or the app is backgrounded -- on Android as much as iOS, even though a
// foreground service *would* let Android keep tracking through a locked
// screen under ordinary ACCESS_FINE_LOCATION (no ACCESS_BACKGROUND_LOCATION
// needed -- see the file header). Confirmed feasible, but this is a final
// decision not to build it -- not deferred, not a lower-priority item still
// on the list. If that decision ever gets revisited, it's
// isAndroidForegroundServiceEnabled in the expo-location config plugin
// (app.json), not a rewrite of the tracking logic here.
// Two profiles, not one -- §2's adaptive-sampling requirement (see
// startMotionMonitoring below for the switching logic). FINE is the
// original, unchanged fixed values; COARSE only kicks in once the
// accelerometer has shown sustained stillness, and switches back to FINE
// on the very first sign of real motion again -- so a real run's actual
// distance is never at risk of being under-sampled, only genuinely
// stationary stretches (stopped at a light, tying a shoe) sample less.
const WATCH_OPTIONS_FINE = {
  accuracy: Location.Accuracy.BestForNavigation,
  timeInterval: 2000,   // ms between updates (Android only, per expo-location docs)
  distanceInterval: 5,  // meters -- don't bother updating for sub-5m jitter
};
const WATCH_OPTIONS_COARSE = {
  accuracy: Location.Accuracy.BestForNavigation,
  timeInterval: 8000,
  distanceInterval: 15,
};

// Accelerometer-based stillness detection, for the coarse/fine switch
// above. Sampled well below running's natural cadence (a footstrike cycle
// is roughly 1.5-3Hz) so genuine running always shows real variation in
// this window; watching for the ABSENCE of that variation is what flags
// "stopped," not any single still-looking reading (one sample can land
// mid-stride at near-1g by chance).
const MOTION_SAMPLE_INTERVAL_MS = 500;
const MOTION_WINDOW_SIZE = 8; // 8 * 500ms = 4s of history before a switch to COARSE is even considered
// Deviation from 1g (at-rest gravity reads ~1.0 on the magnitude below).
// Stationary-in-hand/armband jitter measured well under this; a genuine
// footstrike, even walking gently, measured well over it -- ad hoc, not
// lab-tuned, same "revisit once real outdoor tests exist" status as
// DEVIATION_THRESHOLD_M below.
const STATIONARY_DEVIATION_THRESHOLD = 0.08;

// How far off the generated route (meters) before flagging a deviation.
// Ad hoc starting point, not tuned: needs to comfortably clear normal GPS
// jitter (~5-15m on a good fix) without being so loose that a real
// wrong-turn goes unnoticed for too long. Revisit once real outdoor running
// tests exist -- same "ad hoc, revisit later" spirit as the loop-scoring
// weights in scripts/generate_loop.py.
const DEVIATION_THRESHOLD_M = 40;

// The pure geometry/formatting functions previously defined here
// (projectToLocalMeters, pointToSegmentDistance, distanceToRouteMeters,
// haversineMeters, traceDistanceMeters, formatDuration, formatDistance)
// now live in ./geometry -- moved, not changed, specifically so they're
// testable without pulling in react-native-maps/expo-sqlite (both native
// modules App.js itself imports, which can't load under plain Jest). See
// geometry.js's own header and __tests__/geometry.test.js -- the
// mobile-side counterpart to scripts/tests/test_geometry.py.

export default function App() {
  const mapRef = useRef(null);
  // A separate ref/onMapReady flag from the main session map above -- this
  // is a different MapView instance (mounted only on the run-detail screen,
  // §7's "route replay-on-map for a past run"), and fitToCoordinates has the
  // same "native view must finish its first layout first" requirement as
  // the main map's own effect below, so it needs its own readiness signal.
  const replayMapRef = useRef(null);
  const [replayMapReady, setReplayMapReady] = useState(false);
  const watchSubscriptionRef = useRef(null);
  // Adaptive GPS sampling state -- see WATCH_OPTIONS_FINE/_COARSE above.
  // All plain refs, not state: none of this drives a render directly
  // (gpsModeRef only changes which options the NEXT watchPositionAsync
  // call uses; motionSamplesRef is scratch space for the stillness check),
  // same reasoning as traceRef below.
  const accelSubscriptionRef = useRef(null);
  const motionSamplesRef = useRef([]);
  const gpsModeRef = useRef('fine');

  // The full GPS trace for the run in progress -- a plain ref, not React
  // state: every watchPositionAsync callback appends to it, and doing that
  // as state would re-render the whole component on every single GPS
  // ping just to accumulate history nothing on screen needs per-point.
  // liveCoord (state, below) stays the only per-point value that drives
  // UI (the live marker + deviation check).
  const traceRef = useRef([]);
  // Wall-clock run timing, tracked across pause/resume: `startedAt` is set
  // once, on the first Start; `activeMs` accumulates only the time spent
  // actually running (pauses don't count towards duration); `segmentStartedAt`
  // is when the CURRENT running segment began, added into activeMs on the
  // next pause/end.
  const runTimingRef = useRef({ startedAt: null, activeMs: 0, segmentStartedAt: null });
  // This run's id, assigned fresh in handleStart -- before it's saved
  // locally or synced to the server, so both agree on the same value.
  // Used as the server sync idempotency key (see syncRunToServer);
  // Math.random(), not crypto.randomUUID(), same "not security-sensitive,
  // not worth a Hermes Web Crypto availability check" reasoning as
  // db.js's device id.
  const runUuidRef = useRef(null);

  // idle | generating | ready | running | paused | done
  const [sessionState, setSessionState] = useState('idle');
  const [initialRegion, setInitialRegion] = useState(null);
  // All candidates from the server's response (§5 point 4's "2-3
  // alternatives"), not just the top-ranked one -- see candidates.map in the
  // render below, and selectedCandidateIndex for which one is picked.
  // Each entry: {rank, distanceM, coords}. rank 1 (index 0) is the server's
  // best pick (candidates_to_geojson sorts best-first) and stays selected by
  // default, so a user who never taps an alternate sees the same route this
  // screen always showed.
  const [candidates, setCandidates] = useState(null);
  // See DEFAULT_TARGET_DISTANCE_M/DISTANCE_PRESETS_M above -- this is the
  // one place "how far" actually lives now, instead of a fixed constant.
  const [targetDistanceM, setTargetDistanceM] = useState(DEFAULT_TARGET_DISTANCE_M);
  // The just-finished run, for the post-run summary (sessionState ===
  // 'done', below) -- set once, in handleEnd. Deliberately not read from
  // liveDistanceM/liveDurationMs for this: those are only meaningful
  // while isTracking is true (liveDurationMs is hardcoded to 0 once
  // sessionState leaves 'running'/'paused'), so they'd show a summary of
  // zero, not this run's real numbers.
  const [lastRun, setLastRun] = useState(null);
  const [selectedCandidateIndex, setSelectedCandidateIndex] = useState(0);
  const [startCoord, setStartCoord] = useState(null);
  const [liveCoord, setLiveCoord] = useState(null);
  const [deviationDistance, setDeviationDistance] = useState(null);
  // §3's "make it visible when a run is being recorded ... never track
  // silently" -- was entirely unmet until now: the only signal an active
  // run existed was which buttons happened to be showing (Pause/End Run),
  // with no live distance or duration displayed anywhere during the run
  // itself (formatDistance/formatDuration were only ever used on the
  // Past Runs/replay screens, after the fact). liveDistanceM mirrors
  // traceRef via state (recomputed in the same watchPositionAsync callback
  // that already re-renders for liveCoord/deviationDistance, so this adds
  // no extra re-renders); durationTick exists purely to force a re-render
  // once a second while running so the displayed duration counts up --
  // the actual duration value itself still comes from runTimingRef
  // (below), not from this counter.
  const [liveDistanceM, setLiveDistanceM] = useState(0);
  const [durationTick, setDurationTick] = useState(0);
  const [mapReady, setMapReady] = useState(false);
  const [reportingClosure, setReportingClosure] = useState(false);
  // Separate from sessionState -- the past-runs list is a standalone
  // screen you can visit and leave from, not a step in the run-session
  // state machine.
  const [showHistory, setShowHistory] = useState(false);
  const [pastRuns, setPastRuns] = useState([]);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [deletingData, setDeletingData] = useState(false);
  // Which past run's replay is showing, or null for the plain list --
  // §2's "route replay-on-map for a past run." Holds the whole run record
  // (including its trace) from pastRuns, not just an id, since the list is
  // already in memory and there's nothing to re-fetch.
  const [selectedRun, setSelectedRun] = useState(null);

  // Derived, not stored -- the coords of whichever candidate is currently
  // selected. Used for the deviation check, the single-route render once a
  // run is underway, and (via handleStart) as "the route" for this session
  // from that point on.
  const selectedCandidate = candidates ? candidates[selectedCandidateIndex] : null;
  const selectedCoords = selectedCandidate ? selectedCandidate.coords : null;

  // Auto-fetch once on launch, purely so this screen has something to show
  // without requiring a tap first (useful for a quick screenshot/demo). The
  // button below still lets you regenerate on demand.
  useEffect(() => {
    generateRoute();
  }, []);

  // Stop the location watch on unmount no matter what state we're in --
  // otherwise a hot-reload or navigating away mid-run leaks a live GPS
  // subscription.
  useEffect(() => {
    return () => stopWatching();
  }, []);

  // Ticks once a second, only while actually running -- purely to force a
  // re-render so the live duration display (below, computed from
  // runTimingRef) counts up instead of sitting frozen between GPS pings
  // (watchPositionAsync's callback frequency depends on WATCH_OPTIONS'
  // distance/time filters, not a steady 1s cadence, so relying on it alone
  // would make the clock visibly stutter). Intentionally not running while
  // paused -- the displayed duration is meant to freeze then, not tick.
  useEffect(() => {
    if (sessionState !== 'running') return;
    const id = setInterval(() => setDurationTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, [sessionState]);

  // Fit the map to every candidate's extent once they're loaded, rather than
  // a fixed box around the start point -- a 5km+ loop routinely runs off the
  // edge of a fixed-delta region since its actual extent depends on the
  // bearing/shape GraphHopper picked, not just distance from the start. All
  // candidates, not just the selected one, so an alternate the user hasn't
  // tapped yet is still fully visible/tappable rather than cut off at the
  // frame edge.
  //
  // Gated on mapReady (react-native-maps' onMapReady), not just candidates:
  // calling fitToCoordinates before the native map view has completed its
  // first layout is a known no-op on iOS -- the ref exists (React has
  // mounted and attached it) but the native side isn't ready to compute a
  // fit yet. Confirmed by testing: gating on the route data alone silently
  // did nothing, framing stayed at the fixed initialRegion delta. The extra
  // setTimeout is a pragmatic belt-and-suspenders on top of onMapReady --
  // onMapReady alone has been reported flaky on first launch in some
  // react-native-maps versions.
  //
  // Deliberately depends on [mapReady, candidates], not selectedCandidateIndex
  // -- switching which alternate is selected shouldn't re-fit/re-zoom the
  // map, since all candidates already fit in the frame from this one fit.
  useEffect(() => {
    if (mapReady && mapRef.current && candidates && candidates.length > 0) {
      const allCoords = candidates.flatMap((c) => c.coords);
      const timer = setTimeout(() => {
        mapRef.current?.fitToCoordinates(allCoords, {
          edgePadding: { top: 60, right: 60, bottom: 60, left: 60 },
          animated: true,
        });
      }, 300);
      return () => clearTimeout(timer);
    }
  }, [mapReady, candidates]);

  // Same fitToCoordinates approach as above, applied to the run-detail
  // screen's map instead of the main session one: fit to the recorded
  // trace's actual extent, not a fixed-delta box, and gated on this map's
  // own onMapReady for the same reason (a fit called before the native view
  // has finished its first layout is a no-op on iOS). Depends on
  // selectedRun (not just replayMapReady) so picking a different run from
  // the list re-fits to that run's own trace, even though the MapView
  // component instance itself doesn't remount between selections.
  useEffect(() => {
    if (replayMapReady && replayMapRef.current && selectedRun && selectedRun.trace.length > 0) {
      const timer = setTimeout(() => {
        replayMapRef.current?.fitToCoordinates(selectedRun.trace, {
          edgePadding: { top: 60, right: 60, bottom: 60, left: 60 },
          animated: true,
        });
      }, 300);
      return () => clearTimeout(timer);
    }
  }, [replayMapReady, selectedRun]);

  // distanceOverride: passed directly by selectDistance below, rather than
  // relying on targetDistanceM state -- setTargetDistanceM's update isn't
  // visible in this same call's closure yet (React state updates aren't
  // synchronous), so reading targetDistanceM right after setting it here
  // would still see the OLD value for this request.
  async function generateRoute(distanceOverride) {
    const distance = distanceOverride ?? targetDistanceM;
    setTargetDistanceM(distance);
    setSessionState('generating');
    setCandidates(null);
    setSelectedCandidateIndex(0);
    setLiveCoord(null);
    setDeviationDistance(null);
    try {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') {
        Alert.alert('Location permission required', 'Pathfinder Run needs your location to generate a route from where you are.');
        setSessionState('idle');
        return;
      }

      const position = await Location.getCurrentPositionAsync({});
      const { latitude, longitude } = position.coords;

      const response = await fetch(`${API_BASE_URL}/route`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
        body: JSON.stringify({ lat: latitude, lon: longitude, distance }),
      });
      const body = await response.json();
      if (!response.ok) {
        throw new Error(body.error || `route_api returned ${response.status}`);
      }

      // Server already sorts candidates best-first (§5 point 4, "return 2-3
      // alternatives") -- keep all of them, not just rank 1, so the user can
      // see and pick between them (see the render below). Rank 1 (index 0)
      // stays selected by default.
      const features = body.features || [];
      if (features.length === 0) {
        throw new Error('No route found');
      }
      const newCandidates = features.map((f) => ({
        rank: f.properties.rank,
        distanceM: f.properties.actual_distance_m,
        coords: f.geometry.coordinates.map(([lon, lat]) => ({
          latitude: lat,
          longitude: lon,
        })),
      }));

      setStartCoord({ latitude, longitude });
      setInitialRegion({
        latitude,
        longitude,
        latitudeDelta: 0.03,
        longitudeDelta: 0.03,
      });
      // Set after initialRegion so the fitToCoordinates effect above only
      // fires once the MapView (mounted by initialRegion) actually exists.
      setCandidates(newCandidates);
      setSelectedCandidateIndex(0);
      setSessionState('ready');
    } catch (err) {
      Alert.alert('Could not generate route', String(err.message || err));
      setSessionState('idle');
    }
  }

  function selectDistance(meters) {
    generateRoute(meters);
  }

  // The actual per-position handling, pulled out of the watchPositionAsync
  // call itself (unlike before) so switchGpsMode below can re-subscribe
  // with different options while reusing the exact same handling logic --
  // there's nothing FINE-vs-COARSE-specific about what a position update
  // does once it arrives, only about how often one arrives.
  function handleLocationUpdate(position) {
    const coord = {
      latitude: position.coords.latitude,
      longitude: position.coords.longitude,
    };
    // Full trace, for run history -- every point, not just the latest.
    traceRef.current.push({ ...coord, timestamp: position.timestamp });
    setLiveCoord(coord);
    setDeviationDistance(distanceToRouteMeters(coord, selectedCoords));
    // Recomputed from the same trace saveRun/handleEnd will eventually
    // use (traceDistanceMeters), not tracked separately -- so the live
    // number and the one that ends up in run history can't drift apart.
    setLiveDistanceM(traceDistanceMeters(traceRef.current));
  }

  async function beginLocationWatch(options) {
    watchSubscriptionRef.current = await Location.watchPositionAsync(options, handleLocationUpdate);
  }

  function stopLocationWatch() {
    watchSubscriptionRef.current?.remove();
    watchSubscriptionRef.current = null;
  }

  // Swaps the active watchPositionAsync subscription for one using the
  // other profile (WATCH_OPTIONS_FINE/_COARSE) -- expo-location has no
  // "update an active subscription's options" call, so this is a genuine
  // remove-then-resubscribe, not a config tweak. No-ops if already in the
  // requested mode, so the frequent "still moving, still fine" case from
  // startMotionMonitoring below doesn't churn a subscription on every
  // single accelerometer sample.
  async function switchGpsMode(mode) {
    if (gpsModeRef.current === mode) return;
    gpsModeRef.current = mode;
    stopLocationWatch();
    await beginLocationWatch(mode === 'coarse' ? WATCH_OPTIONS_COARSE : WATCH_OPTIONS_FINE);
  }

  // §2's adaptive sampling: watches the accelerometer for sustained
  // stillness and switches the GPS profile accordingly (see
  // WATCH_OPTIONS_FINE/_COARSE and the constants above for the exact
  // thresholds/reasoning). Runs alongside the location watch for the
  // whole time a run is active, not just at start -- stillness can begin
  // or end at any point mid-run.
  function startMotionMonitoring() {
    motionSamplesRef.current = [];
    Accelerometer.setUpdateInterval(MOTION_SAMPLE_INTERVAL_MS);
    accelSubscriptionRef.current = Accelerometer.addListener(({ x, y, z }) => {
      const deviation = Math.abs(Math.sqrt(x * x + y * y + z * z) - 1);
      const samples = motionSamplesRef.current;

      if (deviation > STATIONARY_DEVIATION_THRESHOLD) {
        // Any real motion at all -- back to FINE immediately, no
        // hysteresis in this direction. Clear the window too: a fresh
        // stillness window has to build up again from here, not carry
        // over old (now stale) quiet samples from before this motion.
        samples.length = 0;
        switchGpsMode('fine');
        return;
      }

      samples.push(deviation);
      if (samples.length > MOTION_WINDOW_SIZE) samples.shift();
      if (samples.length === MOTION_WINDOW_SIZE) {
        switchGpsMode('coarse');
      }
    });
  }

  function stopMotionMonitoring() {
    accelSubscriptionRef.current?.remove();
    accelSubscriptionRef.current = null;
    motionSamplesRef.current = [];
  }

  // Continuous sampling starts here, and ONLY here -- when a run actually
  // starts, not on app load. The one-shot getCurrentPositionAsync above (to
  // know where to generate a route from) is unrelated to this.
  async function startWatching() {
    const { status } = await Location.getForegroundPermissionsAsync();
    if (status !== 'granted') {
      Alert.alert('Location permission required', 'Pathfinder Run needs your location to track this run.');
      return;
    }
    gpsModeRef.current = 'fine';
    await beginLocationWatch(WATCH_OPTIONS_FINE);
    startMotionMonitoring();
  }

  function stopWatching() {
    stopLocationWatch();
    stopMotionMonitoring();
  }

  async function handleStart() {
    // Fresh trace/timing for this run -- handleStart only ever fires from
    // 'ready' (see the state machine), so this is genuinely a new run
    // starting, not a resume (that's handleResume, below).
    traceRef.current = [];
    setLiveDistanceM(0);
    runTimingRef.current = { startedAt: new Date().toISOString(), activeMs: 0, segmentStartedAt: Date.now() };
    runUuidRef.current = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
    setSessionState('running');
    await startWatching();
  }

  function handlePause() {
    stopWatching();
    const timing = runTimingRef.current;
    if (timing.segmentStartedAt) {
      timing.activeMs += Date.now() - timing.segmentStartedAt;
      timing.segmentStartedAt = null;
    }
    setSessionState('paused');
  }

  async function handleResume() {
    runTimingRef.current.segmentStartedAt = Date.now();
    setSessionState('running');
    await startWatching();
  }

  // §2's "offline map tiles for the last route" -- addressed via a
  // substitution, not literally, and worth being explicit about why: real
  // offline *tile* caching would mean swapping react-native-maps for a
  // different maps SDK entirely (e.g. Mapbox/MapLibre with genuine offline
  // region packs) -- neither Apple Maps nor Google Maps (what
  // react-native-maps actually renders) exposes a supported way to cache
  // their own tiles for offline reuse, and their terms don't really allow
  // hand-rolling that either. That's a real product/library decision
  // (a new SDK, possibly a new API key, a licensing/cost model, a native
  // rebuild) -- too large to make unilaterally as a "sweep and fix."
  //
  // What this actually does instead: captures a static image of the map
  // WHILE it's still on-screen and online, using MapView.takeSnapshot --
  // a real, supported react-native-maps API, not a workaround -- and
  // saves it permanently. Viewing that past run later needs no network
  // and no live tiles at all, which is the actual thing "offline map
  // tiles for the last route" was for. Trade-off: one fixed-size raster
  // per run (not pan/zoomable, and it's the last few seconds' MapView
  // state -- the planned route + last known position, not literally the
  // full recorded trace, since that's what's actually on screen at this
  // exact moment), not a real tile cache -- but it solves the underlying
  // need with infrastructure the app already has.
  async function captureRunSnapshot(runUuid) {
    if (!mapRef.current) return null;
    try {
      const tempUri = await mapRef.current.takeSnapshot({
        width: 800,
        height: 600,
        format: 'png',
        quality: 0.8,
        result: 'file',
      });
      const dir = new Directory(Paths.document, 'run-snapshots');
      dir.create({ idempotent: true });
      const dest = new File(dir, `${runUuid}.png`);
      await new File(tempUri).copy(dest);
      return dest.uri;
    } catch (err) {
      // Best-effort, same spirit as syncRunToServer's own failure handling
      // right below -- the run's actual data (trace, distance, duration)
      // is what matters and is already safely captured regardless; a
      // failed snapshot just means this one run's replay falls back to
      // the live MapView (see the replay screen render, below), not a
      // lost run.
      console.warn('Could not capture run map snapshot (replay will use the live map instead):', err.message || err);
      return null;
    }
  }

  async function handleEnd() {
    stopWatching();
    const timing = runTimingRef.current;
    if (timing.segmentStartedAt) {
      timing.activeMs += Date.now() - timing.segmentStartedAt;
      timing.segmentStartedAt = null;
    }
    const trace = traceRef.current;
    // Captured before setSessionState('done') below -- the main map is
    // still showing this run's route + last live position right up until
    // that state change swaps the controls row to "New Route".
    const mapSnapshotUri = await captureRunSnapshot(runUuidRef.current);
    const run = {
      startedAt: timing.startedAt,
      targetDistanceM,
      actualDistanceM: traceDistanceMeters(trace),
      durationMs: timing.activeMs,
      trace,
      runUuid: runUuidRef.current,
      mapSnapshotUri,
    };
    try {
      await saveRun(run);
    } catch (err) {
      // Local storage is still the one place a run's data can be lost for
      // real -- if this fails there's nothing left to fall back on.
      // Surfaced plainly rather than silently swallowed, but doesn't block
      // finishing the session (there's nothing left to retry against here).
      Alert.alert('Could not save run', String(err.message || err));
    }
    // Best-effort server sync, AFTER the local save already succeeded (or
    // failed) -- local storage is authoritative; this is a durability
    // layer on top, not a replacement. Deliberately not surfaced to the
    // user on failure (no network, server down, etc.): the run is already
    // safely saved locally, and interrupting the post-run flow with a
    // sync-specific error the user can't act on would be worse than
    // silently skipping it. See db.js/runs.py for what this sync does and
    // doesn't protect against (notably: not an app reinstall).
    syncRunToServer(run).catch((err) => {
      console.warn('Run sync failed (saved locally, will not retry):', err.message || err);
    });
    setLastRun(run);
    setSessionState('done');
  }

  async function syncRunToServer(run) {
    const deviceId = await getOrCreateDeviceId();
    const response = await fetch(`${API_BASE_URL}/runs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
      body: JSON.stringify({
        deviceId,
        runUuid: run.runUuid,
        startedAt: run.startedAt,
        targetDistanceM: run.targetDistanceM,
        actualDistanceM: run.actualDistanceM,
        durationMs: run.durationMs,
        trace: run.trace,
      }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.error || `route_api returned ${response.status}`);
    }
  }

  // §6/§7 step 5: report a closure using whatever position we already have --
  // live tracking's liveCoord during/just after a run, or a fresh one-shot
  // fix as a fallback (reusing the same plumbing generateRoute uses, not new
  // location-access code). Server does the actual snap-to-way-id and
  // storage (see route_api.py POST /closures, scripts/closures.py) -- this
  // is deliberately just "capture position, send it," per the reporting-
  // only scope of this slice (no cost-function wiring yet).
  async function handleReportClosure() {
    setReportingClosure(true);
    try {
      let coord = liveCoord;
      if (!coord) {
        const position = await Location.getCurrentPositionAsync({});
        coord = { latitude: position.coords.latitude, longitude: position.coords.longitude };
      }
      const response = await fetch(`${API_BASE_URL}/closures`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
        body: JSON.stringify({ lat: coord.latitude, lon: coord.longitude }),
      });
      const body = await response.json();
      if (!response.ok) {
        throw new Error(body.error || `route_api returned ${response.status}`);
      }
      Alert.alert('Closure reported', `Thanks -- matched to way ${body.osm_way_id}.`);
    } catch (err) {
      Alert.alert('Could not report closure', String(err.message || err));
    } finally {
      setReportingClosure(false);
    }
  }

  async function openHistory() {
    setShowHistory(true);
    setSelectedRun(null);
    setLoadingHistory(true);
    try {
      const runs = await getRuns();
      // Merge in anything synced under this device id that the local
      // list doesn't already have. Honest about how little this
      // typically adds right now: since sync only ever pushes local ->
      // server, and the device id lives in the same local database as
      // the runs themselves (see db.js), there's normally nothing on the
      // server this device doesn't already have locally. This mainly
      // matters for a narrower case -- local run data lost to something
      // short of a full reinstall (a bug, partial corruption) while the
      // device row itself survived -- and it's the same code path a real
      // "log in and see your history from another device" feature would
      // reuse later, so it's not dead code even though it rarely
      // surfaces anything new today. Failures here are silent, not
      // alerted -- the local list is already valid and displayed either
      // way; a missing server merge shouldn't block viewing local
      // history.
      let merged = runs;
      try {
        const deviceId = await getOrCreateDeviceId();
        const response = await fetch(`${API_BASE_URL}/runs?deviceId=${encodeURIComponent(deviceId)}`, {
          headers: { 'X-API-Key': API_KEY },
        });
        if (response.ok) {
          const body = await response.json();
          merged = mergeRunHistory(runs, body.runs || []);
        }
      } catch (err) {
        console.warn('Could not fetch server-synced run history:', err.message || err);
      }
      setPastRuns(merged);
    } catch (err) {
      Alert.alert('Could not load past runs', String(err.message || err));
    } finally {
      setLoadingHistory(false);
    }
  }

  function closeHistory() {
    setShowHistory(false);
    setSelectedRun(null);
  }

  // "Delete my data" (§3's user-facing-controls requirement: a real
  // working button, not a support-ticket process). Clears BOTH stores --
  // local (always authoritative) and, if a device id exists, this
  // device's server-synced copy too (DELETE /runs, scripts/route_api.py)
  // -- there's no separate "delete my account" action, since this app's
  // device-scoped identity model (no login) means the synced runs ARE
  // the account data; see runs.py's module docstring. Confirmed first via
  // Alert, the standard React Native confirmation pattern for a
  // destructive action -- not the browser dialogs this project avoids
  // elsewhere, which are a different (blocking, non-native) mechanism.
  function deleteAllData() {
    Alert.alert(
      'Delete all my data?',
      'This permanently deletes your run history from this device and, if synced, from the server. This cannot be undone.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: async () => {
            setDeletingData(true);
            try {
              // Snapshot files (captureRunSnapshot) live outside the SQLite
              // row that references them -- deleteAllRuns only clears the
              // rows, so without this every run's snapshot would become an
              // orphaned file nothing ever references or cleans up again.
              // Gathered from pastRuns (already the current list, since
              // this button only renders on the screen that just loaded
              // it) rather than a fresh query, and deletion is best-effort
              // per file -- one already-missing/unreadable file shouldn't
              // block clearing the rest of a user's data.
              for (const uri of pastRuns.map((r) => r.mapSnapshotUri).filter(Boolean)) {
                try {
                  new File(uri).delete();
                } catch (err) {
                  console.warn('Could not delete run snapshot file:', uri, err.message || err);
                }
              }
              await deleteAllRuns();
              try {
                const deviceId = await getOrCreateDeviceId();
                const response = await fetch(`${API_BASE_URL}/runs`, {
                  method: 'DELETE',
                  headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
                  body: JSON.stringify({ deviceId }),
                });
                if (!response.ok) {
                  const body = await response.json().catch(() => ({}));
                  throw new Error(body.error || `route_api returned ${response.status}`);
                }
              } catch (err) {
                // Local deletion already succeeded and is the part that
                // matters most -- surfaced so the user knows the server
                // copy may still exist, but not treated as a fatal
                // failure of the overall action.
                Alert.alert(
                  'Local data deleted',
                  `Your local run history was deleted, but the server copy could not be reached: ${err.message || err}. Try again to fully clear the server-synced copy.`
                );
              }
              setPastRuns([]);
            } catch (err) {
              Alert.alert('Could not delete data', String(err.message || err));
            } finally {
              setDeletingData(false);
            }
          },
        },
      ]
    );
  }

  // Tapping a row in the past-runs list -- shows that run's actual
  // recorded trace on a map (see the replayMapReady effect above), not the
  // originally-generated route (that's not what's captured/stored; the
  // trace IS the ground truth of where the run actually went).
  function viewRunReplay(run) {
    setReplayMapReady(false);
    setSelectedRun(run);
  }

  function closeRunReplay() {
    setSelectedRun(null);
  }

  function handleReset() {
    setCandidates(null);
    setSelectedCandidateIndex(0);
    setStartCoord(null);
    setLiveCoord(null);
    setDeviationDistance(null);
    setInitialRegion(null);
    setLastRun(null);
    setSessionState('idle');
    generateRoute();
  }

  const isTracking = sessionState === 'running' || sessionState === 'paused';
  const isDeviated = isTracking && deviationDistance !== null && deviationDistance > DEVIATION_THRESHOLD_M;
  // durationTick isn't read directly -- its setter (ticking once a second
  // while running, above) is what forces this to recompute; the value
  // itself always comes fresh from runTimingRef so it can't drift out of
  // sync with the ref the way a separately-tracked duration state could.
  // Frozen correctly while paused: segmentStartedAt is null then, so this
  // is just the already-accumulated activeMs with nothing added.
  const liveDurationMs = isTracking
    ? runTimingRef.current.activeMs +
      (runTimingRef.current.segmentStartedAt ? Date.now() - runTimingRef.current.segmentStartedAt : 0)
    : 0;
  // §6: "during or after a run" -- not idle/generating/ready, there's no
  // meaningful "here" to report yet.
  const canReportClosure = sessionState === 'running' || sessionState === 'paused' || sessionState === 'done';
  // Not while actively tracking/generating -- avoids competing with the
  // in-run controls, and there's nothing new to show mid-run anyway.
  const canShowHistory = sessionState === 'idle' || sessionState === 'ready' || sessionState === 'done';

  if (showHistory && selectedRun) {
    // Route replay -- §2's "route replay-on-map for a past run." Reuses
    // the same MapView/Polyline/fitToCoordinates pattern as the main
    // session map above, applied to selectedRun.trace (the actual
    // recorded GPS points, already {latitude, longitude, timestamp}
    // objects straight from db.js's getRuns -- no reshaping needed) rather
    // than a server-generated route. This is deliberately the real path
    // that was run, jitter and all, not the clean originally-generated
    // line -- see handleEnd/traceRef for where it was captured.
    return (
      <View style={styles.container}>
        <View style={styles.historyHeader}>
          <Text style={styles.historyTitle}>{new Date(selectedRun.startedAt).toLocaleString()}</Text>
          <Button title="Back" onPress={closeRunReplay} />
        </View>
        <Text style={styles.runStats}>
          {formatDistance(selectedRun.actualDistanceM)} (target {formatDistance(selectedRun.targetDistanceM)}) · {formatDuration(selectedRun.durationMs)}
        </Text>
        {selectedRun.mapSnapshotUri ? (
          // §2's "offline map tiles for the last route" -- see
          // captureRunSnapshot's comment for the full reasoning. A plain
          // Image, not a MapView: no live tile fetch at all, so this
          // renders identically with no network. Not pan/zoomable, and
          // shows the planned route + end position (whatever the main map
          // had on screen at handleEnd), not this exact Polyline trace --
          // a real trade-off against the MapView branch below, worth
          // taking for runs new enough to have one.
          <Image
            testID="run-snapshot-image"
            source={{ uri: selectedRun.mapSnapshotUri }}
            style={styles.map}
            resizeMode="cover"
          />
        ) : selectedRun.trace && selectedRun.trace.length > 0 ? (
          <MapView
            ref={replayMapRef}
            style={styles.map}
            initialRegion={{
              latitude: selectedRun.trace[0].latitude,
              longitude: selectedRun.trace[0].longitude,
              latitudeDelta: 0.03,
              longitudeDelta: 0.03,
            }}
            onMapReady={() => setReplayMapReady(true)}
          >
            <Polyline coordinates={selectedRun.trace} strokeColor="#2E7D32" strokeWidth={4} />
          </MapView>
        ) : (
          <View style={styles.placeholder}>
            <Text style={styles.placeholderText}>No GPS trace recorded for this run.</Text>
          </View>
        )}
        <StatusBar style="auto" />
      </View>
    );
  }

  if (showHistory) {
    return (
      <View style={styles.container}>
        <View style={styles.historyHeader}>
          <Text style={styles.historyTitle}>Past Runs</Text>
          <Button title="Back" onPress={closeHistory} />
        </View>
        {loadingHistory ? (
          <ActivityIndicator style={styles.historyLoading} size="large" />
        ) : pastRuns.length === 0 ? (
          <View style={styles.placeholder}>
            <Text style={styles.placeholderText}>No runs saved yet -- finish a run to see it here.</Text>
          </View>
        ) : (
          <FlatList
            data={pastRuns}
            // item.id only exists for locally-saved rows; server-merged
            // runs (openHistory) only have runUuid. Fall back to id for
            // rows saved before the run_uuid column existed (a NULL
            // runUuid there) -- every row has at least one of the two.
            keyExtractor={(item) => item.runUuid || String(item.id)}
            renderItem={({ item }) => (
              <TouchableOpacity style={styles.runRow} onPress={() => viewRunReplay(item)}>
                <Text style={styles.runDate}>{new Date(item.startedAt).toLocaleString()}</Text>
                <Text style={styles.runStats}>
                  {formatDistance(item.actualDistanceM)} (target {formatDistance(item.targetDistanceM)}) · {formatDuration(item.durationMs)}
                </Text>
              </TouchableOpacity>
            )}
          />
        )}
        <View style={styles.deleteDataRow}>
          {deletingData ? (
            <ActivityIndicator size="small" />
          ) : (
            <Button title="Delete All My Data" color="#B00020" onPress={deleteAllData} />
          )}
        </View>
        <StatusBar style="auto" />
      </View>
    );
  }

  return (
    <View style={styles.container}>
      {initialRegion ? (
        <MapView ref={mapRef} style={styles.map} initialRegion={initialRegion} onMapReady={() => setMapReady(true)}>
          {startCoord && <Marker coordinate={startCoord} title="Start" pinColor="red" />}
          {/* Only offer a choice while still deciding ('ready') -- once a run
              is underway (or done), the choice is locked in and this just
              renders the one selected route, same as before this feature. */}
          {sessionState === 'ready' && candidates ? (
            <>
              {/* Unselected alternates first, in a muted color, each tappable
                  to select it. Rendered before the selected one so the
                  selected route's green line draws on top and stays fully
                  visible even where routes overlap. */}
              {candidates.map((c, i) => i !== selectedCandidateIndex && (
                <Polyline
                  key={c.rank}
                  coordinates={c.coords}
                  strokeColor="#9E9E9E"
                  strokeWidth={3}
                  tappable
                  onPress={() => setSelectedCandidateIndex(i)}
                />
              ))}
              {selectedCandidate && (
                <Polyline
                  key={`selected-${selectedCandidate.rank}`}
                  coordinates={selectedCandidate.coords}
                  strokeColor="#2E7D32"
                  strokeWidth={5}
                  tappable
                  onPress={() => {}}
                />
              )}
            </>
          ) : (
            selectedCoords && (
              <Polyline coordinates={selectedCoords} strokeColor="#2E7D32" strokeWidth={4} />
            )
          )}
          {isTracking && liveCoord && (
            <Marker coordinate={liveCoord} title="You" pinColor={isDeviated ? 'orange' : 'dodgerblue'} />
          )}
        </MapView>
      ) : (
        <View style={styles.placeholder}>
          <Text style={styles.placeholderText}>
            {sessionState === 'generating' ? 'Generating route...' : 'No route yet -- tap the button below.'}
          </Text>
        </View>
      )}

      {isDeviated && (
        <View style={styles.deviationBanner}>
          <Text style={styles.deviationBannerText}>
            ⚠️ Off route -- ~{Math.round(deviationDistance)}m from the path
          </Text>
        </View>
      )}

      <View style={styles.controls}>
        {sessionState === 'generating' && <ActivityIndicator size="large" />}
        {sessionState === 'idle' && (
          // onPress={() => generateRoute()}, not onPress={generateRoute} --
          // Button's onPress hands its GestureResponderEvent as the first
          // argument, which generateRoute would otherwise take as a truthy
          // distanceOverride instead of falling back to targetDistanceM
          // state. Same reasoning below for Regenerate.
          <Button title={`Generate ${targetDistanceM / 1000}km route from here`} onPress={() => generateRoute()} />
        )}
        {sessionState === 'ready' && (
          <>
            {candidates && candidates.length > 1 && (
              <Text style={styles.candidateHint}>
                {candidates.length} routes shown -- tap one on the map to choose it.{' '}
                Selected: #{selectedCandidate.rank} ({formatDistance(selectedCandidate.distanceM)})
              </Text>
            )}
            {/* §1's own pitch is "give it a distance" -- this is the one
                place that's actually possible now, instead of a silent
                fixed 5km forever. Tapping a preset regenerates at that
                distance immediately, same as Regenerate but at a new
                target -- see DISTANCE_PRESETS_M/selectDistance above. */}
            <View style={styles.buttonRow}>
              {DISTANCE_PRESETS_M.map((meters) => (
                <TouchableOpacity
                  key={meters}
                  onPress={() => selectDistance(meters)}
                  style={[styles.distanceChip, meters === targetDistanceM && styles.distanceChipSelected]}
                >
                  <Text style={[styles.distanceChipText, meters === targetDistanceM && styles.distanceChipTextSelected]}>
                    {meters / 1000}km
                  </Text>
                </TouchableOpacity>
              ))}
            </View>
            <View style={styles.buttonRow}>
              <Button title="Regenerate" onPress={() => generateRoute()} />
              <Button title="Start Run" onPress={handleStart} />
            </View>
          </>
        )}
        {sessionState === 'running' && (
          <>
            {/* §3's "make it visible when a run is being recorded ...
                never track silently" -- the buttons below already implied
                a run was active, but nothing showed the live distance/
                duration a "recording" indicator should. */}
            <Text style={styles.recordingIndicator}>
              ● Recording -- {formatDistance(liveDistanceM)} · {formatDuration(liveDurationMs)}
            </Text>
            <View style={styles.buttonRow}>
              <Button title="Pause" onPress={handlePause} />
              <Button title="End Run" color="#B00020" onPress={handleEnd} />
            </View>
          </>
        )}
        {sessionState === 'paused' && (
          <>
            <Text style={styles.recordingIndicator}>
              ⏸ Paused -- {formatDistance(liveDistanceM)} · {formatDuration(liveDurationMs)}
            </Text>
            <View style={styles.buttonRow}>
              <Button title="Resume" onPress={handleResume} />
              <Button title="End Run" color="#B00020" onPress={handleEnd} />
            </View>
          </>
        )}
        {sessionState === 'done' && (
          // Was just a bare "New Route" button -- researched running-app
          // UX conventions before building this (post-run screens
          // consistently lead with distance/time/pace right away, not
          // buried in Past Runs), and this app genuinely had zero
          // feedback here: finishing a run gave no confirmation of what
          // was just accomplished at all. lastRun (handleEnd, above) is
          // used instead of liveDistanceM/liveDurationMs specifically
          // because those go stale/zero the moment isTracking becomes
          // false, which happens right as this renders. Guarded on
          // lastRun existing (it always should by the time this state is
          // reachable) so a null/missed-set case still leaves the one
          // essential action -- New Route -- reachable rather than
          // rendering nothing at all.
          <View style={styles.summaryContainer}>
            {lastRun && (
              <>
                <Text style={styles.summaryTitle}>Run complete 🎉</Text>
                <View style={styles.summaryStatsRow}>
                  <View style={styles.summaryStat}>
                    <Text style={styles.summaryStatValue}>{formatDistance(lastRun.actualDistanceM)}</Text>
                    <Text style={styles.summaryStatLabel}>Distance</Text>
                  </View>
                  <View style={styles.summaryStat}>
                    <Text style={styles.summaryStatValue}>{formatDuration(lastRun.durationMs)}</Text>
                    <Text style={styles.summaryStatLabel}>Time</Text>
                  </View>
                  <View style={styles.summaryStat}>
                    <Text style={styles.summaryStatValue}>{formatPace(lastRun.actualDistanceM, lastRun.durationMs)}</Text>
                    <Text style={styles.summaryStatLabel}>Pace</Text>
                  </View>
                </View>
              </>
            )}
            <Button title="New Route" onPress={handleReset} />
          </View>
        )}
        {canReportClosure && (
          <View style={styles.reportRow}>
            {reportingClosure ? (
              <ActivityIndicator />
            ) : (
              <Button title="Report closure" color="#8B4513" onPress={handleReportClosure} />
            )}
          </View>
        )}
        {canShowHistory && (
          <View style={styles.reportRow}>
            <Button title="Past Runs" onPress={openHistory} />
          </View>
        )}
      </View>
      <StatusBar style="auto" />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#fff',
  },
  map: {
    flex: 1,
  },
  placeholder: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
  },
  placeholderText: {
    fontSize: 16,
    textAlign: 'center',
    color: '#555',
  },
  controls: {
    padding: 16,
    paddingBottom: Platform.OS === 'ios' ? 32 : 16,
    backgroundColor: '#fff',
  },
  buttonRow: {
    flexDirection: 'row',
    justifyContent: 'space-evenly',
  },
  candidateHint: {
    textAlign: 'center',
    fontSize: 13,
    color: '#555',
    marginBottom: 10,
  },
  recordingIndicator: {
    textAlign: 'center',
    fontSize: 14,
    fontWeight: '600',
    color: '#2E7D32',
    marginBottom: 10,
  },
  distanceChip: {
    borderWidth: 1,
    borderColor: '#2E7D32',
    borderRadius: 16,
    paddingVertical: 6,
    paddingHorizontal: 14,
    marginBottom: 10,
  },
  distanceChipSelected: {
    backgroundColor: '#2E7D32',
  },
  distanceChipText: {
    color: '#2E7D32',
    fontWeight: '600',
  },
  distanceChipTextSelected: {
    color: '#fff',
  },
  summaryContainer: {
    alignItems: 'center',
  },
  summaryTitle: {
    fontSize: 20,
    fontWeight: '700',
    marginBottom: 14,
  },
  summaryStatsRow: {
    flexDirection: 'row',
    justifyContent: 'space-evenly',
    width: '100%',
    marginBottom: 16,
  },
  summaryStat: {
    alignItems: 'center',
  },
  summaryStatValue: {
    fontSize: 20,
    fontWeight: '700',
    color: '#2E7D32',
    fontVariant: ['tabular-nums'],
  },
  summaryStatLabel: {
    fontSize: 12,
    color: '#555',
    marginTop: 2,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  reportRow: {
    marginTop: 8,
    alignItems: 'center',
  },
  deviationBanner: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    backgroundColor: '#FB8C00',
    paddingTop: Platform.OS === 'ios' ? 56 : 16,
    paddingBottom: 12,
    paddingHorizontal: 16,
  },
  deviationBannerText: {
    color: '#fff',
    fontWeight: '600',
    textAlign: 'center',
  },
  historyHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingTop: Platform.OS === 'ios' ? 56 : 16,
    paddingBottom: 12,
  },
  historyTitle: {
    fontSize: 20,
    fontWeight: '600',
  },
  historyLoading: {
    marginTop: 24,
  },
  deleteDataRow: {
    padding: 16,
    paddingBottom: Platform.OS === 'ios' ? 32 : 16,
    alignItems: 'center',
  },
  runRow: {
    paddingHorizontal: 16,
    paddingVertical: 14,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: '#ccc',
  },
  runDate: {
    fontSize: 16,
    fontWeight: '600',
  },
  runStats: {
    fontSize: 14,
    color: '#555',
    marginTop: 4,
  },
});
