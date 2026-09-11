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

The previous client, engine, tier0, DX11 renderer and material-system modules
are available for investigation. The next useful inputs are:

- `game/bin/win64/scenesystem.dll` from the same Deadlock build, for scene-object
  classification and render submission analysis.
- A single-frame RenderDoc capture (`.rdc`) of a DX11 local replay, with a hero
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
