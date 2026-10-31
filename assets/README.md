# Dolly program icon — 0.2.2

The exact image reattached by the user is preserved byte-for-byte as
`logo-original.png`. Its SHA-256 is
`8edbc0fb726e16876345a861b16591cc9fc99cf30aef7ecacbd2e4c2ad478a2f`.
No redesign, image generation, repainting, cropping or background change was
performed for this release. The white backing is already in the supplied file.

`dolly.png` is a 256 px resampling for Tk fallback. `dolly.ico` contains 16, 20,
24, 32, 40, 48, 64, 128 and 256 px images, all encoded as 32-bit BGRA DIBs with
40-byte BITMAPINFOHEADERs, double-height fields and DWORD-aligned AND masks.
Every decoded frame is pixel-identical to its corresponding source resampling.

This bitmap encoding avoids the PNG-in-ICO incompatibility in older Tk 8.6
Windows readers. It is a packaging conversion, not a change to the logo.
The editor needs no Pillow dependency; icons are ready to use.

The artwork is separate from the source-code license. No ownership or
additional redistribution rights are asserted here.

## Editing game configuration

`editing/gameinfo.gi` uses stock configuration verified against build 6807
Steam depot content. Dolly's scoped changes disable standard user config
reads/writes, keep legacy settings lookup in its isolated USRLOCAL root and
disable the specific early-development warning for the editing session. Normal
intro continuation and hero announcements retain their existing behavior.
The launcher adds only its own temporary mounts, with clean defaults for
both Game and Mod lookups and an owned relative write directory.

`editing/cfg/` contains the current stock configuration/input defaults plus
Dolly's empty autoexec/config entry points. `cfg-manifest.json` records every
payload hash and stock provenance; `profile.json` pins that manifest, gameinfo,
video baseline and the reviewed installed modules/server. Refresh these pins
only with corresponding semantic compatibility evidence. The separate video
baseline imports no competitive quality values, upscaler or render scale.

These are game configuration data, not Dolly source code. The Dolly license
does not confer rights to Valve content. Runtime confirmation and visual checks
remain separate from stock-byte and offline module proof.

## Desktop UI art (assets/ui)

`assets/ui` holds the art of the redesigned desktop shell: Kara's Figma design
(file `design source`, page "Redesign", frame "Dolly / Replay" [design reference],
mode Dolly Blue), exported from Figma and turned into Dolly's files by
`tools/build_ui_art.py`. Every file is generated; do not edit them by hand.
The current set follows Kara's second export of 2026-10-24 (new statue art in
the sidebar, wider halftone rim). `manifest.json` lists each file with its size
and SHA-256, the eight hand-cut poses of the background shard and the colour
token name the shell draws it with. Dolly refuses a set whose hashes do not
match and falls back to flat colours.

* `backdrop_l0.png` to `backdrop_l4.png`: the still screen background
  (gradient, photo, ink wash, sigil and the halftone rim) at the design size,
  cut into five horizontal bands of 162 rows that Dolly stacks again, as 8-bit
  RGB with every row unfiltered so Dolly can read them without a PNG decoder.
  The picture never changes while Dolly runs (a picture that changes repaints
  the whole window); the dark shard is drawn by Dolly as a polygon in eight
  hand-cut poses and is all that moves in the background.
* `sidebar_art@1x.png`, `@1.5x`, `@2x`: the masked statue art at the bottom of
  the sidebar (alpha only, 6/255 at most), which Dolly composites into the
  sidebar's opaque gradient. The 1x file has Figma's pixels; the larger ones
  are bilinear enlargements of it.
* `nav_icons@1x.png`, `@1.5x`, `@2x`: the six navigation icons rasterised from
  their Figma strokes, one row per text colour token (`muted`, `text`).

Rebuild after a change to the Figma exports or to the `muted`/`text` tokens:

    python -I -B tools/build_ui_art.py --source <folder with the Figma exports>
    python -I -B tools/build_ui_art.py --verify

Building needs numpy (tooling only); verification and Dolly itself do not.
The build checks the result against Figma's own renders (mean difference at most
1/255) before it writes anything.

The images include game-derived artwork (the hero photo, the statue, the sigil
and the ink-wash texture). As with the program icon above, the artwork is
separate from the source-code license, and no ownership or additional
redistribution rights are asserted here. Kara should confirm the rights to
these images before a release ships them.

## UI typeface (assets/fonts/inter)

Kara's design sets the desktop UI in Inter (Regular for text, Medium for values
in fields, dropdowns, tables and keycaps). `assets/fonts/inter` holds four static
faces of Inter 4.1 (version string "Version 4.001", from the
[rsms/inter](https://github.com/rsms/inter) release `Inter-4.1.zip`, folder
`extras/ttf`), unmodified, with the release's `LICENSE.txt`:

| File | Windows family | Bytes | SHA-256 |
| --- | --- | --- | --- |
| `Inter-Regular.ttf` | Inter | 411640 | `40d692fce188e4471e2b3cba937be967878f631ad3ebbbdcd587687c7ebe0c82` |
| `Inter-Medium.ttf` | Inter Medium | 417300 | `97ad806f526e41546d46365bb3a393145f75b7b1568913db74549ad8b8dba872` |
| `Inter-SemiBold.ttf` | Inter SemiBold | 419744 | `78a843fade9d4612a5567302fb595b56976eb5fcebf4fea5a5912d638bafcde3` |
| `Inter-Bold.ttf` | Inter (bold) | 420428 | `288316099b1e0a47a4716d159098005eef7c0066921f34e3200393dbdb01947f` |

`dolly/ui/fonts.py` registers them privately for Dolly's process at startup
(`AddFontResourceExW` with `FR_PRIVATE`: nothing is installed in Windows). A
missing file leaves its face to Segoe UI. Inter is licensed under the SIL Open
Font License 1.1 (`LICENSE.txt`, Copyright 2016 The Inter Project Authors); the
fonts may be bundled and redistributed with Dolly but not sold on their own. The
VALVE titles and wordmark are never shipped (see `dolly/ui/fonts.py`).
