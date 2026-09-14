/**
 * Unit tests for App.js's pure, zero-dependency geometry/formatting
 * functions -- the mobile-side counterpart to
 * scripts/tests/test_geometry.py on the Python side, same rationale
 * (deterministic logic worth protecting with real tests, not just manual
 * re-verification). Expected values are hand-derived from the same
 * well-known spherical/flat-plane facts these functions implement, not
 * reverse-engineered from watching the implementation run -- see each
 * test's comment for the derivation.
 */
import {
  distanceToRouteMeters,
  formatDistance,
  formatDuration,
  haversineMeters,
  mergeRunHistory,
  pointToSegmentDistance,
  projectToLocalMeters,
  traceDistanceMeters,
} from '../geometry';

const EARTH_RADIUS_M = 6371000;

// --- haversineMeters ------------------------------------------------------

test('haversineMeters: identical points are zero distance apart', () => {
  const p = { latitude: 43.4643, longitude: -80.5204 };
  expect(haversineMeters(p, p)).toBe(0);
});

test('haversineMeters: pure latitude difference collapses to R * delta_lat_rad exactly', () => {
  // For any two points sharing a longitude, haversine's h term reduces to
  // sin(dLat/2)^2 (the dLon term vanishes), and 2*R*asin(sin(dLat/2)) is
  // exactly R*dLat_rad for any dLat within +-180 degrees -- true here.
  // This holds regardless of the actual latitude value, not just at the
  // equator, so this uses the project's own recurring test point rather
  // than a special-cased 0,0.
  const a = { latitude: 43.4643, longitude: -80.5204 };
  const deltaDeg = 0.01;
  const b = { latitude: a.latitude + deltaDeg, longitude: a.longitude };
  const expected = EARTH_RADIUS_M * (deltaDeg * Math.PI) / 180;
  expect(haversineMeters(a, b)).toBeCloseTo(expected, 3);
});

test('haversineMeters: due-east along the equator collapses to R * delta_lon_rad exactly', () => {
  // At the equator, cos(lat1)=cos(lat2)=1 and dLat=0, so h=sin(dLon/2)^2
  // and 2*R*asin(sin(dLon/2)) = R*dLon_rad exactly, same identity as
  // above but for longitude specifically at lat=0.
  const a = { latitude: 0, longitude: 0 };
  const b = { latitude: 0, longitude: 1 };
  const expected = EARTH_RADIUS_M * Math.PI / 180;
  expect(haversineMeters(a, b)).toBeCloseTo(expected, 3);
});

// --- traceDistanceMeters ----------------------------------------------------

test('traceDistanceMeters: empty or single-point trace is zero', () => {
  expect(traceDistanceMeters([])).toBe(0);
  expect(traceDistanceMeters([{ latitude: 0, longitude: 0 }])).toBe(0);
});

test('traceDistanceMeters: two points equals haversineMeters of that pair', () => {
  const a = { latitude: 43.4643, longitude: -80.5204 };
  const b = { latitude: 43.4700, longitude: -80.5100 };
  expect(traceDistanceMeters([a, b])).toBe(haversineMeters(a, b));
});

test('traceDistanceMeters: three points is the sum of both consecutive legs', () => {
  const a = { latitude: 43.4643, longitude: -80.5204 };
  const b = { latitude: 43.4700, longitude: -80.5100 };
  const c = { latitude: 43.4750, longitude: -80.5050 };
  const expected = haversineMeters(a, b) + haversineMeters(b, c);
  expect(traceDistanceMeters([a, b, c])).toBeCloseTo(expected, 9);
});

// --- projectToLocalMeters ---------------------------------------------------

test('projectToLocalMeters: a point projected against itself is the origin', () => {
  const origin = { latitude: 43.4643, longitude: -80.5204 };
  expect(projectToLocalMeters(origin, origin)).toEqual({ x: 0, y: 0 });
});

test('projectToLocalMeters: at the equator, lon and lat degrees scale identically', () => {
  // cos(0) = 1 exactly, so mPerDegLon == M_PER_DEG_LAT (111320) at the
  // equator -- a 0.001-degree offset in either direction should produce
  // the same magnitude in meters.
  const origin = { latitude: 0, longitude: 0 };
  const point = { latitude: 0.001, longitude: 0.001 };
  const result = projectToLocalMeters(origin, point);
  expect(result.x).toBeCloseTo(result.y, 9);
  expect(result.x).toBeCloseTo(0.001 * 111320, 6); // = 111.32
});

test('projectToLocalMeters: at latitude 60, longitude degrees are scaled by exactly cos(60)=0.5', () => {
  const M_PER_DEG_LAT = 111320;
  const origin = { latitude: 60, longitude: 0 };
  const point = { latitude: 60, longitude: 0.01 };
  const result = projectToLocalMeters(origin, point);
  expect(result.y).toBe(0);
  expect(result.x).toBeCloseTo(0.01 * M_PER_DEG_LAT * 0.5, 6);
});

// --- pointToSegmentDistance --------------------------------------------------

test('pointToSegmentDistance: a point on the segment interior is zero distance', () => {
  const a = { x: 0, y: 0 };
  const b = { x: 10, y: 0 };
  const p = { x: 5, y: 0 };
  expect(pointToSegmentDistance(p, a, b)).toBe(0);
});

test('pointToSegmentDistance: perpendicular offset from the segment interior', () => {
  const a = { x: 0, y: 0 };
  const b = { x: 10, y: 0 };
  const p = { x: 5, y: 3 };
  expect(pointToSegmentDistance(p, a, b)).toBe(3);
});

