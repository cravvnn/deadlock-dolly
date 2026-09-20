"""Tk-side dispatcher for acknowledged native in-game editor actions.

Only this module dispatches game-side events into the existing project editor.
Game lifecycle work stays on Dolly's existing operation worker; the game never
waits for a Python UI callback while rendering a view.
"""
from __future__ import annotations

from dataclasses import replace
import copy
import logging
import math
import time
from .editor_wire import ATTACH_NO_TARGET, ATTACH_OFFSET_LIMIT
from .native_bridge import NativeBridgeError
from .native_effects import model_token
from .path import AttachKey
from .settings import save_settings
from .video_export import (BITRATE_PRESETS, CODEC_BY_KEY, CODEC_CHOICES, CODEC_LABEL_TO_KEY,
                           DEFAULT_CODEC_KEY)

LOG = logging.getLogger(__name__)
VIDEO_FPS = (30, 60, 120, 300, 600)
VIDEO_BITRATE_MBPS = (10, 20, 40)
CODEC_MAX = 10


def _bridge(app):
    return app.controller._native_bridge()


def _value(app, name, default=None):
    value = getattr(app, name, default)
    return value.get() if hasattr(value, "get") else value


def _playback_values(app):
    # The editable desktop speed can briefly be empty, '-' or out of range
    # while typing. Keep publishing the last valid setting until it is valid;
    # an unfinished edit must never block input-owner/camera-count refreshes.
    previous = getattr(app, "_native_editor_config_cache", None) or {}
    speed = previous.get("playback_speed", 1.0)
    rate = previous.get("playback_rate", 60)
    try:
        candidate = float(_value(app, "speed", 1.0))
        if math.isfinite(candidate) and .05 <= candidate <= 4:
            speed = candidate
    except (ValueError, TypeError):
        pass
    try:
        candidate = float(_value(app, "rate", 60))
        if candidate in (30, 60, 120):
            rate = int(candidate)
    except (ValueError, TypeError):
        pass
    return speed, rate


def _codec_label(codec_id):
    for _key, label, _encoder, codec, _needs in CODEC_CHOICES:
        if codec == codec_id:
            return label
    return CODEC_CHOICES[0][1]


def _video_values(app):
    # The in-game Export page mirrors the desktop Export tab. Keep the last
    # valid values while the desktop fields are mid-edit, exactly like playback.
    previous = getattr(app, "_native_editor_config_cache", None) or {}
    fps = previous.get("video_fps", 60)
    bitrate = previous.get("video_bitrate_mbps", 20)
    codec = previous.get("video_encoder", 0)
    fixed = bool(previous.get("video_fixed_step", False))
    depth = bool(previous.get("video_depth", False))
    depth_exr = bool(previous.get("video_depth_exr", False))
    speed = previous.get("video_speed", 1.0)
    try:
        candidate = int(_value(app, "video_fps", 60))
        if candidate in VIDEO_FPS:
            fps = candidate
    except (ValueError, TypeError):
        pass
    try:
        candidate = BITRATE_PRESETS.get(_value(app, "video_bitrate", "20 Mbps"))
        if candidate in (10_000_000, 20_000_000, 40_000_000):
            bitrate = candidate // 1_000_000
    except (ValueError, TypeError, AttributeError):
        pass
    try:
        key = CODEC_LABEL_TO_KEY.get(_value(app, "video_codec", ""), DEFAULT_CODEC_KEY)
        candidate = CODEC_BY_KEY[key][2]
        if 0 <= candidate <= CODEC_MAX:
            codec = candidate
    except (ValueError, TypeError, KeyError, IndexError):
        pass
    fixed = bool(_value(app, "video_fixed_step", fixed))
    depth = bool(_value(app, "video_depth", depth))
    depth_exr = bool(_value(app, "video_depth_exr", depth_exr)) and depth
    try:
        candidate = float(_value(app, "video_export_speed", 1.0))
        if math.isfinite(candidate) and .05 <= candidate <= 4:
            speed = candidate
    except (ValueError, TypeError):
        pass
    return fps, bitrate, codec, fixed, speed, depth, depth_exr


