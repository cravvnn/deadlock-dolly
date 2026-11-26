"""Live rendering debug views for inspecting a running replay session.

These options are strictly session-only viewing aids: they are never stored in
shot projects, settings.json or any other file, and every new or recovered
session begins on Normal.

Switching between options always undoes the previous option completely before
applying the next so modes never stack. ``vis_debug_show`` is a toggle
concommand with no readable cvar state, so :class:`DebugViewState` tracks
whether Dolly turned it on and toggles it off exactly once when leaving.
``sv_cheats`` is only disabled on exit/reset if Dolly turned it on.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DebugViewOption:
    """One selectable live debug view and the console commands that own it."""

    id: str
    label: str
    apply_commands: tuple[str, ...] = ()
    undo_commands: tuple[str, ...] = ()
    needs_cheats: bool = False
    is_toggle: bool = False


DEBUG_VIEW_DEFAULT = "normal"

# Reset commands used to return any known non-toggle debug view to Normal.
# ``mat_debug NONE`` is an unmatched mode name that Deadlock's ``mat_debug``
# concommand uses to clear ``mat_fullbright`` back to 0 without requiring
# ``sv_cheats 1`` (live-verified on DX11 with ``-dev``). ``mat_wireframe 0`` is
# sent with allow_error=True before ``sv_cheats 0`` so it clears wireframe when
# cheats are on and stays harmless when cheats are off.
NORMAL_RESET_COMMANDS: tuple[str, ...] = (
    "mat_debug NONE",
    "mat_wireframe 0",
    "sc_visualize_sceneobjects SCENEOBJECT_VIS_NONE",
    "r_particle_debug_show_control_points 0",
    "r_particle_debug_show_sort_position 0",
    "r_showdebugoverlays 0",
)

DEBUG_VIEW_OPTIONS: tuple[DebugViewOption, ...] = (
    DebugViewOption(
        id="normal",
        label="Normal",
    ),
    DebugViewOption(
        id="albedo",
        label="Albedo",
        apply_commands=("mat_debug ALBEDO",),
        undo_commands=("mat_debug NONE",),
    ),
    DebugViewOption(
        id="normals",
        label="Normals",
        apply_commands=("mat_debug NORMAL_WS",),
        undo_commands=("mat_debug NONE",),
    ),
    DebugViewOption(
        id="roughness",
        label="Roughness",
        apply_commands=("mat_debug ROUGHNESS",),
        undo_commands=("mat_debug NONE",),
    ),
    DebugViewOption(
        id="lighting_complexity",
        label="Lighting complexity",
        apply_commands=("mat_debug LIGHTING_COMPLEXITY",),
        undo_commands=("mat_debug NONE",),
    ),
    DebugViewOption(
        id="fullbright",
        label="Fullbright",
        apply_commands=("mat_debug FULLBRIGHT",),
        undo_commands=("mat_debug NONE",),
    ),
    DebugViewOption(
        id="wireframe",
        label="Wireframe",
        apply_commands=("mat_wireframe 1",),
        undo_commands=("mat_wireframe 0",),
        needs_cheats=True,
    ),
    DebugViewOption(
        id="objects",
        label="Objects",
        apply_commands=("sc_visualize_sceneobjects SCENEOBJECT_VIS_OBJECT",),
        undo_commands=("sc_visualize_sceneobjects SCENEOBJECT_VIS_NONE",),
    ),
    DebugViewOption(
        id="materials",
        label="Materials",
        apply_commands=("sc_visualize_sceneobjects SCENEOBJECT_VIS_MATERIAL",),
        undo_commands=("sc_visualize_sceneobjects SCENEOBJECT_VIS_NONE",),
    ),
    DebugViewOption(
        id="lod",
        label="Detail levels (LOD)",
        apply_commands=("sc_visualize_sceneobjects SCENEOBJECT_VIS_LOD",),
        undo_commands=("sc_visualize_sceneobjects SCENEOBJECT_VIS_NONE",),
    ),
    DebugViewOption(
        id="visibility_grid",
        label="Visibility grid",
        apply_commands=("vis_debug_show",),
        undo_commands=("vis_debug_show",),
        is_toggle=True,
    ),
    DebugViewOption(
        id="particle_overlays",
        label="Particle overlays",
        apply_commands=(
            "r_showdebugoverlays 1",
            "r_particle_debug_show_control_points 1",
            "r_particle_debug_show_sort_position 1",
        ),
        undo_commands=(
            "r_particle_debug_show_control_points 0",
            "r_particle_debug_show_sort_position 0",
            "r_showdebugoverlays 0",
        ),
        needs_cheats=True,
    ),
)

DEBUG_VIEW_BY_ID: dict[str, DebugViewOption] = {opt.id: opt for opt in DEBUG_VIEW_OPTIONS}
DEBUG_VIEW_LABELS: dict[str, str] = {opt.id: opt.label for opt in DEBUG_VIEW_OPTIONS}
DEBUG_VIEW_BY_LABEL: dict[str, str] = {opt.label: opt.id for opt in DEBUG_VIEW_OPTIONS}
DEBUG_VIEW_IDS: tuple[str, ...] = tuple(opt.id for opt in DEBUG_VIEW_OPTIONS)


def debug_view_id(value: str) -> str:
    """Resolve an option id or dropdown label to its canonical option id."""
    if not isinstance(value, str):
        raise ValueError(f"Unknown debug view: {value!r}")
    if value in DEBUG_VIEW_BY_ID:
        return value
    if value in DEBUG_VIEW_BY_LABEL:
        return DEBUG_VIEW_BY_LABEL[value]
    raise ValueError(f"Unknown debug view: {value!r}")


def debug_view_label(option_id: str) -> str:
    """Display label for ``option_id`` (falls back to Normal for unknown ids)."""
    return DEBUG_VIEW_LABELS.get(option_id, DEBUG_VIEW_LABELS[DEBUG_VIEW_DEFAULT])


class DebugViewState:
    """Track live session debug-view state and sequence undo-before-apply transitions."""

    def __init__(self) -> None:
        self.current: str = DEBUG_VIEW_DEFAULT
        self.vis_grid_active: bool = False
        self.cheats_owned: bool = False

    def reset_local(self) -> None:
        """Clear local tracking when a session starts, disconnects or dies."""
        self.current = DEBUG_VIEW_DEFAULT
        self.vis_grid_active = False
        self.cheats_owned = False

    def _undo_active(self, *, send, force_full_reset: bool = False) -> None:
        """Fully undo the active debug view (or all views when ``force_full_reset``)."""
        if self.vis_grid_active:
            send("vis_debug_show", allow_error=False)
            self.vis_grid_active = False
        if force_full_reset:
            for command in NORMAL_RESET_COMMANDS:
                send(command, allow_error=True)
        elif self.current != DEBUG_VIEW_DEFAULT:
            previous = DEBUG_VIEW_BY_ID[self.current]
            if not previous.is_toggle:
                for command in previous.undo_commands:
                    send(command, allow_error=True)
        if self.cheats_owned:
            send("sv_cheats 0", allow_error=True)
            self.cheats_owned = False
        self.current = DEBUG_VIEW_DEFAULT

    def apply(self, target: str, *, send, read_cvar) -> str:
        """Switch to ``target`` (id or label), undoing the previous view first.

        Returns the active canonical option id after the switch.
        """
        target_id = debug_view_id(target)
        option = DEBUG_VIEW_BY_ID[target_id]
        if target_id == DEBUG_VIEW_DEFAULT:
            self._undo_active(send=send, force_full_reset=True)
            return self.current
        if target_id == self.current:
            return self.current

        # Always fully undo any active view before applying the next.
        if self.current != DEBUG_VIEW_DEFAULT or self.vis_grid_active or self.cheats_owned:
            self._undo_active(send=send, force_full_reset=False)

        # Apply the requested option, rolling back on failure so nothing stacks.
        turned_on_cheats = False
        applied_commands: list[str] = []
        try:
            if option.needs_cheats:
                if read_cvar("sv_cheats") != 1.0:
                    send("sv_cheats 1", allow_error=False)
                    self.cheats_owned = True
                    turned_on_cheats = True
            if option.is_toggle:
                if not self.vis_grid_active:
                    send("vis_debug_show", allow_error=False)
                    self.vis_grid_active = True
                    applied_commands.append("vis_debug_show")
            else:
                for command in option.apply_commands:
                    send(command, allow_error=False)
                    applied_commands.append(command)
        except Exception:
            if applied_commands:
                if option.is_toggle and self.vis_grid_active:
                    try:
                        send("vis_debug_show", allow_error=True)
                        self.vis_grid_active = False
                    except Exception:
                        pass
                else:
                    for command in option.undo_commands:
                        try:
                            send(command, allow_error=True)
                        except Exception:
                            pass
            if turned_on_cheats and self.cheats_owned:
                try:
                    send("sv_cheats 0", allow_error=True)
                    self.cheats_owned = False
                except Exception:
                    pass
            self.current = DEBUG_VIEW_DEFAULT
            raise

        self.current = target_id
        return self.current

    def reapply(self, *, send, read_cvar, vis_survives_reload: bool = False) -> None:
        """Re-assert the active debug view after an in-process replay reload.

        ``disconnect`` + ``playdemo`` resets ``sv_cheats`` and cheat-protected
        convars; re-applying keeps the selected debug view active through
        playback and export.
        """
        if self.current == DEBUG_VIEW_DEFAULT:
            return
        option = DEBUG_VIEW_BY_ID[self.current]
        if option.needs_cheats:
            if read_cvar("sv_cheats") != 1.0:
                send("sv_cheats 1", allow_error=False)
                self.cheats_owned = True
        if option.is_toggle:
            if not vis_survives_reload:
                send("vis_debug_show", allow_error=False)
                self.vis_grid_active = True
        else:
            for command in option.apply_commands:
                send(command, allow_error=False)

    def reset_best_effort(self, *, send) -> None:
        """Return the running game to Normal during disconnect without blocking."""
        try:
            if self.current != DEBUG_VIEW_DEFAULT or self.vis_grid_active or self.cheats_owned:
                self._undo_active(send=send, force_full_reset=True)
        finally:
            self.reset_local()
