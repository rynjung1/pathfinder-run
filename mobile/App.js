import { useState, useEffect, useRef } from 'react';
import { StyleSheet, Text, View, Button, ActivityIndicator, Alert, Platform } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import * as Location from 'expo-location';
import MapView, { Polyline, Marker } from 'react-native-maps';

// Pathfinder Run -- v2 mobile client, first slice of §2/§3/§7 step 4: live
// GPS tracking during an active run, shown moving on the map against the
// generated route. Run-session state machine per §2:
//   idle -> generating -> ready -> running -> paused -> done -> (idle)
//
// Deliberately NOT in this slice (see the investigation in this session's
// notes before building any of this):
// - On-device deviation detection (comparing live point to route geometry)
//   and server-side batch sync -- next slices, once this is working.
// - Android foreground service + persistent notification (survives a
//   locked screen under ACCESS_FINE_LOCATION alone, confirmed feasible and
//   NOT requiring ACCESS_BACKGROUND_LOCATION -- verified directly from
//   expo-location's config-plugin source, which adds
//   FOREGROUND_SERVICE/FOREGROUND_SERVICE_LOCATION independently of
//   isAndroidBackgroundLocationEnabled). Not wired up here -- it needs an
//   app.json config plugin change and its own test pass, and this slice's
//   scope was explicitly "state machine + start-on-run + live dot on map,"
//   not lock-screen survival. Confirmed feasible; deferred, not forgotten.
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
//   opt-in, not default. This slice tracks correctly while the app is
//   foregrounded (screen on); locking the screen mid-run stops updates on
//   iOS specifically until that v3 work happens.
//
// Addressing: an iOS Simulator shares the host Mac's network stack, so
// localhost reaches a server running on the same machine directly. That is
// NOT true for a physical device (or an Android emulator, which needs
// 10.0.2.2 instead) -- those need the dev machine's LAN IP. route_api.py
// binds to 0.0.0.0 for exactly this reason; only this constant needs to
// change to point at it from a real device.
const API_BASE_URL = 'http://localhost:5001';
const TARGET_DISTANCE_M = 5000;

const WATCH_OPTIONS = {
  accuracy: Location.Accuracy.BestForNavigation,
  timeInterval: 2000,   // ms between updates (Android only, per expo-location docs)
  distanceInterval: 5,  // meters -- don't bother updating for sub-5m jitter
};

export default function App() {
  const mapRef = useRef(null);
  const watchSubscriptionRef = useRef(null);

  // idle | generating | ready | running | paused | done
  const [sessionState, setSessionState] = useState('idle');
  const [initialRegion, setInitialRegion] = useState(null);
  const [routeCoords, setRouteCoords] = useState(null);
  const [startCoord, setStartCoord] = useState(null);
  const [liveCoord, setLiveCoord] = useState(null);
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
      setLiveCoord({
        latitude: position.coords.latitude,
        longitude: position.coords.longitude,
      });
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
    setInitialRegion(null);
    setSessionState('idle');
    generateRoute();
  }

  const isTracking = sessionState === 'running' || sessionState === 'paused';

  return (
    <View style={styles.container}>
      {initialRegion ? (
        <MapView ref={mapRef} style={styles.map} initialRegion={initialRegion} onMapReady={() => setMapReady(true)}>
          {startCoord && <Marker coordinate={startCoord} title="Start" pinColor="red" />}
          {routeCoords && (
            <Polyline coordinates={routeCoords} strokeColor="#2E7D32" strokeWidth={4} />
          )}
          {isTracking && liveCoord && (
            <Marker coordinate={liveCoord} title="You" pinColor="dodgerblue" />
          )}
        </MapView>
      ) : (
        <View style={styles.placeholder}>
          <Text style={styles.placeholderText}>
            {sessionState === 'generating' ? 'Generating route...' : 'No route yet -- tap the button below.'}
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
});
