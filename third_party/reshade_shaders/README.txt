Dolly bundled ReShade shader library
====================================

Third-party shaders shipped with Deadlock Dolly. Dolly does not ship a
ReShade runtime. These files are registered with the user's own runtime,
and only after they have selected that runtime in Dolly.

Included (redistributable):
  - crosire/reshade-shaders, "slim" branch (CC0 / public domain)
      https://github.com/crosire/reshade-shaders
  - prod80-ReShade-Repository (MIT), see prod80-LICENSE.txt
      https://github.com/prod80/prod80-ReShade-Repository

Deliberately NOT included:
  - iMMERSE and qUINT by Pascal Gilcher ("All rights reserved"). Do not add
    these until written redistribution permission is granted. The bundled
    Deadlock-AO preset references iMMERSE, so it only renders for users who
    install iMMERSE themselves and have a working depth source.

Deadlock-Dolly.ini is the default neutral preset and uses only bundled effects.
On first enable, Dolly copies the presets to its private user configuration
folder. Existing preset edits and the selected user preset are preserved.

Shaders/ and Textures/ map to ReShade's "Effect Search Paths" and "Texture
Search Paths". Do not rename the two folders.

Curated expansion (11 effects)
-----------------------------
  CinematicDOF, AdaptiveFog, DepthHaze (Frans Bouma / OtisFX)
  Vignette (CeeJayDK / SweetFX)
  AspectRatioSuite, HexLensFlare, CRT_Lottes (FXShaders contributors;
    CRTS core by Timothy Lottes)
  Oilify, Halftone (Insane-Shaders)
  Comic (Daodan)
  ColorMask (originalnicodr / CorgiFX)

EXPANSION.json records exact upstream revisions, hashes and local changes.
notices/ contains the complete upstream grants and contribution notices;
source headers also retain their original copyright, terms and disclaimers.
CinematicDOF uses its BSD-style source grant and KinoBokeh MIT contribution.
Its custom-image bokeh block is disabled; dithering reuses the existing MIT
prod80 noise. The six optional third-party bokeh images are not distributed.
SweetFX and the remaining OtisFX/FXShaders additions use MIT grants. Comic
uses BSD-3-Clause; Insane-Shaders and CorgiFX use CC0-1.0. CRT_Lottes
retains its public-domain core.

These effects are available in the existing F11 menu, disabled by default.
Saved presets are not changed. One primary DOF is supplied; no alternate DOF
collection or extra grading/sharpening packs are added. DepthHaze is included
as an optional distance-haze choice alongside AdaptiveFog.

CinematicDOF has its own manual focus and centered/mouse autofocus controls;
it does not follow Dolly's native focus sliders or authored focus animation.
Verify DisplayDepth before depth-based effects. ColorMask's BeforeColorMask
and AfterColorMask techniques bracket the effect(s) to mask, such as existing
prod80 black-and-white. The luma mask can limit grading by brightness.

Qualified with one ReShade 6.8/API20 D3D11 runtime, local replay and 720p SDR
single-frame Color captures. Performance at export resolution, long recordings,
fixed-step autofocus convergence and all effect combinations need user testing.
No game-folder ReShade installer or graphics proxy is used.

Second curated batch (4 effects)
-------------------------------
  CanvasMask (originalnicodr / CorgiFX): bracket existing effects with
    BeforeCanvasMask and AfterCanvasMask to blend by geometric/depth masks.
  ASCII (CeeJayDK / SweetFX): character-based image rendering.
  KinoMirror (papadanku / CShade, Keijiro Takahashi contribution): angular
    repetition and kaleidoscope mirroring, listed as CShade | KinoMirror.
  Droste (SirCobra / CobraFX): recursive circular/rectangular spiral warping.

Only these effects and their required shared headers are included, not the
whole upstream packs. Pinned source hashes and original notices are retained
in EXPANSION.json, notices/ and the source headers. No source adaptations.
These additions are disabled by default; existing presets remain unchanged.
CanvasMask differs from ColorMask's hue/luma selection: it masks the effect
chain spatially, with optional depth conditioning. Mask order matters.
Do not enable CanvasMask before/after techniques without placing the intended
effect(s) between them. Pattern scale and mirroring need tuning per shot.

The second batch passed exact ReShade 6.8 D3D11 offline compilation and
recorded 1280x720 Color samples through Dolly, with normal startup/cleanup.
Each run used one off/on/off sample set. CanvasMask checks geometric masking;
its optional depth modes are not certified by that sample. All four remain
subject to the export-size/performance/combination limits above.
