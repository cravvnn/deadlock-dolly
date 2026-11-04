# Layer export

[Documentation](README.md) · [User guide](USER_GUIDE.md)

Use the desktop **Export** tab to record Color with paired depth and optional
World, Players or Effects takes. The in-game **F8 → Export** page shares these
settings. For ordinary recording and ReShade setup, see
[Video and ReShade](VIDEO_AND_RESHADE.md); for a single still, see
[Screenshots](SCREENSHOTS.md).

## Record a layered take

1. Open a supported local replay through Dolly in **Native** camera mode and
   DirectX 11. Prepare the camera path, or choose **Player POV** and its replay
   duration. Finish or cancel Bone Picker before recording.
2. Choose a new **Output file**. Under **Output passes**, enable the outputs
   you need. Color is always included.
3. Use **Fixed-step export (frame-accurate)** for extra layers. Selecting a
   layer enables it; Dolly rejects a layered export if you turn it off again.
   Select **Video FPS** and **Export speed** before starting.
4. Under **Encoder & quality**, choose an FFmpeg encoder. **Auto** uses the
   bundled FFmpeg when available. The built-in Windows encoder cannot produce
   these depth/layer outputs. Use an MP4 encoder for World or Effects; their
   intermediate takes currently require `.mp4`, so **Lossless FFV1 (.mkv)** is
   not suitable for those two options.
5. Press **Record video** (or **Record POV**). Dolly records Color first, then
   the selected extra takes automatically. Let the shot finish and wait for
   processing and finalization before closing the game.

**Depth master (.mov)** enables fixed-step by default, but depth alone can also
be recorded in real time. **EXR sequence (float)** is available only while the
depth master is selected. The Color take can carry game or reconstructed audio
in fixed-step mode through the engine's frame-locked WAV. Extra layer takes stay
silent; export them separately when you need them.

## Outputs

Choosing `Shot.mp4` with depth or extra layers creates a new `Shot/` folder
beside the requested output. Existing take folders are refused.

```text
Shot/
  Shot.mp4
  shot.json
  depth/
    depth.mov
    manifest.json
    camera.json
    preview.mp4
    exr/
      00000000.exr
      00000001.exr
  world/
    world.mp4
  players/
    players.mov
  effects/
    effects.mov
```

Only selected outputs appear. The EXR directory requires **EXR sequence
(float)**. A lossless Color/depth take uses `Shot.mkv` and a `preview.mkv`;
the Players master is still a `.mov`. Preview encoding is best-effort: if it
fails, the completed depth master remains and `preview_<width>x<height>.raw`
is kept for diagnosis. Shot sidecars describe the frames actually written.

| Output | What it contains | Limits |
| --- | --- | --- |
| Color | Normal scene recording, including active ReShade color effects. | Dolly's interface and guides are excluded; use the HUD controls for the game's own interface. |
| Depth | Paired 10-bit ProRes grayscale `depth.mov`; optional single-channel float-Z EXRs. | Distances run along the camera axis in game units. The MOV maps 0–8192 units to grayscale and clips larger distances; use EXRs for numerical work. |
| World | A separate recording of the reviewed world/lighting scene classes. | Hides skinned units and particle systems, including NPCs; it is not a filter that removes only players. |
| Players | Native player-owned draws and equipment, encoded as a ProRes 4444 alpha master. | The world remains for occlusion. Captures all supported player pawns, not one selected hero. |
| Effects | Particle-system scene classes, combined into a ProRes 4444 alpha master. | Alpha is estimated from separate black/white MP4 takes; temporal differences and encoded matte precision can affect edges. |

Players includes supported player-owned weapons, attachments and carried
objects, while excluding NPCs and creeps. It does not promise a complete
isolated render of every shadow, reflection, glow or transparent effect. The
Players MOV uses a display conversion; it is not a raw linear-HDR render pass.
Missing, transparent or dark regions need inspection in the actual take.
If a Players layer appears blank, export diagnostics from the same editing
session before closing the game. The bundle includes bounded pixel-coverage
measurements linked to the capture request, so support can investigate without
the video file.


