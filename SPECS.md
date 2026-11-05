# Maintained model coverage

FollowSelection.tla beside controller.py models only the selected-player handoff:
paused unchanged/advanced ticks, transient observer mode4, camera readiness,
cancellation and identity loss. Existing seek, monitor and game primitives are
abstracted; this is not whole-program or live proof. Default214states/6guard
mutants checked by tools/check_tla.py and before native release compilation.
The code avoids redundant reconstruction, retains actual drift restoration and
strict rig admission. No state-count reduction claim against a failing baseline.
Other Dolly stateful components remain outside this scoped model.

The live-observed build6849 intro transition lasts7.5 replay seconds. The controller
waits cancellably for readiness while temporarily resuming its owned replay, then
pauses and restores the chosen tick before admitting the rig. It does not change
playback speed. The160s wall bound accommodates7.5s at supported minimum0.05x
plus10s margin. Model time is abstracted to settle/abort; clock duration and exact
speed preservation are covered by owner tests and bounded live evidence. No new
state holder was added for speed. A virtual-time regression exceeds the old5s
budget; actualsix-switch verification uses the matching replay at581 and5000.
