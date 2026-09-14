/**
 * Component-level test for App.js's alternate-route picker (§5 point 4,
 * mobile UI polish) -- formalizing the exact behavior that was manually
 * re-verified multiple times this session (tap an alternate candidate's
 * polyline, confirm the selection and displayed distance update), same
 * "stop manually re-checking this by hand" rationale as
 * test_route_regression.py on the Python side.
 *
 * react-native-maps is mocked via __mocks__/react-native-maps.js (picked
 * up automatically -- see that file's header). expo-location, ./db, and
 * global fetch are mocked here since this test controls exactly what
 * "the server" and "the device" return, rather than needing a real
 * GraphHopper instance or SQLite/location hardware.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import { Alert } from 'react-native';

jest.mock('expo-location', () => ({
  Accuracy: { BestForNavigation: 6 }, // App.js only references this by name at module load, value is irrelevant here
  requestForegroundPermissionsAsync: jest.fn(() => Promise.resolve({ status: 'granted' })),
  getCurrentPositionAsync: jest.fn(() =>
    Promise.resolve({ coords: { latitude: 43.4643, longitude: -80.5204 }, timestamp: Date.now() })
  ),
  watchPositionAsync: jest.fn(() => Promise.resolve({ remove: jest.fn() })),
}));

jest.mock('../db', () => ({
  getRuns: jest.fn(() => Promise.resolve([])),
  saveRun: jest.fn(() => Promise.resolve(1)),
  getOrCreateDeviceId: jest.fn(() => Promise.resolve('test-device-id')),
  deleteAllRuns: jest.fn(() => Promise.resolve()),
}));

import App from '../App';
import { deleteAllRuns } from '../db';

// Three distinct, real-shaped candidates, matching exactly what
// route_api.py's candidates_to_geojson actually returns (see App.js's
// generateRoute -- f.properties.rank / .actual_distance_m,
// f.geometry.coordinates as [lon, lat] pairs) -- rank 1 first, since the
// server already sorts best-first and App.js relies on that order for
// the default selection.
function mockRouteResponse() {
  const candidate = (rank, distanceM, lonOffset) => ({
    type: 'Feature',
    properties: { rank, actual_distance_m: distanceM },
    geometry: {
      type: 'LineString',
      coordinates: [
        [-80.5204, 43.4643],
        [-80.5204 + lonOffset, 43.4700],
      ],
    },
  });
  return {
    type: 'FeatureCollection',
    features: [candidate(1, 2988.28, 0.01), candidate(2, 3015.65, 0.02), candidate(3, 3040.1, 0.03)],
  };
}

beforeEach(() => {
  global.fetch = jest.fn(() =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve(mockRouteResponse()),
    })
  );
});

afterEach(() => {
  jest.restoreAllMocks();
});

test('rank 1 is selected by default after a route is generated', async () => {
  render(<App />);
  await waitFor(() => screen.getByText(/Selected: #1/));
  expect(screen.getByText(/2\.99 km/)).toBeTruthy(); // rank 1's 2988.28m
});

test('tapping an alternate route selects it and updates the displayed distance', async () => {
  render(<App />);
  await waitFor(() => screen.getByText(/Selected: #1/));

  // Per App.js's render order, unselected alternates are drawn first (in
  // index order, skipping whichever is currently selected), then the
  // selected candidate's own Polyline last -- so with rank 1 selected,
  // the alternates array is [rank 2, rank 3], and the mock's first
  // returned Polyline is rank 2's.
  const alternates = screen.getAllByTestId('mock-polyline');
  expect(alternates).toHaveLength(3); // 2 alternates + 1 selected
  fireEvent.press(alternates[0]); // rank 2

  await waitFor(() => screen.getByText(/Selected: #2/));
  expect(screen.getByText(/3\.02 km/)).toBeTruthy(); // rank 2's 3015.65m
  expect(screen.queryByText(/Selected: #1/)).toBeNull();
});

test('Delete All My Data clears local runs and calls the server DELETE endpoint', async () => {
  // Alert.alert is a native modal Jest can't render -- stand in for the
  // user tapping "Delete" by invoking that button's onPress directly,
  // the standard RN Testing Library pattern for confirmation dialogs.
  jest.spyOn(Alert, 'alert').mockImplementation((title, message, buttons) => {
    buttons.find((b) => b.text === 'Delete').onPress();
  });

  render(<App />);
  await waitFor(() => screen.getByText(/Selected: #1/));

  fireEvent.press(screen.getByText('Past Runs'));
  await waitFor(() => screen.getByText('Delete All My Data'));
  fireEvent.press(screen.getByText('Delete All My Data'));

  await waitFor(() => expect(deleteAllRuns).toHaveBeenCalledTimes(1));

  const deleteCall = global.fetch.mock.calls.find(([, opts]) => opts && opts.method === 'DELETE');
  expect(deleteCall).toBeTruthy();
  expect(deleteCall[0]).toBe('http://localhost:5001/runs');
  expect(JSON.parse(deleteCall[1].body)).toEqual({ deviceId: 'test-device-id' });
});
