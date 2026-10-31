# Video and ReShade

[Documentation](README.md) · [User guide](USER_GUIDE.md)

## Replay graphics profiles

Every new Dolly replay session uses its own clean settings folder. Normal-play
saved ConVars, bindings, startup commands and addon mounts are excluded. The
reviewed stock baseline retains fog and shadows; Dolly's clean video settings
start at 100% render scale with upscaling off. Normal-play files stay untouched.

In **Settings > Replay graphics profiles**, **Clean Dolly settings (default)**
uses this baseline. To choose your own quality settings, use **Save normal-play
graphics...** with Deadlock closed, or import a saved `video.txt`. The preview
lists the reviewed quality values; the named profile applies only to the next
Dolly session's separate file.

Profiles include supported shadow, fog, occlusion, texture, shader, bloom,
anti-aliasing, motion blur and related quality controls. They preserve the
normal-play resolution, display mode and GPU identity through a read-only
allowlist. Performance tiers, upscalers, render scale and unrecognized fields
are not imported. **Display launch options** override the copied window size
and mode. Imported quality values are checked for format, not certified for
every GPU. In-game changes last for that Dolly session.

Dolly checks the engine's actual settings root and user-config guard before it
opens the replay. It refuses an unconfirmed or additional USRLOCAL root. The
normal-play file does not need to be replaced or restored for new sessions.
Older versions' interrupted graphics transactions are still recovered through
the original exact-byte path on the next launch or through
**Settings > Troubleshooting & recovery > Recover configuration**. Never delete
the session logs while recovery is pending: they contain the originals.

Unexpected edits to `video.txt` are preserved and reported with the backup path;
compare those files before resolving the conflict. Dolly does not overwrite an
unknown newer configuration. A game update that changes the video format or
removes a profile field requires a new supported profile. Profiles do not swap
arbitrary `gameinfo.gi` files or repair modified game assets. Dolly uses its
reviewed stock configuration and excludes user addon mounts for editing.

The camera support check also records available profile render controls in
diagnostics. Differences or unreadable values produce a warning without writing
additional controls. This readback is not a visual-quality guarantee. Shot and
export effects may deliberately change graphics later in the session.

## Record a video

1. Open a replay through Dolly using DirectX 11 and Native camera mode.
2. On the desktop **Export** tab, choose **Camera path** or **Player POV** under
   **Camera source**, then choose a new output file. Compressed FFmpeg encoders
   support `.mp4`, `.mkv`, and `.mov`; the built-in encoder uses `.mp4` and
   **Lossless FFV1 (.mkv)** requires `.mkv`. Set **Video FPS**, **Export speed**
   and, under **Encoder & quality**, the encoder and bitrate. Defaults are
   60 FPS, 20 Mbps, Auto and real-time capture.
3. Return to Deadlock, open **F8 → Export**, and click **Record video** (or
   **Record POV** for Player POV). Dolly prepares
   the replay, starts the recorder, and plays the shot once the counter is
   live. **Play shot** remains available for previews without recording.
4. Let the shot finish and wait for finalization. A recording that plays a shot
   ends automatically at its endpoint; a free-camera recording without a shot
   stays open until **Finish recording**. Use that button to finish early, or
   **Discard recording** to discard the current recording.

For **Player POV**, first select a hero with **Game Follow** or the game's
**F9** interface, pause at the start, and set **Replay seconds**. Return to
**F8 → Export** to record the segment; the game HUD hides automatically.

Capture uses the current game backbuffer, with even dimensions up to 8192 on
each axis and a separate bounded memory budget. SDR RGBA/BGRA is supported;
HDR is not. Depth and Players have additional resource limits. Change the
resolution before capture; resizing ends the current recording.

Compressed SDR color videos and ProRes alpha masters use limited-range Rec. 709
and carry its color metadata for editing applications. Lossless FFV1 keeps the
captured RGB values; numerical depth exports retain their own depth mapping.

By default this is real-time, video-only H.264 recording. Playback speed
controls how fast the replay moves; export FPS controls how often frames are
captured. Output timestamps preserve elapsed time if frames are missed. The
counter reports missed capture slots; selecting 60 FPS cannot make a slower
game render 60 different frames. Reduce resolution, shader cost or export FPS
if necessary.

**Fixed-step export (frame-accurate)** paces the engine to the chosen video FPS
and waits for readback and encoder queue capacity. Expect rendering to run
slower than real time. Use
**Export speed** to scale replay time per output frame (for example 0.5× for
smooth slow motion at a high FPS). The engine time step is `Export speed / Video FPS`;
Dolly verifies it before recording starts and restores the previous settings
afterward. A clamped or unavailable timing setting prevents capture from starting.
The waits are bounded: a stalled ordinary Color recording can still report
dropped frames, while paired depth stops on a capture/writer failure. Check the
final status rather than treating the fixed-step checkbox as a success guarantee.

