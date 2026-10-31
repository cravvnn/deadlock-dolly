"""Compatibility adapters wiring DollyApp to independent desktop page views."""
from __future__ import annotations

# Preserve the original shared-widget import paths.
from .ui.widgets import (
    GAP, Disclosure, ScrollPage, actions, card, disclosure, field, surface_style,
)


def build_library(app):
    from .ui.library_page import LibraryActions, LibraryPage, LibraryState

    state = LibraryState(
        camera_driver=app.camera_driver,
        demo_path=app.demo_path,
        game_path=app.game_path,
        hotkey_enabled=app.hotkey_enabled,
        hotkey_label=app.hotkey_label,
        path_summary=app.path_summary,
        project_text=app.project_text,
        replay_search=app.replay_search,
        replay_sort=app.replay_sort,
        startup_progress=app.startup_progress,
    )
    commands = LibraryActions(
        browse_demo=app._browse_demo,
        browse_game=app._browse_game,
        cancel_startup=app._cancel_startup,
        filter_replays=app._filter_replays,
        refresh_replays=app._refresh_replays,
        repair_replay=app._repair_selected_replay,
        select_replay=app._select_replay,
        start_editing_session=app._start_editing_session,
        toggle_capture_hotkey=app._toggle_capture_hotkey,
        tree=app._tree,
        use_selected_replay=app._use_selected_replay,
        new=app.new,
        open=app.open,
        rename=app.rename,
        save=app.save,
        stop_session=lambda: app._session_operation("Stopping", app.controller.stop),
        save_as=lambda: app.save(save_as=True),
    )
    view = LibraryPage(app.setup_tab, state, commands)
    app.library_view = view
    app.cancel_startup_button = view.cancel_startup_button
    app.home_camera_driver_combo = view.home_camera_driver_combo
    app.library_capture_toggle = view.library_capture_toggle
    app.library_page = view.library_page
    app.play_replay_button = view.play_replay_button
    app.replay_tree = view.replay_tree
    app.selected_replay_text = view.selected_replay_text
    app.startup_bar = view.startup_bar
    app.capture_hotkey_checkboxes.append(view.library_capture_toggle)
    app._build_advanced_startup()


def build_camera(app):
    """The Camera page (PLAN-5 3.1): its widgets and variables become the app's, as before."""
    from .gui import ASPECT_PRESETS, FIELD_LABELS, FIELDS
    from .ui.camera_page import CameraActions, CameraPage, CameraState

    state = CameraState(
        capture_mode=app.capture_mode,
        segment_seconds=app.segment_seconds,
        hotkey_label=app.hotkey_label,
        hotkey_enabled=app.hotkey_enabled,
        project_text=app.project_text,
        path_summary=app.path_summary,
        lens_interpolation=app.lens_interpolation,
        interpolation=app.interpolation,
        selected_text=app.selected_text,
        standard_aspect=app.standard_aspect,
        key_vars=app.key_vars,
        aspect_presets=tuple(ASPECT_PRESETS),
        tree_fields=FIELDS,
        tree_labels=FIELD_LABELS,
    )
    commands = CameraActions(
        start_path_here=app._start_path_here,
        capture_here=app.capture_here,
        replace_camera_here=app._replace_camera_here,
        show_keybinds=app._show_keybinds,
        capture_mode_changed=app._capture_mode_changed,
        toggle_capture_hotkey=app._toggle_capture_hotkey,
        tree=app._tree,
        select_key=app._select_key,
        delete_key=app._delete_key,
        apply_options=app._apply_options,
        open_coordinates=app._open_coordinates,
        update_key=app._update_key,
        reset_aspect=app._reset_aspect,
        apply_selected=app._apply_selected,
        open_bone_picker=app._open_bone_picker,
        cancel_bone_picker=app._cancel_bone_picker,
        attach_point_picker=app._attach_point_picker,
        apply_attach=app._apply_attach,
        change_unified_curve=app._change_unified_curve,
        select_curve_key=app._select_curve_key,
        reset_rotation_curve=app._reset_rotation_curve,
        change_camera_tangent=app._change_camera_tangent,
        preview_camera_curve=app._preview_camera_curve,
        curve_context=lambda: (app.shot_history.session_id, app.shot_history.revision),
    )
    view = CameraPage(app.camera_tab, state, commands)
    app.camera_view = view
    for name in ("camera_page", "playback_host", "capture_card", "cameras_card", "curves_card", "selected_card",
                 "attach_card", "capture_buttons", "capture_hotkey_checkbox", "segment_entry",
                 "camera_tree", "camera_curve_workspace", "position_curve", "rotation_curve", "aspect_curve",
                 "camera_curve_tabs", "path_curve_mode", "lens_curve_mode", "lens_entry", "standard_combo",
                 "more_button", "attach_checkbox", "attach_player_combo", "attach_point_combo", "attach_bone_combo",
                 "bone_row", "attach_note", "attach_enabled", "attach_player", "attach_point", "attach_bone",
                 "attach_vars", "attach_smoothing", "attach_hide", "attach_clearance", "source_blend",
                 "attach_lock_vertical_aim",
                 "timeline_view"):
        setattr(app, name, getattr(view, name))
    app.capture_hotkey_checkboxes.append(view.capture_hotkey_checkbox)
    app._attach_players = {}
    # Shot Playback is pinned in the page's dock while Camera shows (N10).
    app._playback_hosts[str(app.camera_tab)] = view.playback_host