def _configure_visualization(app, bridge, active, selected):
    publish = getattr(bridge, "publish_visualization", None)
    if publish is None:
        return
    project = app.project
    # Keep primitives, not mutable Keyframe objects: replacing an in-place
    # camera must invalidate the published guides. The native path limit also
    # bounds work on the Tk thread for oversized, console-only projects.
    from .native_path import CHANNELS, MAX_CAMERA_KEYS
    keys = (tuple((key.time, *(getattr(key, name) for name in CHANNELS))
                  for key in project.keyframes)
            if len(project.keyframes) <= MAX_CAMERA_KEYS else len(project.keyframes))
    signature = (active, selected, project.interpolation, project.rotation_mode,
                 project.lens_interpolation, project.standard_aspect, project.duration, keys)
    if (getattr(app, "_native_visualization_bridge", None) is bridge
            and getattr(app, "_native_visualization_cache", None) == signature):
        return
    # Remember failures too. Guides are optional; a malformed/oversized shot
    # must not retry compilation or flood logs on every editor poll.
    app._native_visualization_bridge = bridge
    app._native_visualization_cache = signature
    try:
        if publish(project, enabled=active, selected_camera=selected) is False:
            details = bridge.visualization_diagnostics()
            LOG.warning("Path guides unavailable: %s", details.get("error") or "publication failed")
    except (RuntimeError, ValueError, OSError) as exc:
        LOG.warning("Path guides unavailable: %s", exc)


def configure(app):
    bridge = _bridge(app)
    if bridge is None or getattr(app, "closed", False):
        return
    status = app.controller.status()
    was_active = bool(getattr(app, "native_editor_active", False))
    active = bool(was_active or status.get("native_editor_active"))
    active = active and bool(status.get("connected"))
    # start_flight enables native input before its acknowledgement reaches the
    # controller. Do not turn it off while the startup worker is still arming.
    if not active and not was_active:
        return
    if not active:
        app.native_editor_active = False
    if active and not getattr(app, "native_editor_active", False):
        app.native_editor_active = True
        if hasattr(app, "_disable_external_input"):
            app._disable_external_input()
    settings = app.app_settings
    count = len(app.project.keyframes)
    selected = app._selection_index(app.camera_tree)
    selected = selected if selected is not None and 0 <= selected < count else 0
    playhead = float(_value(app, "shot_time", 0) or 0)
    playback_speed, playback_rate = _playback_values(app)
    video_fps, video_bitrate, video_encoder, video_fixed_step, video_speed, video_depth, video_depth_exr = _video_values(app)
    values = dict(enabled=active, bindings=settings.action_bindings,
                  reshade_binding=settings.reshade_binding,
                  speed=settings.movement_speed, sensitivity=settings.mouse_sensitivity,
                  selected_camera=selected, camera_count=count,
                  shot_name=app.project.name, message=str(_value(app, "status_text", "")),
                  duration=float(app.project.duration), playhead=max(0.0, playhead),
                  replay_tick=int(status.get("tick") or 0),
                  playing=bool(status.get("playing")), busy=bool(app.busy),
                  playback_speed=playback_speed, playback_rate=playback_rate,
                  video_fps=video_fps, video_bitrate_mbps=video_bitrate,
                  video_encoder=video_encoder, video_fixed_step=video_fixed_step,
                  video_depth=video_depth,
                  video_depth_exr=video_depth_exr,
                  video_speed=video_speed,
                  confetti_enabled=bool(app.project.confetti_enabled),
                  confetti_spawn_height=float(app.project.confetti_spawn_height),
                  confetti_despawn_on_ground=bool(app.project.confetti_despawn_on_ground),
                  **{"video_layer_" + layer: bool(_value(app, "video_layer_" + layer, False))
                     for layer in ("world", "players", "effects")})
    # A UI refresh must not overwrite an owner chosen by F7/F8/F9 in-game.
    # Explicit owner changes are handled only by command transitions below.
    if getattr(app, "_native_editor_config_cache", None) != values or getattr(app, "_native_editor_bridge", None) is not bridge:
        bridge.configure_editor(**values)
        app._native_editor_config_cache = values
        app._native_editor_bridge = bridge
    _configure_visualization(app, bridge, active, selected)
    publish_dof = getattr(bridge, "configure_editor_dof", None)
    if callable(publish_dof):
        from .editor_dof import values_at
        dof = (active, values_at(app.project, max(0.0, playhead)))
        if (getattr(app, "_native_dof_bridge", None) is not bridge
                or getattr(app, "_native_dof_cache", None) != dof):
            publish_dof(dof[1], enabled=active)
            app._native_dof_bridge, app._native_dof_cache = bridge, dof
    publish_citadel = getattr(bridge, "configure_editor_citadel_dof", None)
    if callable(publish_citadel):
        from .editor_dof import citadel_values_at
        enabled, sensor, focus = citadel_values_at(app.project, max(0.0, playhead))
        citadel = (active, enabled, round(sensor, 6), round(focus, 6))
        if (getattr(app, "_native_citadel_dof_bridge", None) is not bridge
                or getattr(app, "_native_citadel_dof_cache", None) != citadel):
            publish_citadel(sensor, focus, available=active, enabled=active and enabled)
            app._native_citadel_dof_bridge, app._native_citadel_dof_cache = bridge, citadel
    publish_attach = getattr(bridge, "configure_editor_attach", None)
    fields = getattr(app, "attach_fields", None)
    if callable(publish_attach) and fields and active:
        preview = bool(getattr(app, "preview_attach", False))
        snap_request = int(getattr(app, "_attach_snap_request", 0))
        attach = (tuple(sorted(fields.items())), _attach_state(app, selected, count), preview,
                  snap_request)
        if (getattr(app, "_native_attach_bridge", None) is not bridge
                or getattr(app, "_native_attach_cache", None) != attach):
            publish_attach(dict(fields), attach[1], preview=preview, snap_request=snap_request)
            app._native_attach_bridge, app._native_attach_cache = bridge, attach


