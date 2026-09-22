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
import * as ReactNative from 'react-native';
import { Alert } from 'react-native';
import * as Location from 'expo-location';
import * as SafeAreaContext from 'react-native-safe-area-context';

// Flattens an RN style prop (a value, or an array of values/falsy
// entries/nested arrays -- exactly what style={[a, b && c]} produces)
// into one plain object, the way the real native renderer does
// internally but Jest's test renderer doesn't do for you.
function flattenStyle(style) {
  return Object.assign({}, ...[style].flat(Infinity).filter(Boolean));
}

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

import App, { fetchWithTimeout, FETCH_TIMEOUT_MS, getCurrentPositionWithTimeout, ErrorBoundary } from '../App';
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

test('each route request sends a fresh random bearing, so repeat requests actually explore different directions', async () => {
  // Found on a direct ask: the app always returned the exact same routes
  // for the same start point and distance. Root cause: route_api.py's
  // /route already accepts an optional bearing (the starting compass
  // direction its candidates fan out from) and defaults to due north if
  // omitted -- this app was always omitting it, so the same fixed set of
  // directions got explored on every single request. A real fix has to
  // send a genuinely different bearing each time, not just "a" bearing.
  render(<App />);
  await waitFor(() => screen.getByText(/Selected: #1/));
  fireEvent.press(screen.getByText('Regenerate'));
  await waitFor(() => screen.getByText(/Selected: #1/));

  const routeCalls = global.fetch.mock.calls.filter(([url]) => url.endsWith('/route'));
  expect(routeCalls).toHaveLength(2);
  const bearings = routeCalls.map(([, opts]) => JSON.parse(opts.body).bearing);

  for (const bearing of bearings) {
    expect(typeof bearing).toBe('number');
    expect(bearing).toBeGreaterThanOrEqual(0);
    expect(bearing).toBeLessThan(360);
  }
  // Two independently-random bearings in [0, 360) landing on the exact
  // same float is astronomically unlikely -- a real, meaningful check
  // that this isn't just "some fixed non-zero constant" masquerading as
  // random, not a flaky test.
  expect(bearings[0]).not.toBe(bearings[1]);
});

test('a brand-new install (permission never asked before) sees an in-app explanation before the OS dialog, not a surprise system prompt on launch', async () => {
  // Found on a sweep: the mount effect used to call generateRoute()
  // (whose own first move is requestForegroundPermissionsAsync)
  // completely unconditionally -- so a brand-new user could see the OS
  // location dialog the instant the app opened, before the app had shown
  // them anything explaining why. 'undetermined' is expo-location's own
  // status value for "the system dialog has never been shown to this
  // user" -- distinct from 'granted'/'denied', both of which mean it's
  // already been resolved once.
  Location.getForegroundPermissionsAsync.mockResolvedValueOnce({ status: 'undetermined' });

  render(<App />);

  await waitFor(() => screen.getByText('Before we start'));
  // No network request yet -- the whole point is that permission (and
  // therefore route generation) doesn't happen until the user
  // acknowledges the explanation.
  expect(global.fetch).not.toHaveBeenCalled();
  expect(screen.queryByText(/Selected: #1/)).toBeNull();

  fireEvent.press(screen.getByText('Continue'));

  await waitFor(() => screen.getByText(/Selected: #1/));
  expect(Location.requestForegroundPermissionsAsync).toHaveBeenCalled();
});

test('an already-resolved permission (granted or denied in a prior session) skips the primer entirely', async () => {
  // The default mock everywhere else in this file already resolves
  // 'granted' and every other test proceeds straight to route generation
  // with no primer screen ever appearing -- this test makes that
  // "already resolved -> skip priming" behavior explicit and named,
  // rather than leaving it as an implicit side effect of every other
  // test's shared default mock.
  render(<App />);
  await waitFor(() => screen.getByText(/Selected: #1/));
  expect(screen.queryByText('Before we start')).toBeNull();
});

test('a non-JSON error response (e.g. a real 429 from flask-limiter) shows a clean message, not a raw parse error', async () => {
  // Confirmed against the real running server, not assumed: flask-limiter's
  // default 429 body is Werkzeug's plain HTML error page, not JSON --
  // response.json() on it throws a SyntaxError. Before generateRoute's
  // `.catch(() => ({}))` fix, that SyntaxError propagated all the way to
  // showErrorAlert's `(${String(err.message || err))})` and a user would
  // see something like "Unexpected token '<'..." instead of the intended
  // "route_api returned 429".
  jest.spyOn(Alert, 'alert').mockImplementation(() => {});
  global.fetch = jest.fn(() =>
    Promise.resolve({
      ok: false,
      status: 429,
      json: () => Promise.reject(new SyntaxError("Unexpected token '<', \"<!doctype \"... is not valid JSON")),
    })
  );

  render(<App />);

  await waitFor(() => expect(Alert.alert).toHaveBeenCalled());
  const [title, message] = Alert.alert.mock.calls[0];
  expect(title).toBe('Could not generate a route');
  expect(message).toBe('Check your connection and try again. (route_api returned 429)');
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

test('dark mode uses a brighter accent color for text/borders sitting directly on the background', async () => {
  // Confirms the actual bug this was built to avoid, not just "some
  // color changed": #2E7D32 (light mode's accentForeground) as text on
  // this app's dark background computes to ~3.45:1 contrast, below WCAG
  // AA's 4.5:1 minimum for normal text -- App.js's DARK_COLORS uses
  // #66BB6A instead specifically because it clears that bar (~8:1). This
  // asserts the actual rendered color is the dark-mode value, not merely
  // that it differs from light mode.
  jest.spyOn(ReactNative, 'useColorScheme').mockReturnValue('dark');

  render(<App />);
  await waitFor(() => screen.getByText(/Selected: #1/));

  // "Regenerate" is a secondary-variant AppButton -- its text color IS
  // accentForeground (the outline variant, sitting directly on the page
  // background), unlike primary/destructive buttons whose text stays
  // white in both themes (a solid filled surface, contrast is internal
  // to the button regardless of page theme -- see App.js's own comment
  // on why only accentForeground needed to change per theme).
  const regenerateText = screen.getByText('Regenerate');
  expect(flattenStyle(regenerateText.props.style).color).toBe('#66BB6A');

  // The primary button's text, by contrast, correctly stays white --
  // confirms this test isn't just catching "everything changed color."
  const startRunText = screen.getByText('Start Run');
  expect(flattenStyle(startRunText.props.style).color).toBe('#ffffff');
});

test('picking a distance preset regenerates the route requesting that exact distance', async () => {
  render(<App />);
  await waitFor(() => screen.getByText(/Selected: #1/));

  // The default 5km request from the auto-generate-on-launch effect.
  const initialRouteCalls = global.fetch.mock.calls.filter(([url]) => url.endsWith('/route'));
  expect(initialRouteCalls).toHaveLength(1);
  expect(JSON.parse(initialRouteCalls[0][1].body)).toMatchObject({ distance: 5000 });

  fireEvent.press(screen.getByText('8km'));

  // mockRouteResponse's candidates are the same regardless of the
  // requested distance -- what this test cares about is the REQUEST,
  // not a different response, so waiting for the route to re-settle
  // (back to "Selected: #1") is enough before checking what was asked for.
  await waitFor(() => screen.getByText(/Selected: #1/));
  const routeCalls = global.fetch.mock.calls.filter(([url]) => url.endsWith('/route'));
  expect(routeCalls).toHaveLength(2);
  expect(JSON.parse(routeCalls[1][1].body)).toMatchObject({ distance: 8000 });

  // Picking a different preset afterward requests THAT distance, not the
  // original default -- confirms the choice is sticky (targetDistanceM
  // state), not reset back to 5km on every regenerate.
  fireEvent.press(screen.getByText('3km'));
  await waitFor(() => screen.getByText(/Selected: #1/));
  const finalCalls = global.fetch.mock.calls.filter(([url]) => url.endsWith('/route'));
  expect(finalCalls).toHaveLength(3);
  expect(JSON.parse(finalCalls[2][1].body)).toMatchObject({ distance: 3000 });
});

describe('custom distance input', () => {
  // Found on a direct ask: the four preset chips don't cover every
  // distance someone might actually want (a race taper, matching
  // yesterday's exact route) -- this is the escape hatch for that,
  // validated client-side against the same bounds route_api.py itself
  // enforces (MIN/MAX_CUSTOM_DISTANCE_M), so a bad value gets an
  // immediate, specific in-app message instead of a round trip just to
  // learn the same thing from a 400.

  test('the input is hidden until "Custom" is tapped, then submitting a valid distance requests exactly that', async () => {
    render(<App />);
    await waitFor(() => screen.getByText(/Selected: #1/));

    expect(screen.queryByPlaceholderText('Distance in km')).toBeNull();

    fireEvent.press(screen.getByText('Custom'));
    const input = screen.getByPlaceholderText('Distance in km');
    fireEvent.changeText(input, '7.5');
    fireEvent.press(screen.getByText('Go'));

    await waitFor(() => screen.getByText(/Selected: #1/));
    const routeCalls = global.fetch.mock.calls.filter(([url]) => url.endsWith('/route'));
    expect(routeCalls).toHaveLength(2); // the initial auto-generate, then this one
    expect(JSON.parse(routeCalls[1][1].body)).toMatchObject({ distance: 7500 });

    // Submitting successfully closes the input and clears it -- it
    // shouldn't linger open with stale text after a successful request.
    expect(screen.queryByPlaceholderText('Distance in km')).toBeNull();
  });

  test('an empty or non-numeric entry is rejected with a clear message, and never reaches the network', async () => {
    jest.spyOn(Alert, 'alert').mockImplementation(() => {});
    render(<App />);
    await waitFor(() => screen.getByText(/Selected: #1/));
    global.fetch.mockClear();

    fireEvent.press(screen.getByText('Custom'));
    fireEvent.press(screen.getByText('Go')); // nothing typed yet

    expect(Alert.alert).toHaveBeenCalledWith('Enter a distance', 'Type a number of kilometers, e.g. 7.5.');
    expect(global.fetch).not.toHaveBeenCalled();
    // The input must still be open after a rejected attempt -- so the
    // user can just fix their entry, not have to reopen it from scratch.
    expect(screen.getByPlaceholderText('Distance in km')).toBeTruthy();
  });

  test('a distance outside the valid range is rejected with the real bounds, not a generic message', async () => {
    jest.spyOn(Alert, 'alert').mockImplementation(() => {});
    render(<App />);
    await waitFor(() => screen.getByText(/Selected: #1/));
    global.fetch.mockClear();

    fireEvent.press(screen.getByText('Custom'));
    fireEvent.changeText(screen.getByPlaceholderText('Distance in km'), '50');
    fireEvent.press(screen.getByText('Go'));

    expect(Alert.alert).toHaveBeenCalledWith('Distance out of range', 'Enter a distance between 0.5 and 30 km.');
    expect(global.fetch).not.toHaveBeenCalled();
  });
});

test('distance chips are real accessible buttons, and accessibilityState tracks which one is selected', async () => {
  // Formalizes the accessibility pass -- these were plain TouchableOpacity
  // wrappers with no accessibilityRole/Label/State before, meaning a
  // screen reader had no way to know they were buttons, what they did, or
  // which one was currently active (the selected look was purely visual,
  // a filled background). getByRole with a `selected` matcher only finds
  // an element if accessibilityRole/State are both actually set correctly
  // -- this fails if either prop is missing or wrong, not just if the
  // button doesn't exist at all.
  render(<App />);
  await waitFor(() => screen.getByText(/Selected: #1/));

  expect(screen.getByRole('button', { name: '5 kilometers', selected: true })).toBeTruthy();
  expect(screen.getByRole('button', { name: '3 kilometers', selected: false })).toBeTruthy();
  expect(screen.getByRole('button', { name: '8 kilometers', selected: false })).toBeTruthy();
  expect(screen.getByRole('button', { name: '10 kilometers', selected: false })).toBeTruthy();

  fireEvent.press(screen.getByRole('button', { name: '8 kilometers' }));
  await waitFor(() => screen.getByText(/Selected: #1/));

  // Selection moved with the actual choice -- 8km is now selected, 5km
  // (the previous choice) no longer is.
  expect(screen.getByRole('button', { name: '8 kilometers', selected: true })).toBeTruthy();
  expect(screen.getByRole('button', { name: '5 kilometers', selected: false })).toBeTruthy();
});

test('real device safe-area insets are actually used in layout, not just defaulted to zero', async () => {
  // Distinctive, non-zero, realistic values (a Dynamic-Island-class top
  // inset, a home-indicator-class bottom inset) -- picked so this can't
  // pass by coincidence the way asserting against 0 or the mock's own
  // default could. __mocks__/react-native-safe-area-context.js's default
  // (all zeros) is what every other test in this file implicitly runs
  // against; this one overrides it for real.
  // __setMockInsets, not jest.spyOn/reassigning useSafeAreaInsets itself
  // -- App.js's `import { useSafeAreaInsets }` destructures the function
  // once at module load (confirmed empirically: neither approach affected
  // what App.js actually called), so the mock's return value has to be
  // configurable through a variable the already-bound function reads from
  // on every call instead. Restored in finally since this isn't a
  // jest.spyOn mock afterEach's restoreAllMocks would clean up on its own.
  SafeAreaContext.__setMockInsets({ top: 59, bottom: 34, left: 0, right: 0 });

  try {
    render(<App />);
    await waitFor(() => screen.getByText(/Selected: #1/));

    fireEvent.press(screen.getByText('Past Runs'));
    await waitFor(() => screen.getByText('Delete All My Data'));

    // Past Runs' header title Text -- unambiguous here (the main screen's
    // "Past Runs" button no longer exists, that whole screen unmounted on
    // navigation), so this is definitely historyHeader's child, not the
    // button from before.
    const headerTitle = screen.getByText('Past Runs');
    expect(flattenStyle(headerTitle.parent.parent.props.style).paddingTop).toBe(59 + 16); // insets.top + 16
  } finally {
    SafeAreaContext.__setMockInsets({ top: 0, right: 0, bottom: 0, left: 0 });
  }
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

test('starting a run shows live Distance/Time/Pace stats that update as GPS points come in', async () => {
  // Found on a direct ask: the live stats (not the controls) should be
  // the focal point during a run -- redesigned from one combined
  // "Recording -- 0.63 km · 0:05" line into three separate large stat
  // values (matching the post-run summary's own layout), and pace is
  // now computed live too, not just after End Run.
  render(<App />);
  await waitFor(() => screen.getByText(/Selected: #1/));

  fireEvent.press(screen.getByText('Start Run'));
  await waitFor(() => screen.getByText('● Recording'));
  // No GPS points yet -- a single-point trace has no distance to sum.
  expect(screen.getByText('0.00 km')).toBeTruthy();

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

  await waitFor(() => expect(screen.queryByText('0.00 km')).toBeNull());
  expect(screen.getByText(/0\.6\d km/)).toBeTruthy();
  // A real, non-placeholder pace once there's real distance and elapsed
  // time -- not the "--:-- /km" zero-distance placeholder anymore.
  expect(screen.queryByText('--:-- /km')).toBeNull();
  expect(screen.getByText(/^\d+:\d{2} \/km$/)).toBeTruthy();

  // Pause freezes the label (Paused, not Recording) but keeps showing the
  // same accumulated distance -- doesn't reset to 0.
  fireEvent.press(screen.getByText('Pause'));
  await waitFor(() => screen.getByText('⏸ Paused'));
  expect(screen.getByText(/0\.6\d km/)).toBeTruthy();
});

test('backgrounding the app mid-run auto-pauses it, the same as tapping Pause', async () => {
  // jest-expo's default AppState mock is an inert jest.fn() -- calling
  // addEventListener returns a { remove } object but never actually
  // invokes the registered listener, so there's no built-in way to
  // simulate a real background transition. AppState is destructured as
  // an *object* import (`import { AppState } from 'react-native'`),
  // unlike useSafeAreaInsets's function import elsewhere in this file --
  // App.js calls `AppState.addEventListener(...)` as a property access on
  // that shared object every time, not a function reference captured once
  // at import time, so spying on the method (rather than reassigning the
  // whole export, which empirically doesn't reach App.js's binding -- see
  // the safe-area-insets mock) does reach the real call. Verified
  // empirically before relying on it, same discipline as that mock.
  let appStateCallback = null;
  jest.spyOn(ReactNative.AppState, 'addEventListener').mockImplementation((event, cb) => {
    if (event === 'change') appStateCallback = cb;
    return { remove: jest.fn() };
  });

  render(<App />);
  await waitFor(() => screen.getByText(/Selected: #1/));

  fireEvent.press(screen.getByText('Start Run'));
  await waitFor(() => screen.getByText('● Recording'));
  expect(appStateCallback).toBeTruthy();

  await act(async () => {
    watchCallback({ coords: { latitude: 43.4643, longitude: -80.5204 }, timestamp: Date.now() });
  });
  await act(async () => {
    watchCallback({ coords: { latitude: 43.47, longitude: -80.5204 }, timestamp: Date.now() });
  });
  await waitFor(() => screen.getByText(/0\.6\d km/));

  // Simulate iOS backgrounding the app (no UIBackgroundMode: location, so
  // JS suspends almost immediately) -- should auto-pause exactly like a
  // manual Pause tap: same "Paused" label, same frozen distance, and the
  // GPS watch subscription actually torn down (handlePause's real effect,
  // not just a UI label change) so it isn't silently still "running" with
  // stale state.
  await act(async () => {
    appStateCallback('background');
  });
  await waitFor(() => screen.getByText('⏸ Paused'));
  expect(screen.getByText(/0\.6\d km/)).toBeTruthy();

  // Coming back to the foreground must NOT auto-resume -- resuming a run
  // silently would defeat the whole point (the user should consciously
  // tap Resume, since real elapsed background time already happened).
  await act(async () => {
    appStateCallback('active');
  });
  expect(screen.getByText('⏸ Paused')).toBeTruthy();
  expect(screen.getByText(/0\.6\d km/)).toBeTruthy();
});

test('tapping Start Run does not enter Recording if location permission was revoked since the route was generated', async () => {
  // Found on a sweep: handleStart used to flip sessionState to 'running'
  // BEFORE confirming startWatching() actually succeeded. A real iOS
  // flow -- granting "Allow Once" at route-generation time, then having
  // that grant revert across even a brief background/foreground cycle --
  // left the app showing a live "Recording" screen with a working
  // Pause/End Run, but zero GPS updates ever arriving (no watch
  // subscription actually exists), silently saving a bogus
  // zero-distance run on End Run. getForegroundPermissionsAsync (checked
  // in startWatching, distinct from the requestForegroundPermissionsAsync
  // prompt in generateRoute) is what actually reflects that reversion.
  jest.spyOn(Alert, 'alert').mockImplementation(() => {});

  render(<App />);
  await waitFor(() => screen.getByText(/Selected: #1/));

  // Queued only now, not before render -- the mount effect's own
  // permission-priming check (getForegroundPermissionsAsync, added by
  // the same sweep as this test) already consumes one call before this
  // point; queuing 'denied' any earlier would apply to THAT call instead
  // of the one this test actually means to target (startWatching's, when
  // Start Run is pressed below).
  Location.getForegroundPermissionsAsync.mockResolvedValueOnce({ status: 'denied' });
  fireEvent.press(screen.getByText('Start Run'));

  await waitFor(() =>
    expect(Alert.alert).toHaveBeenCalledWith(
      'Location permission required',
      'Pathfinder Run needs your location to track this run.'
    )
  );
  // Still on the 'ready' screen -- Start Run is still there, and there's
  // no Recording/Paused indicator anywhere claiming a run is underway.
  expect(screen.getByText('Start Run')).toBeTruthy();
  expect(screen.queryByText(/Recording/)).toBeNull();
});

test('resuming a paused run does not enter Recording (or leave the live clock running) if location permission was lost while paused', async () => {
  // Same root cause as the Start Run case above, for handleResume: it
  // stamps runTimingRef.segmentStartedAt (the "resume, start counting
  // active time again" marker) unconditionally, before knowing whether
  // startWatching() will actually succeed. Without reverting that stamp
  // on failure, liveDurationMs -- computed from activeMs + "now minus
  // segmentStartedAt" whenever segmentStartedAt is set, independent of
  // sessionState -- would keep climbing on wall-clock time while the
  // screen still reads "Paused", the exact bug the AppState-backgrounding
  // fix elsewhere in this file exists to prevent, reintroduced through a
  // different door.
  render(<App />);
  await waitFor(() => screen.getByText(/Selected: #1/));

  fireEvent.press(screen.getByText('Start Run'));
  await waitFor(() => screen.getByText('● Recording'));
  fireEvent.press(screen.getByText('Pause'));
  await waitFor(() => screen.getByText('⏸ Paused'));

  Location.getForegroundPermissionsAsync.mockResolvedValueOnce({ status: 'denied' });
  jest.spyOn(Alert, 'alert').mockImplementation(() => {});
  // The Time stat is now its own standalone element (not mixed into a
  // combined "Paused -- ... 0:05" string) -- bare "M:SS" with nothing
  // else uniquely identifies it among the three live stats (Distance
  // always has " km", Pace always has " /km").
  const pausedDurationText = screen.getByText(/^\d+:\d{2}$/).children.join('');

  await act(async () => {
    fireEvent.press(screen.getByText('Resume'));
  });

  await waitFor(() => expect(Alert.alert).toHaveBeenCalled());
  expect(screen.queryByText('● Recording')).toBeNull();
  expect(screen.getByText('Resume')).toBeTruthy();

  // The displayed duration must still be frozen, not silently climbing --
  // this is the actual regression this test guards against, not just
  // "still says Paused".
  await new Promise((resolve) => setTimeout(resolve, 1100));
  expect(screen.getByText(/^\d+:\d{2}$/).children.join('')).toBe(pausedDurationText);
});

test("a failed save deletes the run's already-captured map snapshot, so it isn't orphaned on disk forever", async () => {
  // Found on a sweep: captureRunSnapshot writes a real PNG to disk before
  // saveRun is ever attempted. If saveRun then throws, nothing previously
  // referenced that file (no DB row was created), and deleteAllData's own
  // cleanup only ever walks mapSnapshotUri from rows that exist in the
  // DB -- so without this fix, a failed save left one PNG stranded with
  // no code path in the app able to find or remove it again.
  saveRun.mockClear();
  __deleteMock.mockClear();
  saveRun.mockRejectedValueOnce(new Error('disk full'));
  jest.spyOn(Alert, 'alert').mockImplementation(() => {});

  render(<App />);
  await waitFor(() => screen.getByText(/Selected: #1/));
  fireEvent.press(screen.getByText('Start Run'));
  await waitFor(() => screen.getByText(/Recording/));

  await act(async () => {
    fireEvent.press(screen.getByText('End Run'));
  });

  await waitFor(() =>
    expect(Alert.alert).toHaveBeenCalledWith('Could not save this run', "This run's data may be lost. (disk full)")
  );
  expect(__deleteMock).toHaveBeenCalledTimes(1);
  expect(__deleteMock.mock.calls[0][0].uri).toMatch(/^file:\/\/\/mock-documents\/run-snapshots\/.*\.png$/);
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

test('ending a run shows a post-run summary with real distance, time, and pace', async () => {
  // saveRun is a jest.fn() shared across this whole file -- see the
  // identical reasoning on Location.watchPositionAsync.mockClear() above.
  saveRun.mockClear();
  // Researched running-app UX conventions before building this screen --
  // this test formalizes the actual behavior: finishing a run used to
  // show a bare "New Route" button with zero feedback on what was just
  // run. Two real GPS pings (same ~630m-apart pair as the Recording
  // indicator test) so there's a real, deterministic distance to check,
  // not just "a summary rendered."
  render(<App />);
  await waitFor(() => screen.getByText(/Selected: #1/));
  fireEvent.press(screen.getByText('Start Run'));
  await waitFor(() => screen.getByText(/Recording/));

  await act(async () => {
    watchCallback({ coords: { latitude: 43.4643, longitude: -80.5204 }, timestamp: Date.now() });
  });
  await act(async () => {
    watchCallback({ coords: { latitude: 43.47, longitude: -80.5204 }, timestamp: Date.now() });
  });

  await act(async () => {
    fireEvent.press(screen.getByText('End Run'));
  });

  await waitFor(() => screen.getByText('Run complete 🎉'));
  expect(screen.getByText('0.63 km')).toBeTruthy(); // Distance stat
  expect(screen.getByText('Distance')).toBeTruthy();
  expect(screen.getByText('Time')).toBeTruthy();
  // Pace is timing-dependent (real elapsed wall-clock ms in a fast test),
  // so this checks it's a real "M:SS /km" value, not the zero-distance
  // placeholder or NaN -- the exact number isn't the point here,
  // formatPace's own unit tests (geometry.test.js) already cover the math.
  expect(screen.getByText(/^\d+:\d{2} \/km$/)).toBeTruthy();
  expect(screen.getByText('New Route')).toBeTruthy();
});

test('ending a run captures a map snapshot, and replaying that run shows it instead of a live map', async () => {
  // All shared jest.fn()s across this file -- see the earlier test's
  // identical comment on saveRun.
  saveRun.mockClear();
  MapView.__takeSnapshotMock.mockClear();
  __copyMock.mockClear();
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
  // deleteAllRuns and __deleteMock are jest.fn()s shared across this whole
  // file -- the earlier plain "Delete All My Data" test already left one
  // call on deleteAllRuns, and the failed-save test above left one on
  // __deleteMock, and restoreAllMocks (afterEach, above) doesn't clear a
  // plain jest.fn()'s call history, only jest.spyOn mocks -- same
  // reasoning as Location.watchPositionAsync.mockClear() in the
  // adaptive-sampling test.
  deleteAllRuns.mockClear();
  __deleteMock.mockClear();
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

test('a failed history load shows a distinct, retryable error -- not the ordinary "no runs yet" empty state', async () => {
  // Found on a sweep: a failed getRuns() (a real local SQLite read
  // failure) used to leave pastRuns at its initial [] with only an
  // Alert shown -- once dismissed, the render fell straight into the
  // ordinary "No runs saved yet" empty state, indistinguishable from
  // genuinely having zero history. For an app whose whole value is
  // durable local run history, that's an actively misleading message,
  // not just a missing nice-to-have.
  jest.spyOn(Alert, 'alert').mockImplementation(() => {});
  getRuns.mockRejectedValueOnce(new Error('database is locked'));

  render(<App />);
  await waitFor(() => screen.getByText(/Selected: #1/));
  fireEvent.press(screen.getByText('Past Runs'));

  await waitFor(() => expect(Alert.alert).toHaveBeenCalled());
  expect(screen.getByText(/Couldn't load your past runs/)).toBeTruthy();
  expect(screen.queryByText('No runs saved yet -- finish a run to see it here.')).toBeNull();

  // Retry must actually be able to succeed, not just redisplay the same
  // error state -- proves this is real recovery, not a dead-end button.
  getRuns.mockResolvedValueOnce([]);
  fireEvent.press(screen.getByText('Retry'));
  await waitFor(() => screen.getByText('No runs saved yet -- finish a run to see it here.'));
});

describe('fetchWithTimeout', () => {
  // Found on a sweep: none of this file's 5 fetch() calls had any
  // timeout -- plain fetch() never times out on its own in React Native,
  // so a hung connection (weak signal, a captive portal, a cellular
  // network that silently drops packets) left 3 of them stuck behind a
  // full-screen spinner with no cancel button forever, with no way out
  // short of force-quitting the app. Tested directly against the
  // exported helper, not through a full <App /> render + waitFor --
  // waitFor's own internal polling uses real timers, which fights
  // jest.useFakeTimers() in ways that would make this test fragile for
  // no real benefit; fetchWithTimeout has no React/component dependency
  // at all (confirmed: it only touches fetch/AbortController/setTimeout,
  // all plain JS globals), so it doesn't need one.
  afterEach(() => {
    jest.useRealTimers();
  });

  test('aborts and rejects with a clear message if the request never settles', async () => {
    jest.useFakeTimers();
    global.fetch = jest.fn(
      (url, options) =>
        new Promise((_resolve, reject) => {
          // Mirrors the real contract fetchWithTimeout depends on: real
          // fetch implementations reject with a DOMException named
          // 'AbortError' when their signal aborts -- a naive mock
          // returning a promise that just never settles would make this
          // test pass for the wrong reason (fetchWithTimeout awaiting
          // fetch() forever, same as the bug this fixes) instead of
          // actually exercising the timeout.
          options.signal.addEventListener('abort', () => {
            const err = new Error('Aborted');
            err.name = 'AbortError';
            reject(err);
          });
        })
    );

    const promise = fetchWithTimeout('http://example.com/route');
    const assertion = expect(promise).rejects.toThrow(`Request timed out after ${FETCH_TIMEOUT_MS / 1000}s`);
    await jest.advanceTimersByTimeAsync(FETCH_TIMEOUT_MS);
    await assertion;
  });

  test('resolves normally well within the timeout, and does not leave a stray timer behind', async () => {
    jest.useFakeTimers();
    global.fetch = jest.fn(() => Promise.resolve({ ok: true, status: 200 }));

    const response = await fetchWithTimeout('http://example.com/route');

    expect(response.ok).toBe(true);
    // If the setTimeout scheduled internally wasn't cleared on the
    // success path, it would still be pending here -- a real leak (it
    // would fire later and call an already-aborted controller's abort(),
    // harmlessly in this case, but a leaked timer per successful request
    // over a long session is still worth not having).
    expect(jest.getTimerCount()).toBe(0);
  });
});

describe('getCurrentPositionWithTimeout', () => {
  // Found on the same sweep as fetchWithTimeout, in the same two call
  // chains it already protects (generateRoute, handleReportClosure), one
  // step earlier: Location.getCurrentPositionAsync has no timeout option
  // at all (confirmed directly against expo-location's own LocationOptions
  // type -- not just assumed), so it could hang indefinitely on a weak/no
  // GPS fix, stuck behind a full-screen spinner with no cancel button.
  afterEach(() => {
    jest.useRealTimers();
  });

  test('rejects with a clear message if the GPS fix never arrives', async () => {
    jest.useFakeTimers();
    // A promise that never settles -- the actual failure mode being
    // guarded against (a real device stuck acquiring a fix), not just a
    // slow-but-eventually-resolving one.
    Location.getCurrentPositionAsync.mockReturnValueOnce(new Promise(() => {}));

    const promise = getCurrentPositionWithTimeout();
    const assertion = expect(promise).rejects.toThrow('Location request timed out after 15s');
    await jest.advanceTimersByTimeAsync(15000);
    await assertion;
  });

  test('resolves normally well within the timeout, and does not leave a stray timer behind', async () => {
    jest.useFakeTimers();
    Location.getCurrentPositionAsync.mockResolvedValueOnce({
      coords: { latitude: 43.4643, longitude: -80.5204 },
      timestamp: Date.now(),
    });

    const position = await getCurrentPositionWithTimeout();

    expect(position.coords.latitude).toBe(43.4643);
    // A first version of this fix used Promise.race with no cleanup at
    // all -- caught by Jest's own "a worker process has failed to exit
    // gracefully" warning after adding it, since the timer promise's
    // setTimeout kept firing 15s later regardless of which side of the
    // race won. This is what that regression would show up as here.
    expect(jest.getTimerCount()).toBe(0);
  });
});

describe('ErrorBoundary', () => {
  // Found on a sweep, directly motivated by this session's own real
  // "Property 'createStyles' doesn't exist" bug report: without a
  // top-level error boundary, ANY uncaught render-time exception anywhere
  // in the tree crashes the whole app to a blank screen in a production
  // build (no redbox, no crash reporting -- see deploy/README.md's
  // documented decision), with zero recovery short of force-quitting.
  test('catches a render error and shows a recoverable fallback screen', () => {
    // React logs the caught error to console.error by default (expected,
    // documented behavior of error boundaries, not a real test failure)
    // -- silenced so it doesn't clutter this test's real output.
    jest.spyOn(console, 'error').mockImplementation(() => {});

    function Bomb() {
      throw new Error('kaboom');
    }
    const { getByText } = render(
      <ErrorBoundary>
        <Bomb />
      </ErrorBoundary>
    );

    expect(getByText('Something went wrong')).toBeTruthy();
    // The real thrown message is shown, not a generic placeholder --
    // this app has no crash reporting, so this alert-equivalent text is
    // the only diagnostic trail a user could ever relay back (the exact
    // reasoning showErrorAlert elsewhere in this file already applies).
    expect(getByText('kaboom')).toBeTruthy();
  });

  test('renders children normally when nothing throws', () => {
    const { getByText, queryByText } = render(
      <ErrorBoundary>
        <ReactNative.Text>all fine</ReactNative.Text>
      </ErrorBoundary>
    );
    expect(getByText('all fine')).toBeTruthy();
    expect(queryByText('Something went wrong')).toBeNull();
  });

  test('Try Again actually recovers once the underlying problem is gone, not just resets to the same crash', () => {
    jest.spyOn(console, 'error').mockImplementation(() => {});
    // A real transient failure this simulates: e.g. a null ref that's
    // populated by the time the user retries. Proving recovery needs a
    // component that stops throwing on a later render, not one that
    // always throws (which would only prove the boundary can catch an
    // error, not that "Try Again" leads anywhere better).
    let shouldThrow = true;
    function Flaky() {
      if (shouldThrow) throw new Error('temporary glitch');
      return <ReactNative.Text>recovered</ReactNative.Text>;
    }

    const { getByText, queryByText } = render(
      <ErrorBoundary>
        <Flaky />
      </ErrorBoundary>
    );
    expect(getByText('Something went wrong')).toBeTruthy();

    shouldThrow = false;
    fireEvent.press(getByText('Try Again'));

    expect(getByText('recovered')).toBeTruthy();
    expect(queryByText('Something went wrong')).toBeNull();
  });
});
