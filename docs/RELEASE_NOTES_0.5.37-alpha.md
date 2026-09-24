# Deadlock Dolly 0.5.37-alpha

## Native editor startup with Vulkan preferences

Dolly now explicitly selects DirectX 11 when launching a Native replay session.
Previously, a game using Vulkan could load and pause the replay successfully,
but Dolly would time out waiting for the DX11 panel. Native camera editing,
the in-game panel and recording require the DX11 presentation path.

The flag applies only to Dolly's launched process. Dolly does not edit saved
graphics settings or Steam launch options. Console-backend behavior is unchanged.

## Verification

A bounded local replay test with Vulkan reproduced the exact panel timeout:
camera frames advanced, but no DX11 presentation callback or panel initialization
occurred. The fixed production launcher selected DX11, initialized the panel,
and opened the paused Native editor with input ready. Both runs exited cleanly,
removed their temporary deployments, and left captured configuration bytes
unchanged. A preliminary run exited cleanly because Steam was not yet ready;
it was excluded from the renderer comparison.

Launcher regression coverage checks both console transports, process arguments,
window settings, and rejection of conflicting custom renderer options.
The release-build Python suite passed 1,324 tests with 16 skips, and all 20 enabled native
checks passed. Hardware video-encoder and depth-scene smoke tests remain omitted
under the existing local test restrictions.

This is local verification of the reproduced renderer-selection defect, not a
retest on the reporting user's PC. Separate out-of-memory, access-violation and
depth-export reports are not claimed fixed by this release.
