# Bone Picker (experimental, Native replay editor)

Bone Picker frames the selected player in the actual paused replay scene. Common
body joints appear as clickable dots, with a compact searchable list on the
right. A small crop of the hero appears in the panel header; it is captured
once from the native scene when the picker opens, so no separate game artwork
is bundled or repeatedly copied during editing.

1. Open the in-game **Bone Picker** tab and choose a player. With an empty
   shot, Dolly creates the starting view at the current replay tick and opens
   the picker automatically; you do not need to capture a camera first.
   On the desktop, choose a player and click **Bone Picker**. For an existing
   camera, choose its attached player, then **Bone Picker…** from the point menu
   or **Change bone…**.
2. Click a dot or anywhere inside a boxed bone row. Hover to see the exact rig name. If several dots
   overlap, choose from the small list at that location. Left/right refer to the
   player's own body. Search includes all available named bones, including cloth
   and accessory bones, even with **Common body joints** checked.
3. Choose **Preview attached view**. The right-hand list stays open; selecting another
   bone changes the preview immediately. **Hero overview** returns to the front view.
   Preview uses the camera's existing offsets, visibility, and clearance mode.
4. **Use this bone** saves the point and leaves attached preview active.
   **Cancel** discards the choice and restores the previous camera/preview.

The attached camera's **Forward / Right / Up** offsets are in the selected
point's local frame. For a face-facing shot, move Forward away from the head and
turn Yaw about 180 degrees. **Exact offset** keeps those authored values. The
opt-in **Automatic clearance** mode keeps a close head POV at least four units
forward of the head bone, while other bones use a 48-unit spacing sphere (30
units for eyes or weapon). It applies after smoothing without rewriting the
saved offset or rotation. This is an approximate guide, not mesh collision:
arms, clothing, and abilities can still obscure a shot.
The exact mode warns when the hero remains visible. Preview the motion before
recording.

The replay stays paused while the picker is open. If it was playing when opened,
normal completion resumes it. Finish or cancel before seeking, recording, or
performing another editor operation. Existing saved cameras and offsets are unchanged until Done. Starting from an
empty shot saves the initial attached view first; Cancel keeps that starting
view but discards the bone choice. The ordinary exact-name controls remain available for Console users.

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
Holding the picker pose does not freeze the game's animation system. Front framing cannot guarantee
that a crouched, folded, hidden, or wall-obstructed character will read clearly.
Cancel and choose a better replay moment when needed. There is no verified
mesh collision query or mouse-orbit control in this version.

The picker uses the selected model's verified named skeleton. Extra merged pose
entries without names are not invented as selectable bones. If the model cannot
provide a complete validated name table, the picker reports that limitation and
does not guess indices or silently switch players. Cancel remains available.


Bone and Weapon attachment now sample the completed animation at the reviewed
mesh-submission hook. The next view follows that local pose with bounded
one-view prediction (maximum 50 ms of age and 2 units of extrapolation), rebased
onto the current scene position. Aim remains unchanged. Missing or stale
samples fall back to the validated live provider. History resets on target,
mode, paused-tick changes and backwards replay movement. This works independently
of the Smooth setting; it does not turn off the game's animation interpolation.
Residual weapon animation and mesh intrusion are still possible.

## Recording a bone camera

After **Use this bone**, open the in-game **Export** tab. Keep **Camera path /
Bone camera** selected (Player POV is the separate F9 spectator workflow).
For a single attached view, choose **Replay duration**, then **Record video**.
Dolly saves a matching end view automatically, preserving the bone, offsets,
clearance, visibility and smoothing. The native camera follows that attachment
for the chosen replay seconds and recording finishes at the end. The endpoint
remains in the saved shot, so you can adjust its time or add camera transitions.
For existing multi-view shots, export uses the authored timeline as before.
Output folder and encoder setup remain in the desktop Export tab. Finish or
cancel the picker before recording; the inspection overview is not exported.

These entry and duration controls have offline regression coverage. A new
in-game recording with this shortcut has not yet been validated.
