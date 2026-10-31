# Bone Picker (experimental, Native replay editor)

[Documentation](README.md) · [User guide](USER_GUIDE.md)

Bone Picker frames the selected player in the actual paused replay scene. Common
body joints appear as clickable dots, with a compact searchable list on the
right. The panel header uses the hero's small player icon from the installed game.
Dolly reads it when opening the picker, so updated artwork is picked up without
bundling game assets or capturing the scene. Missing or unsupported artwork leaves
the portrait slot empty; the player name and bone controls remain available.

1. Open the in-game **Bone Picker** tab and choose a player. With an empty
   shot, Dolly creates the starting view at the current replay tick and opens
   the picker automatically; you do not need to capture a camera first.
   On desktop with an empty shot, choose a player and click **Bone Picker...**.
   For an existing camera, enable **Attach to player**, choose the hero and
   **Apply to camera**, then open **Bone Picker...**. In-game, use the point
   menu's **Bone Picker...** or **Change bone...** for an existing attachment.
2. In **Hero overview**, hold the middle mouse button over the scene and drag
   to orbit around the character. Horizontal dragging turns through 360 degrees;
   vertical dragging changes elevation (limited near the poles to avoid flipping).
   The opening camera distance stays fixed. Release to stop. Orbit does not change
   saved camera offsets, zoom, or the selected bone.
3. Click a dot or anywhere inside a boxed bone row. Hover to see the exact rig name. If several dots
   overlap, choose from the small list at that location. Left/right refer to the
   player's own body. Search includes all available named bones, including cloth
   and accessory bones, even with **Common body joints** checked.
4. Choose **Preview attached view**. The right-hand list stays open; selecting another
   bone changes the preview immediately. **Hero overview** returns to your overview angle.
   Preview uses the camera's existing offsets, visibility, and clearance mode.
5. **Use this bone** saves the point and leaves attached preview active.
   **Cancel** discards the choice and restores the previous camera/preview.

The attached camera's **Forward / Sideways (L/R) / Up** offsets are in the selected
point's local frame. For a face-facing shot, move Forward away from the head and
turn Yaw about 180 degrees. **Exact offset** keeps those authored values. The
opt-in **Automatic clearance** mode keeps a close head POV at least four units
forward of the head bone, while other bones use a 48-unit spacing sphere (30
units for eyes or weapon). It applies after smoothing without rewriting the
saved offset or rotation. This is an approximate guide, not mesh collision:
arms, clothing, and abilities can still obscure a shot.
The exact mode warns when the hero remains visible. Preview the motion before
recording.

The picker requires a connected Native replay with validated attachment data.
Finish recording before opening it. The replay stays paused while the picker
is open; normal completion resumes it if it was playing when opened. Finish or
cancel before seeking, recording or performing another editor operation.
Existing saved cameras and offsets are unchanged until **Use this bone**.
Starting from an empty shot saves the initial attached view first; Cancel keeps
that starting view but discards the bone choice.

Opening Bone Picker from paused **Game Follow** transfers camera control
automatically; you do not need **Restore rig** first. **Attach here** also
supports that transfer. **Snap** is different: enter **Fly camera** first so
Dolly can calculate offsets from your free-camera view. If attached preview is
active, choose **Detach** first. Snap keeps the camera's framing relative to
the selected point as the player moves and changes aim. Existing saved
attachments keep their earlier behavior; snap an old camera again to use the
updated framing. The change to the camera is saved with the shot.
Shots saved with the updated Snap need a Dolly version that supports it.

