"""Native editor action handlers; publication is an injected callback."""
from __future__ import annotations

from dataclasses import replace
import copy
import logging
import math
from .editor_wire import ATTACH_OFFSET_LIMIT
from .native_effects import model_token
from .path import AttachKey
from .settings import save_settings
from .video_export import BITRATE_PRESETS

LOG = logging.getLogger("dolly.editor_session")
from .editor_view import VIDEO_FPS, VIDEO_BITRATE_MBPS, CODEC_MAX


from .editor_publication import _value
from .editor_view import codec_label as _codec_label


def _select(app, index, *, view=True):
    if not app.project.keyframes:
        app.status_text.set("Capture a camera first.")
        return
    index = max(0, min(len(app.project.keyframes) - 1, index))
    app.camera_tree.selection_set(str(index))
    app.camera_tree.see(str(index))
    app._select_key()
    if not view:
        return
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


def dispatch(app, event, bridge, *, publish):
    action = event["action"]
    previous = getattr(app, "_history_edit_group", None)
    app._history_edit_group = ((action, app._selection_index(app.camera_tree),
                                float(_value(app, "shot_time", 0) or 0))
                               if action.startswith("set_") else None)
    try:
        return _dispatch(app, event, bridge, publish=publish)
    finally:
        app._history_edit_group = previous


