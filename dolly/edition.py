"""Explicit executable edition, selected before the desktop imports settings."""
_environment = False


def configure_environment():
    global _environment
    _environment = True


def is_environment():
    return _environment


def application_name():
    return 'Dolly Environment Capture' if _environment else 'Deadlock Dolly'


def settings_directory():
    return 'DollyEnvironmentCapture' if _environment else 'DeadlockDolly'


def allow_standard_updates():
    return not _environment


def action_allowed(action):
    """Keep inherited shortcuts and stale native events inside this edition."""
    if not _environment:
        return True
    from .editor_actions import ACTION_ORDER
    from .editor_dof import ACTIONS as dof_actions
    return action in (set(ACTION_ORDER) - {'play_path'}) | set(dof_actions) | {
        'capture_environment', 'console', 'set_speed', 'select_view',
        'set_playback_speed', 'set_playback_rate', 'destroy_ragdolls', 'set_framing',
        'toggle_citadel_glow', 'toggle_healthbars', 'near_player_opacity_fix',
        'set_citadel_dof_enabled', 'set_citadel_dof_sensor_size',
        'set_citadel_dof_focus_distance', 'step_replay_ticks', 'set_replay_hud',
        'select_camera', 'view_camera', 'delete_camera', 'undo_shot', 'redo_shot',
        'camera_page', 'framing_grid', 'take_screenshot', 'save_shot',
        'seek_replay_tick', 'clear_camera_path', 'set_pause_after_capture',
        'set_camera_position', 'level_view', 'seek_camera',
    }
