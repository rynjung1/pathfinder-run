import { useState, useEffect } from 'react';
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
  const [loading, setLoading] = useState(false);
  const [region, setRegion] = useState(null);
  const [routeCoords, setRouteCoords] = useState(null);
  const [startCoord, setStartCoord] = useState(null);

  // Auto-fetch once on launch, purely so this screen has something to show
  // without requiring a tap first (useful for a quick screenshot/demo). The
  // button below still lets you regenerate on demand.
  useEffect(() => {
    generateRoute();
  }, []);

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
      setRouteCoords(coords);
      setRegion({
        latitude,
        longitude,
        latitudeDelta: 0.03,
        longitudeDelta: 0.03,
      });
    } catch (err) {
      Alert.alert('Could not generate route', String(err.message || err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <View style={styles.container}>
      {region ? (
        <MapView style={styles.map} initialRegion={region} region={region}>
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
