### Deadlock Dolly v0.7.17-beta

**0.7.17-beta**

- **Spatial effect masking.** *Before:* the bundled ColorMask selected effects by hue or brightness. *After:* CanvasMask can blend an existing effect chain through geometric gradients, with optional depth controls. Place its Before and After techniques around the effects you want to mask.

- **Three distinct stylized looks.** *Before:* ASCII character rendering, kaleidoscope mirroring and recursive spiral effects needed separate shader downloads. *After:* ASCII, CShade | KinoMirror and Droste are included with their required headers and original notices.

- **Keep your existing setup.** *Before:* trying these four effects required adding their dependencies manually. *After:* they are packaged with Dolly and start disabled in the F11 menu. Existing presets and a working selected ReShade DLL are preserved; no replacement DLL or game-folder ReShade installation is required.

- **Checked Color output.** *Before:* these candidates had only offline compile results. *After:* all four also produced bounded 720p Color captures through Dolly with normal cleanup. Performance at larger export sizes, long recordings and every setting or combination remain for further testing.
