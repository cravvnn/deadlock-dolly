### Deadlock Dolly v0.7.15-beta

**0.7.15-beta**

- **Eleven more ReShade effects.** *Before:* the bundled library focused on color controls. *After:* choose CinematicDOF, AdaptiveFog, DepthHaze, Vignette, AspectRatioSuite, HexLensFlare, Oilify, Halftone, Comic, CRT_Lottes and ColorMask in the existing ReShade menu.

- **Cinematic looks with your own presets.** *Before:* these depth, haze, selective-mask and stylized effects needed separate setup. *After:* the curated bundle includes required dependencies, source records and original notices. New effects start disabled and existing presets remain yours. CinematicDOF has independent focus controls; it does not follow Dolly's focus animation.

- **Stricter still and depth validation.** *Before:* malformed pixels, overflowed camera comparisons, invalid normals options or EXR offsets into metadata could be accepted. *After:* these unsupported values are rejected while valid still, camera and depth data retain their normal behavior.

- **Less repeated camera compilation work.** *Before:* attached shots repeatedly scanned earlier keys, and an inaccessible FFmpeg path could escape optional-tool lookup. *After:* compilation walks sorted keys once, and path inspection errors retain normal unavailable/fallback handling. This changes compile work, not game rendering speed.
