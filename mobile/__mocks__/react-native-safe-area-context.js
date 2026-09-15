// Manual mock for react-native-safe-area-context, picked up automatically
// by Jest (same mechanism as react-native-maps.js in this directory --
// see that file's header). SafeAreaProvider normally measures the real
// device's safe-area frame via a native module and provides it down
// asynchronously; under Jest there's no real device to measure, so
// without this mock the provider renders but never resolves insets,
// and useSafeAreaInsets() (or anything reading them) hangs/times out --
// this is the fix react-native-safe-area-context's own docs recommend
// for exactly this situation, not a workaround specific to this app.
//
// Insets live in a module-level variable, not baked into
// useSafeAreaInsets's own closure -- App.js's `import { useSafeAreaInsets }`
// destructures this function once at module load (confirmed empirically:
// reassigning the exported function or jest.spyOn-ing it afterward had no
// effect on what App.js actually called), so a test overriding the
// RETURN VALUE has to go through __setMockInsets, not swap the function
// reference itself.
let mockInsets = { top: 0, right: 0, bottom: 0, left: 0 };

function __setMockInsets(insets) {
  mockInsets = insets;
}

const SafeAreaProvider = ({ children }) => children;
const useSafeAreaInsets = () => mockInsets;

module.exports = {
  SafeAreaProvider,
  useSafeAreaInsets,
  SafeAreaView: ({ children }) => children,
  SafeAreaConsumer: ({ children }) => children(useSafeAreaInsets()),
  useSafeAreaFrame: () => ({ x: 0, y: 0, width: 402, height: 874 }),
  __setMockInsets,
};
