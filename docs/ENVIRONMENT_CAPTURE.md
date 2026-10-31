# Dolly Environment Capture

This separate edition adds an experimental HDR panorama workflow for map
reference and Blender backgrounds. This tester uses the 0.6.85 runtime and the
current Kara UI integration, copied from the local main source on October 29.
The desktop and in-game interface are reduced around panoramic screenshots. The
six-face HDR capture and restoration path passed a local 1024-square check on
an earlier base; higher resolutions and the packaged keypress-to-output path
still need live acceptance checks. Earlier prototype ZIPs are superseded.

## Current Windows tester (0.6.85 base)

The desktop contains **Replay, Viewpoints, Scene, Capture, Controls and Settings**.
Viewpoints saves and recalls camera positions. Scene keeps fixed camera/scene
settings and depth of field. Capture contains PNG screenshots and HDR panoramas.
F8 opens a small in-game capture strip with pause, flight, level-view and panorama
controls, using Kara's fonts and palette. Choose the destination and size on the
desktop first. F7 remains the console; F8 returns to flying.

Video export, path/curve editing and playback, Follow/Bone Picker, particles,
Players/Depth output passes, ReShade setup, Discord and standard Dolly updates
are unavailable in this edition. A shared single-frame capture engine and one
FFmpeg executable remain for PNG stills. The package omits the separate updater
launcher/runtime, duplicate FFmpeg distribution and game-audio helper. Shared
native render hooks remain because HDR acquisition depends on them.

This package is qualified offline only. Earlier live panorama results do not
qualify this rebuilt DLL against the current installed game. Start at 1024 and
inspect a new capture before using larger sizes or relying on the output.
Panorama capture still forces 100% render scale for the capture, so it works when
the game uses FSR/DLSS upscaling or a reduced render scale. Previously capture
could fail with **"HDR source dimensions were not established"** because the
scene rendered below the HDR texture size. The previous render scale is restored
after capture, and if the source still cannot be established the message names
the setting to change (turn off upscaling or set render scale to 100%).

The user's saved window resolution is also restored after a sized panorama
session, so the next normal launch is not left at the square size.

## Earlier 0.1.x prototype builds

The older prototype's Export page and inherited in-game editor were replaced by
the focused Capture page and in-game strip in the current tester.

For screenshots, choose **Game window (no change)** to use the game's normal
resolution, or select a larger render size before reopening the replay. PNG is
lossless; there is no lossy quality slider. Depth and character cutouts are absent.
Both tools use the capture folder in the Environment panorama
card. Screenshot resolution options do not imply tested 4K/8K capture support.

Panoramas still require square render dimensions. Native widescreen panorama
capture has not been validated and is not enabled in this build. Choosing a
screenshot resolution and choosing a panorama size change the same next-launch
settings; the last choice applies. Close and reopen the replay when changing size.
Existing preferences are preserved, including any square launch size; select
**Game window (no change)** before opening the replay for a normal-resolution still.

## Capture controls

Panorama capture removes gameplay zipline ribbons. An eight-frame A/B/A
comparison on build 6774 verified removal and restoration. Use an already-empty
scene for map references: pausing freezes existing characters and effects rather
than removing them. Complete combat cleanup and exact game-to-Blender lighting
matching are outside this handoff.

A midpoint replay check at tick 77000 also exercised 24 HDR frames beside a
character: baseline, two complete production panoramas, then restored views.
Characters disappeared and returned, but floating objects and a purple light
contribution remained. Hiding the shared `Default` or `BarnLight` classes also
affects map content. Disabling dynamic lights removed the purple contribution
while changing other illumination; it is not enabled automatically. The current
world filter therefore does **not** promise an empty scene during combat.

The EXR passed a Blender import and half-strength lighting response check in a
neutral/reflective sphere scene, assuming Linear Rec.709 input. That assumption
is not yet a verified game color-space contract. Packaged shortcut acceptance
and final clean-map/lighting qualification remain outstanding.

Open **Controls**, select **Capture panorama**, and assign an unused key or
combination. The action starts unbound. **F8 continues to open Dolly.**
The desktop and in-game **Capture panorama** buttons run the same operation.

In the Environment panorama section, choose an existing output folder. Select
a square render size and press **Apply size** before opening the replay.
Begin with 1024, which produces a 4096×2048 panorama. The borderless window may
fill the display; capture validates the HDR render texture's actual dimensions.

Open a local replay through this edition, pause, and position the free camera
at the desired panorama centre. Keep the camera clear of walls and other map
geometry. Press the assigned key or the capture button once. Dolly takes six
overlapping 100-degree views from that fixed centre, restores the camera and HUD, then
assembles the panorama using the measured projection and smoothly normalized overlap weights. The outer four source pixels are excluded to avoid dark capture borders. Additional requests are rejected while it is busy.
**Cancel capture** requests cancellation; camera and HUD restoration still runs.
Cancellation during stitching retains the six faces and any `.partial` output.
If Deadlock waits at its intro during startup, focus the game and press Escape
once. Dolly still verifies preload completion before opening the replay.

Every capture gets a new `panorama-...` folder containing six face EXRs,
`capture.json`, and, after successful capture and restoration, `panorama.exr`.
Failed runs retain their available faces and report for diagnosis.

## Using the output

The stitched EXR uses an equirectangular projection. In Blender it can supply
an Environment Texture for a World shader, or serve as a background and
projection reference. Match its position and orientation to the capture point;
a panorama has no depth and cannot reproduce parallax when the camera moves.

These are rendered HDR references, not calibrated measurements of illumination.
Values above 1 are preserved. The source includes the game's rendering choices,
so Blender lighting may require adjustment and will not automatically reproduce
the game's lighting. Display previews may look different from the EXR.

For map reconstruction, use a probe near the subject it should illuminate.
Blender's World treats the image as distant: a room panorama cannot replace
nearby lamps throughout a reconstructed map, and walls can block World lighting.
Keep already-lit projection references separate from unlit material textures;
lighting a captured shaded surface again can double its shading. The source RGB
primaries and any upstream exposure scaling are still under investigation, so
there is no verified universal Blender color-space/strength preset yet.

The world filter hides animated units and effects broadly. Inspect the result
for remaining attachments, shadows, environmental animation and seams. An EXR
extension does not establish that the scene is completely empty or physically
calibrated. The capture report keeps that distinction explicit.

This edition keeps its own preferences and does not use regular Dolly's update
channel. Its launcher includes `-dev -insecure`; close the owned editing session
before playing normally.
