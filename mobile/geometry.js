// Pure, zero-dependency geometry/formatting functions, extracted out of
// App.js (originally defined there) specifically so they're testable in
// isolation. Importing App.js itself pulls in react-native-maps and
// expo-sqlite (via ./db) at module load time -- both native modules that
// can't load outside a real native runtime, which made App.js untestable
// with plain Jest (TurboModuleRegistry.getEnforcing throws immediately on
// import, before any test even runs). Rather than mock every native
// dependency App.js happens to pull in, this file holds only the logic
// that never touches React Native or a native module at all -- the
// correct fix, not a testing workaround. See __tests__/geometry.test.js
// (mobile-side counterpart to scripts/tests/test_geometry.py).
//
// No behavior changes from moving this code -- App.js now imports these
// from here instead of defining them locally.
//
// mergeRunHistory, below, isn't geometry, but lives here for the same
// reason: it's pure (no fetch, no SQLite), previously inline in App.js's
// openHistory where it wasn't unit-testable on its own.

// Local flat-plane projection centered on `origin`, in meters. Accurate
// enough at the scale this is used for (a deviation check over tens to a
// few hundred meters) -- not attempting true great-circle math for a
// difference this small.
export function projectToLocalMeters(origin, point) {
  const M_PER_DEG_LAT = 111320;
  const mPerDegLon = M_PER_DEG_LAT * Math.cos((origin.latitude * Math.PI) / 180);
  return {
    x: (point.longitude - origin.longitude) * mPerDegLon,
    y: (point.latitude - origin.latitude) * M_PER_DEG_LAT,
  };
}

// Shortest distance from point p to segment a-b, all in the same local xy
// plane (meters).
export function pointToSegmentDistance(p, a, b) {
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
export function distanceToRouteMeters(point, routeCoords) {
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

// True great-circle distance (meters) between two lat/lon points -- unlike
// the local flat-plane projection above (fine for a single deviation check
// over tens/hundreds of meters), a run's full trace can span kilometers,
// so this uses the standard haversine formula rather than a local
// approximation that could drift over that range.
export function haversineMeters(a, b) {
  const R = 6371000;
  const toRad = (deg) => (deg * Math.PI) / 180;
  const dLat = toRad(b.latitude - a.latitude);
  const dLon = toRad(b.longitude - a.longitude);
  const lat1 = toRad(a.latitude);
  const lat2 = toRad(b.latitude);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

// Sum of consecutive point-to-point distances along a captured trace --
// the run's actual covered distance, computed from what was really
// walked/run, not just re-reported as the target distance.
export function traceDistanceMeters(trace) {
  let total = 0;
  for (let i = 0; i < trace.length - 1; i++) {
    total += haversineMeters(trace[i], trace[i + 1]);
  }
  return total;
}

export function formatDuration(ms) {
  const totalSeconds = Math.round(ms / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const pad = (n) => String(n).padStart(2, '0');
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(seconds)}` : `${minutes}:${pad(seconds)}`;
}

export function formatDistance(meters) {
  return `${(meters / 1000).toFixed(2)} km`;
}

// Combines a device's local run history with its server-synced copy for
// display (App.js's openHistory) -- local is always authoritative and
// kept as-is; a server run only gets added if its runUuid isn't already
// present locally (sync only ever pushes local -> server today, so this
// normally adds nothing, but see openHistory's own comment for the
// narrower case it does cover, and why this is still worth having).
// Returns a new array, most-recent-first; does not mutate either input.
export function mergeRunHistory(localRuns, serverRuns) {
  const localUuids = new Set(localRuns.map((r) => r.runUuid).filter(Boolean));
  const serverOnly = serverRuns.filter((r) => !localUuids.has(r.runUuid));
  return [...localRuns, ...serverOnly].sort(
    (a, b) => new Date(b.startedAt) - new Date(a.startedAt)
  );
}