def _attach_state(app, selected, count):
    """Selected key's attach data for the in-game card, or None for free keys."""
    if not count or selected is None or not 0 <= selected < count:
        return None
    key = app.project.keyframes[selected]
    attach = getattr(key, "attach", None)
    if getattr(key, "source", "free") != "attach" or not isinstance(attach, AttachKey):
        if key.source_blend:
            return {"selected": False, "source_blend": key.source_blend,
                    "attached_keys": sum(item.source == "attach" for item in app.project.keyframes),
                    "key_count": count}
        return None
    roster = getattr(app, "attach_roster", None)
    players = roster.get("players", []) if isinstance(roster, dict) else []
    target_index = ATTACH_NO_TARGET
    for position, player in enumerate(players):
        if (isinstance(player, dict) and player.get("handle") == attach.handle
                and str(player.get("model_path") or "") == attach.model):
            target_index = position
            break
    return {"source_blend": key.source_blend, "handle": attach.handle, "entity_id": attach.entity_id,
            "target_index": target_index, "model": model_token(attach.model),
            "point": ("eyes", "weapon", "bone").index(attach.point),
            "bone": attach.bone,
            "hide_body": bool(attach.hide_body), "offset": tuple(attach.offset),
            "smoothing": float(attach.smoothing),
            "attached_keys": sum(1 for item in app.project.keyframes
                                 if getattr(item, "source", "free") == "attach"),
            "key_count": len(app.project.keyframes)}


def _select(app, index):
    if not app.project.keyframes:
        app.status_text.set("Capture a camera first.")
        return
    index = max(0, min(len(app.project.keyframes) - 1, index))
    app.camera_tree.selection_set(str(index))
    app.camera_tree.see(str(index))
    app._select_key()
    # The selected camera changes at the current paused replay moment.
    # Reuse the paused-view controller instead of seeking to its shot time.
    key = app.project.keyframes[index]
    project = app._snapshot()
    app._submit("Viewing saved camera", lambda: app.controller.select_paused_camera(project, key.time))


def _native_operation(app, label, function, bridge):
    def run():
        try:
            return function()
        except Exception:
            # Preserve F7/ordinary game input recovery after a failed transition.
            try:
                state = bridge.editor_status()
                # Native F9 suspends input optimistically before the worker
                # runs. A failed opening must not be mistaken for confirmed
                # game-UI ownership; keep Dolly's retry controls available.
                confirmed_ui = app.controller.status().get("game_ui_visible", False)
                owner = "console" if state.get("console_open") else ("game_ui" if confirmed_ui else "panel")
                bridge.configure_editor(owner=owner)
            except (RuntimeError, ValueError, OSError):
                LOG.exception("Could not restore native input after a failed action")
            raise
    return app._submit(label, run)


def _pose_matches_key(app, index, pose):
    """True when the live camera is parked on the indexed saved camera."""
    try:
        key = app.project.keyframes[index]
    except (IndexError, TypeError):
        return False
    for axis, name in enumerate(("x", "y", "z", "pitch", "yaw", "roll")):
        if not math.isfinite(pose[axis]):
            return False
        tolerance = 1.0 if axis < 3 else .05
        if abs(pose[axis] - getattr(key, name)) > tolerance:
            return False
    return True


