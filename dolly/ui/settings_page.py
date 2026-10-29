"""Settings page view: explicit variables/actions, with locally owned widgets.

The Settings screen (Figma [design reference]): five kit cards on a :class:`.kit_page.KitPage`
over the window's backdrop, in one column like Replay's (USER-ADDITIONS UPDATE 17.4,
Kara's comment #4): Updates, Troubleshooting & Recovery, Game & Files, Replay
Graphics Profiles and ReShade, 613 design px wide (narrower windows: the content
width), left-aligned, 15 apart. Each card keeps the rows the frame draws. The whole
page scrolls as one; Tab reads it top to bottom.

Every button is a canvas-drawn kit button; paths, the profile dropdown and the
checkboxes stay ttk widgets. The update note and the graphics help became info
bubbles and the Troubleshooting disclosure an always-open card: no control was
lost. ``CameraFeel`` and the sensitivity mapping stay here for the Controls page.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import math
import tkinter as tk
from tkinter import ttk

from .kit_fields import elide_at_rest
from .kit_page import KitPage
from .scale import chars, scaler
from .widgets import GAP, card

TITLE = "Settings"
SUBTITLE = "Set the game and replay paths, replay graphics profiles, ReShade and update checks."
UPDATES_INFO = "Nothing downloads or installs until you choose Update now."
UPDATE_STATUS = "Checks published Latest releases; experimental pre-releases are ignored."
LABEL_WIDTH = 125      # the form's label column; the Browse buttons are as wide
INDENT = 135           # button rows start under the fields

SENSITIVITY_MIN = 0.005
SENSITIVITY_MAX = 2.0
SENSITIVITY_TICKS = 1000
SENSITIVITY_PRESETS = (
    ("Very slow", 0.02),
    ("Slow", 0.05),
    ("Normal", 0.12),
    ("Fast", 0.25),
    ("Very fast", 0.5),
)


def sensitivity_to_position(value: float) -> float:
    bounded = min(max(float(value), SENSITIVITY_MIN), SENSITIVITY_MAX)
    position = SENSITIVITY_TICKS * math.log(bounded / SENSITIVITY_MIN) / math.log(SENSITIVITY_MAX / SENSITIVITY_MIN)
    return min(max(position, 0.0), float(SENSITIVITY_TICKS))


def position_to_sensitivity(position: float) -> float:
    bounded = min(max(float(position), 0.0), float(SENSITIVITY_TICKS))
    return SENSITIVITY_MIN * (SENSITIVITY_MAX / SENSITIVITY_MIN) ** (bounded / SENSITIVITY_TICKS)


def sensitivity_preset_name(value: float) -> str:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "Custom"
    if not math.isfinite(value) or value <= 0:
        return "Custom"
    label, preset = min(SENSITIVITY_PRESETS, key=lambda item: abs(math.log(value / item[1])))
    return label if abs(math.log(value / preset)) <= math.log(1.25) else "Custom"


@dataclass(frozen=True)
class SettingsState:
    game_path: tk.Variable
    replay_folder: tk.Variable
    reshade_runtime_path: tk.Variable
    reshade_status_text: tk.Variable
    show_log: tk.Variable
    auto_updates_initial: bool
    allow_mod_overrides_initial: bool = False
    discord_presence_initial: bool = True
    nvidia_dlss_initial: bool = False


@dataclass(frozen=True)
class SettingsActions:
    browse_game: Callable[..., object]
    browse_replay_folder: Callable[..., object]
    browse_reshade: Callable[..., object]
    browse_reshade_library: Callable[..., object]
    check_updates: Callable[..., object]
    configure_reshade: Callable[..., object]
    diagnostics: Callable[..., object]
    disable_reshade: Callable[..., object]
    forget_reshade: Callable[..., object]
    open_advanced_startup: Callable[..., object]
    open_launch_options: Callable[..., object]
    recover_game_config: Callable[..., object]
    save_layout_paths: Callable[..., object]
    save_mod_preference: Callable[..., object]
    save_update_preference: Callable[..., object]
    save_discord_preference: Callable[..., object]
    save_dlss_preference: Callable[..., object]
    select_reshade_runtime: Callable[..., object]
    toggle_log: Callable[..., object]
    show_reshade_keybinds: Callable[..., object]


class SettingsPage:
    def __init__(self, parent, state: SettingsState, commands: SettingsActions, *, root, on_error):
        # Not at import: the profiles module reaches the game's files (page modules stay light).
        from ..graphics_profiles_ui import PROFILES_INFO, GraphicsProfiles
        # One column like Replay's; cards are stacked (and Tab reads them) in build order.
        page = KitPage(parent)
        page.pack(fill="both", expand=True)
        self.settings_page = page
        self.commands = commands
        self.auto_updates = tk.BooleanVar(page, value=state.auto_updates_initial)
        self.update_status = tk.StringVar(page, value=UPDATE_STATUS)

        updates = self.updates_card = page.add_card("Updates")
        row = updates.row(height=18)
        self.auto_updates_checkbox = row.widget(ttk.Checkbutton(
            row.host, text="Automatically check for updates", variable=self.auto_updates,
            command=commands.save_update_preference, style="Kit.TCheckbutton"))
        row.gap(5)
        self.updates_info = row.info(UPDATES_INFO)
        self.update_check_button, = self._buttons(updates, (("Check for updates", commands.check_updates),))
        self.update_status_text = updates.text(textvariable=self.update_status, top=15)

        # Always open (it was a closed disclosure); one button per row, as the frame draws it.
        tools = self.tools_card = page.add_card("Troubleshooting & Recovery")
        for text, command in (("Startup controls...", commands.open_advanced_startup),
                              ("Recover configuration...", commands.recover_game_config),
                              ("Export diagnostics", commands.diagnostics)):
            self._buttons(tools, ((text, command),))
        row = tools.row(height=18)
        self.show_log_checkbox = row.widget(ttk.Checkbutton(
            row.host, text="Show log", variable=state.show_log, command=commands.toggle_log,
            style="Kit.TCheckbutton"))

        game = self.game_card = page.add_card("Game & Files")
        self.game_path_entry, self.game_path_cover, _ = self._path_row(
            game, "Deadlock executable", state.game_path, "Browse game...", commands.browse_game)
        self.replay_folder_entry, self.replay_folder_cover, _ = self._path_row(
            game, "Replay folder", state.replay_folder, "Browse folder...", commands.browse_replay_folder)
        self._buttons(game, (("Save paths", commands.save_layout_paths),
                             ("Display launch options...", commands.open_launch_options)), indent=INDENT)

        self.profiles_card = page.add_card("Replay Graphics Profiles", info=PROFILES_INFO)
        self.graphics_profiles = GraphicsProfiles(None, root=root, on_error=on_error, card=self.profiles_card)

        dlss = self.dlss_card = page.add_card("NVIDIA DLSS", info=(
            "Dolly keeps engine, received console and native-hook diagnostics with DLSS. "
            "Some game output before the engine logger starts may be unavailable. "
            "Turn this off before a new launch to use standard game logging when troubleshooting."))
        self.nvidia_dlss = tk.BooleanVar(page, value=state.nvidia_dlss_initial)
        row = dlss.row(height=18)
        self.dlss_checkbox = row.widget(ttk.Checkbutton(
            row.host, text="Enable NVIDIA DLSS support", variable=self.nvidia_dlss,
            command=commands.save_dlss_preference, style="Kit.TCheckbutton"))
        dlss.text("Applies to the next launch. Then choose NVIDIA DLSS in the game's Video settings. "
                  "Requires a compatible NVIDIA GPU; affects previews and recorded Color video.", top=15)

        shade = self.reshade_card = page.add_card("ReShade")
        self.reshade_path_entry, self.reshade_path_cover, self.reshade_browse_button = self._path_row(
            shade, "Runtime DLL", state.reshade_runtime_path, "Browse runtime...", commands.browse_reshade)
        self.reshade_path_entry.bind(
            "<Return>", lambda _event: commands.select_reshade_runtime(state.reshade_runtime_path.get()), add="+")
        # Disable only while ReShade loads or is ready; the app's poll owns the three states from here.
        buttons = self._buttons(shade, (("Enable ReShade", commands.configure_reshade),
                                        ("Disable for this session", commands.disable_reshade, "disabled"),
                                        ("Forget runtime", commands.forget_reshade)), indent=INDENT)
        self.reshade_configure_button, self.reshade_disable_button, self.reshade_forget_button = buttons
        self.reshade_library_button, _ = self._buttons(
            shade, (("Browse FX library...", commands.browse_reshade_library),
                    ("Menu shortcut...", commands.show_reshade_keybinds)), indent=INDENT)
        self.reshade_status = shade.text(textvariable=state.reshade_status_text, top=15)
        presence = self.presence_card = page.add_card("Discord Rich Presence")
        self.discord_presence = tk.BooleanVar(page, value=state.discord_presence_initial)
        self.discord_status = tk.StringVar(page, value="Requires the Discord desktop app and activity sharing enabled in Discord.")
        row = presence.row(height=18)
        self.discord_presence_checkbox = row.widget(ttk.Checkbutton(
            row.host, text="Show Dolly activity in Discord", variable=self.discord_presence,
            command=commands.save_discord_preference, style="Kit.TCheckbutton"))
        presence.text(textvariable=self.discord_status, top=15)

        mods = self.mods_card = page.add_card("Replay Mod Overrides")
        self.allow_mod_overrides = tk.BooleanVar(page, value=state.allow_mod_overrides_initial)
        row = mods.row(height=18)
        self.mod_overrides_checkbox = row.widget(ttk.Checkbutton(
            row.host, text="Load installed mod overrides", variable=self.allow_mod_overrides,
            command=commands.save_mod_preference, style="Kit.TCheckbutton"))
        mods.text("Extra mods can change the captured view or override Dolly's interface hiding. "
                  "This applies to the next replay session.", top=15)

        page.watch_focus()

    @staticmethod
    def _path_row(card, label, variable, button, command):
        """Label column, a filling path field (elided at rest) and a fixed-width Browse button."""
        row = card.row()
        row.label(label, width=LABEL_WIDTH)
        row.gap(10)
        entry = row.widget(ttk.Entry(row.host, textvariable=variable, style="Kit.TEntry"), width="fill")
        cover = elide_at_rest(entry, variable)
        row.gap(10)
        return entry, cover, row.button(button, command, width=LABEL_WIDTH)

    @staticmethod
    def _buttons(card, specs, *, indent=0):
        """One row of hugging buttons 10 apart: (text, command[, state]) each."""
        row = card.row()
        if indent:
            row.gap(indent)
        made = []
        for index, (text, command, *state) in enumerate(specs):
            if index:
                row.gap(10)
            made.append(row.button(text, command, state=state[0] if state else "normal"))
        return made


class CameraFeel:
    """Camera look preferences, owned by the Controls page."""
    def __init__(self, body, mouse_sensitivity, save_sensitivity):
        self.mouse_sensitivity = mouse_sensitivity
        self.save_sensitivity = save_sensitivity
        self._sensitivity_syncing = False
        dp = scaler(body)
        self.sensitivity_position = tk.DoubleVar(value=sensitivity_to_position(self._sensitivity_value()))
        self.sensitivity_text = tk.StringVar()
        feel = card(body, "Camera feel")
        ttk.Label(feel, text="Free-camera look speed. Lower it when a high-DPI mouse makes the "
                             "camera spin, or raise it for a slow desktop mouse.",
                  style="CardMuted.TLabel", wraplength=dp(760)).pack(anchor="w", pady=dp((0, GAP)))
        presets = ttk.Frame(feel, style="Card.TFrame")
        presets.pack(fill="x", pady=dp((0, GAP)))
        for label, value in SENSITIVITY_PRESETS:
            ttk.Button(presets, text=label, style="Card.Quiet.TButton",
                       command=lambda preset=value: self._apply_sensitivity(preset)).pack(side="left", padx=dp((0, 8)))
        slider = ttk.Scale(feel, from_=0, to=SENSITIVITY_TICKS, orient="horizontal",
                           variable=self.sensitivity_position, command=self._sensitivity_moved)
        slider.pack(fill="x")
        slider.bind("<ButtonRelease-1>", self._sensitivity_released)
        ttk.Label(feel, textvariable=self.sensitivity_text, style="CardMuted.TLabel").pack(anchor="w", pady=dp((6, 10)))
        exact = ttk.Frame(feel, style="Card.TFrame")
        exact.pack(fill="x")
        ttk.Label(exact, text="Mouse sensitivity", style="CardMuted.TLabel").pack(side="left", padx=dp((0, 10)))
        self.sensitivity_entry = ttk.Entry(exact, textvariable=mouse_sensitivity, width=chars(exact, 12), style="Card.TEntry")
        self.sensitivity_entry.pack(side="left", padx=dp((0, 10)))
        self.sensitivity_entry.bind("<Return>", lambda _event: self.save_sensitivity())
        ttk.Button(exact, text="Save sensitivity", command=save_sensitivity,
                   style="Card.Quiet.TButton").pack(side="left")
        self.mouse_sensitivity.trace_add("write", self._sensitivity_state_changed)
        self._sensitivity_text_refresh(self._sensitivity_value())

    def _sensitivity_value(self):
        try:
            value = float(self.mouse_sensitivity.get())
        except (TypeError, ValueError):
            return SENSITIVITY_PRESETS[2][1]
        return value if math.isfinite(value) and value > 0 else SENSITIVITY_PRESETS[2][1]

    def _sensitivity_text_refresh(self, value):
        self.sensitivity_text.set(f"Current: {value:.3g} ({sensitivity_preset_name(value)})")

    def _sensitivity_moved(self, _position=None):
        self._sensitivity_text_refresh(position_to_sensitivity(self.sensitivity_position.get()))

    def _sensitivity_released(self, _event=None):
        self._apply_sensitivity(position_to_sensitivity(self.sensitivity_position.get()))

    def _apply_sensitivity(self, value):
        value = position_to_sensitivity(sensitivity_to_position(value))
        self._sensitivity_syncing = True
        try:
            self.sensitivity_position.set(sensitivity_to_position(value))
            self.mouse_sensitivity.set(f"{value:.4g}")
            self._sensitivity_text_refresh(value)
        finally:
            self._sensitivity_syncing = False
        self.save_sensitivity()

    def _sensitivity_state_changed(self, *_args):
        if self._sensitivity_syncing:
            return
        value = self._sensitivity_value()
        self._sensitivity_syncing = True
        try:
            self.sensitivity_position.set(sensitivity_to_position(value))
            self._sensitivity_text_refresh(value)
        finally:
            self._sensitivity_syncing = False
