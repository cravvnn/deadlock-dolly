# Deadlock Dolly 0.5.35-alpha

Changes since the published 0.5.34-alpha release.

## Visual Bone Picker

Choose bones directly on the hero inside the real replay scene. The picker opens
with a front-facing third-person overview, clickable joint markers, a small hero
portrait and a searchable bone list. Common body joints keep the initial list
manageable; full-rig search includes named accessory and cloth bones.

Click anywhere in a boxed bone row to select it. The filled selection dot replaces
the extra left-edge selection line. Bone controls now have their own **Bone Picker**
tab, leaving the Camera tab easier to read. The overview avoids connecting skeleton
lines, which could obscure the hero or shift with animation.

**Preview attached view** lets you inspect the chosen bone while keeping the list
open. Switch bones or return to **Hero overview** without leaving the picker.
**Use this bone** saves the choice and keeps its attached preview active.

## Start with a player, not camera setup

With an empty shot, open Bone Picker and choose a player. Dolly creates the starting
view at the current replay moment and opens the picker automatically. The desktop
player selector and Bone Picker button also support this workflow. Cancelling the
picker discards the bone choice but keeps that initial attached view.

## Record bone cameras from the in-game editor

After saving the bone, open **Export** and select **Camera path / Bone camera**.
A single attached view now offers **Replay duration**. Choose the duration and
record; Dolly adds the matching end view and records the attached shot through its
native camera-path exporter. Bone selection, offsets, visibility, clearance and
smoothing carry into the shot. Recording automatically finishes at the end.

The saved endpoint remains editable for later timing changes or camera transitions.
Existing multi-view shots use their authored timeline. Output folder and encoder
setup remain in the desktop Export tab. **Player POV** remains the separate F9
spectator-camera workflow. Finish or cancel the picker before recording.

## Smoother attached motion and clearer offsets

Attachment smoothing follows the player's movement immediately while filtering
local bone motion, reducing the camera falling behind during jumps and dashes.
Paused markers and previews hold a validated pose until the replay tick changes.

Bone and weapon cameras also use a more recent completed animation pose, with
bounded prediction between views. This reduces the mismatch between a smooth world
view and a jittering attached body or weapon, particularly in slow-motion replay.
Deadlock continues rendering its own models with animation interpolation enabled.

Offsets are labelled **Forward, Left/Right and Up**. **Exact offset** preserves the
position you author and warns about possible mesh intrusion when the hero is
visible. Optional **Automatic clearance** provides additional spacing: close head
POVs stay slightly forward, while other bones use a wider spacing guide. It does
not rewrite saved offsets and is not collision detection; arms, clothing or certain
animations can still enter the shot.

## Selection and recovery fixes

- Corrected stale attachment caching that could place a head camera near the
  character's feet after returning from the picker.
- Improved attachment revalidation after replay or game-UI transitions.
- Improved picker cancellation and camera ownership recovery, including restoring
  replay controls after attachment setup fails.
- More tolerant paused-view confirmation and clearer feedback when Deadlock has
  not rendered a stable paused frame yet. This does not claim to fix engine DX11
  stalls or all replay faults.

## Validation and current limits

The recent motion changes were checked in bounded local replay tests, including
Priest/Venator at 0.25x and 0.5x playback. These are not a guarantee of zero jitter
across every hero, ability, replay or bone. The newest player-first entry and
single-view recording shortcut have automated coverage but have not yet had a new
end-to-end in-game recording test.

Release checks include Python regressions, native checks and packaged startup and
updater smoke tests. The depth-scene graphics smoke test was deliberately omitted
after an earlier PC watchdog reset of unproven cause; the hardware encoder smoke
was also omitted. The omissions are recorded in native build metadata.

## Short publish description

**0.5.35-alpha**
- **Pick bones in the replay.** *Before:* Choosing a bone meant using a name list and checking the result separately. *After:* Select visible joints or searchable boxed rows, preview the attached view, and switch back to a hero overview in a dedicated Bone Picker tab.
- **Start and record a bone camera more easily.** *Before:* You needed to capture a camera first and set up a timed path. *After:* Choose a player to start, then choose a duration in Export to record a single attached view with its settings preserved.
- **Smoother attached shots.** *Before:* The camera could trail during sudden movement, and the attached body or guns could jump against a smooth background. *After:* Updated pose timing and smoothing reduce that mismatch, with Exact offset and optional Automatic clearance controls.
- **More reliable selection and recovery.** *Before:* Returning from the picker could reuse the wrong attachment or leave camera controls confusing after a failure. *After:* Attachment identity is revalidated, paused markers hold steady, and failed setup restores replay controls more reliably.
