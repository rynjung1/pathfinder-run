import { useState, useEffect, useRef } from 'react';
import { StyleSheet, Text, View, Button, ActivityIndicator, Alert, Platform } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import * as Location from 'expo-location';
import MapView, { Polyline, Marker } from 'react-native-maps';

// Pathfinder Run -- v2 mobile client. §2/§3/§7 step 4: live GPS tracking
// during an active run, shown moving on the map against the generated
// route, plus on-device deviation detection (comparing the live point to
// the cached route geometry, §3's data-minimization guidance -- no server
// round trip needed for this). Run-session state machine per §2:
//   idle -> generating -> ready -> running -> paused -> done -> (idle)
//
// Deliberately NOT here yet:
// - Rerouting once a deviation is detected -- detection + a UI indicator
//   only for now, no recalculation logic.
// - Server-side batch sync of the run track.
// - Android foreground service + persistent notification. Confirmed
//   feasible without ACCESS_BACKGROUND_LOCATION (verified directly from
//   expo-location's config-plugin source: isAndroidForegroundServiceEnabled
//   and isAndroidBackgroundLocationEnabled are independent flags), but
//   deliberately not implemented -- decided not a priority right now, see
//   the comment on WATCH_OPTIONS/startWatching below for what this means
//   in practice.
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
// Addressing: an iOS Simulator shares the host Mac's network stack, so
// localhost reaches a server running on the same machine directly. That is
// NOT true for a physical device (or an Android emulator, which needs
// 10.0.2.2 instead) -- those need the dev machine's LAN IP. route_api.py
// binds to 0.0.0.0 for exactly this reason; only this constant needs to
// change to point at it from a real device.
const API_BASE_URL = 'http://localhost:5001';
const TARGET_DISTANCE_M = 5000;

// Known limitation, not an oversight: this is plain watchPositionAsync, no
// foreground service. Tracking stops silently the moment the screen locks
// or the app is backgrounded -- on Android as much as iOS, even though a
// foreground service *would* let Android keep tracking through a locked
// screen under ordinary ACCESS_FINE_LOCATION (no ACCESS_BACKGROUND_LOCATION
// needed -- see the file header). That's confirmed feasible but explicitly
// not built yet: skipped for now as a lower priority than deviation
// detection, not because it's hard. If this needs revisiting, it's
// isAndroidForegroundServiceEnabled in the expo-location config plugin
// (app.json), not a rewrite of the tracking logic here.
const WATCH_OPTIONS = {
  accuracy: Location.Accuracy.BestForNavigation,
  timeInterval: 2000,   // ms between updates (Android only, per expo-location docs)
  distanceInterval: 5,  // meters -- don't bother updating for sub-5m jitter
};

// How far off the generated route (meters) before flagging a deviation.
// Ad hoc starting point, not tuned: needs to comfortably clear normal GPS
// jitter (~5-15m on a good fix) without being so loose that a real
// wrong-turn goes unnoticed for too long. Revisit once real outdoor running
// tests exist -- same "ad hoc, revisit later" spirit as the loop-scoring
// weights in scripts/generate_loop.py.
const DEVIATION_THRESHOLD_M = 40;

// Local flat-plane projection centered on `origin`, in meters. Accurate
// enough at the scale this is used for (a deviation check over tens to a
// few hundred meters) -- not attempting true great-circle math for a
// difference this small.
function projectToLocalMeters(origin, point) {
  const M_PER_DEG_LAT = 111320;
  const mPerDegLon = M_PER_DEG_LAT * Math.cos((origin.latitude * Math.PI) / 180);
  return {
    x: (point.longitude - origin.longitude) * mPerDegLon,
    y: (point.latitude - origin.latitude) * M_PER_DEG_LAT,
  };
}

// Shortest distance from point p to segment a-b, all in the same local xy
// plane (meters).
function pointToSegmentDistance(p, a, b) {
  const abx = b.x - a.x;
  const aby = b.y - a.y;
  const abLenSq = abx * abx + aby * aby;
  let t = abLenSq === 0 ? 0 : ((p.x - a.x) * abx + (p.y - a.y) * aby) / abLenSq;
  t = Math.max(0, Math.min(1, t)); // clamp to the segment, not the infinite line
  const closestX = a.x + t * abx;
  const closestY = a.y + t * aby;
  const dx = p.x - closestX;
  const dy = p.y - closestY;
  return Math.sqrt(dx * dx + dy * dy);
}

// Minimum distance (meters) from `point` to the route polyline -- the
// on-device deviation check. Projects each route segment into a plane
// centered on `point` itself (recomputed per call; the route is short
// enough that this is cheap) and takes the smallest point-to-segment
// distance across all of it. §3: this runs entirely on-device against the
// already-fetched route geometry, no server round trip per position update.
function distanceToRouteMeters(point, routeCoords) {
  if (!routeCoords || routeCoords.length < 2) return Infinity;
  const p = { x: 0, y: 0 };
  let minDist = Infinity;
  for (let i = 0; i < routeCoords.length - 1; i++) {
    const a = projectToLocalMeters(point, routeCoords[i]);
    const b = projectToLocalMeters(point, routeCoords[i + 1]);
    const d = pointToSegmentDistance(p, a, b);
    if (d < minDist) minDist = d;
  }
  return minDist;
}