def build_look(app):
    """The Look page (PLAN-5 3.2); it creates the Citadel DOF variables the app's helpers use (N12)."""
    from .particles import PARTICLE_INTENSITY_MAX, PARTICLE_INTENSITY_MIN, PARTICLE_LABELS
    from .ui.look_page import LookActions, LookPage, LookState

    state = LookState(
        confetti_enabled=app.confetti_enabled,
        particles_preset=app.particles_preset,
        particles_intensity=app.particles_intensity,
        confetti_spawn_height=app.confetti_spawn_height,
        confetti_despawn_on_ground=app.confetti_despawn_on_ground,
        track_name=app.track_name,
        track_mode=app.track_mode,
        restore_value=app.restore_value,
        track_time=app.track_time,
        track_value=app.track_value,
        particle_labels=tuple(PARTICLE_LABELS[name] for name in PARTICLE_LABELS),
        intensity_unit=f"x ({PARTICLE_INTENSITY_MIN:g}–{PARTICLE_INTENSITY_MAX:g})",
    )
    commands = LookActions(
        range_dof_preset=app._range_dof_preset,
        citadel_dof_preset=app._dof_preset,
        remove_track=app._remove_track,
        select_track=app._select_track,
        create_track=app._create_track,
        update_track=app._update_track,
        select_value=app._select_value,
        put_value=app._put_value,
        delete_value=app._delete_value,
        particles_changed=app._particles_changed,
        toggle_citadel_dof=app._toggle_citadel_dof,
        sensor_changed=app._citadel_sensor_changed,
        sensor_commit=app._citadel_sensor_commit,
        focus_changed=app._citadel_focus_changed,
        focus_commit=app._citadel_focus_commit,
        show_fixed_values=app._show_fixed_values,
        tree=app._tree,
    )
    view = LookPage(app.cvar_tab, state, commands)
    app.look_view = view
    for name in ("look_page", "track_tree", "value_tree", "citadel_dof_checkbox", "citadel_sensor_scale",
                 "citadel_focus_scale", "citadel_enabled", "citadel_sensor", "citadel_sensor_text",
                 "citadel_focus_internal", "citadel_focus_text"):
        setattr(app, name, getattr(view, name))
    # Shot Playback is pinned in the page's dock while Look shows, as on Camera.
    app._playback_hosts[str(app.cvar_tab)] = view.playback_host


