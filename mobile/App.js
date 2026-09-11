import { useState, useEffect, useRef } from 'react';
import { StyleSheet, Text, View, Button, ActivityIndicator, Alert, Platform } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import * as Location from 'expo-location';
import MapView, { Polyline, Marker } from 'react-native-maps';

// Pathfinder Run -- v1 mobile client, §7 step 3: request a route, show it on
// a map. No live tracking, no alternative-route picker, no persistence.
//
// Addressing: an iOS Simulator shares the host Mac's network stack, so
// localhost reaches a server running on the same machine directly. That is
// NOT true for a physical device (or an Android emulator, which needs
// 10.0.2.2 instead) -- those need the dev machine's LAN IP. route_api.py
// binds to 0.0.0.0 for exactly this reason; only this constant needs to
// change to point at it from a real device.
const API_BASE_URL = 'http://localhost:5001';
const TARGET_DISTANCE_M = 5000;

export default function App() {
  const mapRef = useRef(null);
  const [loading, setLoading] = useState(false);
  const [initialRegion, setInitialRegion] = useState(null);
  const [routeCoords, setRouteCoords] = useState(null);
  const [startCoord, setStartCoord] = useState(null);
  const [mapReady, setMapReady] = useState(false);

  // Auto-fetch once on launch, purely so this screen has something to show
  // without requiring a tap first (useful for a quick screenshot/demo). The
  // button below still lets you regenerate on demand.
  useEffect(() => {
    generateRoute();
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
    setLoading(true);
    setRouteCoords(null);
    try {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') {
        Alert.alert('Location permission required', 'Pathfinder Run needs your location to generate a route from where you are.');
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
    } catch (err) {
      Alert.alert('Could not generate route', String(err.message || err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <View style={styles.container}>
      {initialRegion ? (
        <MapView ref={mapRef} style={styles.map} initialRegion={initialRegion} onMapReady={() => setMapReady(true)}>
          {startCoord && <Marker coordinate={startCoord} title="Start" />}
          {routeCoords && (
            <Polyline coordinates={routeCoords} strokeColor="#2E7D32" strokeWidth={4} />
          )}
        </MapView>
      ) : (
        <View style={styles.placeholder}>
          <Text style={styles.placeholderText}>No route yet -- tap the button below.</Text>
        </View>
      )}

      <View style={styles.controls}>
        {loading ? (
          <ActivityIndicator size="large" />
        ) : (
          <Button title="Generate 5km route from here" onPress={generateRoute} />
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
});