def dispatch(app, event, bridge):
    """Dispatch one event on Tk's thread. False means leave it queued."""
    if app.busy:
        return False
    action = event["action"]
    if action in ("capture", "replace"):
        operation = "replace" if action == "replace" else ("append" if app.project.keyframes else "start")
        app._capture_view(operation, native_snapshot=event)
    elif action == "set_framing":
        factor = event["pose"][6]
        native_index = event["value"]
        if (not math.isfinite(factor) or factor <= 0
                or not math.isfinite(native_index) or native_index != int(native_index)
                or native_index < -1):
            raise ValueError("Invalid in-game framing edit")
        # The native event can lag a selection change. The camera list is the
        # authority; fall back to the reported index only when none is selected.
        index = app._selection_index(app.camera_tree)
        if index is None:
            index = int(native_index)
        # Scroll edits the shot being framed. A saved camera is only edited
        # while the live camera is actually parked on it (the in-game previous/
        # next view or the camera list). Once the user flies away, the edit is
        # live-only and the next capture stores it.
        parked = (isinstance(index, int) and 0 <= index < len(app.project.keyframes)
                  and _pose_matches_key(app, index, event["pose"]))
        if not parked:
            app.status_text.set("Live framing updated. Capture a camera to save it.")
        else:
            keys = copy.deepcopy(app.project.keyframes)
            key = keys[index]
            value = max(.5, min(4.0, round(key.aspect_ratio * factor, 4)))
            if value != key.aspect_ratio:
                key.aspect_ratio = value
                app._commit_camera(keys, key.time)
                app.status_text.set(f"Camera {index + 1} framing set to {value:.4f}.")
    elif action.startswith("set_dof_"):
        from .editor_dof import ACTIONS, RANGE_NAME, edited_project
        if action not in ACTIONS:
            raise ValueError("Unsupported native DOF control")
        if app.controller.status().get("playing") or getattr(app, "playing", False):
            raise ValueError("Pause shot playback before editing DOF.")
        at = float(_value(app, "shot_time", 0) or 0)
        candidate = edited_project(app.project, at, ACTIONS.index(action), event["value"])
        def complete(_result):
            app.project = candidate
            app._mark_dirty()
            selected = next((index for index, track in enumerate(candidate.tracks)
                             if track.name == RANGE_NAME), None)
            app._refresh_tracks(selected)
            app._refresh_fixed()
            configure(app)
        app._submit("Applying native DOF", lambda: app.controller.preview_native_effects(candidate, at), complete)
    elif action.startswith("set_citadel_dof_"):
        from .editor_dof import CITADEL_ACTIONS, edited_citadel_project
        if action not in CITADEL_ACTIONS:
            raise ValueError("Unsupported Citadel DOF control")
        if app.controller.status().get("playing") or getattr(app, "playing", False):
            raise ValueError("Pause shot playback before editing DOF.")
        at = float(_value(app, "shot_time", 0) or 0)
        candidate = edited_citadel_project(app.project, at,
                                           CITADEL_ACTIONS.index(action), event["value"])
        def complete(_result):
            app.project = candidate
            app._mark_dirty()
            app._refresh_tracks()
            app._refresh_fixed()
            configure(app)
        app._submit("Applying Citadel DOF", lambda: app.controller.preview_native_effects(candidate, at), complete)
    elif action.startswith("set_confetti_"):
        value = event["value"]
        if action == "set_confetti_spawn_height":
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not 100 <= value <= 1500):
                raise ValueError("Confetti spawn height must be between 100 and 1500 units.")
            changes = {"confetti_spawn_height": float(round(value))}
        else:
            if value not in (0, 1):
                raise ValueError("Confetti switch must be on or off.")
            field = ("confetti_enabled" if action == "set_confetti_enabled"
                     else "confetti_despawn_on_ground")
            changes = {field: bool(value)}
        app.project = replace(app.project, **changes)
        if hasattr(app, "confetti_enabled"):
            app.confetti_enabled.set(app.project.confetti_enabled)
            app.confetti_spawn_height.set(f"{app.project.confetti_spawn_height:g}")
            app.confetti_despawn_on_ground.set(app.project.confetti_despawn_on_ground)
        app._mark_dirty()
        app.controller.set_native_confetti(
            app.project.confetti_enabled, app.project.confetti_spawn_height,
            app.project.confetti_despawn_on_ground)
        app.status_text.set(
            (f"Confetti rain enabled at {app.project.confetti_spawn_height:g} units; "
             + ("despawns on ground." if app.project.confetti_despawn_on_ground
                else "remains on the ground."))
            if app.project.confetti_enabled else "Confetti rain disabled for this shot.")
        configure(app)
    elif action == "play_pause":
        _native_operation(app, "Toggling replay playback", app.controller.toggle_replay, bridge)
    elif action == "play_path":
        app._play()
    elif action == "seek_shot":
        value = event["value"]
        if (not math.isfinite(value) or not app.project.keyframes
                or not 0 <= value <= app.project.duration):
            raise ValueError("Choose a time within the current shot.")
        if app.controller.status().get("playing") or getattr(app, "playing", False):
            raise ValueError("Pause shot playback before seeking an in-between view.")
        app._set_time(value)
        app._seek()
    elif action == "destroy_ragdolls":
        _native_operation(app, "Clearing ragdolls", app.controller.destroy_ragdolls, bridge)
    elif action == "toggle_citadel_glow":
        _native_operation(app, "Toggling Citadel glow", app.controller.toggle_citadel_glow, bridge)
    elif action == "toggle_healthbars":
        _native_operation(app, "Toggling health bars", app.controller.toggle_healthbars, bridge)
    elif action == "near_player_opacity_fix":
        _native_operation(app, "Fixing near-player opacity",
                          app.controller.near_player_opacity_fix, bridge)
    elif action == "start_video":
        app._start_video_recording()
    elif action == "stop_video":
        app._stop_video_recording(cancel=False)
    elif action == "stop":
        _native_operation(app, "Stopping and restoring", app.controller.stop, bridge)
    elif action in ("previous_view", "next_view", "select_view"):
        selected = app._selection_index(app.camera_tree)
        if action == "select_view":
            value = event["value"]
            if not math.isfinite(value) or value != int(value) or not 0 <= value < len(app.project.keyframes):
                raise ValueError("Selected in-game camera no longer exists")
            selected = int(value)
        else:
            selected = (selected if selected is not None else 0) + (-1 if action == "previous_view" else 1)
        _select(app, selected)
    elif action == "step_replay_ticks":
        if getattr(app, "preview_attach", False):
            raise ValueError("Detach the camera before stepping with a fixed view.")
        value = event["value"]
        if isinstance(value, bool) or value not in (-25, -10, -5, -2, -1, 1, 2, 5, 10, 25):
            raise ValueError("Choose a tick step of 1, 2, 5, 10 or 25.")
        project = app.project
        def complete(result):
            if app.project is project and project.keyframes:
                app._set_time(max(0, (result["tick"] - project.start_tick) / project.tick_rate))
        app._submit("Stepping replay ticks", lambda: app.controller.step_replay_ticks(int(value)), complete)
    elif action in ("seek_back", "seek_forward"):
        delta = -1.0 if action == "seek_back" else 1.0
        _native_operation(app, "Seeking replay", lambda: app.controller.seek_relative(delta, tick_rate=app.project.tick_rate), bridge)
    elif action == "console":
        _native_operation(app, "Toggling game console", lambda: app.controller.toggle_console(enabled=bool(event["value"])), bridge)
    elif action == "game_ui":
        _native_operation(app, "Switching replay UI", lambda: app.controller.toggle_game_ui(enabled=bool(event["value"])), bridge)
    elif action == "flight":
        # A failed target lookup must not remain cached across an explicit retry
        # (for example after returning from the game UI or seeking).
        app._native_attach_cache = None
        configure(app)
        _native_operation(app, "Entering free camera", app.controller.enter_native_flight, bridge)
    elif action == "panel":
        # Normal F8 is handled locally. From the game's own UI, first return
        # camera ownership before displaying the editor panel.
        if event["value"] == 1:
            def return_to_editor():
                # F8 from the console is a return to Dolly, whereas F7 closes
                # the console back to the underlying game UI when it was open.
                if getattr(app.controller, "_console_open", False) is True:
                    app.controller.toggle_console(enabled=False)
                app.controller.toggle_game_ui(enabled=False)
                bridge.configure_editor(owner="panel")
            _native_operation(app, "Opening in-game editor", return_to_editor, bridge)
    elif action == "set_speed":
        value = event["value"]
        if not math.isfinite(value) or not 1 <= value <= 10000:
            raise ValueError("Movement speed must be between 1 and 10,000")
        settings = replace(app.app_settings, movement_speed=value)
        # Save on the worker, not on either UI/render thread.
        def saved(_result):
            app.app_settings = settings
            if hasattr(app, "editor_move_speed"):
                app.editor_move_speed.set(f"{value:g}")
            configure(app)
        app._submit("Saving movement speed", lambda: save_settings(settings), saved)
    elif action in ("set_playback_speed", "set_playback_rate"):
        value = event["value"]
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            raise ValueError("Playback option must be a finite number")
        if action == "set_playback_speed":
            if not .05 <= value <= 4:
                raise ValueError("Playback speed must be between 0.05 and 4.")
            app.speed.set(f"{value:g}")
            # Apply immediately: the controller updates demo_timescale for the
            # running or paused replay as well as the next Play shot.
            app._submit("Applying playback speed", lambda: app.controller.set_playback_speed(value))
        else:
            # The update rate paces Dolly's monitoring thread and is fixed when
            # a shot starts; changing it mid-shot is still unsafe.
            if app.controller.status().get("playing") or getattr(app, "playing", False):
                app.status_text.set("Stop path playback before changing the update rate.")
                return True
            if value not in (30, 60, 120):
                raise ValueError("Choose a playback update rate of 30, 60, or 120.")
            app.rate.set(str(int(value)))
        configure(app)
    elif action in ("set_video_fps", "set_video_bitrate", "set_video_encoder"):
        value = event["value"]
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            raise ValueError("Export option must be a finite number")
        if value != int(value):
            raise ValueError("Export option must be a whole number")
        value = int(value)
        if action == "set_video_fps":
            if value not in VIDEO_FPS:
                raise ValueError("Video FPS must be 30, 60, 120, 300, or 600.")
            app.video_fps.set(str(value))
        elif action == "set_video_bitrate":
            if value not in VIDEO_BITRATE_MBPS:
                raise ValueError("Video bitrate must be 10, 20, or 40 Mbps.")
            label = next((name for name, bps in BITRATE_PRESETS.items()
                          if bps == value * 1_000_000), None)
            if label is None:
                raise ValueError("Video bitrate must be 10, 20, or 40 Mbps.")
            app.video_bitrate.set(label)
        else:
            if not 0 <= value <= CODEC_MAX:
                raise ValueError("Unknown video encoder.")
            app.video_codec.set(_codec_label(value))
        configure(app)
    elif action == "set_video_fixed_step":
        app.video_fixed_step.set(bool(event["value"]))
        configure(app)
    elif action == "set_video_depth":
        if event["value"] not in (0, 1):
            raise ValueError("Depth master must be on or off.")
        app.video_depth.set(bool(event["value"]))
        # A depth master is a paired, render-paced export. Default to the same
        # fixed-step pairing as the desktop Export tab.
        if app.video_depth.get():
            app.video_fixed_step.set(True)
        else:
            app.video_depth_exr.set(False)
        configure(app)
    elif action == "set_video_depth_exr":
        if event["value"] not in (0, 1):
            raise ValueError("Depth EXR sequence must be on or off.")
        app.video_depth_exr.set(bool(event["value"]) and bool(app.video_depth.get()))
        configure(app)
    elif action in ("set_video_layer_world", "set_video_layer_players", "set_video_layer_effects"):
        if event["value"] not in (0, 1):
            raise ValueError("Video layer must be on or off.")
        getattr(app, action.removeprefix("set_")).set(bool(event["value"]))
        app._layer_toggled()
        configure(app)
    elif action == "set_video_speed":
        value = event["value"]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not .05 <= value <= 4):
            raise ValueError("Export speed must be between 0.05 and 4.")
        app.video_export_speed.set(f"{value:g}")
        configure(app)
    elif action in ("set_attach_target", "attach_cycle_target"):
        roster = getattr(app, "attach_roster", None)
        players = roster.get("players", []) if isinstance(roster, dict) else []
        if not players:
            app.status_text.set("No live players are available for the attach camera yet.")
            return True
        requested = int(event["value"]) if action == "set_attach_target" else -1

        def mutate_target(key):
            if action == "set_attach_target":
                if not 0 <= requested < len(players):
                    raise ValueError("The selected attach player is no longer in the roster.")
                player = players[requested]
            else:
                current = -1
                for position, candidate in enumerate(players):
                    if (isinstance(key.attach, AttachKey)
                            and candidate.get("handle") == key.attach.handle
                            and str(candidate.get("model_path") or "") == key.attach.model):
                        current = position
                        break
                player = players[(current + 1) % len(players)]
            attach = key.attach if isinstance(key.attach, AttachKey) else AttachKey()
            attach.handle = int(player.get("handle", 0))
            attach.entity_id = int(player.get("entity_index", 0) or 0)
            attach.model = str(player.get("model_path") or "")
            key.attach = attach
            key.source = "attach"

        _attach_edit(app, mutate_target)
        configure(app)
    elif action in ("set_attach_point", "attach_cycle_point"):
        requested = int(event["value"]) if action == "set_attach_point" else -1
        if action == "set_attach_point" and requested not in (0, 1, 2):
            raise ValueError("Unknown attach point.")

        def mutate_point(key):
            if not isinstance(key.attach, AttachKey):
                raise ValueError("Choose a live player before changing the attach point.")
            if action == "set_attach_point":
                key.attach.point = ("eyes", "weapon", "bone")[requested]
            else:
                key.attach.point = "weapon" if key.attach.point == "eyes" else "eyes"
            key.attach.bone = (key.attach.bone or "head") if key.attach.point == "bone" else ""

        _attach_edit(app, mutate_point)
        configure(app)
    elif action == "set_attach_bone":
        bones = bridge.editor_bones()
        index = int(event["value"])
        pose = event.get("pose") or ()
        if (not bones or not pose or pose[0] != bones["sequence"] or
                event["value"] != index or not 0 <= index < len(bones["names"])):
            raise ValueError("The bone picker changed; select the bone again.")

        def mutate_bone(key):
            attach = key.attach
            if (not isinstance(attach, AttachKey) or attach.handle != bones["handle"] or
                    attach.entity_id != bones["entity_id"] or model_token(attach.model) != bones["model"]):
                raise ValueError("The bone picker belongs to a different player.")
            attach.point = "bone"
            attach.bone = bones["names"][index]

        _attach_edit(app, mutate_bone)

    elif action == "set_attach_offsets":
        pose = event.get("pose") or []
        if len(pose) != 7 or any(not math.isfinite(component) for component in pose):
            raise ValueError("Attach offsets must be finite numbers.")
        offsets = tuple(float(component) for component in pose[:6])
        if any(abs(component) > ATTACH_OFFSET_LIMIT for component in offsets):
            raise ValueError("Attach offsets are outside the supported range.")

        def mutate_offsets(key):
            if not isinstance(key.attach, AttachKey):
                raise ValueError("Choose a live player before editing offsets.")
            key.attach.offset = offsets

        _attach_edit(app, mutate_offsets)
        configure(app)
    elif action == "set_attach_smoothing":
        smoothing = event["value"]
        if (isinstance(smoothing, bool) or not isinstance(smoothing, (int, float))
                or not math.isfinite(smoothing) or not 0 <= smoothing <= 5):
            raise ValueError("Attach smoothing must be between 0 and 5 seconds.")

        def mutate_smoothing(key):
            if not isinstance(key.attach, AttachKey):
                raise ValueError("Choose a live player before setting smoothing.")
            key.attach.smoothing = float(smoothing)

        _attach_edit(app, mutate_smoothing)
        configure(app)
    elif action == "set_attach_hide":
        if event["value"] not in (0, 1):
            raise ValueError("Hide this hero must be on or off.")
        hide = bool(event["value"])

        def mutate_hide(key):
            if not isinstance(key.attach, AttachKey):
                raise ValueError("Choose a live player before hiding the body.")
            key.attach.hide_body = hide

        _attach_edit(app, mutate_hide)
        configure(app)
    elif action == "set_source_blend":
        value = float(event["value"])
        if not math.isfinite(value) or not 0 <= value <= 10:
            raise ValueError("Source blend must be between 0 and 10 seconds")
        _attach_edit(app, lambda key: setattr(key, "source_blend", value))
        configure(app)
    elif action == "attach_reset":
        app.preview_attach = False
        app._attach_snap_pending = False

        def mutate_reset(key):
            key.source = "free"
            key.attach = None

        _attach_edit(app, mutate_reset)
        configure(app)
    elif action == "attach_preview":
        if event["value"] not in (0, 1):
            raise ValueError("Attach preview must be on or off.")
        app.preview_attach = bool(event["value"])
        app.status_text.set("Attach preview on. Fly with WASD/mouse to edit the offsets."
                            if app.preview_attach else "Attach preview off.")
        configure(app)
        if not app.preview_attach:
            # Clearing preview alone cannot clear the native command's latched
            # fault. Re-arm a free view, keeping the editor panel available.
            _native_operation(app, "Detaching camera",
                              lambda: app.controller.enter_native_flight(owner="panel"), bridge)
    elif action == "attach_snap":
        app._attach_snap_request = int(getattr(app, "_attach_snap_request", 0)) + 1
        app._attach_snap_pending = True
        app.status_text.set("Snap requested; the next free-camera frame stores its offsets.")
        configure(app)
    else:
        raise ValueError("The native editor requested an unsupported UI action")
    return True