When the scene uses a smaller viewport inside its render-target allocation,
Players maps that active region across the exported canvas instead of retaining
the unused padding. The original draw and scenery depth test remain unchanged.
This corrects framing; it does not reproduce the game's temporal upscaler or
guarantee identical antialiasing at every edge. Unsupported or changing viewports
stop the capture rather than silently changing its framing.

Players uses the completed Color take's frame count. Its fixed-step engine clock
is `Video FPS / Export speed`; keep it at or below about 300 Hz for reliable
captures (for example 120 FPS at 0.5x = 240 Hz). Dolly rejects clocks above
600 Hz before recording. If the shot still misses frames, the empty Players
folder is removed and the operation reports the capture status; retry with a
lower Video FPS or an Export speed closer to 1x. The scratch capture remains
for diagnosis.

## Depth and camera data

Color and depth are copied from the same admitted render sample. Missing or
ambiguous scene depth, conflicting calibration, changed resources or failed
frame pairing stops the take instead of writing an unmatched pair. EXR file
indices follow encoded Color frame order; capture timestamps can differ from
the encoded video's timeline.

The depth master keeps the verified scene target's own dimensions. The
manifest records those as `width`/`height`, with the Color size separately as
`color_width`/`color_height`. `camera.json` records the matching view and
projection for reconstruction; see [Camera bundle](CAMERA_BUNDLE.md).

Depth export temporarily sets render scale to 100% for Color and its selected
extra passes, then restores the saved scale. This does not change window size,
antialiasing or the selected upscaler. Full scale can cost performance, and
does not make every antialiasing/upscaling mode pass the current projection
checks. The depth readback has its own GPU memory budget; the screenshot
menu's 5K and 8K sizes exceed that budget at full scale.

The preview is an 8-bit, reduced-resolution viewing aid with 8192 units mapped
to white. It is not a numerical master or a Players matte. EXRs retain float
precision and represent infinite far-plane/sky depth as positive infinity.

## Alignment, processing and restoration

Fixed-step uses `Export speed / Video FPS` as the simulation time per output
frame and waits for readback and writer capacity. Waiting is bounded: a
capture or encoder failure can still stop a take. Check the final status and
frame counts. Replaying several fixed-step takes does not guarantee identical
particles, animation histories or temporal post-processing between them.

World and Effects use reviewed scene-class keep-lists. A missing required
class stops that layer; newly discovered classes are excluded rather than
silently included. Effects temporarily changes supported bloom settings for
its matte pair. Dolly restores layer visibility, matte settings and export
timing at the end; follow any restoration error before continuing to edit.

Players uses one native ownership capture, not a black/white matte pair.
During fixed-step capture, Dolly automatically waits when GPU readback or the
scratch writer needs more time. The selected Video FPS and frame count stay
the same; slower hardware takes longer to export. The status shows when Dolly
is waiting and how many frames have been written. Capture memory stays bounded.
A write error or 30 seconds without GPU or disk progress stops the take and
keeps its scratch data for diagnosis.

**Players processing** offers Background, Balanced and Fast conversion modes.
**Players scratch folder** accepts an existing ordinary local folder; blank
uses the output directory. Raw capture can be much larger than the MOV, so
Dolly checks free space before recording. Failed or cancelled scratch captures
remain in their own `dolly-players-<request>/` folders with `capture.json`.

**Finish recording** finalizes the current take; **Discard recording** cancels
it. Finished outputs from earlier passes can remain if a later pass fails.
Native failed/cancelled depth output is removed, while failed matte inputs and
Players scratch data can be kept for diagnosis. Existing unrelated files are
preserved. The export audit reports missing metadata and frame-count problems;
it does not certify visual completeness.

## Technical background

[Renderer and capture notes](internal/LAYER_EXPORT.md) describe the backend
and retain the early frame-capture research. Those notes are separate from
the workflow above.

## Credits

See [Dolly credits and contributors](https://github.com/cravvnn/deadlock-dolly/blob/main/CREDITS.md)
for the people, projects and tools behind Dolly.
