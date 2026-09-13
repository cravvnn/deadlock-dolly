# Video and ReShade

## Record an MP4

1. Open a replay through Dolly using DirectX 11 and Native camera mode.
2. On the desktop **Export** tab, choose a new `.mp4` filename and the export
   settings: video FPS (30, 60, 120, 300 or 600), bitrate, encoder, and optional
   fixed-step and export speed. 60 FPS, 20 Mbps and automatic encoder are the
   defaults.
3. Return to Deadlock, open **F8**, and click **Record video**. Dolly prepares
   the replay, starts the recorder, and plays the shot once the counter is
   live. **Play shot** remains available for previews without recording.
4. Open **F8** and click **Finish recording**. The MP4 is ready after
   finalization. Recording continues through camera handoffs and desktop controls.

Capture uses the current game resolution, up to 3840 × 2160. Both dimensions
must be even. SDR RGBA/BGRA backbuffers are supported; HDR is not. Change the
game resolution before recording. Resizing during recording finishes the file.

By default this is real-time, video-only H.264 recording. Playback speed
controls how fast the replay moves; export FPS controls how often frames are
captured. Output timestamps preserve elapsed time if frames are missed. The
counter reports missed capture slots; selecting 60 FPS cannot make a slower
game render 60 different frames. Reduce resolution, shader cost or export FPS
if necessary.

**Fixed-step** export instead paces the replay to the chosen video FPS, so the
output contains exactly the authored frames with no missing slots. The game's
simulation pauses and steps one frame at a time; each step completes its
readback and waits for encoder capacity before the next step runs. Expect the
game to render slower than real time during a fixed-step recording. Use
**Export speed** to scale replay time per output frame (for example 0.5× for
smooth slow motion at a high FPS). The engine time step is `Export speed / Video FPS`;
Dolly verifies it before recording starts and restores the previous settings
afterward. A clamped or unavailable timing setting prevents capture from starting.

Dolly's panel and path guides are excluded from the file. ReShade color effects
are included, while its menu, splash and FPS display are excluded. Deadlock's
own HUD remains part of the scene: enable **Hide HUD during playback** for a
clean shot. Recording starts when the game is focused; return within ten seconds
if starting from the desktop. Manual recording includes any seeking or setup
performed after recording starts.

Pressing **Play shot** before the recorder reports *recording* waits briefly for
the counter instead of starting the shot early. If the recorder never starts, or
it finishes or fails first, Dolly stops and asks you to finish or discard the
recording and start a new one.

Existing files are never overwritten. **Discard** removes the incomplete file
created by that recording. Encoder failures report an error and remove an
unfinished output. Keep Deadlock open while an MP4 is finalizing. Windows N
editions need Microsoft's Media Feature Pack to record; camera editing remains
available without it.

Audio, separate layers and arbitrary output resizing are not included in this
version. Fixed-step video export is available; see below.

## High frame rates and fixed-step

Choose the frame rate in Export → Video FPS before starting a recording. The
in-game Record video button uses that selection. Playback speed and camera
update rate are separate settings; changing video FPS does not change either.

At 300 or 600 FPS, real-time capture depends on the game and encoder sustaining
that throughput; without fixed-step, Dolly reports missed slots and preserves
elapsed time rather than synthesizing frames. Fixed-step export removes that
dependence: the simulation advances one step at a time and waits for readback
and the encoder, so every output frame is present. Encoder support still
depends on resolution and the selected codec; lower the resolution or choose a
lower FPS if the encoder rejects the configuration.

Depth, hero-only and world-only export are not available in this build. See
[LAYER_EXPORT.md](LAYER_EXPORT.md) for renderer requirements.

## Set up ReShade

1. Download **ReShade 6.8.0 or a compatible newer full add-on support** build from the [official ReShade
   website](https://reshade.me/). Dolly requires a 64-bit runtime supporting
   add-on API 20 and the manual effect-runtime functions.
2. Open the downloaded setup EXE as an archive with 7-Zip. Extract
   **ReShade64.dll** into a separate folder, such as `Documents\Dolly-ReShade`.
   The setup program's appended ZIP contains this DLL; running the installer
   against Deadlock is unnecessary.
3. In Dolly's **Export → ReShade** section, select that DLL and enable it.
   The status changes from loading to ready after a game frame initializes it.
4. Press **F11** in Deadlock to open ReShade's own menu. Rebind this action on
   **Keybinds** if needed. **F7** continues to open the game console.
5. Dolly registers its bundled crosire/prod80 shader and texture library and
   copies editable presets into its private `ReShade\presets` folder. A new
   configuration selects **Deadlock-Dolly**, a neutral color-control preset.
   Open ReShade's menu to adjust its sliders or enable the other bundled effects.
   Existing shader paths, selected presets and edited preset files are preserved.
   You can add other downloaded shader packs through ReShade's settings.

The optional **Deadlock-AO** preset requires separately installed iMMERSE shaders
and a working depth source. Those shaders and the ReShade runtime are not bundled.
It is not selected automatically; Dolly's current ReShade integration still
needs the depth hookup described below before depth-based AO can work.

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
image. Color effects are supported. **A game depth texture is not
supplied yet**, so depth-dependent shaders such as MXAO or ReShade DOF are not
supported. Dolly's existing native camera DOF controls continue to work.

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
Build checks and remaining runtime tests are recorded in [VALIDATION.md](VALIDATION.md).
