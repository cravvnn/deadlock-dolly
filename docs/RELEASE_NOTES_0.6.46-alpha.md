# Deadlock Dolly 0.6.46-alpha

Players export controls, remembered destinations, and capture reliability fixes.

## Changes

- **Conversion controls.** **Before:** Players conversion could monopolize CPU time with limited feedback. **After:** conversion runs in a separate worker with progress, cancellation, and Background, Balanced, or Fast resource modes. These modes control worker threads and priority rather than a fixed CPU percentage.
- **Custom scratch folders.** **Before:** temporary Players capture data was written beside the deployed native module. **After:** choose a local scratch folder, including another drive. Successful conversion removes its temporary capture files; cancelled or failed conversion retains raw capture data.
- **Remembered export folders.** **Before:** restarting Dolly, including after an update, reset the export destination. **After:** Dolly remembers the selected folder while generating a fresh filename. An unavailable drive does not erase the saved preference.
- **More reliable capture.** **Before:** playback could start before the recorder was ready, shaders created before the first presentation could lack Players metadata, and a full disk-writer queue could cause a crash. **After:** playback waits for recorder readiness, shader metadata collection starts earlier, and rejected frame buffers remain valid while capture stops safely.

## Validation

The full Python suite passes: 2,043 tests, 22 existing skips. All 29 enabled native checks pass; the hardware encoder stress test remains excluded. Shader metadata was tested with both software and hardware DirectX 11 devices before their first presentation. A deterministic writer-overflow regression fails on the old code and passes with the fix.

Bounded local replay checks verified 30-frame Players completion, decoded output and alpha, conversion cancellation with retained raw data, and exact runtime/configuration/deployment restoration. The existing Steam shutdown assertion remains visible in the test records. Resource modes and folder persistence also have offline regression coverage.

## Updating

Extract the Windows ZIP into a fresh folder and run Dolly.exe, or use Dolly's updater once this release is published. Python, Tcl/Tk, and the encoder are bundled.
