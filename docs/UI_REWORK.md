# In-game editor

[Documentation](README.md) · [User guide](USER_GUIDE.md)

The default interface uses Kara's top rail: **Camera**, **Follow**, **Bone
Picker**, **Look** and **Export** open drawers, with **Campaths** on the right
and the replay/shot/curve transport below. See the [redesigned interface
guide](UI_REDESIGN.md) for placement, Escape behavior and the classic fallback.

The control reference below also describes the classic overlay. Its main panel
has five tabs and its Campaths list sits on the left. Layout and Arrange
instructions apply to that fallback; the rail uses fixed drawers.

Fresh layouts put Campaths in the upper left and the main panel in the upper
right, leaving space above for ReShade's startup banner. Saved main-panel and
Campaths arrangements take priority over these defaults. Outside Arrange, the
timeline stays near the bottom of the screen while keeping its custom width
and horizontal position.

The UI rework and saved layouts were contributed by
[@tbhtho](https://github.com/tbhtho).

See the full [credits](../CREDITS.md) for Dolly's contributors and supporting
projects, including [@paralin](https://github.com/paralin)
for Linux/Proton discovery and compatibility fixes.

## Show or hide the panels

Press **F8** to hide the main panel, Campaths and the timeline together. Press it
again to bring them back in their current layout. Path guides and the
framing grid can stay visible with the panels hidden; path guides hide during
playback.

Starting **Play cinematic** hides the panels automatically. You can press F8 to
bring them back while the shot keeps playing. **F9** opens Deadlock's own replay
controls.

## Arrange your workspace

Drag the main panel or Campaths by its header. The four-arrow cursor shows where
you can grab it; you do not need to turn on an arrangement mode first.

Open **Layout** for the other layout controls:

- **Arrange panels** lets you move the timeline, resize panels and rearrange
  sections within a tab. Leaving Arrange reattaches the timeline near the bottom
  of the screen, keeping its width and horizontal position.
- **Classic arrangement** restores the default upper-left Campaths, upper-right
  main panel and bottom timeline.
- **Reset sections in this tab** restores the section order for the current tab.
- **Reduce motion** turns off optional UI transitions.

Layout changes save automatically in your user profile, separately from camera
projects and the app folder, so installing an update keeps them. Completed
moves, resizes and layout choices save promptly. Dolly retains a valid backup
and restores it if the latest layout file is damaged after a crash.

Panel arrangements, timeline width and horizontal position, the main panel's
tab and curve-view choices are remembered. The timeline fits its Replay or
Campaths controls, grows for Curves and adjusts to viewport changes. It starts on **Replay** in each new
session. Switching tabs or hiding the panels with F8 keeps your current view
during that session. The temporary original-curve snapshot is not saved.

Cards beside one another share a row height. Shorter cards keep space below their
controls; text and buttons keep their normal spacing. Narrow windows stack the
cards, and longer pages scroll inside the panel.

## Capture and view cameras

In-game **Capture** keeps the replay running by default. To pause after each
capture, check **Pause after capture** beside **Resume/Pause** on the timeline.
Dolly remembers your choice between sessions. It also applies to in-game
**Replace** and the capture shortcut.

Campaths lists each saved camera's arrival time, aspect ratio and bank. Click a
row to select it without moving the view; double-click to view it at the current
paused replay moment. **Previous** and **Forward** view the cameras on either side of
the current selection at the current replay moment. In **Replay**, select a
camera row and press **Seek camera** to go to its recorded replay time and saved
view. The replay slider and tick field seek while retaining your current camera.
In **Campaths**, **Seek here** applies the selected point on the shot timeline.

**Play cinematic** prepares and starts the shot. You do not need to press Play
again when preparation finishes.

Select, view, seek and edit controls require the appropriate paused, idle
editor state. Finish a recording or finalization and detach a bone preview
before changing saved cameras. **Save Path**, **Delete**, **Clear All** and
shot Undo/Redo are below the Campaths table.

## Finish a recording before editing cameras

An authored camera-path take finishes automatically when the shot ends. A
free-camera recording without a path stays open until **Finish recording**;
pausing the replay alone does not finalize it. **Finish recording** is in the
main panel's footer and remains available on every tab while recording. Use it
to stop early, then wait for **Finalizing video...** to finish saving. Camera
edits, including **Clear All**, become available again afterward.

**Stop / restore** controls playback and restoration. Use **Finish recording**
to close and save the video take.

For a shot with one attached camera, **Replay duration** on in-game Export
(**Replay seconds** on desktop) chooses the length of that take. Recording
keeps the single saved camera intact, so you can choose another duration for
the next take. A shot with multiple cameras keeps its authored timing.

**Player POV** records the game's selected spectator view instead. Select a
hero with Game Follow or F9, pause at the start, return with F8 and choose
**Record POV**. Keep **Camera path / Bone camera** selected for a saved bone
attachment or path. Both workflows use the chosen export settings; see
[Video and ReShade](VIDEO_AND_RESHADE.md) and [layer export](LAYER_EXPORT.md)
for audio, fixed-step and pass restrictions.

## Edit camera curves

Choose **Curves** on the bottom timeline, then open **Position**, **Rotation** or
**Framing**. Position has X, Y and Z; Rotation has Pitch, Yaw and Roll; Framing
controls Aspect Ratio. Switching channels keeps your selected camera and time
window. Each channel keeps its own value scale.

Click a point to select its camera. Drag the selected point vertically to change
its value, or drag the number and Ctrl+click to type. Hold **Alt** during a graph
drag to align with a nearby value on the immediately preceding or following
camera. Release to apply; **right-click** cancels an active graph or numeric
adjustment. The camera's arrival time stays the same, and each completed gesture
is one Undo step. Use **Seek here** or **Play cinematic** to check the result.

Use the mouse wheel to zoom time and right-drag to pan when no adjustment is
active. **Fit all** brings the
whole shot and its visible values back into view, including cameras on another
page of Campaths. It changes the view, not your camera values or Undo history.
New camera ranges fit the full path, including shots shorter than one second.
An accepted value outside the plot expands its value range so the key remains
accessible; deliberate time zoom and panning are retained for in-range edits.

Check **Stack graphs** to see groups together. **Graphs** chooses which groups
are visible; it replaces the group tabs while stacked. The plots share a time
ruler and playhead, with separate value scales. Choose X, Y or Z, or Pitch, Yaw
or Roll, with the buttons beside each graph title. A compact dropdown takes
their place when the panel is too narrow. At least one graph stays visible.

**Replay** and **Campaths** keep their existing navigation controls. In Curves,
open **More** for previous/next camera, Redo and FOV. The main Camera, Follow,
Bone Picker, Look and Export tabs remain available; scroll their contents when
the graphs need more space. **Stop / restore** and **Finish recording** stay
outside that scrolling area.
In a narrow Curves panel, dropdowns replace the mode and single-view group tabs;
**View** contains **Fit all** and **Update original**.

Check **Show original** to pin the current curves as a read-only snapshot. They
appear as faded dashed lines behind the solid current curves. This snapshot
stays fixed through edits and Undo; **Update original** replaces it with the
current curves. It does not restore camera values. Opening or creating another
shot clears the snapshot. It is temporary and is not saved in the project.

Curve edits work on free-camera keys while paused. For a camera attached to a
player or bone, use its attachment offsets instead. Rotation curves change the
authored rotation override; they preserve the captured angles. The desktop
Camera page uses the same Position / Rotation / Framing organization, with
stacked graphs and an original snapshot. Its path interpolation and rotation
reset controls remain available. Both editors use the normal shot save and
undo history.

### Saved arrival and departure handles

In the desktop Camera page or the in-game timeline's **Curves** view, select a
free camera in the **Position** graph and choose X, Y or Z. With path
interpolation set to **Smooth**, the **Arrival** and
**Departure** controls set that axis's slope in world units per shot second.
Drag a round handle vertically or type a slope; in-game numeric fields support
Ctrl+click to type. The short handle lines are a display aid: zooming or resizing
them does not change the saved slope or the camera's time and position.
Each completed edit is one Undo step, and unfinished drags are cancelled
when the selection, shot or editing state changes.

With path guides enabled in a paused replay, dragging a graph key or a Position
handle previews the path line and camera markers in the game. You can stand back
and inspect the changing path from your current view. The desktop graph's value
also follows the drag. Release to save the adjustment, or right-click to cancel
and restore the saved path. Double-clicking a key or handle still lets you drag;
double-click empty graph space to fit the curve.

**Auto** uses Dolly's calculated slope. **Flat** sets both slopes to zero;
flatten all three axes for a stationary arrival/departure at that camera.
**Linked** keeps both slopes equal; **Broken** lets them differ. These are value
curve handles with fixed horizontal positions, rather than separate time keys.
The first camera has no arrival segment, and the last has no departure segment.

Handles affect **XYZ movement in Smooth only**. Switching to Linear or Spline
preserves the saved handles while using that mode's normal interpolation.
Returning to Smooth restores their effect. Rotation and framing retain their
existing interpolation. The desktop and in-game graph, preview and normal
export use the same saved movement. In-game edits require a paused replay and
Dolly's panel input; **right-click** cancels an adjustment without closing F8.

Handles survive save/reopen, Undo and Update view/recapture. Shots containing
authored handles use project format 12 and need a Dolly version that supports it;
shots without handles keep their existing format. Converting the shot's tick
rate preserves motion at corresponding replay ticks by scaling slopes with
time. Editing an individual camera time keeps its saved units-per-second slope.

## Level the camera again

While the replay is paused and Dolly owns the camera, press **R** to level the
view and return it to the shot's normal aspect and zoom. Use it after banking
with the roll keys or reframing with the wheel or the FOV box.

Level view changes only the live camera you are composing with. No saved camera,
project value or Undo step is touched, so pressing **R** never edits your shot;
selecting another camera or previewing one again returns that camera's own
framing. Rebind it under **Controls** like any other shortcut — **Ctrl+Alt+R**
stays Replace.

The default **Stop / restore** shortcut is **Backspace**. F6 belongs to
Deadlock's developer UI; existing saved bindings may differ. Check the desktop
**Controls** page before relying on a shortcut.

## Find a player or a replay moment

A newly loaded replay starts with the full timeline in view. Dolly reads the
replay events in the background, then shows the selected player's kills in blue
and deaths in red. Hover their name for full-replay totals. Click an event marker
to seek to it; zoom in to separate markers that are close together.

To follow a player, pause the replay and use the arrows beside their name, or
click the name to select that player again. Detach a Bone Picker camera first
if one is active. Selecting a player does not change the tick or resume playback.
Replay seeks and tick steps keep that participant selected and resolve their
current hero after the seek. Choose **Fly camera** to leave Follow and compose a
fixed view. If the hero is absent, Dolly shows a status message and never moves
the camera to a guessed or stale target.

When you close the game, Dolly returns to the launcher and keeps your open shot
and editing history.

## Desktop controls

The desktop always shows Replay, Camera, Look, Export, Controls and Settings.
The sidebar has a **Controls** page for shortcuts, camera look sensitivity
and movement speed. Search for an action or its current shortcut, then click the
shortcut button and press its replacement. Review it before choosing **Save shortcut**.
A conflict leaves the existing shortcuts in place. **Clear** removes one binding;
**Reset** restores its default. The ReShade shortcut uses the same editor.

The desktop Camera table and graphs share one outer scroll region. The table
keeps its own scrolling when it has more cameras than it can show. Closed
selectors let the wheel scroll the page without changing their value; curve
plots keep wheel zoom and right-drag panning.
