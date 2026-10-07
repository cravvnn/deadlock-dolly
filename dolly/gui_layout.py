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
        startup_progress=app.startup_progress,
    )
    commands = LibraryActions(
        browse_demo=app._browse_demo,
        browse_game=app._browse_game,
        cancel_startup=app._cancel_startup,
        filter_replays=app._filter_replays,
        refresh_replays=app._refresh_replays,
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
    app.startup_label = view.startup_label
    app.capture_hotkey_checkboxes.append(view.library_capture_toggle)
    app._build_advanced_startup()


def build_settings(app):
    from .ui.settings_page import SettingsActions, SettingsPage, SettingsState

    def build_keybinds(parent):
        app.keybinds_tab = parent
        app._build_keybinds()

    state = SettingsState(
        game_path=app.game_path,
        replay_folder=app.replay_folder,
        reshade_runtime_path=app.reshade_runtime_path,
        reshade_status_text=app.reshade_status_text,
        show_log=app.show_log,
        auto_updates_initial=app.app_settings.auto_updates,
        mouse_sensitivity=app.editor_sensitivity,
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
        save_mouse_sensitivity=app._save_mouse_sensitivity,
        save_update_preference=app._save_update_preference,
        select_reshade_runtime=app._select_reshade_runtime,
        toggle_log=app._toggle_log,
        build_keybinds=build_keybinds,
        show_reshade_keybinds=lambda: app._show_keybinds("reshade"),
    )
    view = SettingsPage(app.settings_tab, state, commands, root=app.root, on_error=app._error)
    app.settings_view = view
    app.auto_updates = view.auto_updates
    app.controls_disclosure = view.controls_disclosure
    app.graphics_profiles = view.graphics_profiles
    app.keybinds_tab = view.keybinds_tab
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


def build_export(app):
    from .ui.export_page import ExportActions, ExportPage, ExportState
    from .video_export import CODEC_CHOICES, BITRATE_PRESETS

    state = ExportState(
        ffmpeg_path=app.ffmpeg_path,
        video_bitrate=app.video_bitrate,
        video_codec=app.video_codec,
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
        codec_choices=tuple(c[1] for c in CODEC_CHOICES),
        bitrate_choices=tuple(BITRATE_PRESETS),
    )
    commands = ExportActions(
        browse_ffmpeg=app._browse_ffmpeg,
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
    screenshot_ui.build(app, view.export_page.body, before=view.video_destination_card)
    app.export_camera_note = view.export_camera_note
    app.export_capture_card = view.export_capture_card
    app.export_page = view.export_page
    app.export_passes_card = view.export_passes_card
    app.ffmpeg_browse_button = view.ffmpeg_browse_button
    app.ffmpeg_path_entry = view.ffmpeg_path_entry
    app.video_bitrate_combo = view.video_bitrate_combo
    app.video_browse_button = view.video_browse_button
    app.video_cancel_button = view.video_cancel_button
    app.video_codec_combo = view.video_codec_combo
    app.video_depth_checkbox = view.video_depth_checkbox
    app.video_depth_exr_checkbox = view.video_depth_exr_checkbox
    app.video_fixed_checkbox = view.video_fixed_checkbox
    app.video_fps_combo = view.video_fps_combo
    app.video_game_audio_check = view.video_game_audio_check
    app.video_layer_checkboxes = view.video_layer_checkboxes
    app.video_path_entry = view.video_path_entry
    app.video_pov_controls = view.video_pov_controls
    app.video_pov_duration_combo = view.video_pov_duration_combo
    app.video_reconstructed_audio_check = view.video_reconstructed_audio_check
    app.video_source_combo = view.video_source_combo
    app.video_speed_combo = view.video_speed_combo
    app.video_start_button = view.video_start_button
    app.video_stop_button = view.video_stop_button