The **Frame the camera** card has **Lock vertical aim** for Bone and Weapon.
With it on, the camera follows the point's position and yaw without tilting as
the player aims up or down or rolls. Eyes keeps its normal aim. The setting is
saved per camera and defaults off for existing shots. Detach before changing it,
then use **Snap** again to keep your current view with the new setting. This can
help with unwanted tilt; it does not remove all animation or motion jitter.
Shots using this option require a Dolly version that supports it.
The desktop Camera inspector offers the same setting; apply it to the camera
before snapping. Thanks to [@ExoCalibra](https://github.com/ExoCalibra) for the
original vertical-aim lock contribution.

Overview dots and attached previews hold one validated pose at each paused tick.
Stepping or resuming refreshes the pose; character/model identity is still
checked on every view. During playback, Dolly rebases the readable bone pose from its skeleton root
onto the current player scene position before applying offsets and smoothing.
This reduces a measured delay between camera sampling and mesh submission;
local animation can still update later in the frame, so some model-relative
motion remains. Smoothing uses that same current scene position. The picker deliberately draws no connecting skeleton lines.
Dots can be visible through the body or
scenery. They represent bone origins, not surface hit points. Animated poses can
still move slightly in the game's rendered mesh while the replay is paused.
While orbiting, markers project directly from the same held world pose using
the applied camera view; screen smoothing resets when that view changes so dots
do not trail camera motion. Holding the picker pose does not freeze the game's
animation system. Overview framing cannot guarantee
that a crouched, folded, hidden, or wall-obstructed character will read clearly.
Cancel and choose a better replay moment when needed. There is no verified
mesh collision query; orbiting near walls can put the camera behind scenery.

During attached preview, flight movement follows the camera's displayed view.
With the default bindings, **W/S** move forward or backward along the view,
**A/D** move sideways, and the vertical controls move up and down. These movements update the
selected camera's attachment offsets. The view and target frames can differ
while smoothing; Dolly uses the displayed view for movement and keeps the
selected camera's aim-lock setting.

The picker uses the selected model's verified named skeleton. Extra merged pose
entries without names are not invented as selectable bones. If the model cannot
provide a complete validated name table, the picker reports that limitation and
does not guess indices or silently switch players. Cancel remains available.


Bone and Weapon attachment sample the completed animation at the reviewed
mesh-submission hook. The next view follows that local pose with bounded
one-view prediction (maximum 50 ms of age and 2 units of extrapolation), rebased
onto the current scene position. Prediction itself leaves aim unchanged; the
optional vertical-aim lock changes the camera frame as described above. Missing or stale
samples fall back to the validated live provider. History resets on target,
mode, paused-tick changes and backwards replay movement. This works independently
of the Smooth setting; it does not turn off the game's animation interpolation.
Residual weapon animation and mesh intrusion are still possible.

## Recording a bone camera

After **Use this bone**, open the in-game **Export** tab. Keep **Camera path /
Bone camera** selected (**Camera path** on desktop). **Player POV** is the
separate spectator workflow, selected through Game Follow or F9.
For a single attached view, choose **Replay duration** in-game or **Replay
seconds** on desktop, then **Record video**. Dolly builds a timed take from that
view, preserving its bone, offsets, clearance, visibility and smoothing. The
native camera follows the attachment for those replay seconds and recording
finishes at the end. The temporary endpoint belongs only to the take: your
single saved camera stays intact, ready for another duration or later edits.
For existing multi-view shots, export uses the authored timeline as before.
Choose the output file on desktop Export; encoder and capture settings are also
available in-game. Finish or cancel the picker before recording; the inspection
overview is not exported. Camera edits stay locked until the recorder finishes
finalizing. **Finish recording** saves an early end; **Stop / restore** controls
camera playback and restoration instead. See [video settings](VIDEO_AND_RESHADE.md)
and [layer export](LAYER_EXPORT.md) for output and audio restrictions.

## Limits and troubleshooting

Heroes, cosmetics and replay poses can have animation or visibility differences;
neither a held pose nor smoothing guarantees zero jitter. If the model, target
or named skeleton changes, cancel and reopen the picker rather than copying
indices from another model. **Hide this hero** does not suppress every rendering
pass, and automatic clearance is not mesh collision. The
[changelog](CHANGELOG.md) records past attachment changes; those releases do
not certify every hero or current game update.

## Credits

See [Dolly credits and contributors](https://github.com/cravvnn/deadlock-dolly/blob/main/CREDITS.md)
for the people, projects and tools behind Dolly.
