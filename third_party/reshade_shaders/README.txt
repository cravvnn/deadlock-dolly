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
