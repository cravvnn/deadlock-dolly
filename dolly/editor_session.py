"""Tk-side dispatcher for acknowledged native in-game editor actions.

Only this module dispatches game-side events into the existing project editor.
Game lifecycle work stays on Dolly's existing operation worker; the game never
waits for a Python UI callback while rendering a view.
"""
from __future__ import annotations

from dataclasses import replace
import logging
import math

from .native_bridge import NativeBridgeError
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
    try:
        candidate = float(_value(app, "video_export_speed", 1.0))
        if math.isfinite(candidate) and .05 <= candidate <= 4:
            speed = candidate
    except (ValueError, TypeError):
        pass
    return fps, bitrate, codec, fixed, speed


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
    video_fps, video_bitrate, video_encoder, video_fixed_step, video_speed = _video_values(app)
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
                  video_speed=video_speed)
    # A UI refresh must not overwrite an owner chosen by F7/F8/F9 in-game.
    # Explicit owner changes are handled only by command transitions below.
    if getattr(app, "_native_editor_config_cache", None) != values or getattr(app, "_native_editor_bridge", None) is not bridge:
        bridge.configure_editor(**values)
        app._native_editor_config_cache = values
        app._native_editor_bridge = bridge
    _configure_visualization(app, bridge, active, selected)


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


def dispatch(app, event, bridge):
    """Dispatch one event on Tk's thread. False means leave it queued."""
    if app.busy:
        return False
    action = event["action"]
    if action in ("capture", "replace"):
        operation = "replace" if action == "replace" else ("append" if app.project.keyframes else "start")
        app._capture_view(operation, native_snapshot=event)
    elif action == "play_pause":
        _native_operation(app, "Toggling replay playback", app.controller.toggle_replay, bridge)
    elif action == "play_path":
        app._play()
    elif action == "destroy_ragdolls":
        _native_operation(app, "Clearing ragdolls", app.controller.destroy_ragdolls, bridge)
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
    elif action in ("seek_back", "seek_forward"):
        delta = -1.0 if action == "seek_back" else 1.0
        _native_operation(app, "Seeking replay", lambda: app.controller.seek_relative(delta, tick_rate=app.project.tick_rate), bridge)
    elif action == "console":
        _native_operation(app, "Toggling game console", lambda: app.controller.toggle_console(enabled=bool(event["value"])), bridge)
    elif action == "game_ui":
        _native_operation(app, "Switching replay UI", lambda: app.controller.toggle_game_ui(enabled=bool(event["value"])), bridge)
    elif action == "flight":
        _native_operation(app, "Entering paused camera", app.controller.enter_native_flight, bridge)
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
        # These settings apply to the next shot. Changing a running shot's
        # displayed speed without updating its replay/native clock is unsafe.
        if app.controller.status().get("playing") or getattr(app, "playing", False):
            app.status_text.set("Stop path playback before changing playback options.")
            return True
        value = event["value"]
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            raise ValueError("Playback option must be a finite number")
        if action == "set_playback_speed":
            if not .05 <= value <= 4:
                raise ValueError("Playback speed must be between 0.05 and 4.")
            app.speed.set(f"{value:g}")
        else:
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
    elif action == "set_video_speed":
        value = event["value"]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not .05 <= value <= 4):
            raise ValueError("Export speed must be between 0.05 and 4.")
        app.video_export_speed.set(f"{value:g}")
        configure(app)
    else:
        raise ValueError("The native editor requested an unsupported UI action")
    return True


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
