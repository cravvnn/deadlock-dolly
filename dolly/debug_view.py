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
    suspend_while_positioning: bool = False


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
        suspend_while_positioning=True,
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
        suspend_while_positioning=True,
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


VIS_GRID_UNKNOWN_MESSAGE = "Visibility grid state is unknown; load the replay again to clear it."


class DebugViewState:
    """Track live session debug-view state and sequence undo-before-apply transitions."""

    def __init__(self) -> None:
        self.current: str = DEBUG_VIEW_DEFAULT
        self.vis_grid_active: bool = False
        self.vis_grid_uncertain: bool = False
        self.cheats_owned: bool = False
        self.suspended: bool = False

    def is_heavy(self) -> bool:
        """True when the active view should pause while Dolly seeks or warms up a shot."""
        return (
            self.current != DEBUG_VIEW_DEFAULT
            and DEBUG_VIEW_BY_ID[self.current].suspend_while_positioning
        )

    def reset_local(self) -> None:
        """Clear local tracking when a session starts or dies (the game state went with it)."""
        self.current = DEBUG_VIEW_DEFAULT
        self.vis_grid_active = False
        self.vis_grid_uncertain = False
        self.cheats_owned = False
        self.suspended = False

    def after_reload(self) -> None:
        """The replay was reloaded (``disconnect`` / ``playdemo``): the game dropped the view.

        A confirmed reload turns ``vis_debug_show`` off (live-verified with an export that
        reloads the replay), so the toggle returns to known-off and must be sent again rather
        than assumed on. The selected view and ``sv_cheats`` ownership are kept so
        :meth:`reapply` or :meth:`resume` can restore it.
        """
        self.vis_grid_active = False
        self.vis_grid_uncertain = False
        self.suspended = False

    def drop_view(self) -> None:
        """The replay was reloaded and the view will not be re-applied: show Normal.

        ``cheats_owned`` is kept so the next switch still turns off cheats Dolly turned on.
        """
        self.after_reload()
        self.current = DEBUG_VIEW_DEFAULT

    def _undo_active(self, *, send, force_full_reset: bool = False) -> None:
        """Fully undo the active debug view (or all views when ``force_full_reset``).

        Every step is attempted even when an earlier one fails, so one lost reply cannot
        leave cheats or overlays on; the first failure is raised afterwards.
        """
        failure = None

        def attempt(command):
            nonlocal failure
            try:
                send(command, allow_error=True)
                return True
            except Exception as exc:  # noqa: BLE001 - keep undoing, report the first failure
                failure = failure or exc
                return False

        if force_full_reset:
            for command in NORMAL_RESET_COMMANDS:
                attempt(command)
        elif self.current != DEBUG_VIEW_DEFAULT:
            previous = DEBUG_VIEW_BY_ID[self.current]
            if not previous.is_toggle:
                for command in previous.undo_commands:
                    attempt(command)
        if self.cheats_owned and attempt("sv_cheats 0"):
            self.cheats_owned = False
        # The grid toggle goes last: it is the one command whose state cannot be read back.
        # Only toggle it when known active; never toggle blindly while uncertain.
        if self.vis_grid_active and not self.vis_grid_uncertain:
            if attempt("vis_debug_show"):
                self.vis_grid_active = False
            else:
                self.vis_grid_active = False
                self.vis_grid_uncertain = True
        self.current = DEBUG_VIEW_DEFAULT
        self.suspended = False
        if failure is not None:
            raise failure

    def _ensure_cheats(self, *, send, read_cvar) -> bool:
        """Turn ``sv_cheats`` on if it is not already; True when this call turned it on."""
        if read_cvar("sv_cheats") == 1.0:
            return False
        send("sv_cheats 1", allow_error=False)
        self.cheats_owned = True
        return True

    def _toggle_vis_grid_on(self, *, send) -> bool:
        """Turn ``vis_debug_show`` on if known off; mark uncertain on send failure."""
        if self.vis_grid_uncertain:
            raise RuntimeError(VIS_GRID_UNKNOWN_MESSAGE)
        if self.vis_grid_active:
            return False
        try:
            send("vis_debug_show", allow_error=False)
        except Exception:
            # The game may have processed the toggle before the reply was lost.
            self.vis_grid_active = False
            self.vis_grid_uncertain = True
            raise
        self.vis_grid_active = True
        return True

    def apply(self, target: str, *, send, read_cvar) -> str:
        """Switch to ``target`` (id or label), undoing the previous view first.

        Choosing the current view again re-sends it (except the grid toggle), so a view
        lost to a failed reload can be re-asserted. Returns the active option id.
        """
        target_id = debug_view_id(target)
        option = DEBUG_VIEW_BY_ID[target_id]
        if target_id == DEBUG_VIEW_DEFAULT:
            self._undo_active(send=send, force_full_reset=True)
            return self.current
        if target_id == self.current:
            if self.suspended:
                self.resume(send=send, read_cvar=read_cvar)
            elif not option.is_toggle:
                if option.needs_cheats:
                    self._ensure_cheats(send=send, read_cvar=read_cvar)
                for command in option.apply_commands:
                    send(command, allow_error=False)
            return self.current

        if option.is_toggle and self.vis_grid_uncertain:
            raise RuntimeError(VIS_GRID_UNKNOWN_MESSAGE)

        # Always fully undo any active view before applying the next.
        if (
            self.current != DEBUG_VIEW_DEFAULT
            or self.vis_grid_active
            or self.cheats_owned
            or self.suspended
        ):
            self._undo_active(send=send, force_full_reset=False)

        # Apply the requested option, rolling back on failure so nothing stacks.
        turned_on_cheats = False
        applied_commands: list[str] = []
        try:
            if option.needs_cheats:
                turned_on_cheats = self._ensure_cheats(send=send, read_cvar=read_cvar)
            if option.is_toggle:
                if self._toggle_vis_grid_on(send=send):
                    applied_commands.append("vis_debug_show")
            else:
                for command in option.apply_commands:
                    send(command, allow_error=False)
                    applied_commands.append(command)
        except Exception:
            if applied_commands:
                if option.is_toggle and self.vis_grid_active and not self.vis_grid_uncertain:
                    try:
                        send("vis_debug_show", allow_error=True)
                        self.vis_grid_active = False
                    except Exception:
                        self.vis_grid_active = False
                        self.vis_grid_uncertain = True
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
            self.suspended = False
            raise

        self.current = target_id
        self.suspended = False
        return self.current

    def reapply(self, *, send, read_cvar) -> None:
        """Re-assert the active debug view after a replay reload (call :meth:`after_reload` first).

        ``disconnect`` + ``playdemo`` resets cheat-protected convars and the grid toggle;
        re-applying keeps the selected view through playback and export.
        """
        if self.current == DEBUG_VIEW_DEFAULT:
            return
        option = DEBUG_VIEW_BY_ID[self.current]
        if option.needs_cheats:
            self._ensure_cheats(send=send, read_cvar=read_cvar)
        if option.is_toggle:
            self._toggle_vis_grid_on(send=send)
        else:
            for command in option.apply_commands:
                send(command, allow_error=False)
        self.suspended = False

    def defer_heavy(self) -> bool:
        """Keep an active heavy view off after a confirmed reload until :meth:`resume`."""
        if not self.is_heavy():
            return False
        self.suspended = True
        return True

    def suspend(self, *, send) -> bool:
        """Temporarily undo a heavy view while Dolly seeks or warms up a shot.

        Keeps ``current`` and ``cheats_owned`` intact so :meth:`resume` can restore the
        view afterwards. Safe to call when already suspended or on non-heavy views.
        """
        if self.suspended or not self.is_heavy():
            return False
        option = DEBUG_VIEW_BY_ID[self.current]
        if option.is_toggle:
            if self.vis_grid_uncertain:
                return False
            if self.vis_grid_active:
                try:
                    send("vis_debug_show", allow_error=False)
                except Exception:
                    self.vis_grid_active = False
                    self.vis_grid_uncertain = True
                    raise
                self.vis_grid_active = False
            self.suspended = True
            return True
        failure = None
        for command in option.undo_commands:
            try:
                send(command, allow_error=True)
            except Exception as exc:  # noqa: BLE001 - undo every command, report first failure
                failure = failure or exc
        self.suspended = True
        if failure is not None:
            raise failure
        return True

    def resume(self, *, send, read_cvar) -> bool:
        """Re-apply a heavy view paused by :meth:`suspend` or :meth:`defer_heavy`."""
        if not self.suspended:
            return False
        if self.current == DEBUG_VIEW_DEFAULT:
            self.suspended = False
            return False
        option = DEBUG_VIEW_BY_ID[self.current]
        if option.needs_cheats:
            self._ensure_cheats(send=send, read_cvar=read_cvar)
        if option.is_toggle:
            try:
                self._toggle_vis_grid_on(send=send)
            except Exception:
                self.suspended = False
                raise
        else:
            for command in option.apply_commands:
                send(command, allow_error=False)
        self.suspended = False
        return True

    def reset_best_effort(self, *, send) -> None:
        """Return the running game to Normal during disconnect without blocking."""
        try:
            if (
                self.current != DEBUG_VIEW_DEFAULT
                or self.vis_grid_active
                or self.cheats_owned
                or self.suspended
            ):
                self._undo_active(send=send, force_full_reset=True)
        finally:
            self.reset_local()
