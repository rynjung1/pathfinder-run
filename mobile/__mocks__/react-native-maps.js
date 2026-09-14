// Manual mock for react-native-maps, picked up automatically by Jest for
// any test that imports it (no explicit jest.mock() call needed -- see
// https://jestjs.io/docs/manual-mocks#mocking-node-modules). Needed for
// the same reason geometry.js was split out of App.js: the real package
// registers a native module (TurboModuleRegistry.getEnforcing) that
// throws immediately outside a real native runtime, which would make
// App.js untestable at the component level, not just for the pure
// functions already covered by geometry.test.js.
//
// Renders plain Views standing in for MapView/Marker/Polyline -- enough
// for App.js to mount and for its state/text output to be assertable,
// without needing a real map. MapView exposes fitToCoordinates as a
// no-op via a ref (App.js calls mapRef.current?.fitToCoordinates(...)
// directly, not optionally-called, so the mock needs to actually provide
// the method or that call throws).
const React = require('react');
const { View } = require('react-native');

// Module-scoped, not created fresh per mount, so a test can hold a
// reference to it (MapView.__takeSnapshotMock) and assert on calls --
// App.js's captureRunSnapshot calls mapRef.current.takeSnapshot(...)
// directly (§2's offline-map-tiles substitution), same "not optionally
// called" reasoning as fitToCoordinates above.
const takeSnapshotMock = jest.fn(() => Promise.resolve('file:///tmp/mock-snapshot.png'));

const MapView = React.forwardRef((props, ref) => {
  React.useImperativeHandle(ref, () => ({
    fitToCoordinates: () => {},
    takeSnapshot: takeSnapshotMock,
  }));
  return React.createElement(View, { testID: 'mock-map-view' }, props.children);
});

const Marker = (props) => React.createElement(View, { testID: 'mock-marker', ...props });
const Polyline = (props) => React.createElement(View, { testID: 'mock-polyline', ...props });

module.exports = MapView;
module.exports.default = MapView;
module.exports.Marker = Marker;
module.exports.Polyline = Polyline;
module.exports.__takeSnapshotMock = takeSnapshotMock;