def build_playback(app, parent):
    """Shot Playback (PLAN-5 3.1.1): one frame in the stage workspace, adopted by Camera or Look."""
    from .ui.playback_card import PlaybackActions, PlaybackCard, PlaybackState

    state = PlaybackState(
        shot_time=app.shot_time,
        time_text=app.time_text,
        speed=app.speed,
        hide_hud=app.hide_hud,
        frozen=app.frozen,
        seek_relief=app.seek_relief,
        rate=app.rate,
        smoothing=app.smoothing,
    )
    commands = PlaybackActions(
        slide=app._slide,
        set_dragging=lambda flag: setattr(app, "dragging", flag),
        play=app._play,
        pause=lambda: app._session_operation("Pausing", app.controller.pause),
        stop=lambda: app._session_operation("Stopping", app.controller.stop),
        apply_playback_speed=app._apply_playback_speed,
        set_seek_relief=app._set_seek_relief,
        apply_frame=app._apply_frame,
        seek=app._seek,
    )
    view = PlaybackCard(parent, state, commands)
    app.playback_view = view
    # Never gridded: the page being shown adopts it (gui.DollyApp._host_timeline).
    for name in ("timeline_frame", "timeline_body", "slider", "time_entry", "speed_combo", "rate_combo",
                 "smoothing_row", "smoothing_combo", "playback_disclosure", "play_button", "pause_button",
                 "stop_button", "preview_frame_button", "seek_button"):
        setattr(app, name, getattr(view, name))
    # The card packs the console smoothing row; the poll unpacks it for the native driver.
    app._smoothing_packed = True


def build_settings(app):
    from .ui.settings_page import SettingsActions, SettingsPage, SettingsState

    state = SettingsState(
        game_path=app.game_path,
        replay_folder=app.replay_folder,
        reshade_runtime_path=app.reshade_runtime_path,
        reshade_status_text=app.reshade_status_text,
        show_log=app.show_log,
        auto_updates_initial=app.app_settings.auto_updates,
        allow_mod_overrides_initial=app.app_settings.allow_mod_overrides,
        discord_presence_initial=app.app_settings.discord_presence,
        nvidia_dlss_initial=app.app_settings.nvidia_dlss,
    )
    commands = SettingsActions(
        browse_game=app._browse_game,
        browse_replay_folder=app._browse_replay_folder,
        browse_reshade=app._browse_reshade,
        browse_reshade_library=app._browse_reshade_library,
        check_updates=app._check_updates,
        configure_reshade=app._configure_reshade,
        diagnostics=app._diagnostics,
        disable_reshade=app._disable_reshade,
        forget_reshade=app._forget_reshade,
        open_advanced_startup=app._open_advanced_startup,
        open_launch_options=app._open_launch_options,
        recover_game_config=app._recover_game_config,
        save_layout_paths=app._save_layout_paths,
        save_mod_preference=app._save_mod_preference,
        save_update_preference=app._save_update_preference,
        save_discord_preference=app._save_discord_preference,
        save_dlss_preference=app._save_dlss_preference,
        select_reshade_runtime=app._select_reshade_runtime,
        toggle_log=app._toggle_log,
        show_reshade_keybinds=lambda: app._show_keybinds("reshade"),
    )
    view = SettingsPage(app.settings_tab, state, commands, root=app.root, on_error=app._error)
    app.settings_view = view
    app.auto_updates = view.auto_updates
    app.nvidia_dlss = view.nvidia_dlss
    app.discord_presence = view.discord_presence
    app.discord_status = view.discord_status
    app.allow_mod_overrides = view.allow_mod_overrides
    app.graphics_profiles = view.graphics_profiles
    app.reshade_browse_button = view.reshade_browse_button
    app.reshade_library_button = view.reshade_library_button
    app.reshade_card = view.reshade_card
    app.reshade_configure_button = view.reshade_configure_button
    app.reshade_disable_button = view.reshade_disable_button
    app.reshade_forget_button = view.reshade_forget_button
    app.reshade_path_entry = view.reshade_path_entry
    app.settings_page = view.settings_page
    app.update_check_button = view.update_check_button
    app.update_status = view.update_status
    app.game_card = view.game_card
    app.profiles_card = view.profiles_card
    app.updates_card = view.updates_card
    app.tools_card = view.tools_card