def _dispatch(app, event, bridge, *, publish):
    """Dispatch one event on Tk's thread. False means leave it queued."""
    if app.busy:
        return False
    action = event["action"]
    if action in ("open_bone_picker", "cancel_bone_picker", "finish_bone_picker"):
        from .bone_picker import dispatch as picker_dispatch
        return picker_dispatch(app, event, bridge, publish=publish)
    if getattr(app, "_bone_picker_context", None):
        if action in ("game_ui", "console", "stop"):
            from .bone_picker import close_picker
            close_picker(app, resume=False, publish=publish)
        else:
            raise ValueError("Finish or cancel Bone Picker before using other camera controls.")
    if (action in ("flight", "panel") and getattr(app, "preview_attach", False)
            and bridge.status().get("state") in ("fault", "stopped", "probe")):
        # Re-enter a free camera after release. Reusing a failed live attach
        # preview would immediately fault the recovery command again. The
        # authored target/bone/offsets remain intact for an explicit retry.
        app.preview_attach = False
        app._native_attach_cache = None
        publish()
    if action == "reset_camera_path":
        if app.playing:
            raise ValueError("Stop path playback before resetting the camera path.")
        # The native panel confirms replacement before sending this snapshot.
        app._capture_view("start", native_snapshot=event, replacement_confirmed=True)
    elif action in ("select_camera", "view_camera", "delete_camera", "undo_shot", "redo_shot",
                    "camera_page", "set_camera_time", "set_camera_roll"):
        history = getattr(app, "shot_history", None)
        revision = event["pose"][0]
        if history is None or revision != history.revision:
            raise ValueError("The camera list changed. Select the camera again.")
        if not app._shot_edit_ready():
            return True
        value = event["value"]
        if not math.isfinite(value) or value != int(value):
            raise ValueError("Invalid in-game camera selection")
        if action in ("undo_shot", "redo_shot"):
            if value != 0:
                raise ValueError("Invalid shot history action")
            (app.redo_shot if action == "redo_shot" else app.undo_shot)()
        elif action == "camera_page":
            from .editor_wire import CAMERA_LIST_COUNT
            if value < 0 or value % CAMERA_LIST_COUNT or value > max(0, len(app.project.keyframes) - 1):
                raise ValueError("Invalid camera list page")
            app._native_camera_page = int(value)
            publish()
        elif action in ("set_camera_time", "set_camera_roll"):
            if not 0 <= value < len(app.project.keyframes):
                raise ValueError("Selected in-game camera no longer exists")
            _edit_camera_value(app, action, int(value), event["pose"][1])
            publish()
        else:
            if not 0 <= value < len(app.project.keyframes):
                raise ValueError("Selected in-game camera no longer exists")
            _select(app, int(value), view=action == "view_camera")
            if action == "delete_camera":
                app._delete_key()
            publish()
    elif action in ("capture", "replace"):
        operation = "replace" if action == "replace" else ("append" if app.project.keyframes else "start")
        snapshot = dict(event)
        # New native builds put the rendered horizontal FOV in the otherwise
        # unused Capture/Replace value. Zero remains the legacy wire value.
        if event.get("value", 0) != 0:
            snapshot["horizontal_fov"] = event["value"]
        app._capture_view(operation, native_snapshot=snapshot)
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
            publish()
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
            publish()
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
        publish()
    elif action == "set_replay_hud":
        if event['value'] not in (0, 1):
            raise ValueError('Choose whether to show the replay HUD.')
        _native_operation(app, 'Changing replay HUD',
                          lambda: app.controller.set_replay_hud(bool(event['value'])), bridge)
    elif action == "framing_grid":
        settings = replace(app.app_settings,
                           framing_grid_enabled=not app.app_settings.framing_grid_enabled)
        def saved(_result):
            app.app_settings = settings
            publish()
        app.status_text.set("Framing guide " +
                            ("on." if settings.framing_grid_enabled else "off."))
        app._submit("Saving framing guide", lambda: save_settings(settings), saved)
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
    elif action == "take_screenshot":
        from . import screenshot_ui
        screenshot_ui.start(app)
    elif action == "stop":
        _native_operation(app, "Stopping and restoring",
                          lambda: app.controller.stop(preserve_speed=True), bridge)
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
        publish()
        _native_operation(app, "Entering free camera", app.controller.enter_native_flight, bridge)
    elif action == "panel":
        # Normal F8 is handled locally. From the game's own UI, first return
        # camera ownership before displaying the editor panel.
        if event["value"] == 1:
            def return_to_editor():
                # Both controller handoffs close the console themselves. Let
                # them validate readiness first so a rejected F8 keeps the
                # console and ordinary game input available for recovery.
                if (_value(app, "video_source", "Camera path") == "Player POV"
                        or getattr(app.controller, "_follow_active", False) is True):
                    app.controller.open_pov_panel()
                else:
                    app.controller.toggle_game_ui(enabled=False)
                bridge.configure_editor(owner="panel")
            _native_operation(app, "Opening in-game editor", return_to_editor, bridge)
    elif action == "start_game_follow":
        from .follow_camera import FollowSettings
        settings = FollowSettings(distance=event['value'], shoulder=event['pose'][0],
                                  height=event['pose'][1])
        settings.values()
        if getattr(app, 'preview_attach', False):
            raise ValueError('Detach the bone preview before Game Follow.')
        roster = bridge.editor_roster()
        pose = event['pose']
        index = int(pose[2])
        players = roster.get('players', []) if isinstance(roster, dict) else []
        if (pose[2] != index or not 0 <= index < len(players)
                or players[index].get('handle') != pose[3]
                or players[index].get('entity_index') != pose[4]
                or pose[5] != (players[index].get('model', 0) & 0xffffffff)
                or pose[6] != (players[index].get('model', 0) >> 32)):
            raise ValueError('The player list changed. Select the hero again.')
        player = dict(players[index])
        _native_operation(app, 'Starting Game Follow',
                          lambda: app.controller.start_selected_game_follow(settings, player), bridge)
    elif action == "stop_game_follow":
        _native_operation(app, 'Restoring Game Follow settings', app.controller.stop_game_follow, bridge)
    elif action == "set_video_source":
        if event["value"] not in (0, 1):
            raise ValueError("Unknown video camera source")
        app.video_source.set("Player POV" if event["value"] else "Camera path")
        app._video_source_changed()
    elif action == "set_pov_duration":
        value = event["value"]
        if not math.isfinite(value) or not .1 <= value <= 120:
            raise ValueError("POV duration must be between 0.1 and 120 seconds")
        app.video_pov_duration.set(str(value))
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
            publish()
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
        publish()
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
        publish()
    elif action == "set_video_fixed_step":
        app.video_fixed_step.set(bool(event["value"]))
        publish()
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
        publish()
    elif action == "set_video_depth_exr":
        if event["value"] not in (0, 1):
            raise ValueError("Depth EXR sequence must be on or off.")
        app.video_depth_exr.set(bool(event["value"]) and bool(app.video_depth.get()))
        publish()
    elif action in ("set_video_layer_world", "set_video_layer_players", "set_video_layer_effects"):
        if event["value"] not in (0, 1):
            raise ValueError("Video layer must be on or off.")
        getattr(app, action.removeprefix("set_")).set(bool(event["value"]))
        app._layer_toggled()
        publish()
    elif action == "set_video_speed":
        value = event["value"]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not .05 <= value <= 4):
            raise ValueError("Export speed must be between 0.05 and 4.")
        app.video_export_speed.set(f"{value:g}")
        publish()
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

        if not app.project.keyframes:
            from .bone_picker import start_attached_view
            start_attached_view(app, mutate_target, publish=publish)
            return True
        _attach_edit(app, mutate_target)
        publish()
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
        publish()
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
        publish()
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
        publish()
    elif action == "set_attach_hide":
        if event["value"] not in (0, 1, 2, 3):
            raise ValueError("Attach visibility and clearance choice is invalid.")
        hide = bool(int(event["value"]) & 1)
        clearance = "auto" if int(event["value"]) & 2 else "exact"

        def mutate_hide(key):
            if not isinstance(key.attach, AttachKey):
                raise ValueError("Choose a live player before hiding the body.")
            key.attach.hide_body = hide
            key.attach.clearance_mode = clearance

        _attach_edit(app, mutate_hide)
        publish()
    elif action == "set_source_blend":
        value = float(event["value"])
        if not math.isfinite(value) or not 0 <= value <= 10:
            raise ValueError("Source blend must be between 0 and 10 seconds")
        _attach_edit(app, lambda key: setattr(key, "source_blend", value))
        publish()
    elif action == "attach_reset":
        app.preview_attach = False
        app._attach_snap_pending = False

        def mutate_reset(key):
            key.source = "free"
            key.attach = None

        _attach_edit(app, mutate_reset)
        publish()
    elif action == "attach_preview":
        if event["value"] not in (0, 1):
            raise ValueError("Attach preview must be on or off.")
        if event["value"] == 1 and getattr(app.controller, "_follow_active", False) is True:
            project = app.project

            def attached_after_follow(_):
                if app.project is not project:
                    raise ValueError("The shot changed while switching from Follow. Select the camera again.")
                app.preview_attach = True
                app.status_text.set("Attach preview on. Fly with WASD/mouse to edit the offsets.")
                publish()

            app._submit("Switching Follow to attached camera",
                        lambda: app.controller.enter_native_flight(owner="panel"), attached_after_follow)
            return True
        app.preview_attach = bool(event["value"])
        app.status_text.set("Attach preview on. Fly with WASD/mouse to edit the offsets."
                            if app.preview_attach else "Attach preview off.")
        publish()
        if not app.preview_attach:
            # Clearing preview alone cannot clear the native command's latched
            # fault. Re-arm a free view, keeping the editor panel available.
            _native_operation(app, "Detaching camera",
                              lambda: app.controller.enter_native_flight(owner="panel"), bridge)
    elif action == "attach_snap":
        app._attach_snap_request = int(getattr(app, "_attach_snap_request", 0)) + 1
        app._attach_snap_pending = True
        app.status_text.set("Snap requested; the next free-camera frame stores its offsets.")
        publish()
    else:
        raise ValueError("The native editor requested an unsupported UI action")
    return True


def _edit_camera_value(app, action, index, value):
    """Apply an in-game inline Arrive/Bank edit to one camera key."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Invalid in-game camera edit")
    keys = copy.deepcopy(app.project.keyframes)
    key = keys[index]
    if action == "set_camera_time":
        if value < 0:
            raise ValueError("Camera arrival times cannot be negative.")
        edit = float(value)
        # Path times stay strictly increasing. A drag that lands exactly on
        # another camera is nudged forward instead of rejected.
        for _ in range(16):
            if not any(i != index and abs(other.time - edit) < 1e-6
                       for i, other in enumerate(keys)):
                break
            edit += 0.001
        else:
            raise ValueError("Another camera already arrives at that time.")
        key.time = edit
        app._commit_camera(keys, key.time)
        app.status_text.set(f"Camera {index + 1:02d} arrives at {key.time:.2f} s.")
    else:
        key.roll = float(value)
        app._commit_camera(keys, key.time)
        app.status_text.set(f"Camera {index + 1:02d} bank set to {key.roll:.1f}°.")


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


