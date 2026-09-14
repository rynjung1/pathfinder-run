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
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import { Alert } from 'react-native';
import * as Location from 'expo-location';

// Captured by the watchPositionAsync mock below so a test can simulate a
// real GPS ping by calling it directly -- App.js's handleStart flow calls
// Location.getForegroundPermissionsAsync() (distinct from
// requestForegroundPermissionsAsync, used only pre-route-generation), so
// that needs its own mock too, not just the request variant.
let watchCallback = null;
jest.mock('expo-location', () => ({
  Accuracy: { BestForNavigation: 6 }, // App.js only references this by name at module load, value is irrelevant here
  requestForegroundPermissionsAsync: jest.fn(() => Promise.resolve({ status: 'granted' })),
  getForegroundPermissionsAsync: jest.fn(() => Promise.resolve({ status: 'granted' })),
  getCurrentPositionAsync: jest.fn(() =>
    Promise.resolve({ coords: { latitude: 43.4643, longitude: -80.5204 }, timestamp: Date.now() })
  ),
  watchPositionAsync: jest.fn((options, callback) => {
    watchCallback = callback;
    return Promise.resolve({ remove: jest.fn() });
  }),
}));

// Captured the same way, for the adaptive-sampling test -- jest-expo's
// default mock for expo-sensors exists but never actually invokes a
// registered listener, which would make the switching logic untestable
// (and untested) rather than just untriggered in this environment.
let accelerometerCallback = null;
jest.mock('expo-sensors', () => ({
  Accelerometer: {
    setUpdateInterval: jest.fn(),
    addListener: jest.fn((callback) => {
      accelerometerCallback = callback;
      return { remove: jest.fn() };
    }),
  },
}));

jest.mock('../db', () => ({
  getRuns: jest.fn(() => Promise.resolve([])),
  saveRun: jest.fn(() => Promise.resolve(1)),
  getOrCreateDeviceId: jest.fn(() => Promise.resolve('test-device-id')),
  deleteAllRuns: jest.fn(() => Promise.resolve()),
}));

// Module-scoped mocks (not per-instance jest.fn()s) so tests can assert on
// calls the same way __mocks__/react-native-maps.js's __takeSnapshotMock
// does -- App.js's captureRunSnapshot/deleteAllData call File/Directory
// instance methods it creates itself, so there's no other way to intercept
// them. Good enough to stand in for real file paths: App.js only ever
// treats a File/Directory's .uri as an opaque string it stores/reads back,
// never parses it.
jest.mock('expo-file-system', () => {
  const copyMock = jest.fn(() => Promise.resolve());
  const deleteMock = jest.fn();
  const createMock = jest.fn();
  class MockFile {
    constructor(...parts) {
      this.uri = parts.map((p) => (typeof p === 'string' ? p : p.uri)).join('/');
    }
    copy(dest) {
      return copyMock(this, dest);
    }
    delete() {
      return deleteMock(this);
    }
  }
  class MockDirectory {
    constructor(...parts) {
      this.uri = parts.map((p) => (typeof p === 'string' ? p : p.uri)).join('/');
    }
    create(options) {
      return createMock(this, options);
    }
  }
  return {
    Paths: { document: { uri: 'file:///mock-documents' } },
    File: MockFile,
    Directory: MockDirectory,
    __copyMock: copyMock,
    __deleteMock: deleteMock,
  };
});

import App from '../App';
import { deleteAllRuns, getRuns, saveRun } from '../db';
import MapView from 'react-native-maps';
import { __copyMock, __deleteMock } from 'expo-file-system';

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