**Depth master (.mov)** is a default-off option on the Export tab. It writes a
paired 10-bit ProRes depth master and a normalized preview beside the color
video; **EXR sequence (float)** optionally adds the numerical master.

Depth export temporarily uses 100% render scale for both the color/depth take
and its selected extra passes. The game can overwrite reduced-scale scene
depth during spatial upscaling, leaving no verified depth sample. Dolly reads
and verifies the temporary setting before capture and restores the previous
scale after finish, discard or a failed start. Window resolution, anti-aliasing
and the selected upscaling mode stay as configured. Full render scale can make
export slower, particularly on GPUs normally using reduced render scale.

Every admitted color frame in a depth take requires verified scene depth and
the same replay time.
The manifest records depth dimensions (`width`/`height`) and color dimensions
(`color_width`/`color_height`). The depth layer's normalized preview maps depths
at or beyond 8192 camera-axis game units to white. Unsupported or ambiguous
depth stops recording instead of writing an unpaired frame. Depth values are
camera-axis game units, not metres; the optional EXR sequence preserves float
precision, including infinity for the far plane. The 10-bit MOV maps the
0–8192 range to grayscale and is not a float-distance master. A supported
resolution and 100% render scale do not guarantee that every antialiasing or
upscaling mode passes the current calibration checks.

Depth and extra layers use a new take folder. For `Shot.mp4`, the Color file
is `Shot/Shot.mp4`, with depth data in `Shot/depth/`. See
[Layer export](LAYER_EXPORT.md) for exact files, alpha outputs and limitations.

Dolly's panel and path guides are excluded from the file. ReShade color effects
are included, while its menu, splash and FPS display are excluded. Deadlock's
own HUD remains part of the scene: enable **Hide HUD during playback** for a
clean shot. Bring the game into view while capture prepares. Manual recording includes any seeking or setup
performed after recording starts.

Pressing **Play shot** before the recorder reports *recording* waits briefly for
the counter instead of starting the shot early. If the recorder never starts, or
it finishes or fails first, Dolly stops and asks you to finish or discard the
recording and start a new one.

Existing files are never overwritten. **Discard recording** removes the current
unfinished recording; completed earlier layer passes or diagnostic scratch
data may remain. Encoder failures report an error and remove an unfinished
native output. Keep Deadlock open while the recording is finalizing. Windows N
editions need Microsoft's Media Feature Pack to record; camera editing remains
available without it.

Enable **Include game audio** to record Deadlock's sound with the Color export.
The take folder contains the video with audio and a separate
`<video-stem>_game_audio.wav`; other desktop audio is excluded.
**Include reconstructed audio** is experimental. On the first reconstructed
export, Dolly downloads a pinned, SHA-256-verified Source 2 Viewer CLI (about
53 MB) from ValveResourceFormat and caches it locally. No manual install or
environment variable is needed; advanced users can still set
`DOLLY_SOURCE2VIEWER` to use an existing CLI. Its sound files,
`reconstructed.wav` and `usage.csv` are saved under
`<video-stem>_reconstructed/` beside the Color file.

Audio works with both real-time and **Fixed-step** Color exports. Real-time
audio uses the game process loopback. Fixed-step audio uses the engine's
frame-locked WAV recorder: DollyNative splits the movie's engine clock from its
WAV clock, so the reference lasts exactly as long as the slow-motion video and
keeps the authored replay window. Reconstructed audio is fitted on replay time,
including at 0.25x/0.5x. Extra layer takes remain silent; export them separately.
Arbitrary output resizing is not included. Paired depth masters and isolated
layer takes are available; see [Layer export](LAYER_EXPORT.md).

## High frame rates and fixed-step

Choose the frame rate in Export → Video FPS before starting a recording. The
in-game Record video button uses that selection. Playback speed and camera
update rate are separate settings; changing video FPS does not change either.

Use fixed-step for 300 or 600 FPS. Real-time capture depends on the game and
encoder sustaining the selected rate; missed slots preserve elapsed time rather
than synthesizing frames. Fixed-step adds backpressure, subject to the failure
and timeout limits above. Encoder support still
depends on resolution and the selected codec; lower the resolution or choose a
lower FPS if the encoder rejects the configuration.

Paired depth requires a verified scene depth sample for each admitted Color
frame. Players separately verifies its ownership capture; World and Effects
require the reviewed scene classes. These checks stop an unsupported take
instead of presenting it as a complete layer. See [Layer export](LAYER_EXPORT.md)
for the different requirements.