export default function App() {
  const mapRef = useRef(null);
  const watchSubscriptionRef = useRef(null);

  // idle | generating | ready | running | paused | done
  const [sessionState, setSessionState] = useState('idle');
  const [initialRegion, setInitialRegion] = useState(null);
  const [routeCoords, setRouteCoords] = useState(null);
  const [startCoord, setStartCoord] = useState(null);
  const [liveCoord, setLiveCoord] = useState(null);
  const [deviationDistance, setDeviationDistance] = useState(null);
  const [mapReady, setMapReady] = useState(false);

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

  // Fit the map to the whole loop once it's loaded, rather than a fixed box
  // around the start point -- a 5km+ loop routinely runs off the edge of a
  // fixed-delta region since its actual extent depends on the bearing/shape
  // GraphHopper picked, not just distance from the start.
  //
  // Gated on mapReady (react-native-maps' onMapReady), not just routeCoords:
  // calling fitToCoordinates before the native map view has completed its
  // first layout is a known no-op on iOS -- the ref exists (React has
  // mounted and attached it) but the native side isn't ready to compute a
  // fit yet. Confirmed by testing: gating on routeCoords alone silently did
  // nothing, framing stayed at the fixed initialRegion delta. The extra
  // setTimeout is a pragmatic belt-and-suspenders on top of onMapReady --
  // onMapReady alone has been reported flaky on first launch in some
  // react-native-maps versions.
  useEffect(() => {
    if (mapReady && mapRef.current && routeCoords && routeCoords.length > 0) {
      const timer = setTimeout(() => {
        mapRef.current?.fitToCoordinates(routeCoords, {
          edgePadding: { top: 60, right: 60, bottom: 60, left: 60 },
          animated: true,
        });
      }, 300);
      return () => clearTimeout(timer);
    }
  }, [mapReady, routeCoords]);

  async function generateRoute() {
    setSessionState('generating');
    setRouteCoords(null);
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
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lat: latitude, lon: longitude, distance: TARGET_DISTANCE_M }),
      });
      const body = await response.json();
      if (!response.ok) {
        throw new Error(body.error || `route_api returned ${response.status}`);
      }

      // Server already sorts candidates best-first (§5 point 4) -- take rank 1.
      const best = body.features[0];
      const coords = best.geometry.coordinates.map(([lon, lat]) => ({
        latitude: lat,
        longitude: lon,
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
      setRouteCoords(coords);
      setSessionState('ready');
    } catch (err) {
      Alert.alert('Could not generate route', String(err.message || err));
      setSessionState('idle');
    }
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
    watchSubscriptionRef.current = await Location.watchPositionAsync(WATCH_OPTIONS, (position) => {
      const coord = {
        latitude: position.coords.latitude,
        longitude: position.coords.longitude,
      };
      setLiveCoord(coord);
      setDeviationDistance(distanceToRouteMeters(coord, routeCoords));
    });
  }

  function stopWatching() {
    watchSubscriptionRef.current?.remove();
    watchSubscriptionRef.current = null;
  }

  async function handleStart() {
    setSessionState('running');
    await startWatching();
  }

  function handlePause() {
    stopWatching();
    setSessionState('paused');
  }

  async function handleResume() {
    setSessionState('running');
    await startWatching();
  }

  function handleEnd() {
    stopWatching();
    setSessionState('done');
  }

  function handleReset() {
    setRouteCoords(null);
    setStartCoord(null);
    setLiveCoord(null);
    setDeviationDistance(null);
    setInitialRegion(null);
    setSessionState('idle');
    generateRoute();
  }

  const isTracking = sessionState === 'running' || sessionState === 'paused';
  const isDeviated = isTracking && deviationDistance !== null && deviationDistance > DEVIATION_THRESHOLD_M;

  return (
    <View style={styles.container}>
      {initialRegion ? (
        <MapView ref={mapRef} style={styles.map} initialRegion={initialRegion} onMapReady={() => setMapReady(true)}>
          {startCoord && <Marker coordinate={startCoord} title="Start" pinColor="red" />}
          {routeCoords && (
            <Polyline coordinates={routeCoords} strokeColor="#2E7D32" strokeWidth={4} />
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
          <Button title="Generate 5km route from here" onPress={generateRoute} />
        )}
        {sessionState === 'ready' && (
          <View style={styles.buttonRow}>
            <Button title="Regenerate" onPress={generateRoute} />
            <Button title="Start Run" onPress={handleStart} />
          </View>
        )}
        {sessionState === 'running' && (
          <View style={styles.buttonRow}>
            <Button title="Pause" onPress={handlePause} />
            <Button title="End Run" color="#B00020" onPress={handleEnd} />
          </View>
        )}
        {sessionState === 'paused' && (
          <View style={styles.buttonRow}>
            <Button title="Resume" onPress={handleResume} />
            <Button title="End Run" color="#B00020" onPress={handleEnd} />
          </View>
        )}
        {sessionState === 'done' && (
          <Button title="New Route" onPress={handleReset} />
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
});