def _attach_edit(app, mutate):
    """Apply an in-game attach edit to the selected camera key."""
    index = app._selection_index(app.camera_tree)
    if index is None or not 0 <= index < len(app.project.keyframes):
        app.status_text.set("Select a camera view before editing the attach camera.")
        return
    keys = copy.deepcopy(app.project.keyframes)
    key = keys[index]
    mutate(key)
    app._commit_camera(keys, key.time)


def poll(app):
    if getattr(app, "closed", False):
        return
    bridge = _bridge(app)
    if bridge is None:
        app.native_editor_active = False
        app._native_editor_bridge = None
        app._native_visualization_bridge = None
        app._native_visualization_cache = None
        return
    try:
        # Flight can fail before the controller marks the editor active even
        # though DX11/input finish initializing afterwards. Keep that live
        # panel's console/stop/retry actions connected to the launcher. Do not
        # interfere with the startup worker while it is still arming flight.
        if (not getattr(app, "native_editor_active", False) and not app.busy
                and app.controller.status().get("connected")):
            recovery = bridge.editor_status()
            if recovery.get("enabled"):
                app.native_editor_active = True
                if hasattr(app, "_disable_external_input"):
                    app._disable_external_input()
        configure(app)
        if not getattr(app, "native_editor_active", False):
            return
        # The attach target picker refreshes from the native roster at a low
        # rate; a stale or unavailable block must never break camera control.
        roster_now = time.monotonic()
        if roster_now - getattr(app, "_attach_roster_at", 0.0) >= 2.0:
            app._attach_roster_at = roster_now
            roster = None
            reader = getattr(bridge, "editor_roster", None)
            if callable(reader):
                try:
                    roster = reader()
                except (NativeBridgeError, ValueError, OSError):
                    roster = None
            app.attach_roster = roster
            handler = getattr(app, "_attach_roster_changed", None)
            if callable(handler):
                handler(roster)
            bones = None
            reader = getattr(bridge, "editor_bones", None)
            if callable(reader):
                try:
                    bones = reader()
                except (NativeBridgeError, ValueError, OSError):
                    pass
            app.attach_bones = bones
            handler = getattr(app, "_attach_bones_changed", None)
            if callable(handler):
                handler(bones)
        # Native snap / attached-fly offsets feed the selected attach key.
        result_reader = getattr(bridge, "editor_attach_result", None)
        if callable(result_reader):
            attach_result = None
            try:
                attach_result = result_reader()
            except (NativeBridgeError, ValueError, OSError):
                attach_result = None
            if attach_result and attach_result.get("valid"):
                sequence = attach_result.get("sequence")
                if sequence and sequence != getattr(app, "_attach_result_sequence", None):
                    app._attach_result_sequence = sequence
                    handler = getattr(app, "_attach_result_changed", None)
                    if callable(handler):
                        handler(attach_result)
        status = bridge.editor_status()
        app._native_editor_status = status
        if status.get("dropped_events", 0) > getattr(app, "_native_editor_dropped", 0):
            app._native_editor_dropped = status["dropped_events"]
            app.status_text.set("The editor was busy; an in-game action was ignored. Retry the action.")
        for event in status.get("events", ()):
            if app.busy:
                break
            try:
                accepted = dispatch(app, event, bridge)
            except (RuntimeError, ValueError, OSError) as exc:
                LOG.exception("Native editor action failed: %s", event["action"])
                app._error("In-game editor", exc)
                accepted = True  # Do not replay a failed action on every tick.
            if not accepted:
                break
            bridge.acknowledge_editor_event(event["sequence"])
    except NativeBridgeError as exc:
        if "being updated" in str(exc):
            return
        if str(exc) != getattr(app, "_native_editor_error", None):
            app._native_editor_error = str(exc)
            LOG.error("Native editor: %s", exc)
            app.status_text.set(str(exc))
    except (ValueError, OSError) as exc:
        if str(exc) != getattr(app, "_native_editor_error", None):
            app._native_editor_error = str(exc)
            LOG.exception("Native editor configuration failed")
            app.status_text.set(str(exc))


def close(app):
    bridge = _bridge(app)
    if bridge is not None:
        try:
            bridge.configure_editor(enabled=False, owner="disabled")
        except (NativeBridgeError, OSError, ValueError):
            pass
    app.native_editor_active = False
    app._native_editor_config_cache = None
    app._native_visualization_bridge = None
    app._native_visualization_cache = None