## Export the camera

When recording, Dolly can save Camera data so that 3D layers, text and
effects can follow the shot in After Effects, Blender and compatible USD apps.

### Turn it on

Tick **Export After Effects camera (.jsx)**, **Export USD camera (.usda)** or
both. They're in the **Camera source** card on the desktop **Export** tab and on
game overlay's Export page. Camera exports work for camera paths, single bone
cameras and Player POV. The files are written when the recording finishes. Camera export is off by default.
No depth pass is required.

### Files

| File | What it is |
| --- | --- |
| `<video>.mp4.camera-track.json` (or `.mkv.camera-track.json`) | Validated per-frame camera motion and recording identity. |
| `<video>_camera.jsx` | After Effects script that builds a composition with the camera and video. |
| `<video>_camera.usda` | USD layer with the camera, for Blender and other apps. |

### After Effects

1. In After Effects, choose **File → Scripts → Run Script File…** and pick the
   `_camera.jsx`.
2. The script imports the video from the script's own folder (or asks you to
   locate it), conforms it to the take's frame rate and builds a comp at the
   video's size with a **Dolly camera** and the video as its bottom layer.
   Running it again reuses the imported video.

If After Effects cannot open the footage, the camera still imports at the
recorded size. The script explains the footage error and keeps that message in
the composition's comment. For example, FFV1 lossless MKV recordings may need
conversion to a supported codec/container first. Keep the dimensions, frame count
and FPS unchanged, then add the converted video at frame 0 of the camera comp.
Canceling the Locate dialog also imports the camera without video.

### Blender / other USD apps

1. In Blender, choose **File → Import → Universal Scene Description** and pick
   the `_camera.usda`. Keep **Apply Unit Conversion Scale** on and **Scale** at
   1, the defaults: the layer is in inches (`metersPerUnit` 0.0254, Z up), and
   depth of field relies on that conversion.
2. Set the render resolution to the video's size and the frame rate to the
   take's FPS.
3. Add the video as the camera's background (a Movie Clip) and set the clip's
   **Start Frame** to 0: the USD camera starts at frame 0, Blender's clips at 1.

The camera's frames match the video's frames one for one, so scrub frame by
frame to check alignment. If the background seems to jump during playback,
use **Playback → Sync → Play Every Frame**, or a timecode proxy or image
sequence for frame-exact video.

### Key markers

Each keyframe gets a marker (a guide null in After Effects, an Xform under
`/Dolly/Keys` in USD) at the camera's actual position when the shot reached
that key, labelled with its time in the video. Keys the take never reached are
left out. Player POV takes have no markers.

### Depth of field

Both **Citadel DOF** and **Native DOF** are converted from authored settings,
rather than measured from the renderer. Native DOF is an approximation. Check the
imported focus and blur in your app and adjust them as needed.

### Complete data and alignment

Dolly requires a camera sample for every encoded frame and verifies that the
metadata belongs to this recording. It does not silently substitute authored
keys for missing camera motion. If camera data is missing, incomplete, too large
or mismatched, the video is kept and the log explains why camera export failed.
Existing camera tracks and JSX/USD outputs are never overwritten.

Camera collection is enabled only when either camera export is requested. It
supports up to 262,144 frames (about 73 minutes at 60 FPS, or 7 minutes at 600 FPS).
Beyond that, camera export is incomplete and refused; video recording continues.

Both outputs use encoded-frame order at the take's FPS. AE conforms the imported
footage to that rate. This is a frame-for-frame editing timeline, which can differ
from original elapsed time in real-time recordings with variable timestamps.
Capture and encoded timestamps are retained in the track.

For paired depth takes, Dolly cross-checks the hook track against the complete
renderer camera bundle and uses its calibrated camera. External cameras use the
stable projection; temporal antialiasing jitter remains in the depth bundle for
reconstruction. A mismatch is reported rather than choosing a source silently.
Camera exports contain no map or player geometry, animation or materials.

## Set up ReShade