def build_export(app):
    from .ui.export_page import ExportActions, ExportPage, ExportState
    from .video_export import CODEC_CHOICES, CONTAINER_CHOICES, BITRATE_PRESETS

    state = ExportState(
        ffmpeg_path=app.ffmpeg_path,
        video_bitrate=app.video_bitrate,
        video_codec=app.video_codec,
        video_format=app.video_format,
        player_processing=app.player_processing,
        players_scratch=app.players_scratch,
        video_depth=app.video_depth,
        video_depth_exr=app.video_depth_exr,
        video_export_speed=app.video_export_speed,
        video_fixed_step=app.video_fixed_step,
        video_fps=app.video_fps,
        video_game_audio=app.video_game_audio,
        video_layer_effects=app.video_layer_effects,
        video_layer_players=app.video_layer_players,
        video_layer_world=app.video_layer_world,
        video_path=app.video_path,
        video_pov_duration=app.video_pov_duration,
        video_reconstructed_audio=app.video_reconstructed_audio,
        video_source=app.video_source,
        video_status_text=app.video_status_text,
        video_ae_camera=app.video_ae_camera,
        video_usd_camera=app.video_usd_camera,
        codec_choices=tuple(c[1] for c in CODEC_CHOICES),
        format_choices=tuple(label for _key, label in CONTAINER_CHOICES),
        bitrate_choices=tuple(BITRATE_PRESETS),
    )
    commands = ExportActions(
        browse_ffmpeg=app._browse_ffmpeg,
        browse_players_scratch=app._browse_players_scratch,
        browse_video=app._browse_video,
        depth_toggled=app._depth_toggled,
        layer_toggled=app._layer_toggled,
        open_output_folder=app._open_output_folder,
        play=app._play,
        save_ffmpeg_preference=app._save_ffmpeg_preference,
        start_video_recording=app._start_video_recording,
        stop_video_recording=app._stop_video_recording,
        use_bundled_ffmpeg=app._use_bundled_ffmpeg,
        video_source_changed=app._video_source_changed,
        discard_recording=lambda: app._stop_video_recording(cancel=True),
    )
    view = ExportPage(app.export_tab, state, commands)
    app.export_view = view
    from . import screenshot_ui
    screenshot_ui.build(app, card=view.screenshot_card)
    from . import edition, environment_ui
    if edition.is_environment():
        environment_ui.build(app, view.export_page)
    app.export_camera_note = view.export_camera_note
    app.export_capture_card = view.export_capture_card
    app.export_page = view.export_page
    app.export_passes_card = view.export_passes_card
    app.camera_source_card = view.camera_source_card
    app.encoder_card = view.encoder_card
    app.screenshot_card = view.screenshot_card
    app.ffmpeg_card = view.ffmpeg_card
    app.recording_card = view.recording_card
    app.ffmpeg_browse_button = view.ffmpeg_browse_button
    app.ffmpeg_path_entry = view.ffmpeg_path_entry
    app.video_bitrate_combo = view.video_bitrate_combo
    app.video_browse_button = view.video_browse_button
    app.video_cancel_button = view.video_cancel_button
    app.video_codec_combo = view.video_codec_combo
    app.video_format_combo = view.video_format_combo
    app.video_format_combo.bind("<<ComboboxSelected>>", lambda _event: app._format_changed())
    app.video_codec_combo.bind("<<ComboboxSelected>>", lambda _event: app._codec_changed())
    app.player_processing_combo = view.player_processing_combo
    app.players_scratch_entry = view.players_scratch_entry
    app.players_scratch_browse = view.players_scratch_browse
    app.video_depth_checkbox = view.video_depth_checkbox
    app.video_depth_exr_checkbox = view.video_depth_exr_checkbox
    app.video_fixed_checkbox = view.video_fixed_checkbox
    app.video_fps_combo = view.video_fps_combo
    app.video_game_audio_check = view.video_game_audio_check
    app.video_layer_checkboxes = view.video_layer_checkboxes
    app.video_path_entry = view.video_path_entry
    # Added to the field's own bindings: leaving it also puts back the elided path cover.
    app.video_path_entry.bind("<FocusOut>", app._remember_export_folder, add="+")
    app.video_path_entry.bind("<Return>", app._remember_export_folder, add="+")
    app.video_pov_controls = view.video_pov_controls
    app.video_camera_options = view.video_camera_options
    app.video_pov_duration_combo = view.video_pov_duration_combo
    app.video_reconstructed_audio_check = view.video_reconstructed_audio_check
    app.video_ae_camera_check = view.video_ae_camera_check
    app.video_usd_camera_check = view.video_usd_camera_check
    app.video_source_combo = view.video_source_combo
    app.video_speed_combo = view.video_speed_combo
    app.video_start_button = view.video_start_button
    app.video_stop_button = view.video_stop_button
