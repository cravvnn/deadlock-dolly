# Layer export

0.5.3 records the normal scene at 30/60/120 FPS. Separated passes are not
implemented yet. The existing recorder copies the final color backbuffer;
it does not receive a verified main-view depth texture or hero draw IDs.

## Required render passes

| Output | Implementation requirement | Intended master |
| --- | --- | --- |
| Depth | Capture the main camera's depth before it is cleared or reused; verify projection, reverse Z, resolution scaling and sample count. | Float OpenEXR sequence, with optional grayscale video preview. |
| Heroes | Identify hero draw objects and their attachments; render foreground color and coverage while retaining world occlusion. | Color with alpha or a separate lossless matte. |
| World without heroes | Render the same scene time with identified heroes omitted, including the background they previously covered. | Normal color recording. |

A generic animated-object filter can also remove creeps, other NPCs and moving
props. It must not be presented as a hero-only filter. Weapons, ragdolls, shadows,
reflections and attached effects need explicit inclusion rules. Transparent
particles and glow are separate compositing problems.

Multiple passes need one shared scene sample. Replaying a shot several times
at real-time speed does not establish frame correspondence for particles,
animation or temporal effects. Depth should bypass ReShade color grading;
lossy H.264 is not a suitable master for numerical depth or a precise matte.

## Renderer evidence needed

### Native depth backend foundation

The native `dolly_depth` and `dolly_depth_readback` libraries now implement
calibrated D24/D32 conversion, a float `Z`-channel OpenEXR writer, and a bounded
three-slot GPU readback queue. Depth and candidate per-view buffers are copied
at the same command boundary. Calibration is validated from buffer contents,
including the viewport, projection inverse and camera basis; it does not rely
on a fixed shader slot. Missing or conflicting calibrations reject the frame.

The queue preserves sample IDs while the scene projection changes between
queued frames, handles padded mapped rows, and rejects deferred-context reads
and unannounced resolution changes. Synthetic DX11 tests pass on WARP and a
local hardware device. The OpenEXR reference reader preserves all test float
bits, including infinity and values beyond the half-float range.

The separate `dolly_depth_scene` library observes actual DX11 draw calls and
retains GPU copies of their camera constants. Its selector requires the reviewed
scene texture debug name, a full-size viewport and reversed depth writes.
Immediate and deferred draws are tracked, with command-list metadata owned by
the command list itself. Late depth clears, incompatible viewport writes,
duplicate scene targets and command lists from an untracked session reject the
frame. Tests exercise the hooks and resulting pixel/projection readback on both
WARP and a local hardware device, including repeated command-list execution.

The selector's texture name and per-view layout are supported by the two supplied
captures; they still need live verification in the gated replay session. The
tracker does not classify heroes or intercept arbitrary resource-copy mutations.

These libraries are not yet connected to the in-game recorder. Paired
color/depth submission, output-worker
integration, normalized video preview and UI controls remain to be completed.
Their tests establish the readback/writer behavior, not a working live layer
export or a hero/world classifier.

### Captured-frame investigation (September 12, 2026)

Two supplied DX11 captures were replayed locally with RenderDoc 1.46. Both
contain a 2560 x 1440, single-sample D24S8 scene depth target separate from the
depth target bound at Present. The latter is cleared during late UI passes;
reading only the currently bound depth target at Present would return the
wrong image. The scene target's depth follows the visible world and actor
silhouettes in the inspected frame.

The reflected `PerViewConstantBuffer_t` contains
`g_vInvProjLowerRight2x2 = (0, -1, 1/7, 0)` and viewport depth range `[0, 1]`
in both captures. With coefficients `(a, b, c, d)` and normalized device
depth `z`, positive camera-axis distance is `-(a*z+b)/(c*z+d)`, approximately
`7/z` for these captures. These are captured projection values, not defaults
to hard-code for every camera, game build or viewport.

A local single-frame float OpenEXR proof was generated from the scene's D24
samples. Decoding the EXR reproduced the input float buffer byte-for-byte.
Its distances are game world units along the camera axis, not metres or
radial distance. The normalized image is a viewing preview only.

This advances resource identification and depth conversion; it does not
implement live Dolly depth recording. Next, capture the verified scene depth
and projection with the same rendered color sample, then test resize,
resolution scaling, cancellation and resource reuse. Resource IDs in a
RenderDoc file are not runtime identifiers. Stencil values observed on actors
are not yet a verified hero-only mask, and removing their visible pixels
cannot reconstruct the world behind them.

The previous client, engine, tier0, DX11 renderer and material-system modules
are available for investigation. Further useful inputs are:

- `game/bin/win64/scenesystem.dll` from the same Deadlock build, for scene-object
  classification and render submission analysis.
- Further single-frame RenderDoc captures (`.rdc`) of DX11 local replays, with a hero
  visible against nearby and distant world geometry. Include the game build,
  resolution, antialiasing and upscaling settings. A video or ordinary Dolly
  diagnostic ZIP does not contain the GPU textures and draw commands.

Use the existing dev/-insecure replay workflow for inspection. Keep ReShade
disabled for the initial capture so its textures and UI do not obscure the
game's resources. Capture while the game is rendering normally, then save the
frame as `.rdc`. If the capture tool cannot attach or produces a blank capture,
send its error rather than changing Dolly's working render hooks.

These inputs support inspection; they do not establish that all requested
passes are already compatible. A first depth implementation must verify that
depth edges follow hero/world geometry as the camera moves, and that color and
depth come from the same frame. It must reject unsupported resources cleanly.
Hero/world filtering also needs tests for NPCs, attached weapons, occlusion,
resize, replay seeking and restoration after export stops.

## References

- [HLAE Source 2 streams](https://github.com/advancedfx/advancedfx/wiki/Source2:mirv_streams)
  documents separate depth streams, float depth output and a depth-matte
  example using an additional render with `SkinnedObject` hidden. Its documented
  Source 2 support is CS2; that example does not verify Deadlock hero filtering.
- [DX11 OMGetRenderTargets](https://learn.microsoft.com/en-us/windows/win32/api/d3d11/nf-d3d11-id3d11devicecontext-omgetrendertargets)
  exposes currently bound render targets and depth-stencil state. It does not
  identify which depth resource represents the main scene at final Present.
- [RenderDoc](https://github.com/baldurk/renderdoc) provides frame inspection
  for checking resources and draw calls.
