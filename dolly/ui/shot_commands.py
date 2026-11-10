"""Shared shot-edit transactions; no Tk or engine access.

Desktop and native actions reach these through the same application adapters.
The caller owns prompts, publication, UI refresh and controller capture.
"""
import copy

from ..path import TrackKey


def commit_camera(project, keys):
    candidate, keys = copy.deepcopy((project, list(keys)))
    candidate.keyframes = sorted(keys, key=lambda key: key.time)
    if candidate.keyframes:
        candidate.validate()
    return candidate


def delete_camera(project, index):
    """Delete one camera; deleting the leading camera rebases the shot start.

    Shot time zero is anchored to ``start_tick`` (``tick = start_tick + time *
    tick_rate``). Removing the leading camera would otherwise leave a dead
    lead-in ahead of the new first camera and keep the old anchor, so the user
    cannot re-start the shot there. Shift every authored time back by the
    removed offset and advance the anchor by the same number of ticks, so each
    camera and effect keeps its replay tick.
    """
    if not isinstance(index, int) or isinstance(index, bool) \
            or not 0 <= index < len(project.keyframes):
        raise ValueError("Choose a camera to delete.")
    candidate = copy.deepcopy(project)
    candidate.keyframes = sorted(
        (key for i, key in enumerate(candidate.keyframes) if i != index),
        key=lambda key: key.time)
    if index == 0 and candidate.keyframes:
        offset = candidate.keyframes[0].time
        if offset > 0:
            candidate.start_tick = int(round(candidate.start_tick + offset * candidate.tick_rate))
            for key in candidate.keyframes:
                key.time -= offset
            for track in candidate.tracks:
                _rebase_track(track, offset)
    if candidate.keyframes:
        candidate.validate()
    return candidate


def _rebase_track(track, offset):
    """Shift one effect track and collapse anything before the new start to 0."""
    kept = []
    boundary = None
    for key in sorted(track.keys, key=lambda key: key.time):
        time = key.time - offset
        if time <= 0:
            # Only the value in effect as the shot now begins still matters.
            boundary = key
        else:
            kept.append(TrackKey(time, key.value))
    if boundary is not None:
        kept.insert(0, TrackKey(0.0, boundary.value))
    track.keys = kept


def finish_capture(candidate, action, key, tick, selected=None):
    if action == "start":
        candidate.keyframes = [key]
        if tick is not None:
            candidate.start_tick = int(tick)
        candidate.interpolation = "smooth"
    elif action == "replace":
        # Recapturing the view changes its pose, not the user's saved movement
        # intent. New cameras still start with automatic handles.
        key.tangents = copy.deepcopy(candidate.keyframes[selected].tangents)
        candidate.keyframes[selected] = key
    else:
        if key.time < 0:
            # The in-game Capture path can report a tick before the shot's start
            # (replay scrubbed back); refuse it clearly instead of failing later
            # in Project.validate with "timestamps must be nonnegative".
            raise ValueError("Replay is before this shot's start. Start a new path here or choose Timed shot.")
        if any(existing.time == key.time for existing in candidate.keyframes):
            raise ValueError("This replay moment already has a view. Advance the replay, use Replace selected camera, "
                             "or choose Timed shot to add several views while paused.")
        candidate.keyframes.append(key)
    candidate.keyframes.sort(key=lambda item: item.time)
    candidate.validate()


def reset_rotation(keys, channel):
    keys = copy.deepcopy(keys)
    changed = False
    for key in keys:
        if getattr(key, "curve_" + channel) is not None:
            setattr(key, "curve_" + channel, None)
            changed = True
    return keys, changed


def reset_aspect(project, index):
    keys = copy.deepcopy(project.keyframes)
    keys[index].aspect_ratio = project.standard_aspect
    return keys


def selection_index(selected):
    return int(selected[0]) if selected else None


def move_history(redo, *, ready, sync, move, restore, empty):
    if not ready():
        return
    # Synchronize valid option edits before moving; no-op edits preserve Redo.
    sync()
    state = move(redo)
    if state is None:
        empty(redo)
        return
    restore(state, redo)
