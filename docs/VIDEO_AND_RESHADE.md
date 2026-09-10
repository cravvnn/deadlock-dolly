# Video and ReShade

## Record an MP4

1. Open a replay through Dolly using DirectX 11 and Native camera mode.
2. On the desktop **Export** tab, choose a new `.mp4` filename, 30, 60 or 120 FPS,
   and a bitrate. 20 Mbps is the default.
3. Return to Deadlock, open **F8**, and click **Record video**. Wait for the
   recording counter, then play the shot with **Play shot** or **F5**.
4. Open **F8** and click **Finish recording**. The MP4 is ready after
   finalization. Recording continues through camera handoffs and desktop controls.

Capture uses the current game resolution, up to 3840 × 2160. Both dimensions
must be even. SDR RGBA/BGRA backbuffers are supported; HDR is not. Change the
game resolution before recording. Resizing during recording finishes the file.

This is real-time, video-only H.264 recording. Playback speed controls how fast
the replay moves; export FPS controls how often frames are captured. Output
timestamps preserve elapsed time if frames are missed. The counter reports
missed capture slots; selecting 60 FPS cannot make a slower game render 60
different frames. Reduce resolution, shader cost or export FPS if necessary.

Dolly's panel and path guides are excluded from the file. ReShade color effects
are included, while its menu, splash and FPS display are excluded. Deadlock's
own HUD remains part of the scene: enable **Hide HUD during playback** for a
clean shot. Recording starts when the game is focused; return within ten seconds
if starting from the desktop. Manual recording includes any seeking or setup
performed after recording starts.

Existing files are never overwritten. **Discard** removes the incomplete file
created by that recording. Encoder failures report an error and remove an
unfinished output. Keep Deadlock open while an MP4 is finalizing. Windows N
editions need Microsoft's Media Feature Pack to record; camera editing remains
available without it.

Fixed-step offline rendering, audio, separate layers and arbitrary output
resizing are not included in this version.

## 120 FPS

Choose 120 in Export → Video FPS before starting a recording. The in-game
Record video button uses that selection. Playback speed and camera update rate
are separate settings; changing video FPS does not change either.

This remains real-time capture. 120 distinct frames per second requires the
game and encoder to sustain that throughput. Dolly reports missed capture
slots and preserves elapsed time; it does not synthesize missing frames or
slow the demo to wait for encoding. Encoder support depends on the selected
resolution and Windows codec. Lower the resolution or choose 60 if the encoder
rejects the configuration.

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
5. Add the shader and texture search paths for your downloaded shader pack in
   ReShade's settings, then select or create a preset. Shaders and presets are
   separate downloads and are not bundled with Dolly.

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
Readback checks GPU readiness without waiting. Encoding and disk writes run on
a separate Media Foundation worker; overload drops capture opportunities rather
than blocking camera rendering. The original camera hook, interpolation and
replay clock are unchanged.

ReShade runs through its manual runtime API inside Dolly's existing Present
callback. Capture occurs at its post-effects, pre-interface callback. Optional
loading runs outside the camera worker. The public API headers are pinned in
`native/vendor/reshade/UPSTREAM.json`; runtime DLLs and shader packs are not
redistributed.

References: [ReShade runtime API](https://crosire.github.io/reshade-docs/structreshade_1_1api_1_1effect__runtime.html),
[official installer source](https://github.com/crosire/reshade/blob/18deaa52de0c425a78b329e9cb3c497281cd00ec/setup/MainWindow.xaml.cs),
[Media Foundation sink writer](https://learn.microsoft.com/en-us/windows/win32/medfound/tutorial--using-the-sink-writer-to-encode-video).
Build checks and remaining runtime tests are recorded in [VALIDATION.md](VALIDATION.md).