test('starting a run shows a live Recording indicator that updates as GPS points come in', async () => {
  render(<App />);
  await waitFor(() => screen.getByText(/Selected: #1/));

  fireEvent.press(screen.getByText('Start Run'));
  // No GPS points yet -- a single-point trace has no distance to sum.
  await waitFor(() => screen.getByText(/Recording.*0\.00 km/));

  // Two pings, ~630m apart (0.0057° latitude at this longitude) -- enough
  // points for traceDistanceMeters (geometry.js) to have a real consecutive
  // pair to sum, mirroring exactly what watchPositionAsync's real callback
  // does in startWatching.
  await act(async () => {
    watchCallback({ coords: { latitude: 43.4643, longitude: -80.5204 }, timestamp: Date.now() });
  });
  await act(async () => {
    watchCallback({ coords: { latitude: 43.47, longitude: -80.5204 }, timestamp: Date.now() });
  });

  await waitFor(() => expect(screen.queryByText(/Recording.*0\.00 km/)).toBeNull());
  expect(screen.getByText(/Recording.*0\.6\d km/)).toBeTruthy();

  // Pause freezes the label (Paused, not Recording) but keeps showing the
  // same accumulated distance -- doesn't reset to 0.
  fireEvent.press(screen.getByText('Pause'));
  await waitFor(() => screen.getByText(/Paused.*0\.6\d km/));
});

test('adaptive GPS sampling switches to the coarse profile after sustained stillness, and back to fine on real motion', async () => {
  // watchPositionAsync is a single jest.fn() shared across this whole
  // file (defined once in the jest.mock factory above) -- other tests
  // that also press Start Run leave calls on it, so this test needs its
  // own clean baseline rather than assuming call #1 is its own.
  Location.watchPositionAsync.mockClear();

  render(<App />);
  await waitFor(() => screen.getByText(/Selected: #1/));

  fireEvent.press(screen.getByText('Start Run'));
  await waitFor(() => screen.getByText(/Recording/));

  // The initial subscription, from handleStart -- always FINE.
  await waitFor(() => expect(Location.watchPositionAsync).toHaveBeenCalledTimes(1));
  expect(Location.watchPositionAsync.mock.calls[0][0]).toMatchObject({ distanceInterval: 5 });

  // MOTION_WINDOW_SIZE (8) consecutive at-rest readings (x=y=0, z=1g --
  // deviation from 1g is exactly 0) -- the exact number needed to cross
  // App.js's stillness threshold, no more.
  for (let i = 0; i < 8; i++) {
    await act(async () => {
      accelerometerCallback({ x: 0, y: 0, z: 1 });
    });
  }
  await waitFor(() => expect(Location.watchPositionAsync).toHaveBeenCalledTimes(2));
  expect(Location.watchPositionAsync.mock.calls[1][0]).toMatchObject({ distanceInterval: 15 });

  // One real-motion reading (magnitude ~1.22g, comfortably over the
  // threshold) switches back to FINE immediately -- no waiting for a
  // window of motion samples the way switching TO coarse required.
  await act(async () => {
    accelerometerCallback({ x: 0.5, y: 0.5, z: 1 });
  });
  await waitFor(() => expect(Location.watchPositionAsync).toHaveBeenCalledTimes(3));
  expect(Location.watchPositionAsync.mock.calls[2][0]).toMatchObject({ distanceInterval: 5 });
});

test('ending a run captures a map snapshot, and replaying that run shows it instead of a live map', async () => {
  render(<App />);
  await waitFor(() => screen.getByText(/Selected: #1/));

  fireEvent.press(screen.getByText('Start Run'));
  await waitFor(() => screen.getByText(/Recording/));

  await act(async () => {
    fireEvent.press(screen.getByText('End Run'));
  });
  await waitFor(() => expect(saveRun).toHaveBeenCalledTimes(1));
  const savedRun = saveRun.mock.calls[0][0];

  // §2's "offline map tiles for the last route" -- takeSnapshot (a real
  // react-native-maps API, mocked in __mocks__/react-native-maps.js) is
  // called while the map is still on screen, then copied out of its temp
  // location into permanent storage; the saved run carries that permanent
  // path (this mock's Directory/File .uri join), not the temp one
  // takeSnapshot returned.
  expect(MapView.__takeSnapshotMock).toHaveBeenCalledTimes(1);
  expect(__copyMock).toHaveBeenCalledTimes(1);
  expect(savedRun.mapSnapshotUri).toBe(`file:///mock-documents/run-snapshots/${savedRun.runUuid}.png`);

  // Replaying that run should show the saved snapshot, not spin up a live
  // MapView -- getRuns() is stubbed here to hand back exactly the run just
  // "saved" (these mocks aren't backed by a real DB), matching how a real
  // getRuns() would return this same row, mapSnapshotUri included.
  getRuns.mockResolvedValueOnce([savedRun]);
  fireEvent.press(screen.getByText('New Route')); // handleReset -- back to a fresh 'ready' state
  await waitFor(() => screen.getByText(/Selected: #1/));
  fireEvent.press(screen.getByText('Past Runs'));
  await waitFor(() => screen.getByText('Delete All My Data'));
  fireEvent.press(screen.getByText(new Date(savedRun.startedAt).toLocaleString()));

  await waitFor(() => screen.getByTestId('run-snapshot-image'));
  expect(screen.queryAllByTestId('mock-map-view')).toHaveLength(0);
  expect(screen.queryAllByTestId('mock-polyline')).toHaveLength(0);
});

test('Delete All My Data also deletes each run\'s snapshot file, not just the database rows', async () => {
  // deleteAllRuns is a jest.fn() shared across this whole file -- the
  // earlier plain "Delete All My Data" test already left one call on it,
  // and restoreAllMocks (afterEach, above) doesn't clear a plain jest.fn()'s
  // call history, only jest.spyOn mocks -- same reasoning as
  // Location.watchPositionAsync.mockClear() in the adaptive-sampling test.
  deleteAllRuns.mockClear();
  jest.spyOn(Alert, 'alert').mockImplementation((title, message, buttons) => {
    buttons.find((b) => b.text === 'Delete').onPress();
  });
  // A snapshot file with no backing DB row is an orphan nothing will ever
  // clean up again (see deleteAllData's comment) -- so this has to happen
  // for every run's snapshot, not just be a no-op when there's nothing to
  // delete (already implicitly covered by the plain "Delete All My Data"
  // test above, where pastRuns is empty).
  getRuns.mockResolvedValueOnce([
    { id: 1, runUuid: 'a', startedAt: new Date().toISOString(), mapSnapshotUri: 'file:///mock-documents/run-snapshots/a.png' },
    { id: 2, runUuid: 'b', startedAt: new Date().toISOString(), mapSnapshotUri: null }, // no snapshot -- must not blow up
  ]);

  render(<App />);
  await waitFor(() => screen.getByText(/Selected: #1/));
  fireEvent.press(screen.getByText('Past Runs'));
  await waitFor(() => screen.getByText('Delete All My Data'));
  fireEvent.press(screen.getByText('Delete All My Data'));

  await waitFor(() => expect(deleteAllRuns).toHaveBeenCalledTimes(1));
  expect(__deleteMock).toHaveBeenCalledTimes(1); // only for run 'a' -- run 'b' had nothing to delete
  expect(__deleteMock.mock.calls[0][0].uri).toBe('file:///mock-documents/run-snapshots/a.png');
});