1. Obtain a **64-bit ReShade runtime with full add-on support** from the
   [official ReShade website](https://reshade.me/). Dolly's headers target
   ReShade 6.8.0/API 20; the selected runtime must support API 20 and the manual
   effect-runtime functions. A newer version number alone does not establish
   compatibility.
2. Open the downloaded setup EXE as an archive with 7-Zip. Extract
   **ReShade64.dll** into a separate folder, such as `Documents\Dolly-ReShade`.
   The setup program's appended ZIP contains this DLL; running the installer
   against Deadlock is unnecessary.
3. In Dolly's **Settings → ReShade** section, select that DLL and enable it.
   The status changes from loading to ready after a game frame initializes it.
4. Press **F11** in Deadlock to open ReShade's own menu. Rebind this action on
   **Settings → Controls & keybinds** if needed. **F7** continues to open the game console.
5. Dolly registers its bundled crosire/prod80 shader and texture library and
   copies editable presets into its private `ReShade\presets` folder. A new
   configuration selects **Deadlock-Dolly**, a neutral color-control preset.
   Open ReShade's menu to adjust its sliders or enable the other bundled effects.
   Existing shader paths, selected presets and edited preset files are preserved.
   You can add other downloaded shader packs through ReShade's settings.
   If ReShade's menu reports that it cannot find the effect files, press
   **Browse FX library...** in Dolly's Settings → ReShade and select the folder
   that contains the `.fx` shaders (for example the bundled
   `third_party\reshade_shaders\Shaders`). The folder is added to Dolly's private
   configuration and loads with the next ReShade start.

The optional **Deadlock-AO** preset requires separately installed iMMERSE shaders.
Those shaders and the ReShade runtime are not bundled. It is not selected
automatically. Enable ReShade while the replay editor has focus and Dolly
publishes the verified scene depth to ReShade's `DEPTH` semantic, so MXAO and
other depth-dependent effects can use it.

The selected runtime is remembered for later Dolly editing sessions.
**Disable** turns it off for the current session. **Forget runtime** clears the saved runtime path
to stop automatic loading on future sessions. Changing the DLL requires a fresh
editing session. An existing ReShade instance in Deadlock is refused to avoid
two runtimes controlling the same graphics/input state.

Dolly uses a private configuration at
`%APPDATA%\DeadlockDolly\ReShade\ReShade.ini`. Presets, shader paths and the
selected runtime remain on disk as settings; Dolly does not copy a graphics
loader into Deadlock's normal game folder. A loaded runtime remains resident
until the editing game's process exits.

This integration requires a single-sample SDR swapchain. MSAA or HDR
swapchains are refused with an error instead of recording an incorrect effects
image. Color effects are supported. Dolly copies the verified scene depth into
a sampleable texture and binds it as ReShade's `DEPTH` texture each frame, so
depth-dependent shaders such as MXAO or ReShade DOF can work without ReShade's
disabled automatic graphics hooks. The depth is the same reverse-projection
scene target used by the depth export; the bundled presets set
`RESHADE_DEPTH_INPUT_IS_REVERSED=1` and a far plane of 1000 (tune the far plane
in ReShade's UI if a depth effect's falloff looks wrong). Dolly's existing
native camera DOF controls continue to work.

ReShade releases also disable their own depth detection and pause add-on events
for a while after they see non-local network traffic, as an online-game
protection. Dolly does not use that detection: it publishes its own verified
scene depth through the runtime API, which that gate does not cover, so depth
effects keep working while Deadlock is connected. Renaming the game executable
is not needed. While add-on events are paused, video capture through ReShade
waits for them to resume; Dolly reports that state instead of failing the
runtime, and color effects continue to render.

## Implementation and validation

The recorder uses three reusable GPU staging slots and a bounded CPU queue.
Readback, encoding and disk writes run on separate workers. In real-time mode a
busy GPU or full queue skips a capture opportunity instead of blocking camera
rendering. In fixed-step mode the producer applies backpressure: it waits for
the previous readback and for encoder queue room before the next simulation
step, with a bounded fallback to the drop path if the encoder stalls. The
original camera hook, interpolation and replay clock are unchanged.

ReShade runs through its manual runtime API inside Dolly's existing Present
callback. Capture occurs at its post-effects, pre-interface callback. Optional
loading runs outside the camera worker. The public API headers are pinned in
`native/vendor/reshade/UPSTREAM.json`. The shader library in
`third_party/reshade_shaders` includes its CC0/MIT notices; runtime DLLs are not
redistributed.

References: [ReShade runtime API](https://crosire.github.io/reshade-docs/structreshade_1_1api_1_1effect__runtime.html),
[official installer source](https://github.com/crosire/reshade/blob/18deaa52de0c425a78b329e9cb3c497281cd00ec/setup/MainWindow.xaml.cs),
[Media Foundation sink writer](https://learn.microsoft.com/en-us/windows/win32/medfound/tutorial--using-the-sink-writer-to-encode-video).
Build checks and remaining runtime tests are recorded in [VALIDATION.md](internal/VALIDATION.md).

## Credits

See [Dolly credits and contributors](https://github.com/cravvnn/deadlock-dolly/blob/main/CREDITS.md)
for the people, projects and tools behind Dolly.
