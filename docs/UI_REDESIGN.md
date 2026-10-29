# Dolly's redesigned interface

Kara's desktop design and in-game top rail use Dolly's current replay, camera,
attachment, curve and recording workflows. Existing shot files keep their saved
camera behavior.

- **Replay:** choose the game and replay, sort or filter the library, then use
  **START**. **Repair...** remains beside the selected replay. Shot files are in
  the File menu; Stop / restore and startup cancellation are footer controls.
- **Camera:** capture and edit cameras, curves and attachment in their cards.
  **Lock vertical aim** remains in Attach Camera. It affects Bone/Weapon aim;
  Eyes is unaffected. Detach before changing it, then Snap again to keep framing.
- **Look:** edit depth of field, effect tracks and scene appearance.
- **Export:** choose the camera source, destination format, encoder, output
  passes and still-image options. Current MP4, MOV and MKV handling, Players
  processing, audio and camera exports are retained.
- **Settings:** graphics profiles, recovery, ReShade and updates remain
  available, along with Discord Rich Presence and replay mod preferences.

In game, the Camera, Follow, Bone Picker, Look and Export buttons open one left
drawer at a time. Campaths can stay open alongside it. The Replay / Campaths /
Curves transport uses the live replay and camera path, including camera timing,
bank edits, curve gestures and undo/redo. Dropdowns and shortcut hints use the
current values and bindings.

The rail uses Kara's component layouts: Camera and Follow use narrow drawers;
Bone, Look and Export use two columns. Bone Picker uses the left-side joint
panel and view tabs. Numeric fields retain exact entry, and Campaths bank edits
retain live preview while waiting for the editor's acknowledgement.
Scene joints use clickable blue circles with contrasting rings. Switch between
Hero overview and Attached preview at the top of the picker; the duplicate lower
preview button is removed.
Campaths keeps **Seek camera**, **Replace selected**, then **Save Path** in
matching full-width rows. The Replay bar keeps Resume/Pause and the view arrows;
the Campaths timeline's **Seek here** still seeks along the shot.
If a saved endpoint falls between recorded replay packets, paused seeking uses
the next recorded packet while holding the final camera and effects at their
saved time. It does not extend the shot or relax playback/export range checks.

**F8** shows or hides the complete editor. **Escape** closes open drawers first;
the next press returns to flying. Text fields and popup dialogs retain Escape
while they are active. Clicking empty game space closes drawers while keeping
the editor's input ownership. The transport can be moved by dragging its empty
background; double-clicking that background returns it to the bottom dock.

The desktop sticker and backdrop retain the handoff's animation pace. Page
titles keep Oracle Medium when available; card titles stay at 15 design pixels.
Inter is bundled under its font license. Oracle faces use the user's available
fonts with a fallback; Dolly does not distribute the game's fonts.
The native overlay also reads the verified private Oracle cache. Font sizes
follow the design's em sizes at each viewport scale.

The classic overlay remains available for comparison by setting
`DOLLY_OVERLAY_UI=classic` before starting Dolly. This changes presentation only.
The redesigned loading-shaders card is compiled into the native UI pack.

The update notice's documented resize glitch remains a separate follow-up.
Native-caption dialogs and the text-based detailed DOF report retain their
existing workflows; the handoff does not provide complete redesigned versions
of every dialog. Offline screenshots and synthetic checks do not establish
in-game visual or camera-motion acceptance.
