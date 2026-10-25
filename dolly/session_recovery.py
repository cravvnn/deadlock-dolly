"""Coordinate independent configuration restorations in their original order."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import gameinfo_transaction, game_processes
from .launch_errors import LaunchError


def restore_session_configs(
    session_dir: Path, *, restore_gameinfo: Callable[[Path], bool] | None = None,
    game_is_running: Callable[[], bool] | None = None,
) -> None:
    """Attempt every independent restoration, even if any has a conflict."""
    from . import graphics_profiles, display_resolution
    if restore_gameinfo is None:
        restore_gameinfo = gameinfo_transaction.restore_record
    if game_is_running is None:
        game_is_running = lambda: game_processes._game_is_running(game_processes.running_processes())
    errors = []
    for restore in (restore_gameinfo, graphics_profiles.restore, display_resolution.restore):
        try:
            if restore is graphics_profiles.restore and graphics_profiles.pending(session_dir):
                if game_is_running():
                    raise LaunchError("Exit Deadlock before restoring this session's graphics settings.")
            if restore is display_resolution.restore and display_resolution.pending(session_dir):
                if game_is_running():
                    raise LaunchError("Exit Deadlock before restoring the saved display resolution.")
            restore(session_dir)
        except (OSError, LaunchError) as exc:
            errors.append(str(exc))
    if errors:
        raise LaunchError("\n".join(errors))
