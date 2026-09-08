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
