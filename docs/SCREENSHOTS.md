# High-resolution screenshots

A screenshot is a clean still of the current paused view, built for
thumbnails: the normal game frame plus a players-only matte you can use as a
mask in Photoshop.

## Taking one

1. Open a replay in Dolly, pause it and frame the shot with the Dolly camera
   (free camera, Game Follow, Bone Picker or a camera from your path).
2. Click **Take screenshot** on the desktop **Export** tab, or
   **Screenshot (hero + plate)** on the in-game **F8 → Export** tab.
3. Leave the game alone while Dolly records. It runs a lossless color pass
   (plus depth), then a players pass, and assembles the still. The status line
   and log show the folder when it is done.

The still is written to a new `Dolly_Still_<shot>_<time>` folder beside the
Export tab's output file:

| File | Contents |
| --- | --- |
| `plate.png` | The normal game frame, 8-bit RGB. |
| `hero_alpha.png` | Players-only coverage matte, 16-bit grayscale. White is hero. |
| `hero_rgba.png` | The plate with that matte as its alpha, 16-bit RGBA: a ready cut-out. |
| `hero_isolated.png` | The players layer rendered on its own (no background bleed at the edges). It uses a preview tonemap, so its colors differ slightly from the plate. |
| `depth.exr` | Float scene depth in game units (optional, **Include depth (EXR)**). |

"Hero" means every player pawn and its equipment (weapons, attachments,
carried objects). NPCs, creeps and the world are excluded, and scenery in
front of a hero still occludes it.

### Photoshop

Open `plate.png`, then load `hero_alpha.png` as a layer mask (or
**Select → Load Selection** from it). `hero_rgba.png` already has that mask
applied. Use `hero_isolated.png` when the plate's edge pixels carry too much
background color.

## Resolution

Stills are saved at the game's render size, like video export. To render
larger than your monitor, pick a **Render size (next launch)** on the
Screenshot card (up to 7680 × 4320), then close the game and open the replay
again. Dolly writes `-windowed -noborder -w <width> -h <height>` into your
launch options; choose **Game window (no change)** to remove the size again.

The window then extends past the screen edges while you edit; this is
expected. Rendering at 8K is heavy: expect lower editing frame rates and
several GB of temporary space on the game drive during the players pass.
The card shows the size the game is rendering at right now.

## How it works

The still is a short fixed-step layered export of a held camera pose: 12
frames at 60 FPS and 0.05× replay speed, so the replay advances about a
hundredth of a second. Frame 8 is used for every output, after temporal
anti-aliasing and other frame-history effects have settled. Depth passes
force 100% render scale for the take, then restore your scale.

## Troubleshooting

- **Take screenshot is disabled:** launch a replay through Dolly and wait for
  the native editor; finish any recording first.
- **"Players capture did not finish cleanly":** the players layer is tied to
  the reviewed game build. After a Deadlock update it can stop working until
  Dolly's compatibility data is updated.
- **Depth failed:** untick **Include depth (EXR)** and take the still again.
- **Recording requires an even-sized backbuffer:** choose an even render size.
- If a still fails, its folder is kept with the raw takes for diagnosis.