test('pointToSegmentDistance: a point beyond the far endpoint clamps to that endpoint, not the infinite line', () => {
  // Nearest point on the infinite line through (0,0)-(10,0) to (15,4)
  // would be (15,0) with distance 4 -- but clamped to the segment, the
  // nearest point is b=(10,0), giving the 3-4-5-scaled distance
  // sqrt(5^2+4^2).
  const a = { x: 0, y: 0 };
  const b = { x: 10, y: 0 };
  const p = { x: 15, y: 4 };
  expect(pointToSegmentDistance(p, a, b)).toBeCloseTo(Math.sqrt(41), 9);
});

test('pointToSegmentDistance: a degenerate (zero-length) segment is just point-to-point distance', () => {
  const a = { x: 0, y: 0 };
  const b = { x: 0, y: 0 };
  const p = { x: 3, y: 4 };
  expect(pointToSegmentDistance(p, a, b)).toBe(5); // 3-4-5 triangle
});

// --- distanceToRouteMeters --------------------------------------------------

test('distanceToRouteMeters: fewer than 2 route points is Infinity, not a crash', () => {
  expect(distanceToRouteMeters({ latitude: 0, longitude: 0 }, [])).toBe(Infinity);
  expect(distanceToRouteMeters({ latitude: 0, longitude: 0 }, [{ latitude: 0, longitude: 0 }])).toBe(Infinity);
});

test('distanceToRouteMeters: a point sitting on the route is ~0', () => {
  const route = [
    { latitude: 43.4643, longitude: -80.5204 },
    { latitude: 43.4700, longitude: -80.5100 },
  ];
  // The segment's own midpoint, computed the same simple way a real GPS
  // fix landing mid-segment would.
  const onRoute = {
    latitude: (route[0].latitude + route[1].latitude) / 2,
    longitude: (route[0].longitude + route[1].longitude) / 2,
  };
  expect(distanceToRouteMeters(onRoute, route)).toBeCloseTo(0, 3);
});

test('distanceToRouteMeters: picks the minimum across multiple segments, not just the first', () => {
  const route = [
    { latitude: 0, longitude: 0 },
    { latitude: 0, longitude: 0.01 },
    { latitude: 0.01, longitude: 0.01 },
  ];
  // Sits essentially on the second segment (constant longitude 0.01),
  // far from the first -- confirms the function doesn't just check
  // segment 0.
  const point = { latitude: 0.005, longitude: 0.01 };
  expect(distanceToRouteMeters(point, route)).toBeCloseTo(0, 0);
});

// --- formatDuration ----------------------------------------------------------

test('formatDuration: under an hour omits the hours component', () => {
  expect(formatDuration(0)).toBe('0:00');
  expect(formatDuration(65000)).toBe('1:05');
  expect(formatDuration(5000)).toBe('0:05');
});

test('formatDuration: an hour or more includes a zero-padded hours component', () => {
  expect(formatDuration(3661000)).toBe('1:01:01'); // 1h 1m 1s
  expect(formatDuration(3600000)).toBe('1:00:00'); // exactly 1h
});

// --- formatDistance ------------------------------------------------------

test('formatDistance: renders kilometers to 2 decimal places', () => {
  expect(formatDistance(5000)).toBe('5.00 km');
  expect(formatDistance(1234)).toBe('1.23 km');
  expect(formatDistance(0)).toBe('0.00 km');
});

// --- mergeRunHistory ---------------------------------------------------

test('mergeRunHistory: server runs already present locally (by runUuid) are not duplicated', () => {
  const local = [{ runUuid: 'a', startedAt: '2026-09-14T00:00:00Z' }];
  const server = [{ runUuid: 'a', startedAt: '2026-09-14T00:00:00Z' }];
  expect(mergeRunHistory(local, server)).toEqual(local);
});

test('mergeRunHistory: a server-only run gets added', () => {
  const local = [{ runUuid: 'a', startedAt: '2026-09-14T01:00:00Z' }];
  const server = [{ runUuid: 'b', startedAt: '2026-09-14T00:00:00Z' }];
  const result = mergeRunHistory(local, server);
  expect(result.map((r) => r.runUuid)).toEqual(['a', 'b']);
});

test('mergeRunHistory: result is sorted most-recent-first regardless of input order', () => {
  const local = [{ runUuid: 'older', startedAt: '2026-09-14T00:00:00Z' }];
  const server = [{ runUuid: 'newer', startedAt: '2026-09-14T02:00:00Z' }];
  const result = mergeRunHistory(local, server);
  expect(result.map((r) => r.runUuid)).toEqual(['newer', 'older']);
});

test('mergeRunHistory: does not mutate its inputs', () => {
  const local = [{ runUuid: 'a', startedAt: '2026-09-14T00:00:00Z' }];
  const server = [{ runUuid: 'b', startedAt: '2026-09-14T01:00:00Z' }];
  const localCopy = [...local];
  const serverCopy = [...server];
  mergeRunHistory(local, server);
  expect(local).toEqual(localCopy);
  expect(server).toEqual(serverCopy);
});

test('mergeRunHistory: local runs with no runUuid (pre-migration rows) are kept, never treated as duplicates', () => {
  const local = [{ id: 1, runUuid: null, startedAt: '2026-09-14T00:00:00Z' }];
  const server = [{ runUuid: 'b', startedAt: '2026-09-14T01:00:00Z' }];
  const result = mergeRunHistory(local, server);
  expect(result).toHaveLength(2);
});
