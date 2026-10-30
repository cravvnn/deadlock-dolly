"""Dependency-free desktop editor for local Deadlock replay shots.

The UI thread owns every Tk object. Game operations run on one worker, and
worker results/log messages travel through an event queue polled by Tk.
"""
from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime
import json
import logging
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from dolly import __version__, attach_camera, dialogs, dof_preflight, editor_session, gui_layout, player_layer, screenshot_ui, ui_theme
from .ui import (art, camera_page, chrome, controls_page, export_page, fonts, library_page as replay_page, look_page,
                 settings_page, shot_commands, window_bars, window_chrome)
from .ui.action_proxy import ActionProxy
from .ui.backdrop import Backdrop
from .ui.boil import BoilClock
from .ui.chrome import FooterLink, Stage
from .ui.confirm import ask_reconstructed_helper
from .ui import text_dialog as simpledialog
from .ui import question_dialog as messagebox
from .ui.kit_page import KitPage
from .ui.scale import chars, enable_dpi_awareness, even_px, px, ui_scale
from .ui.stage_page import end_gap
from .ui.sidebar import Sidebar
from dolly.editor_actions import ACTION_LABELS, ACTION_ORDER, OBJECT_PICKER_ENABLED, default_action_bindings
from dolly.replays import discover_replays, find_replay_folder, parse_launch_options
from dolly.bindings import CaptureBinding, DEFAULT_BINDING
from dolly.branding import apply_window_icon
from dolly.controller import (Controller, PreloadUnavailableError, StartupAcknowledgementRequired,
                              tick_rate_advice, tick_rates_match)
from dolly.clip_audio import ClipAudioCapture
from dolly.curve import AspectCurve, PositionCurve, POSITION_CHANNELS, POSITION_LIMIT, RotationCurve, TimelineView
from dolly.display import focus_window
from dolly.ffmpeg_color import REC709_FRAME_TAGS, REC709_OUTPUT_ARGS, REC709_SCALE
from dolly.hotkey import CaptureHotkey
# Compatibility import for existing callback users.
from .gui_theme import _clear_committed_combobox_selection
from dolly.launcher import discover_game, recover_pending, validate_game
from dolly.launcher import discover_game, recover_pending
from dolly.memory_watch import PausedMemoryWatch
from dolly.navigation import CameraMotion
from dolly.path import (AttachKey, CONFETTI_SPAWN_HEIGHT_DEFAULT, CURVE_CHANNELS,
                        CvarTrack, Keyframe, Project, TrackKey, parse_cvar_value,
                        format_cvar_value)
from dolly.settings import AppSettings, load_settings, save_settings
from dolly.shot_history import ShotHistory
from dolly.particles import (LABEL_TO_ID, PARTICLE_DEFAULT, PARTICLE_INTENSITY_DEFAULT,
                             PARTICLE_INTENSITY_MAX, PARTICLE_INTENSITY_MIN, PARTICLE_LABELS)
from dolly.smoothing import smoothing_window
from dolly.source2viewer import HELP_SIZE_MB, find_viewer
from dolly.video_export import (ACTIVE_STATES, BITRATE_PRESETS, CODEC_BY_KEY, CODEC_CHOICES,
                                CODEC_LABEL_TO_KEY, CONTAINER_CHOICES, CONTAINER_LABEL_TO_KEY,
                                DEFAULT_CODEC_KEY, DEFAULT_CONTAINER, VideoExport, VideoOptions,
                                bundled_ffmpeg_path, default_video_path, format_video_status,
                                recording_ready, resolve_container, resolve_ffmpeg, video_suffix)


FIELDS = ("time", "x", "y", "z", "pitch", "yaw", "roll", "aspect_ratio")
FIELD_LABELS = ("Shot seconds", "X", "Y", "Height · Z", "Pitch °", "Yaw °", "Bank °", "Aspect ratio")
_T = ui_theme.TOKENS
_F = ui_theme.FONT_SIZES
FAMILY = ui_theme.FONT_FAMILY
MONO = ui_theme.FONT_MONO
BG = _T["bg"]
PANEL = _T["panel"]
PANEL_ALT = _T["panel_alt"]
FIELD = _T["field"]
EDGE = _T["edge"]
TEXT = _T["text"]
MUTED = _T["muted"]
STRONG = _T["strong"]
ACCENT = _T["accent"]
ACCENT_HOVER = _T["accent_hover"]
ACCENT_INK = _T["accent_ink"]
BUTTON = _T["button"]
BUTTON_HOVER = _T["button_hover"]
DISABLED_BG = _T["disabled_bg"]
DISABLED_FG = _T["disabled_fg"]
SELECTED = _T["selected"]
SELECTED_INK = _T["selected_ink"]
TRACK = _T["track"]
WARN_BG = _T["warn_bg"]
LOG = logging.getLogger("dolly")
ASPECT_PRESETS = {"16:9": 16 / 9, "16:10": 16 / 10, "21:9": 21 / 9, "4:3": 4 / 3}
# Header game-status tones: resting states read dim, trouble reads as an error.
_QUIET_SESSION = {"Game not connected", "Game not launched", "Game closed"}


def _session_tone(text):
    if text in _QUIET_SESSION:
        return "dim"
    if text == "Session needs attention" or text.startswith("Disconnected"):
        return "error"
    return "muted"



def _number(value: object) -> str:
    """Human-friendly round-trip formatting for editable numeric cells."""
    try:
        return f"{float(value):.9g}"
    except (ValueError, TypeError):
        return str(value)


def _finite(value: str, label: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be a number.") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite.")
    return number


def _set_var(variable, value):
    """Assign a Tk variable only when it changes.

    The status poll runs several times a second; re-setting identical strings
    makes Tk redraw labels and comboboxes for no visible change, which is the
    main source of idle stutter in dropdowns, sliders and scrolling.
    """
    try:
        current = variable.get()
    except (AttributeError, tk.TclError):
        variable.set(value)
        return
    if current != value:
        variable.set(value)


def _set_widget_state(widget, state):
    """Apply a widget state only when it differs, for the same reason."""
    try:
        current = str(widget.cget("state"))
    except (AttributeError, tk.TclError):
        widget.configure(state=state)
        return
    if current != state:
        widget.configure(state=state)


def _binding_event_key(event):
    from .ui.binding_capture import key_from_event
    return key_from_event(event)


# Bound pending UI events so a chatty failure loop cannot grow memory without
# limit. Log lines may be dropped (and are reported); command results never are.
from .ui.operations import EVENT_QUEUE_LIMIT, OperationQueue
from .ui.layer_pipeline import LayerPipeline, recover_export_failure


class DollyApp:
    @property
    def layer_pipeline(self):
        if "_layer_pipeline" not in self.__dict__:
            self._layer_pipeline = LayerPipeline()
        return self._layer_pipeline

    @property
    def _pending_auto_play(self):
        return self.layer_pipeline.pending_auto_play

    @_pending_auto_play.setter
    def _pending_auto_play(self, value):
        self.layer_pipeline.pending_auto_play = value

    @property
    def _auto_finish_layered(self):
        return self.layer_pipeline.auto_finish_layered

    @_auto_finish_layered.setter
    def _auto_finish_layered(self, value):
        self.layer_pipeline.auto_finish_layered = value

    @property
    def _take_playing_seen(self):
        return self.layer_pipeline.take_playing_seen

    @_take_playing_seen.setter
    def _take_playing_seen(self, value):
        self.layer_pipeline.take_playing_seen = value

    @property
    def _pending_combine(self):
        return self.layer_pipeline.pending_combine

    @_pending_combine.setter
    def _pending_combine(self, value):
        self.layer_pipeline.pending_combine = value

    @property
    def _pending_capture(self):
        return self.layer_pipeline.pending_capture

    @_pending_capture.setter
    def _pending_capture(self, value):
        self.layer_pipeline.pending_capture = value

    @property
    def _pipeline_advance(self):
        return self.layer_pipeline.advance_pending

    @_pipeline_advance.setter
    def _pipeline_advance(self, value):
        self.layer_pipeline.advance_pending = value

    @property
    def _base_capture(self):
        return self.layer_pipeline.base_capture

    @_base_capture.setter
    def _base_capture(self, value):
        self.layer_pipeline.base_capture = value

    @property
    def _layer_queue(self):
        return self.layer_pipeline.queue

    @_layer_queue.setter
    def _layer_queue(self, value):
        self.layer_pipeline.queue = value

    @property
    def _active_layer_take(self):
        return self.layer_pipeline.active_take

    @_active_layer_take.setter
    def _active_layer_take(self, value):
        self.layer_pipeline.active_take = value

    @property
    def operations(self):
        if "_operations" not in self.__dict__:
            self._operations = OperationQueue()
        return self._operations

    @property
    def events(self):
        return self.operations.events

    @events.setter
    def events(self, value):
        self.operations.events = value

    @property
    def jobs(self):
        return self.operations.jobs

    @jobs.setter
    def jobs(self, value):
        self.operations.jobs = value

    @property
    def _dropped_logs(self):
        return self.operations.dropped_logs

    @_dropped_logs.setter
    def _dropped_logs(self, value):
        self.operations.dropped_logs = value

    @property
    def closed(self):
        return self.operations.closed

    @closed.setter
    def closed(self, value):
        self.operations.closed = value

    @property
    def busy(self):
        return self.operations.busy

    @busy.setter
    def busy(self, value):
        self.operations.busy = value

    def __init__(self, root: tk.Tk):
        self.root = root
        # USER-ADDITIONS UPDATE 20: Dolly's own title bar replaces the Windows caption. Tk has to
        # know the window is frameless before its Windows frame exists, so this comes before
        # anything that creates it: setting the window icon does (hidden test windows keep their
        # native frame).
        self.window_chrome = window_chrome.WindowChrome(self.root)
        self.window_chrome.prepare()
        apply_window_icon(root)
        self.root.title("Deadlock Dolly — Untitled shot")
        self.ui_scale = ui_scale(self.root)
        self._operations = OperationQueue()
        self._memory_watch = PausedMemoryWatch()
        self._take_audit_seen: set[str] = set()
        self._take_audit_reported = False
        self.closed = False
        self._discord_presence = None
        self._closing = False
        self.busy = False
        self._error_dialog_open = False
        self.dirty = False
        self.file_path: Path | None = None
        self.project = Project(name="Untitled shot", keyframes=[], tracks=[],
                               interpolation="smooth")
        self.shot_history = ShotHistory(self.project)
        self.controller = Controller(log_callback=self._enqueue_log)
        self.video_export = VideoExport(self.controller)
        self.worker = self.operations.start(self._operation_failure_kind)
        self.game_path = tk.StringVar()
        self.demo_path = tk.StringVar()
        self.protocol = tk.StringVar(value="Netconsole")
        self.camera_driver = tk.StringVar(value="Native (experimental)")
        self.status_text = tk.StringVar(value="Choose a replay and press START to begin editing.")
        self.session_text = tk.StringVar(value="Game not connected")
        self.driver_indicator = tk.StringVar(value="DRIVER · NATIVE")
        self.project_text = tk.StringVar(value="Untitled shot")
        self.busy_text = tk.StringVar(value="")
        self.start_tick = tk.StringVar(value="0")
        self.tick_rate = tk.StringVar(value="64")
        self.interpolation = tk.StringVar(value="smooth")
        self.rotation = tk.StringVar(value="shortest")
        self.standard_aspect = tk.StringVar(value="16:9")
        self.lens_interpolation = tk.StringVar(value="smooth")
        self.confetti_enabled = tk.BooleanVar(value=False)
        self.confetti_spawn_height = tk.StringVar(value=_number(CONFETTI_SPAWN_HEIGHT_DEFAULT))
        self.confetti_despawn_on_ground = tk.BooleanVar(value=False)
        self.particles_preset = tk.StringVar(value=PARTICLE_LABELS[PARTICLE_DEFAULT])
        self.particles_intensity = tk.StringVar(value=_number(PARTICLE_INTENSITY_DEFAULT))
        self.selected_text = tk.StringVar(value="No camera selected")
        self.path_summary = tk.StringVar(value="0 cameras · 0.00 s")
        self.coordinates_dialog = None
        self.key_vars = {field: tk.StringVar(value=("1.77777778" if field == "aspect_ratio" else "0")) for field in FIELDS}
        self.track_name = tk.StringVar()
        self.track_mode = tk.StringVar(value="linear")
        self.restore_value = tk.StringVar()
        self.track_time = tk.StringVar(value="0")
        self.track_value = tk.StringVar(value="0")
        self.setup_name = tk.StringVar()
        self.setup_value = tk.StringVar(value="1")
        self.shot_time = tk.DoubleVar(value=0.0)
        self.time_text = tk.StringVar(value="0")
        self.speed = tk.StringVar(value="1")
        self.rate = tk.StringVar(value="60")
        self.smoothing = tk.StringVar(value="Balanced")
        self.frozen = tk.BooleanVar(value=False)
        self.hide_hud = tk.BooleanVar(value=True)
        self.seek_relief = tk.BooleanVar(value=True)
        self.capture_mode = tk.StringVar(value="Replay timing")
        self.segment_seconds = tk.StringVar(value="3")
        self._tick_rate_prompted = set()
        self.show_coordinates = tk.BooleanVar(value=False)
        self.app_settings = self._load_app_settings()
        self.allow_mod_overrides = tk.BooleanVar(value=self.app_settings.allow_mod_overrides)
        # Retain the legacy variable for internal callers; every desktop page is available.
        self.full_editor = tk.BooleanVar(value=True)
        self.video_path = tk.StringVar(value=str(default_video_path(self.project.name,
            Path(self.app_settings.export_folder) if self.app_settings.export_folder else None)))
        self.video_source = tk.StringVar(value="Camera path")
        self.video_pov_duration = tk.StringVar(value="10")
        self._pov_project = None
        self.video_fps = tk.StringVar(value="60")
        self.video_bitrate = tk.StringVar(value="20 Mbps")
        self.video_codec = tk.StringVar(value=CODEC_BY_KEY[DEFAULT_CODEC_KEY][0])
        self.video_format = tk.StringVar(value=dict(CONTAINER_CHOICES)[DEFAULT_CONTAINER])
        self.player_processing = tk.StringVar(value="Background")
        self.players_scratch = tk.StringVar(value="")
        self._player_encode_cancel = None
        self._player_encode_progress = 0
        self.video_fixed_step = tk.BooleanVar(value=False)
        self.video_depth = tk.BooleanVar(value=False)
        self.video_depth_exr = tk.BooleanVar(value=False)
        # The EXR sequence requires the depth master; follow the variable so
        # the gate also covers in-game depth changes (editor events).
        self.video_depth.trace_add("write", self._sync_depth_controls)
        self.video_layer_world = tk.BooleanVar(value=False)
        self.video_layer_players = tk.BooleanVar(value=False)
        self.video_layer_effects = tk.BooleanVar(value=False)
        self.video_export_speed = tk.StringVar(value="1")
        _bundled_ffmpeg = bundled_ffmpeg_path()
        self.ffmpeg_path = tk.StringVar(value=self.app_settings.ffmpeg_path or (str(_bundled_ffmpeg) if _bundled_ffmpeg else ""))
        self.video_status_text = tk.StringVar(value="Launch a replay to record video.")
        self.video_game_audio = tk.BooleanVar(value=False)
        self.video_reconstructed_audio = tk.BooleanVar(value=False)
        self.video_ae_camera = tk.BooleanVar(value=False)
        self.video_usd_camera = tk.BooleanVar(value=False)
        self.clip_audio = None
        self._pending_auto_play = False
        # Layered takes capture exactly the authored path, so they finish
        # themselves when the shot completes instead of waiting for a manual
        # Finish press (post-shot frames carry no replay time anyway).
        self._auto_finish_layered = False
        self._base_capture = None
        self._layer_queue = []
        self._active_layer_take = None
        self._pending_combine = None
        self._pipeline_advance = False
        # Set once the active take's shot has actually started playing, so a
        # stale "shot finished" message cannot finish a fresh take early.
        self._take_playing_seen = False
        self.reshade_runtime_path = tk.StringVar(value=getattr(self.app_settings, "reshade_runtime_path", ""))
        self.reshade_status_text = tk.StringVar(value="Choose the ReShade runtime to enable its in-game menu.")
        self.game_path.set(self.app_settings.game_path)
        self.demo_path.set(self.app_settings.demo_path)
        self.replay_folder = tk.StringVar(value=self.app_settings.replay_folder)
        self.launch_options = tk.StringVar(value=self.app_settings.launch_options)
        screenshot_ui.init(self)
        self.editor_move_speed = tk.StringVar(value=_number(self.app_settings.movement_speed))
        self.editor_sensitivity = tk.StringVar(value=_number(self.app_settings.mouse_sensitivity))
        self.replay_search = tk.StringVar()
        from dolly.replays import DEFAULT_REPLAY_SORT
        self.replay_sort = tk.StringVar(value=DEFAULT_REPLAY_SORT)
        self.replay_summary = tk.StringVar(value="Choose your replay folder.")
        self.startup_progress = tk.StringVar(value="Ready when you are.")
        self.startup_cancel = None
        self.native_editor_active = False
        # Live schema offsets for the attach camera; queried once per session
        # after startup and republished by editor_session.configure.
        self.attach_fields = None
        self.preview_attach = False
        # Object Picker mode: a session-only toggle entered by its bindable key.
        self.object_picker_open = False
        self.object_picker_selected = -1
        self.replay_entries = []
        self.binding_feedback = tk.StringVar(value="Choose a shortcut below. Clear removes it; Reset restores that action's default.")
        self.capture_binding = self.app_settings.capture_binding
        self.hotkey_enabled = tk.BooleanVar(value=False)
        self.hotkey_label = tk.StringVar(value=self._capture_binding_label())
        self.capture_hotkey = None
        self.capture_hotkey_checkboxes = []
        self.capture_generation = 0
        self.binding_dialog = None
        self.capture_hint = tk.StringVar(value="Move the replay free camera, then capture each view. Capture pauses a playing replay; the views form a smooth path.")
        self.show_log = tk.BooleanVar(value=False)
        self.playing = False
        self.dragging = False
        self._last_controller_message = ""
        self._style()
        self._build_menu()
        # The Figma frame: a 1200 x 810 design px page area (1800 x 1215 screen pixels at
        # 150 %) between the 26 px title bar and the 29 px link bar (Frame 79, [design reference]).
        self.root.geometry(self._main_size(1200, 810, centered=True))
        self.root.minsize(*self._main_dimensions(1000, 700))
        self._build_window()
        self._refresh_project()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        # The title bar's menus open from the keyboard as the native menu bar's did.
        for key, name in (("f", "File"), ("F", "File"), ("e", "Edit"), ("E", "Edit")):
            self.root.bind(f"<Alt-KeyPress-{key}>", lambda _event, name=name: self.title_bar.open_menu(name))
        # The custom frame takes over once the window has mapped (the page builders may
        # already have mapped it, unseen: it stays transparent until then). Bound on the
        # window's own tag (not on every child's <Map>), and unbound once it has run.
        self._window_chrome_map = None
        if self.root.winfo_ismapped():
            self._finish_window_chrome()
        else:
            tag = window_chrome.window_tag(self.root)
            self._window_chrome_map = (tag, self.root.bind_class(tag, "<Map>", self._finish_window_chrome, add="+"))
        self.root.bind("<Control-s>", lambda _event: self.save())
        self.root.bind("<Control-o>", lambda _event: self.open())
        self.root.bind("<Control-n>", lambda _event: self.new())
        self.root.bind("<Control-z>", self._history_shortcut)
        self.root.bind("<Control-y>", lambda event: self._history_shortcut(event, True))
        self.root.bind("<Control-Shift-Z>", lambda event: self._history_shortcut(event, True))
        self.root.after(80, self._poll)
        self._submit("Looking for Deadlock", discover_game, self._discovered)

    def _px(self, value):
        """Design pixels (a number or a padding tuple) at this window's display scale."""
        # Bare test objects (DollyApp.__new__) draw at 100 %.
        return even_px(getattr(self, "ui_scale", 1.0), value)

    def _chars(self, count):
        """A ttk ``width`` of ``count`` characters, kept at its 100 % size in design px."""
        return chars(self.root, count) if getattr(self, "ui_scale", 1.0) != 1.0 else count

    def _window_dimensions(self, width, height):
        """A dialog's design-px client size in screen pixels, clamped so its framed window fits the work area."""
        left, top, right, bottom = chrome.work_area(self.root)
        frame_width, frame_height = chrome.frame_size(menu=False)
        # Tk reports the primary display size even when the owner is on a
        # larger monitor. Do not let that smaller size cap the chosen work area.
        screen_width = max(self.root.winfo_screenwidth(), right - left)
        screen_height = max(self.root.winfo_screenheight(), bottom - top)
        return (min(self._px(width), right - left - frame_width, screen_width - self._px(40)),
                min(self._px(height), bottom - top - frame_height, screen_height - self._px(80)))

    def _window_size(self, width, height, *, centered=False):
        """Tk geometry for a dialog's design-px client size; ``centered`` also centres it on the work area."""
        width, height = self._window_dimensions(width, height)
        if not centered:
            return f"{width}x{height}"
        left, top, right, bottom = chrome.work_area(self.root)
        frame_width, frame_height = chrome.frame_size(menu=False)
        x = left + max(0, (right - left - width - frame_width) // 2)
        y = top + max(0, (bottom - top - height - frame_height) // 2)
        return f"{width}x{height}+{x}+{y}"

    def _frameless(self):
        """Whether Dolly draws the main window's whole frame (its own title bar, no Windows caption)."""
        window = getattr(self, "window_chrome", None)
        return bool(window is not None and window.frameless)

    def _frame_size(self):
        """Width and height the main window's frame adds round its client area (none when frameless)."""
        return (0, 0) if self._frameless() else chrome.frame_size(menu=False)

    def _bars_px(self):
        """Screen px of the title bar and the link bar (USER-ADDITIONS UPDATE 20)."""
        return window_bars.bars_px(getattr(self, "ui_scale", 1.0))

    def _main_dimensions(self, width, height):
        """The main window's client size for a ``width`` x ``height`` design-px page area.

        The client is the stage plus the title and link bars (USER-ADDITIONS UPDATE 20), so
        every page keeps the area it had under the native caption. Clamped so the window with
        its frame fits the monitor's work area; a framed window also keeps clear of the screen
        edges, as before (the frameless one fits its work area exactly).
        """
        left, top, right, bottom = chrome.work_area(self.root)
        frame_width, frame_height = self._frame_size()
        width = min(self._px(width), right - left - frame_width)
        height = min(self._px(height) + self._bars_px(), bottom - top - frame_height)
        if not self._frameless():
            width = min(width, max(self.root.winfo_screenwidth(), right - left) - self._px(40))
            height = min(height, max(self.root.winfo_screenheight(), bottom - top) - self._px(80))
        return width, height

    def _main_size(self, width, height, *, centered=False):
        """Tk geometry of the main window for a design-px page area; ``centered`` centres it on the work area."""
        width, height = self._main_dimensions(width, height)
        if not centered:
            return f"{width}x{height}"
        left, top, right, bottom = chrome.work_area(self.root)
        frame_width, frame_height = self._frame_size()
        x = left + max(0, (right - left - width - frame_width) // 2)
        y = top + max(0, (bottom - top - height - frame_height) // 2)
        return f"{width}x{height}+{x}+{y}"

    def _caption_buttons_shown(self):
        """Whether the title bar draws minimize/maximize/close: not beside a native caption.

        Hidden test and capture windows (and the hidden bundle self-test) keep a native frame
        nobody sees; they draw the buttons too, so they look like the real window.
        """
        window = getattr(self, "window_chrome", None)
        return window is None or window.frameless or window.skipped in ("quiet", "withdrawn")

    def _finish_window_chrome(self, event=None):
        """On the first map: the custom frame takes over, or the window keeps its native one."""
        if event is not None and event.widget is not self.root:
            return
        if getattr(self, "_window_chrome_done", False):
            return
        self._window_chrome_done = True
        binding, self._window_chrome_map = getattr(self, "_window_chrome_map", None), None
        if binding is not None:
            window_chrome.unbind_tag(self.root, binding[0], "<Map>", binding[1])
        window = self.window_chrome
        if window.frameless:
            if window.finish():
                self._edge_resizer = window_chrome.EdgeResizer(self.root, window)
                self._edge_resizer.attach()
                return
            # The native frame is back: its caption has the buttons, and it takes its own space.
            # The window was sized and centred for no frame (it may fill the work area exactly):
            # size it again so the framed window fits the work area.
            self.title_bar.set_caption_buttons(False)
            self.root.minsize(*self._main_dimensions(1000, 700))
            self.root.geometry(self._main_size(1200, 810, centered=True))
            chrome.style_title_bar(self.root)

    def _style(self):
        from . import gui_theme
        gui_theme.initialize(self.root)
        # Valve's display faces load privately from the user's own game install
        # when it has them; the named fonts update in place, so a later game
        # path (discovery, Browse) can still upgrade them.
        fonts.request_private_fonts(self.root, self.game_path.get())
        fonts.on_change(self.root, lambda: gui_theme.apply_display_fonts(self.root))

    def _request_display_fonts(self):
        """Look for the display faces in a newly chosen game folder, once per burst of edits."""
        if getattr(self, "_font_request_job", None) is not None:
            return

        def request():
            self._font_request_job = None
            fonts.request_private_fonts(self.root, self.game_path.get())
        self._font_request_job = self.root.after_idle(request)

    def _build_menu(self):
        """The File and Edit menus, opened from the title bar (USER-ADDITIONS UPDATE 20)."""
        file_menu = self.file_menu = tk.Menu(self.root, tearoff=False)
        file_menu.add_command(label="New shot", accelerator="Ctrl+N", command=self.new)
        file_menu.add_command(label="Open shot…", accelerator="Ctrl+O", command=self.open)
        file_menu.add_command(label="Save", accelerator="Ctrl+S", command=self.save)
        file_menu.add_command(label="Save as…", command=lambda: self.save(save_as=True))
        file_menu.add_command(label="Rename shot…", command=self.rename)
        file_menu.add_separator()
        file_menu.add_command(label="Controls…", command=self._show_keybinds)
        file_menu.add_command(label="Export diagnostics…", command=self._diagnostics)
        file_menu.add_command(label="Check Native DOF support", command=self._check_dof)
        file_menu.add_command(label="Recover game configuration…", command=self._recover_game_config)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)
        self.edit_menu = tk.Menu(self.root, tearoff=False, postcommand=self._refresh_history_menu)
        self.edit_menu.add_command(label="Undo shot edit", accelerator="Ctrl+Z", command=self.undo_shot)
        self.edit_menu.add_command(label="Redo shot edit", accelerator="Ctrl+Y", command=self.redo_shot)

    def _build_window(self):
        # The title bar on top and the link bar at the bottom span the window (UPDATE 20); between
        # them the sidebar column sits beside the stage canvas. Pages stay in the hidden-tab
        # notebook placed on the stage, so notebook.select() still switches them.
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(1, weight=1)
        self.title_bar = window_bars.TitleBar(self.root, scale=self.ui_scale, chrome=self.window_chrome,
                                              menus=(("File", self.file_menu), ("Edit", self.edit_menu)),
                                              on_close=self._on_close)
        self.title_bar.set_caption_buttons(self._caption_buttons_shown())
        self.title_bar.set_title("Untitled shot")
        self.title_bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.link_bar = window_bars.LinkBar(self.root, scale=self.ui_scale)
        self.link_bar.grid(row=2, column=0, columnspan=2, sticky="ew")
        # One posterized clock per window moves the backdrop, stickers, busy dot
        # and progress line together; the backdrop registers first.
        self.boil = BoilClock.of(self.root)
        assets = art.load(self.root)
        self.backdrop = Backdrop(self.root, scale=self.ui_scale, assets=assets)
        self.backdrop.install()
        self.sidebar = Sidebar(self.root, scale=self.ui_scale, version=__version__,
                               driver_var=self.driver_indicator, assets=assets)
        self.sidebar.grid(row=1, column=0, sticky="ns")
        self.stage = Stage(self.root, scale=self.ui_scale, backdrop=self.backdrop)
        self.stage.grid(row=1, column=1, sticky="nsew")
        self.update_notice_parent = self.stage.notice_host
        workspace = self.stage.workspace
        style = ttk.Style(self.root)
        style.layout("Navigation.TNotebook.Tab", [])
        # No client element either: clam's draws a fixed 2 px border, which
        # would frame the Replay page's backdrop in the window colour.
        style.layout("Navigation.TNotebook", [])
        self.notebook = ttk.Notebook(workspace, style="Navigation.TNotebook")
        self.notebook.grid(row=0, column=0, sticky="nsew")
        self.setup_tab = ttk.Frame(self.notebook)
        self.camera_tab = ttk.Frame(self.notebook)
        self.cvar_tab = ttk.Frame(self.notebook)
        self.settings_tab = ttk.Frame(self.notebook)
        self.controls_tab = ttk.Frame(self.notebook)
        self.export_tab = ttk.Frame(self.notebook)
        self.replays_tab = self.setup_tab
        # Shot Playback host cards by notebook tab (the docks of Camera and Look).
        self._playback_hosts = {}
        self.navigation = {}
        for title, icon, tab in (("Replay", "replay", self.setup_tab), ("Camera", "camera", self.camera_tab),
                                 ("Look", "look", self.cvar_tab), ("Export", "export", self.export_tab),
                                 ("Controls", "controls", self.controls_tab),
                                 ("Settings", "settings", self.settings_tab)):
            self.notebook.add(tab, text=title)
            self.navigation[str(tab)] = self.sidebar.add_item(
                str(tab), title, icon, lambda page=tab: self._select_page(page))
        self._build_setup()
        gui_layout.build_settings(self)
        self._build_keybinds()
        gui_layout.build_camera(self)
        self._build_fixed_dialog()
        gui_layout.build_look(self)
        self._refresh_citadel_dof()
        self._build_export()
        self._apply_editor_mode()
        # Shot Playback is built after the notebook so it stacks above every page.
        gui_layout.build_playback(self, workspace)
        self._build_shell()
        self.notebook.bind("<<NotebookTabChanged>>", self._tab_changed)
        self._tab_changed()
        self.log_dialog = tk.Toplevel(self.root)
        chrome.style_title_bar(self.log_dialog)
        self.log_dialog.title("Dolly activity log")
        self.log_dialog.geometry(self._window_size(880, 458))
        self.log_dialog.configure(bg=BG)
        self.log_dialog.transient(self.root)
        self.log_dialog.protocol("WM_DELETE_WINDOW", self._close_log)
        self.log_frame = ttk.Frame(self.log_dialog, padding=self._px((24, 22, 24, 24)))
        self.log_frame.pack(fill="both", expand=True)
        tk.Label(self.log_frame, text="Dolly activity log", bg=BG, fg=TEXT,
                 font=fonts.font(self.log_dialog, "page_title"), anchor="w").pack(
                     fill="x", pady=self._px((0, 15)))
        log_card = ttk.Frame(self.log_frame, style="Rounded.Card.TFrame", padding=self._px(20))
        log_card.pack(fill="both", expand=True)
        self.log_widget = tk.Text(log_card, height=3, bg=FIELD, fg=TEXT, relief="flat",
                                  padx=self._px(14), pady=self._px(12),
                                  font=(MONO, -self._px(12)), wrap="word", state="disabled")
        self.log_widget.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(log_card, command=self.log_widget.yview)
        scroll.pack(side="right", fill="y")
        self.log_widget.configure(yscrollcommand=scroll.set)
        for entry in getattr(self, "_early_log", ()):
            self._log(entry)
        self._early_log = []
        self.log_dialog.withdraw()
        if not self._frameless():
            chrome.style_title_bar(self.root)   # the native caption stays: recolour it

    def _build_shell(self):
        """Header, footer links, status line and stickers on the stage (Figma [design reference], [design reference])."""
        self.stop_action = ActionProxy("Stop / restore",
                                       lambda: self._session_operation("Stopping", self.controller.stop),
                                       label="STOP/RESTORE")
        self.stage.set_secondary(self.stop_action)
        self.stage.register_page(str(self.setup_tab), title=replay_page.TITLE, subtitle=replay_page.SUBTITLE,
                                 primary=self.play_replay_button)
        # START stays Replay's; the other pages take their header copy from their modules.
        for tab, page in ((self.camera_tab, camera_page), (self.cvar_tab, look_page), (self.export_tab, export_page),
                          (self.controls_tab, controls_page), (self.settings_tab, settings_page)):
            self.stage.register_page(str(tab), title=page.TITLE, subtitle=page.SUBTITLE)
        # The kit pages' rail sits in a gutter right of the page column (PLAN-5 N8).
        self.stage.set_rail_gutter(KitPage.GUTTER)
        # CANCEL follows cancel_startup_button exactly: the poll enables it while
        # a startup can still be cancelled, and pressing it disables it again.
        self.stage.set_cancel(self.cancel_startup_button)
        self._log_link_text = tk.StringVar(value="Show log")
        self.show_log.trace_add("write", lambda *_args: self._log_link_text.set(
            "Hide log" if self.show_log.get() else "Show log"))
        self.stage.set_links((FooterLink("Open output folder", self._open_output_folder),
                              FooterLink(self._log_link_text, self._toggle_log_link),
                              FooterLink("Export Diagnostics", self._diagnostics)))
        self.stage.bind_progress(self.startup_bar)
        self.stage.bind_status(self._footer_status, (self.status_text, self.startup_progress, self.busy_text))
        self.stage.bind_session(self.session_text, _session_tone)
        self.stage.bind_context(self._shell_context, (self.busy_text,))
        self.status_label = self.stage.status_label
        self.game_path.trace_add("write", lambda *_args: self._request_display_fonts())

    def _footer_status(self):
        """(text, dot state): startup progress while starting, otherwise the status message."""
        starting = getattr(self, "startup_cancel", None) is not None
        text = self.startup_progress.get() if starting else self.status_text.get()
        if starting or getattr(self, "busy", False):
            return text, "busy"
        if self.status_text.get() and self.status_text.get() == getattr(self, "_status_error_text", None):
            return text, "error"
        return text, "ready"

    def _shell_context(self):
        """Header right, second line: the current work, if any (the shot name is the window title)."""
        return self.busy_text.get()

    def _toggle_log_link(self):
        self.show_log.set(not self.show_log.get())
        self._toggle_log()

    def _build_setup(self):
        gui_layout.build_library(self)

    def _apply_editor_mode(self):
        # Old saved full_editor=False values must not hide desktop tools.
        self.full_editor.set(True)
        for tab in (self.camera_tab, self.cvar_tab):
            self.notebook.tab(tab, state="normal")
        self._tab_changed()

    def _toggle_full_editor(self):
        """Compatibility route for older internal callers; no saved mode toggle."""
        self._apply_editor_mode()

    def _save_layout_paths(self):
        def save():
            settings = replace(self.app_settings, game_path=self.game_path.get(),
                               replay_folder=self.replay_folder.get(), demo_path=self.demo_path.get())
            save_settings(settings)
            self.app_settings = settings
            self.status_text.set("Game and replay paths saved.")
        self._guard("Save paths", save)

    def _open_output_folder(self):
        def open_folder():
            path = Path(self.video_path.get()).expanduser().resolve().parent
            if not path.is_dir():
                raise ValueError("Choose an existing output folder first.")
            os.startfile(str(path))
        self._guard("Open output folder", open_folder)

    def _select_page(self, tab):
        """Switch to ``tab`` with Shot Playback already in that page's host card.

        Camera and Look pin the shared playback frame in their docks and show in a
        window tall enough for their cards. Adopting the frame and sizing the window
        before the switch lets the page lay out once at its final size;
        ``<<NotebookTabChanged>>`` (still the fallback) would lay it out twice. The page
        already shown keeps the window as it is.
        """
        self._host_timeline(tab)
        if str(tab) == self.notebook.select():
            return
        self._fit_window_to_page(tab)
        self.notebook.select(tab)

    def _tall_page(self, tab):
        """The kit page of ``tab`` when it is Camera or Look (shown in a taller window), else None."""
        for name, page in (("camera_tab", "camera_page"), ("cvar_tab", "look_page")):
            if hasattr(self, name) and str(tab) == str(getattr(self, name)):
                return getattr(self, page, None)
        return None

    def _height_page(self, tab):
        """The page whose cards set ``tab``'s window height: Camera takes Look's, Look its own.

        USER-ADDITIONS UPDATE 19: Camera opens as tall as Look (Look shows every card), and
        Camera's bento scrolls inside that height.
        """
        if hasattr(self, "camera_tab") and str(tab) == str(self.camera_tab):
            return getattr(self, "look_page", None) or self._tall_page(tab)
        return self._tall_page(tab)

    def _page_height(self, tab):
        """Client height (px) for Camera or Look: every card of Look with no page scroll; None elsewhere.

        USER-ADDITIONS UPDATE 17.3 and 19. The shell's header rows, the cards of
        :meth:`_height_page` as laid out now (adopting Shot Playback lays the page out,
        :meth:`_host_timeline`), the pinned Shot Playback card (the same card on both pages)
        with Replay's gap under it, and the footer rows; the stage uses its design rows from
        ``chrome.DESIGN_HEIGHT`` up, its compact rows below. The client is that stage plus
        the rows outside it: the title and link bars (USER-ADDITIONS UPDATE 20).
        """
        page, stage = self._height_page(tab), getattr(self, "stage", None)
        if page is None or stage is None:
            return None
        s = self.ui_scale
        host = getattr(self, "_playback_hosts", {}).get(str(tab))
        # Unpinned (a very short window) the card is the last card of the content instead.
        docked = 0
        if host is not None and host.adopted is not None and host.mode == "dock":
            docked = px(s, page.DOCK_TOP_GAP) + host.card_height()
        notice = stage.notice_host.winfo_reqheight()

        def height(row):
            top = px(s, chrome.PAGE_TOP[row])
            if notice > 1:
                top = max(top, px(s, chrome.NOTICE_TOP[row]) + notice + px(s, chrome.NOTICE_GAP))
            return top + page.content_height + docked + end_gap(s, row == 1) + px(s, chrome.PAGE_BOTTOM[row])
        needed = height(0)
        if needed < px(s, chrome.DESIGN_HEIGHT):
            needed = height(1)
        if stage.winfo_height() > 1:
            needed += max(0, self.root.winfo_height() - stage.winfo_height())
        else:
            needed += self._bars_px()
        return needed

    def _laid_out_for_switch(self, page):
        """Whether ``page`` (hidden) was laid out at the width it gets when shown.

        Hidden notebook pages keep the width they last had; one never shown lays out at
        its design width. A window resized on another page leaves it stale until shown.
        """
        width = self.notebook.winfo_width()
        if page.winfo_width() > 1:
            return page.winfo_width() == width
        spec = page.spec
        design = spec.main + spec.gap + spec.side if spec is not None else 613
        return px(self.ui_scale, design) + px(self.ui_scale, page.GUTTER) == width

    def _fit_window_to_page(self, tab, *, shown=False):
        """Camera and Look get a window tall enough for all of Look's cards; other pages give the height back.

        USER-ADDITIONS UPDATE 10, 17.3 and 19: Look shows all its cards with no page scroll,
        and Camera opens at that same height, its bento scrolling inside (:meth:`_page_height`,
        clamped to the work area). One geometry change per switch: the width stays, and so does
        the top edge unless the taller window would leave the work area. A size the user
        chose by hand is kept: the window only grows while it is shorter than the page's
        height, and a height changed by hand while grown is never taken back. Only a shown
        window in its normal state changes (a withdrawn one reports a stale size; a
        maximized one is sized by Windows). ``shown``: ``tab`` already shows, laid out at its width.
        Heights are client heights, title and link bars included; Dolly's own frame
        (USER-ADDITIONS UPDATE 20) needs no room round them (:meth:`_frame_size`).
        """
        root = getattr(self, "root", None)
        if root is None or not hasattr(self, "camera_tab") or not hasattr(self, "cvar_tab"):
            return   # a bare test app without the editor pages
        self._fitted_tab = str(tab)
        try:
            if root.wm_state() != "normal" or not root.winfo_ismapped():
                return
            width, height = root.winfo_width(), root.winfo_height()
            if width <= 1 or height <= 1:
                return
            page = self._tall_page(tab)
            if page is not None and not shown and hasattr(self, "notebook") and not self._laid_out_for_switch(page):
                # Its cards follow the new width only once it shows: measured then, at idle.
                self._schedule_refit(tab)
                return
            source = self._height_page(tab)
            if source is not None and source.winfo_width() <= 1:
                # Never shown: its placed widgets take their sizes at idle, and some (the curve
                # workspace's tab row) only then know their own height. Settle, then measure.
                root.update_idletasks()
                source.layout_now()
            x, y = root.winfo_x(), root.winfo_y()
            grown = getattr(self, "_grown_window", None)
            if grown is not None and height != grown[1]:
                grown = self._grown_window = None   # resized by hand while grown: that height is the user's
            before = height if grown is None else grown[0]
            target = self._page_height(tab)
            _left, top, _right, bottom = chrome.work_area(root)
            frame_height = self._frame_size()[1]
            if target is not None:
                # As much of the page as the work area holds (the framed window fits it), and
                # no more than Tk lets the window reach.
                target = min(target, bottom - top - frame_height)
                try:
                    target = min(target, int(root.wm_maxsize()[1]))
                except (TypeError, ValueError, IndexError):
                    pass
            if target is None or target <= before:
                if grown is None:
                    return   # nothing to give back, or the window already shows the whole page
                self._grown_window = None
                _before, _target, old_y, moved = grown
                # Back to the height it had; also back down if growing moved it up and it is still there.
                root.geometry(f"{width}x{before}" if old_y == moved or y != moved else f"{width}x{before}+{x}+{old_y}")
                return
            if grown is not None and target == grown[1]:
                return   # Camera and Look at one height (both clamped to the work area)
            # The top edge stays (where it was before growing, unless the user moved the window
            # since); the window moves up only as far as the work area needs.
            anchor = grown[2] if grown is not None and y == grown[3] else y
            moved = min(anchor, max(top, bottom - frame_height - target))
            self._grown_window = (before, target, anchor, moved)
            root.geometry(f"{width}x{target}" if moved == y else f"{width}x{target}+{x}+{moved}")
        except tk.TclError:
            pass   # the window is closing

    def _schedule_refit(self, tab, passes=3):
        """Fit the window to ``tab`` once it shows at its width (a few idle passes at most)."""
        self._refit_tab = (str(tab), passes)
        if getattr(self, "_refit_job", None) is None:
            self._refit_job = self.root.after_idle(self._refit)

    def _refit(self):
        self._refit_job = None
        tab, passes = self._refit_tab
        try:
            if self.notebook.select() != tab:
                return   # left before it ran
            page = self._tall_page(tab)
            if page.winfo_width() != self.notebook.winfo_width() and passes > 1:
                self._schedule_refit(tab, passes - 1)
                return
            page.layout_now()
        except tk.TclError:
            return   # closed before it ran
        self._fit_window_to_page(tab, shown=True)

    def _host_timeline(self, tab):
        """Hand ``timeline_frame`` to ``tab``'s Shot Playback card, or hide it (PLAN-5 N10).

        One host at a time: the frame is released from the page that held it, and the
        handover is one ``place`` and one ``place_forget`` (nothing is created).
        """
        frame = getattr(self, "timeline_frame", None)
        if frame is None:
            return
        host = getattr(self, "_playback_hosts", {}).get(str(tab))
        current = getattr(frame, "_kit_host", None)
        if current is not None and current is not host:
            current.release(frame)
        if host is not None:
            host.adopt(frame)

    def _tab_changed(self, _event=None):
        if not hasattr(self, "timeline_frame"):
            return
        selected = self.notebook.select()
        sidebar = getattr(self, "sidebar", None)
        if sidebar is not None:
            sidebar.select(selected)
        stage = getattr(self, "stage", None)
        if stage is not None:
            stage.show_page(selected)
        # Direct notebook.select() callers land here. The window is sized once Tk has
        # applied any geometry that is still pending, so the size read is the real one.
        self._host_timeline(selected)
        if selected != getattr(self, "_fitted_tab", None) and getattr(self, "_fit_job", None) is None:
            def fit():
                self._fit_job = None
                try:
                    page = self.notebook.select()
                except tk.TclError:
                    return   # closed before it ran
                self._fit_window_to_page(page)
            self._fit_job = self.root.after_idle(fit)

    def _build_export(self):
        gui_layout.build_export(self)

    def _video_source_changed(self, _event=None):
        pov = self.video_source.get() == "Player POV"
        single_attach = len(self.project.keyframes) == 1 and self.project.keyframes[0].source == "attach"
        if pov or single_attach:
            self.video_pov_controls.pack(fill="x", before=self.video_camera_options)
        else:
            self.video_pov_controls.pack_forget()
        self.video_start_button.configure(text="Record POV" if pov else "Record video")
        self._update_export_camera_note()

    def _browse_video(self):
        if self.busy or self.video_export.status().get("state") in ACTIVE_STATES:
            return
        current = Path(self.video_path.get())
        codec_key = CODEC_LABEL_TO_KEY.get(self.video_codec.get(), DEFAULT_CODEC_KEY)
        container = CONTAINER_LABEL_TO_KEY.get(self.video_format.get(), DEFAULT_CONTAINER)
        suffix = video_suffix(codec_key, container, ffmpeg_available=self._ffmpeg_available())
        filetypes = {"mp4": (("MP4 video", "*.mp4"),),
                     "mkv": (("Matroska video", "*.mkv"),),
                     "mov": (("QuickTime movie", "*.mov"),)}[
                         resolve_container(codec_key, container,
                                           ffmpeg_available=self._ffmpeg_available())]
        path = filedialog.asksaveasfilename(parent=self.root, title="Record video", defaultextension=suffix,
                                          initialdir=str(current.parent), initialfile=current.name,
                                          filetypes=filetypes, confirmoverwrite=False)
        if path:
            self.video_path.set(path)
            self._remember_export_folder()

    def _export_camera(self, video, status=None):
        """Freeze UI intent, then validate and write optional exports on the worker."""
        from . import camera_export
        jobs = []
        for attribute, export, suffix, label, extra in (
                ("_ae_camera_take", "export_jsx", "_camera.jsx", "After Effects camera", {"video": video.name}),
                ("_usd_camera_take", "export_usda", "_camera.usda", "USD camera", {})):
            pending = getattr(self, attribute, None)
            setattr(self, attribute, None)
            if pending is not None:
                jobs.append((pending, export, video.with_name(video.stem + suffix), label, extra))
        if not jobs:
            return
        recorded_status = dict(status or {})

        def export_recorded_camera():
            messages = []
            project, fps, speed, pov = jobs[0][0]
            camera_data = video.with_name(video.name + ".camera-track.json")
            try:
                take = camera_export.load_take(project, fps,
                    camera_data=camera_data if camera_data.is_file() else None,
                    speed=speed, pov=pov, video_frames=recorded_status.get("frames_written"),
                    expected_status=recorded_status, require_recorded=True)
            except (OSError, ValueError) as exc:
                return [f"{label} was not saved: {exc}" for _, _, _, label, _ in jobs]
            for pending, export, path, label, extra in jobs:
                try:
                    warnings = getattr(camera_export, export)(project, path, fps, take=take, **extra)
                except (OSError, ValueError) as exc:
                    messages.append(f"{label} was not saved: {exc}")
                    continue
                messages.append(f"{label} saved to {path}")
                messages.extend(f"{label}: {warning}" for warning in warnings)
            return messages

        def exported(messages):
            for message in messages:
                self._log(message)
            failures = [message for message in messages if "was not saved:" in message]
            if failures:
                self.status_text.set("Video saved; camera export failed. See the log for details.")

        self._submit("Exporting recorded camera", export_recorded_camera, exported)

    def _remember_export_folder(self, _event=None):
        """Persist explicit destination edits, never generated per-layer paths."""
        settings = getattr(self, "app_settings", None)
        if settings is None:
            return
        try:
            raw = self.video_path.get().strip()
            if not raw:
                return
            folder = Path(raw).expanduser().absolute().parent
            # An incomplete edit or an unplugged drive must not erase the last choice.
            if not folder.is_dir() or str(folder) == settings.export_folder:
                return
            updated = replace(settings, export_folder=str(folder))
            save_settings(updated)
            self.app_settings = updated
        except (OSError, ValueError) as exc:
            self._error("Save export folder", exc)

    def _browse_ffmpeg(self):
        if self.busy or self.video_export.status().get("state") in ACTIVE_STATES:
            return
        current = Path(self.ffmpeg_path.get()) if self.ffmpeg_path.get() else None
        path = filedialog.askopenfilename(
            parent=self.root, title="Select ffmpeg.exe",
            initialdir=str(current.parent) if current and current.parent.is_dir() else None,
            filetypes=(("FFmpeg", "ffmpeg.exe"), ("Executable", "*.exe"), ("All files", "*.*")))
        if path:
            self.ffmpeg_path.set(path)
            self._save_ffmpeg_preference()

    def _browse_players_scratch(self):
        folder = filedialog.askdirectory(parent=self.root, title="Players scratch folder")
        if folder:
            self.players_scratch.set(folder)

    def _save_ffmpeg_preference(self):
        def save():
            selected = self.ffmpeg_path.get().strip()
            bundled = bundled_ffmpeg_path()
            if selected and not Path(selected).is_file():
                raise ValueError("Choose an existing FFmpeg executable.")
            custom = "" if bundled and selected and Path(selected).resolve() == bundled.resolve() else selected
            settings = replace(self.app_settings, ffmpeg_path=custom)
            save_settings(settings)
            self.app_settings = settings
        self._guard("Save FFmpeg preference", save)

    def _use_bundled_ffmpeg(self):
        if self.busy or self.video_export.status().get("state") in ACTIVE_STATES:
            return
        bundled = bundled_ffmpeg_path()
        if bundled:
            self.ffmpeg_path.set(str(bundled))
            self._save_ffmpeg_preference()

    def _check_updates(self):
        if hasattr(self, "update_manager"):
            self.update_manager.check()

    def _save_update_preference(self):
        def save():
            settings = replace(self.app_settings, auto_updates=self.auto_updates.get())
            save_settings(settings)
            self.app_settings = settings
        self._guard("Save update preference", save)

    def start_discord_presence(self):
        # Real desktop entry only. Bundle self-tests and constructed test UIs
        # must never publish activity to a maintainer's Discord account.
        if self._discord_presence is None and not self.closed:
            try:
                from .discord_presence import RichPresence
                self._discord_presence = RichPresence()
                self._refresh_discord_presence()
                self._discord_presence.start()
            except Exception:
                LOG.debug("Discord presence startup unavailable", exc_info=True)

    def _save_discord_preference(self):
        # Sharing controls remain available during playback and exports.
        try:
            settings = replace(self.app_settings, discord_presence=self.discord_presence.get())
            save_settings(settings)
        except Exception as exc:
            self.discord_presence.set(self.app_settings.discord_presence)
            self._error("Save Discord preference", exc)
            return
        self.app_settings = settings
        self._refresh_discord_presence()

    def _refresh_discord_presence(self, status=None):
        service = getattr(self, "_discord_presence", None)
        if service is None:
            return
        try:
            if status is not None:
                self._discord_controller_status = status
            else:
                status = getattr(self, "_discord_controller_status", {})
            from .discord_presence import activity_for
            service.update(activity_for(status, {"state": getattr(self, "_last_video_state", "idle")},
                cameras=len(self.project.keyframes),
                processing_players=bool(getattr(self, "_player_encode_cancel", None))),
                enabled=self.app_settings.discord_presence)
            _set_var(self.discord_status, service.status)
        except Exception:
            LOG.debug("Discord activity update unavailable", exc_info=True)

    def _save_dlss_preference(self):
        try:
            settings = replace(self.app_settings, nvidia_dlss=self.nvidia_dlss.get())
            save_settings(settings)
        except Exception as exc:
            self.nvidia_dlss.set(self.app_settings.nvidia_dlss)
            self._error("Save NVIDIA DLSS preference", exc)
            return
        self.app_settings = settings
        self.status_text.set("NVIDIA DLSS support preference saved for the next launch.")

    def _save_mod_preference(self):
        def save():
            settings = replace(self.app_settings,
                               allow_mod_overrides=self.allow_mod_overrides.get())
            save_settings(settings)
            self.app_settings = settings
            self.status_text.set(
                "Mixed mod packs will load on the next launch. UI, shader and "
                "post-processing overrides may appear in captures.")
        self._guard("Save mod loading preference", save)

    def _depth_toggled(self):
        # A depth master is a paired, render-paced export: capture exactly one
        # frame per Present so color and depth cannot diverge. Fixed-step is
        # the default for it and can still be turned off for a real-time take.
        if self.video_depth.get():
            self.video_fixed_step.set(True)
        else:
            self.video_depth_exr.set(False)

    def _sync_depth_controls(self, *_args):
        """Gate the EXR sequence control on the depth master in both UIs.

        A depth change can come from this window or from the in-game panel, so
        the gate follows the variable instead of one control's click. While the
        depth master is off the EXR control is greyed out (the in-game panel
        greys it the same way), and an EXR tick never survives without depth.
        """
        checkbox = getattr(self, "video_depth_exr_checkbox", None)
        if checkbox is not None:
            checkbox.configure(state="normal" if self.video_depth.get() else "disabled")
        if not self.video_depth.get() and self.video_depth_exr.get():
            self.video_depth_exr.set(False)

    def _layer_toggled(self):
        # Layer takes must align with the color take frame for frame, which
        # only the fixed-step engine pacing guarantees.
        if (self.video_layer_world.get() or self.video_layer_players.get()
                or self.video_layer_effects.get()):
            self.video_fixed_step.set(True)

    def _ffmpeg_available(self):
        return resolve_ffmpeg(self.ffmpeg_path.get().strip() or None) is not None

    def _format_changed(self):
        """Keep the encoder and the output extension valid for the container."""
        container = CONTAINER_LABEL_TO_KEY.get(self.video_format.get(), DEFAULT_CONTAINER)
        codec_key = CODEC_LABEL_TO_KEY.get(self.video_codec.get(), DEFAULT_CODEC_KEY)
        available = self._ffmpeg_available()
        if resolve_container(codec_key, container, ffmpeg_available=available) != container:
            # Lossless is MKV-only; the built-in writer (and Auto without
            # FFmpeg) is MP4-only. Show what will actually be written.
            self.video_codec.set(CODEC_BY_KEY[DEFAULT_CODEC_KEY][0])
            container = resolve_container(DEFAULT_CODEC_KEY, container, ffmpeg_available=available)
        self.video_format.set(dict(CONTAINER_CHOICES)[container])
        self._sync_video_extension()

    def _codec_changed(self):
        """Show the container the chosen encoder actually needs."""
        codec_key = CODEC_LABEL_TO_KEY.get(self.video_codec.get(), DEFAULT_CODEC_KEY)
        container = resolve_container(codec_key,
                                      CONTAINER_LABEL_TO_KEY.get(self.video_format.get(), DEFAULT_CONTAINER),
                                      ffmpeg_available=self._ffmpeg_available())
        self.video_format.set(dict(CONTAINER_CHOICES)[container])
        self._sync_video_extension()

    def _sync_video_extension(self):
        if not self.video_path.get().strip():
            return
        codec_key = CODEC_LABEL_TO_KEY.get(self.video_codec.get(), DEFAULT_CODEC_KEY)
        container = CONTAINER_LABEL_TO_KEY.get(self.video_format.get(), DEFAULT_CONTAINER)
        wanted = video_suffix(codec_key, container, ffmpeg_available=self._ffmpeg_available())
        chosen = Path(self.video_path.get().strip())
        if chosen.suffix.lower() != wanted:
            self.video_path.set(str(chosen.with_suffix(wanted)))

    def _start_video_recording(self):
        if getattr(self, "_bone_picker_context", None):
            self.status_text.set("Finish or cancel Bone Picker before recording.")
            return
        if self.busy:
            self.status_text.set("Finish the current operation before starting a recording.")
            return
        self._playback_failure_recovery_pending = False
        try:
            pov = hasattr(self, "video_source") and self.video_source.get() == "Player POV"
            self._pov_project = None
            bone_project = None
            if not pov and len(self.project.keyframes) == 1 and self.project.keyframes[0].source == "attach":
                from .bone_picker import timed_attachment
                bone_project = timed_attachment(self.project, self.video_pov_duration.get())
            if pov:
                duration = float(self.video_pov_duration.get())
                if not math.isfinite(duration) or not .1 <= duration <= 120:
                    raise ValueError("Choose a POV duration between 0.1 and 120 replay seconds.")
                state = self.controller.status()
                tick = state.get("tick")
                if tick is None or not (state.get("game_ui_visible")
                                        or state.get("game_follow_active")):
                    raise ValueError("Choose Player POV, then select a hero with Follow, "
                                     "or press F9 to select a hero.")
                self._pov_project = Project(name="Player POV", start_tick=int(tick),
                    confetti_enabled=self.project.confetti_enabled,
                    confetti_spawn_height=self.project.confetti_spawn_height,
                    confetti_despawn_on_ground=self.project.confetti_despawn_on_ground,
                    particles_preset=self.project.particles_preset,
                    particles_intensity=self.project.particles_intensity,
                    tick_rate=self.project.tick_rate,
                    keyframes=[Keyframe(0, 0, 0, 0, 0, 0, 0), Keyframe(duration, 0, 0, 0, 0, 0, 0)])
            codec_key = CODEC_LABEL_TO_KEY.get(self.video_codec.get(), DEFAULT_CODEC_KEY)
            container = CONTAINER_LABEL_TO_KEY.get(self.video_format.get(), DEFAULT_CONTAINER)
            resolved = resolve_container(codec_key, container,
                                         ffmpeg_available=self._ffmpeg_available())
            if resolved != container:
                # The lossless codec is MKV-only and the built-in writer is
                # MP4-only; show the container actually used.
                container = resolved
                self.video_format.set(dict(CONTAINER_CHOICES)[resolved])
            # Correct a hand-typed extension instead of failing after the user
            # prepared a shot.
            if self.video_path.get().strip():
                wanted = "." + container
                chosen = Path(self.video_path.get().strip())
                if chosen.suffix.lower() != wanted:
                    chosen = chosen.with_suffix(wanted)
                    self.video_path.set(str(chosen))
                    LOG.info("Output file extension set to %s for the selected format.", wanted)
            layers = tuple(name for name, var in (("world", self.video_layer_world),
                                                  ("players", self.video_layer_players),
                                                  ("effects", self.video_layer_effects))
                           if var.get())
            options = VideoOptions(Path(self.video_path.get().strip()), int(self.video_fps.get()),
                                   BITRATE_PRESETS[self.video_bitrate.get()], codec=codec_key,
                                   ffmpeg_path=self.ffmpeg_path.get().strip() or None,
                                   fixed_step=bool(self.video_fixed_step.get()),
                                   speed=float(self.video_export_speed.get()),
                                   depth=bool(self.video_depth.get()),
                                   depth_exr=bool(self.video_depth_exr.get()),
                                   layers=layers, shot_only=pov or bone_project is not None,
                                   players_scratch=(getattr(self, "players_scratch", None).get().strip() or None) if getattr(self, "players_scratch", None) is not None else None,
                                   camera_track=any(getattr(self, name, None) is not None and getattr(self, name).get()
                                                    for name in ("video_ae_camera", "video_usd_camera")),
                                   bundle_audio=bool(self.video_game_audio.get() or
                                                     self.video_reconstructed_audio.get()),
                                   container=container).validated()
            if "players" in options.layers:
                self._player_layer_frames(options, project=bone_project)
            from .player_encode import resource_policy
            choice = getattr(self, 'player_processing', None)
            self._player_resource_mode = choice.get() if choice is not None else 'Background'
            resource_policy(self._player_resource_mode)
        except (ValueError, KeyError, OSError, RuntimeError) as exc:
            self._error("Record video", exc)
            return
        self._remember_export_folder()
        # The generated endpoint belongs to this take, not the authored shot.
        # Keep the single attached view editable for another chosen duration.
        self._bone_take_project = copy.deepcopy(bone_project)
        project = self._pov_project or bone_project or (Project.from_dict(self.project.to_dict()) if len(self.project.keyframes) >= 2 else None)
        frozen = False if pov else self.frozen.get()
        # Export speed only applies to fixed-step takes.
        speed = options.speed if options.fixed_step else 1.0
        camera_project = project or Project.from_dict(self.project.to_dict())
        camera_take = (camera_project, options.fps, speed, pov)
        for attribute, variable in (("_ae_camera_take", "video_ae_camera"),
                                    ("_usd_camera_take", "video_usd_camera")):
            ticked = getattr(self, variable, None)
            setattr(self, attribute, camera_take if ticked is not None and ticked.get() else None)
        self._pending_auto_play = project is not None
        # Every take that plays an authored project ends with that shot: the path
        # clamps at its last camera, so nothing further can be captured for it.
        # Finishing here writes the manifest and sidecars instead of leaving the
        # recorder open. A plain colour take used to stay open until someone
        # pressed Finish recording, and because a finished shot stops producing
        # frames, the status line froze and looked complete while the video was
        # still unfinalized -- closing either app then left an unusable file.
        # Free-camera recordings author no project and stay manual.
        self._auto_finish_layered = project is not None
        self._base_capture = options if options.layers else None
        # Players need a real alpha channel: their layer is recorded by the
        # native ownership capture (players plus their equipment, no NPCs).
        # Every other layer records a black and white pass and combines them.
        queue = []
        for name in options.layers:
            if name == "players":
                queue.append((name, "capture"))
                continue
            queue.append((name, "black"))
            if name == "effects":
                queue.append((name, "white"))
        self._layer_queue = queue
        self._active_layer_take = None
        self._take_playing_seen = False
        self._take_audit_seen = set()
        self._take_audit_reported = False
        controller = getattr(self, "controller", None)
        game_pid = getattr(controller, "game_pid", None)
        if callable(game_pid):
            # Bring the game into view for recording. Capture also works if Windows
            # refuses this request or desktop controls take focus during setup.
            focus_window(game_pid())
        game_audio = self.video_game_audio.get()
        reconstructed = self.video_reconstructed_audio.get()
        if reconstructed and find_viewer() is None:
            if not ask_reconstructed_helper(self.root, size_mb=HELP_SIZE_MB):
                self.status_text.set(
                    "Reconstructed audio helper download cancelled; the video was not started.")
                return
        game_executable = self.game_path.get() if game_audio or reconstructed else None
        start_engine_seconds = (float(project.start_tick) / float(project.tick_rate)
                                if project is not None else None)
        def start():
            if options.fixed_step and reconstructed and start_engine_seconds is None:
                raise RuntimeError("Fixed-step reconstructed audio needs a camera path or POV shot.")
            audio = None
            if game_audio or reconstructed:
                bridge = self.controller._native_bridge()
                if bridge is None:
                    raise RuntimeError("The native media bridge is not connected")
                color = options.path.with_suffix("") / options.path.name
                audio = ClipAudioCapture(color, game_audio=game_audio, reconstructed=reconstructed,
                                         bridge=bridge, game=validate_game(game_executable).executable,
                                         game_pid=self.controller.game_pid(),
                                         controller=self.controller, movie_fps=options.fps,
                                         fixed_step=options.fixed_step,
                                         speed=options.speed if options.fixed_step else 1.0,
                                         start_engine_seconds=start_engine_seconds)
                audio.start()
            try:
                status = self.video_export.start(
                    options, project=project, frozen=frozen, pov=pov,
                    before_record=audio.start_movie if audio is not None else None)
            except BaseException:
                if audio:
                    if self.video_export.status().get("state") in ACTIVE_STATES:
                        self.clip_audio = audio
                    else:
                        audio.cancel()
                raise
            self.clip_audio = audio
            return status
        self._submit("Preparing replay and recording", start, self._video_operation_done)

    def _stop_video_recording(self, cancel=False):
        encoding_cancel = getattr(self, '_player_encode_cancel', None)
        if cancel and encoding_cancel is not None:
            encoding_cancel.set()
            self.status_text.set("Cancelling Players processing; raw capture will be kept.")
            return
        if self.busy:
            self.status_text.set("Finish the current operation before stopping the recording.")
            return
        self._auto_finish_layered = False
        def finish():
            status = self.video_export.stop(cancel=cancel)
            audio = self.clip_audio
            self.clip_audio = None
            if audio:
                if cancel or status.get("state") != "completed":
                    audio.cancel()
                else:
                    folder = audio.finish(status)
                    if folder:
                        status = {**status, "reconstructed_folder": str(folder)}
            return status
        if (getattr(self, "_pov_project", None) is not None or getattr(self, "_bone_take_project", None) is not None) and self.controller.status().get("playing"):
            # An early Finish ends this take, including its queued passes.
            # Do not silently export longer layer takes than the color video.
            self._layer_queue = []
        self._submit("Discarding recording" if cancel else "Finishing video recording",
                     finish, self._video_operation_done)

    def _next_take_directory(self):
        """The folder that receives new takes, never the inside of a take."""
        base = getattr(self, "_base_capture", None)
        if base is not None:
            # A layered run's side takes and alpha combines live inside the
            # base take folder. Suggest the next take beside that folder
            # instead of nesting every later take one level deeper.
            return base.path.parent
        output = self.video_export.output_path
        if output is None:
            return Path(self.video_path.get()).parent
        return self.video_export.output_directory or output.parent

    def _video_operation_done(self, status):
        state = status.get("state", "idle")
        if state == "completed" and getattr(self.video_export, "finish_pending", False) is True:
            if not self.busy:
                self._stop_video_recording()
            return
        self.video_status_text.set(format_video_status(status))
        self.status_text.set(self.video_status_text.get())
        self._last_video_state = state
        if state == "completed" and self.video_export.output_path:
            if status.get("reconstructed_folder"):
                self._log("Reconstructed audio saved to " + status["reconstructed_folder"])
            master = status.get("master")
            if master:
                self._log("Layer alpha saved to " + str(master))
            elif getattr(self, "_active_layer_take", None) is None:
                self._log("Video saved to " + str(self.video_export.output_path))
                self._export_camera(self.video_export.output_path, status)
            directory = self._next_take_directory()
            if not directory.is_dir():
                directory = Path(self.video_path.get()).parent
            self.video_path.set(str(default_video_path(self.project.name, directory)))
        if state == "completed":
            audit = status.get("audit") if isinstance(status, dict) else None
            if isinstance(audit, dict):
                self._report_take_audit(audit.get("findings", ()))
            if self.layer_pipeline.completed(status):
                # The color take of a layered run just landed. Say so, because
                # the selected pass takes start immediately afterwards.
                self.status_text.set("Color take saved. Recording the selected passes…")
        elif state in ("failed", "cancelled"):
            self._ae_camera_take = self._usd_camera_take = None
            screenshot_ui.abandon(self)
            layered = self.layer_pipeline.failed()
            if layered:
                self._submit("Restoring scene layers", self._restore_scene_state,
                             lambda _: None)
        if self._pending_auto_play:
            # Starting only acknowledges the request; render resources and the
            # encoder may still be initializing. Play needs responsive views.
            if state == "recording":
                self._pending_auto_play = False
                if getattr(self, "_pov_project", None) is not None:
                    project = self._pov_project
                    speed = float(self.video_export_speed.get())
                    def start_pov():
                        try:
                            self.controller.play_pov(project, speed)
                        except Exception:
                            self.video_export.stop(cancel=True)
                            raise
                    self._submit("Starting POV segment", start_pov)
                elif getattr(self, "_bone_take_project", None) is not None:
                    self._play(project=self._bone_take_project)
                else:
                    self._play()
            elif state in ("failed", "cancelled", "completed", "idle"):
                self._pending_auto_play = False
        self._advance_layer_pipeline()
        if state in ("failed", "cancelled") or (state == "completed" and not (
                self._base_capture or self._layer_queue or self._active_layer_take or
                self._pending_capture or self._pending_combine)):
            self._bone_take_project = None

    def _restore_scene_state(self, *, strict=False):
        errors = []
        try:
            self.controller.end_matte_layer()
        except (RuntimeError, ValueError, OSError) as exc:
            if strict:
                errors.append("matte settings: " + str(exc))
        try:
            self.controller.reset_layer_modes()
        except (RuntimeError, ValueError, OSError) as exc:
            LOG.warning("Scene classes could not all be restored: %s", exc)
            if strict:
                errors.append("scene classes: " + str(exc))
        if errors:
            raise RuntimeError(" ".join(errors))

    def _clear_layer_pipeline(self):
        self.layer_pipeline.clear()
        self._bone_take_project = None
        screenshot_ui.abandon(self)

    def _recover_export_failure(self, *, cancel=False):
        stop_recording = (lambda: self.video_export.stop(cancel=True)) if cancel else self.video_export.stop
        restore_scene = (lambda: self._restore_scene_state(strict=True)) if cancel else self._restore_scene_state
        stop_camera = self.controller.stop
        if cancel:
            def guarded(action):
                def restore():
                    self.controller._require_demo(require_tick=False)
                    return action()
                return restore
            stop_recording, stop_camera, restore_scene = (
                guarded(action) for action in (stop_recording, stop_camera, restore_scene))
        errors = recover_export_failure(stop_recording, stop_camera, restore_scene)
        if cancel:
            pending = self.controller.pending_export_restoration()
            if pending:
                errors.append("Restoration remains pending: " + ", ".join(pending) + ".")
        return errors

    def _abort_failed_export(self, reason):
        """Worker recovery for a camera-thread failure after capture started."""
        errors = []
        audio = self.clip_audio
        if audio is not None:
            try:
                # Cancelling movie/source audio can write to the game too.
                self.controller._require_demo(require_tick=False)
                audio.cancel()
            except Exception as exc:
                LOG.exception("Could not cancel audio after playback failed")
                errors.append("audio: " + str(exc))
            else:
                self.clip_audio = None
        try:
            errors.extend(self._recover_export_failure(cancel=True))
        except Exception as exc:
            # Reporting recovery failures must preserve the triggering camera
            # error, including an unexpected failure to inspect its journals.
            LOG.exception("Could not complete failed-export recovery")
            errors.append("recovery: " + str(exc))
        pending = (" Recovery still needs attention: " + " ".join(errors)) if errors else ""
        raise RuntimeError("Export stopped because camera playback failed: " + str(reason)
                           + " Completed takes and Players scratch data were kept. "
                             "Record a new take after correcting the reported problem." + pending)

    def _advance_layer_pipeline(self):
        self.layer_pipeline.advance(
            busy=self.busy,
            recorder_active=lambda: self.video_export.status().get("state") in ACTIVE_STATES,
            submit=self._submit,
            finish_capture=self._finish_player_capture,
            combine=self._combine_layer,
            start_next=self._start_next_layer_take,
            complete=self._video_operation_done,
            audit=lambda base: screenshot_ui.audit(self, base),
            restore=lambda: screenshot_ui.restore(self),
            restored=lambda folder: screenshot_ui.restored(self, folder),
        )

    def _report_take_audit(self, findings, *, final=False):
        """Log new take-validation findings once, and one pass line per run.

        The validator is diagnostic only: findings never change an export's
        result and duplicate lines are suppressed between the color folder
        check and the final layered check.
        """
        findings = [str(item) for item in findings]
        seen = getattr(self, "_take_audit_seen", set())
        for finding in findings:
            if finding not in seen:
                self._log("Take audit: " + finding)
        self._take_audit_seen = set(findings)
        if final:
            if not findings and not getattr(self, "_take_audit_reported", False):
                self._log("Take audit: all recorded layers passed validation.")
            self._take_audit_reported = True

    def _audit_finished_layers(self, base):
        """Validate the finished layered take folder; never fatal."""
        try:
            from dolly.export_audit import audit_take
            report = audit_take(base.path.with_suffix(""))
        except Exception:  # noqa: BLE001 - diagnostics must never fail an export
            LOG.exception("Take audit could not run")
            return
        self._report_take_audit(report.get("findings", ()), final=True)

    def _start_next_layer_take(self):
        """Worker step: hide the layer's classes and start its next pass."""
        layer, pass_name = self._layer_queue.pop(0)
        if pass_name == "capture":
            return self._start_player_capture(layer)
        base = self._base_capture
        folder = base.path.with_suffix("")
        # Every generated take keeps the color take's container. The lossless
        # codec requires .mkv, and a hard-coded .mp4 used to abort the layered
        # export with "The output filename must end in .mkv."
        suffix = base.path.suffix
        white = pass_name == "white"
        def configure_layer():
            # Preparation calls Stop/restore, so apply filtering afterwards.
            applied = self.controller.apply_layer_mode(layer)
            LOG.info("Layer take %s (%s pass) hidden classes: %s", layer, pass_name,
                     ", ".join(applied.get("hidden", ())))
            if layer in ("players", "effects"):
                self.controller.begin_matte_layer()
        if white:
            # The black pass created the layer folder; this pass writes the
            # white matte beside it and skips frames like every layer take.
            target = folder / layer / (layer + "_white" + suffix)
            options = VideoOptions(target, base.fps, base.bitrate, base.codec, base.quality,
                                   base.preset, base.ffmpeg_path, True, base.speed,
                                   False, False, (), True, True, full_resolution=base.full_resolution,
                                   container=base.container).validated()
        else:
            # The layer selection gives the take its own subfolder; the native
            # recorder captures the filtered color into it.
            target = folder / (layer + suffix)
            options = VideoOptions(target, base.fps, base.bitrate, base.codec, base.quality,
                                   base.preset, base.ffmpeg_path, True, base.speed,
                                   False, False, (layer,), full_resolution=base.full_resolution,
                                   container=base.container).validated()
        self._active_layer_take = (layer, pass_name)
        self._pending_auto_play = True
        self._auto_finish_layered = True
        self._take_playing_seen = False
        pov_project = getattr(self, "_pov_project", None)
        bone_project = copy.deepcopy(getattr(self, "_bone_take_project", None))
        return self.video_export.start(options, project=pov_project or bone_project or self._snapshot(),
                                       before_record=configure_layer, pov=pov_project is not None)

    def _player_layer_frames(self, base, *, project=None):
        """Preflight estimate; actual capture uses the completed color take's count."""
        player_layer.validate_engine_rate(base.fps, base.speed)
        project = project or getattr(self, "_pov_project", None) or self.project
        if project is None or len(project.keyframes) < 2:
            raise RuntimeError("The players layer needs a camera path so its frames match the color take.")
        frames = int(math.ceil(project.duration * base.fps / base.speed - 1e-6))
        if not player_layer.MIN_FRAMES <= frames <= player_layer.MAX_FRAMES:
            raise RuntimeError("The players layer supports %d..%d frames at %d FPS."
                               % (player_layer.MIN_FRAMES, player_layer.MAX_FRAMES, base.fps))
        return frames

    def _start_player_capture(self, layer):
        """Worker step: arm the native players-only capture, then record its take.

        Unlike the other layers this hides no scene classes: the native capture
        selects the exact draws owned by player pawns (and their equipment), so
        NPCs stay out while the world stays in for occlusion. Every class must
        be visible though: a previous layer take (for example the world layer)
        hides the skinned objects, and the capture would then seal no frames.
        """
        base = self._base_capture
        player_layer.validate_engine_rate(base.fps, base.speed)
        deployment = self.controller.deployment_directory()
        if deployment is None:
            raise RuntimeError("Launch the game through Dolly before recording the players layer.")
        self.controller.reset_layer_modes()
        LOG.info("Scene classes restored for the players layer capture.")
        layout = self.controller._request(
            "schema_detailed_class_layout " + player_layer.OWNER_LAYOUT_CLASS, timeout=5)
        owner_offset = player_layer.parse_owner_offset(str(layout))
        entity_layout = self.controller._request(
            "schema_detailed_class_layout " + player_layer.ENTITY_LAYOUT_CLASS, timeout=5)
        back_offset = player_layer.parse_scene_node_offset(str(entity_layout))
        frames = player_layer.reference_frame_count(
            base.path.with_suffix("") / base.path.name, base.fps)
        dimensions = self.video_export.status()
        required_bytes = player_layer.check_capture_space(
            base.players_scratch or deployment, dimensions.get("width"), dimensions.get("height"), frames)
        LOG.info("Players capture raw storage estimate: %d bytes", required_bytes)
        def arm_capture():
            player_layer.begin_capture(deployment, owner_offset, frames, back_offset, fps=base.fps, speed=base.speed,
                                       scratch_root=base.players_scratch,
                                       output=base.path.with_suffix("") / layer / (layer + ".mov"))
            if base.players_scratch is not None:
                from .player_scratch import from_marker
                LOG.info("Players scratch capture created: %s", from_marker(deployment))
            player_layer.wait_until_armed(deployment)
        LOG.info("Players layer capture armed: %d frames, owner offset %d, back link %d",
                 frames, owner_offset, back_offset)
        folder = base.path.with_suffix("")
        # Match the color take's container: lossless FFV1 takes (stills) need .mkv.
        target = folder / layer / (layer + base.path.suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        options = VideoOptions(target, base.fps, base.bitrate, base.codec, base.quality,
                               base.preset, base.ffmpeg_path, True, base.speed,
                               False, False, (), False, True, full_resolution=base.full_resolution,
                               container=base.container).validated()
        self._active_layer_take = (layer, "capture")
        self._pending_auto_play = True
        self._auto_finish_layered = True
        self._take_playing_seen = False
        pov_project = getattr(self, "_pov_project", None)
        bone_project = copy.deepcopy(getattr(self, "_bone_take_project", None))
        return self.video_export.start(options, project=pov_project or bone_project or self._snapshot(),
                                       before_record=arm_capture, capture_only=True, pov=pov_project is not None)

    def _finish_player_capture(self, layer):
        """Worker step: wait for the capture, then write the alpha layer master."""
        if getattr(self, "_still_run", None) is not None:
            return screenshot_ui.finish_player_capture(self, layer)
        base = self._base_capture
        if base is None:
            # A cancel, discard or competing stop tore the layered run down after
            # this step was queued. There is nothing left to finalize; raising
            # here would only stack a confusing error on top of the real cause.
            LOG.warning("Players layer finalize skipped: the layered run was already torn down")
            return {"state": "cancelled", "layer": layer, "reason": "run_torn_down"}
        deployment = self.controller.deployment_directory()
        if deployment is None:
            raise RuntimeError("The players layer capture directory is gone.")
        try:
            player_layer.request_stop(deployment)
        except OSError as exc:
            LOG.warning("Players layer stop request failed: %s", exc)
        layer_dir = base.path.with_suffix("") / layer
        status = player_layer.wait_for_capture(deployment, timeout=180.0)
        if not status.startswith("complete"):
            # Do not leave an empty Players folder behind when the capture
            # aborts before producing a frame. The color take and diagnostics
            # remain available for a retry.
            try:
                if layer_dir.is_dir() and not any(layer_dir.iterdir()):
                    layer_dir.rmdir()
            except OSError:
                pass
            raise RuntimeError("The players layer capture did not finish cleanly: "
                               + (status or "no status was reported"))
        ffmpeg = resolve_ffmpeg(base.ffmpeg_path) or bundled_ffmpeg_path()
        if ffmpeg is None:
            raise RuntimeError("The players layer needs ffmpeg for its alpha master.")
        from .player_encode import encode_in_worker
        from . import player_scratch
        data_dir = player_scratch.from_marker(deployment)
        if data_dir != Path(deployment):
            player_scratch.update(data_dir, 'captured')
            LOG.info("Players scratch capture: %s", data_dir)
        self._player_encode_cancel = threading.Event()
        self._player_encode_progress = 0
        try:
            master = encode_in_worker(Path(ffmpeg), data_dir / player_layer.BUNDLE_NAME,
                layer_dir / (layer + ".mov"), fps=base.fps,
                resource_mode=getattr(self, '_player_resource_mode', 'Background'),
                cancel=self._player_encode_cancel,
                progress=lambda count: setattr(self, '_player_encode_progress', count))
        except InterruptedError:
            if data_dir != Path(deployment): player_scratch.update(data_dir, 'cancelled')
            return {"state": "cancelled", "layer": layer, "reason": "processing_cancelled"}
        except Exception as exc:
            if data_dir != Path(deployment): player_scratch.update(data_dir, 'encode_failed', error=exc)
            raise
        finally:
            self._player_encode_cancel = None
        leftovers = player_layer.cleanup_capture(deployment, layer_dir)
        if leftovers:
            LOG.warning("Players MOV saved; could not remove intermediates: %s", ", ".join(leftovers))
        LOG.info("Players layer alpha master saved to %s", master)
        return {"state": "completed", "layer": layer, "master": str(master)}

    def _combine_layer(self, layer):
        """Build the RGBA layer master from its black and white passes.

        alpha = 1 - (white - black); the black pass is the premultiplied color.
        Works for opaque character pixels and additive particles alike.
        """
        base = self._base_capture
        try:
            self.controller.end_matte_layer()
        except (RuntimeError, ValueError, OSError):
            LOG.warning("Matte post-processing could not be restored")
        if base is None:
            # The layered run was torn down before this queued combine ran; the
            # matte state is restored above and there is nothing left to build.
            LOG.warning("Layer alpha combine skipped: the layered run was already torn down")
            return {"state": "cancelled", "layer": layer, "reason": "run_torn_down"}
        folder = base.path.with_suffix("")
        layer_dir = folder / layer
        suffix = base.path.suffix
        black = layer_dir / (layer + suffix)
        white = layer_dir / (layer + "_white" + suffix)
        master = layer_dir / (layer + ".mov")
        if not black.is_file() or not white.is_file():
            raise RuntimeError("The " + layer + " matte passes are incomplete.")
        ffmpeg = resolve_ffmpeg(base.ffmpeg_path) or bundled_ffmpeg_path()
        if ffmpeg is None:
            raise RuntimeError("Layer alpha needs ffmpeg; the matte passes are kept.")
        args = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(black), "-i", str(white),
                "-filter_complex",
                "[0:v]scale=in_range=tv:out_range=pc,format=gbrp[b];"
                "[1:v]scale=in_range=tv:out_range=pc,format=gbrp[w];"
                "[w][b]blend=all_mode=difference[d];"
                "[d]format=gray,negate[a];"
                f"[b][a]alphamerge,{REC709_SCALE},format=yuva444p10le,{REC709_FRAME_TAGS}[out]",
                "-map", "[out]", "-an",
                "-c:v", "prores_ks", "-profile:v", "4444",
                "-pix_fmt", "yuva444p10le", *REC709_OUTPUT_ARGS, "-vendor", "apl0", str(master)]
        from dolly.runtime import external_program_environment
        with external_program_environment() as environment:
            result = subprocess.run(args, capture_output=True, text=True,
                                    timeout=max(300.0, 1.0),
                                    env=environment,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode != 0 or not master.is_file():
            detail = (result.stderr or "").strip().splitlines()
            raise RuntimeError("The " + layer + " alpha combine failed: "
                               + (detail[-1] if detail else "unknown error")
                               + " (matte passes kept)")
        sidecar = layer_dir / "shot.json"
        if sidecar.is_file():
            try:
                meta = json.loads(sidecar.read_text(encoding="utf-8"))
                meta["video_file"] = layer + ".mov"
                sidecar.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
            except (OSError, ValueError):
                LOG.warning("Layer sidecar could not be updated for %s", layer)
        for path in (black, white, layer_dir / (layer + "_white" + suffix + ".shot.json"),
                     layer_dir / (layer + suffix + ".shot.json")):
            if os.environ.get("DOLLY_KEEP_MATTE") == "1":
                break
            try:
                if path.is_file():
                    path.unlink()
            except OSError:
                LOG.warning("Intermediate matte file could not be removed: %s", path)
        LOG.info("Layer alpha master saved to %s", master)
        return {"state": "completed", "layer": layer, "master": str(master)}

    def _refresh_video(self, controller_status):
        status = self.video_export.status()
        state = status.get("state", "idle")
        previous = getattr(self, "_last_video_state", "idle")
        playback_error = controller_status.get("playback_error")
        generation = controller_status.get("recording_generation")
        recovery_pending = (getattr(self, "_playback_failure_recovery_pending", False)
                            if generation is None else
                            getattr(self, "_playback_failure_recovery_generation", None) == generation)
        if (state in ACTIVE_STATES and playback_error and not controller_status.get("playing")
                and not self.busy and not getattr(self, "_pending_auto_play", False)
                and not recovery_pending):
            # The native camera monitor reports failures outside the operation
            # worker. In particular, a Players capture has no video bridge to
            # transition it out of 'recording' by itself. Stop that capture and
            # restore the scene before accepting another camera operation.
            # Clear queued passes now so the failure cannot advance the run.
            self._clear_layer_pipeline()
            self._playback_failure_recovery_pending = True
            previous_generation = getattr(self, "_playback_failure_recovery_generation", None)
            self._playback_failure_recovery_generation = generation
            submitted = self._submit("Restoring failed export",
                                     lambda reason=str(playback_error): self._abort_failed_export(reason))
            if submitted is False:
                self._playback_failure_recovery_pending = False
                self._playback_failure_recovery_generation = previous_generation
            return
        if (getattr(self, "_pending_auto_play", False) and state == "recording"
                and not self.busy):
            self._video_operation_done(status)
        if state != previous:
            if state == "completed":
                if self.busy:
                    return  # Final camera export needs the operation worker.
                if (self.clip_audio is not None
                        or getattr(self.video_export, "finish_pending", False) is True):
                    if not self.busy:
                        self._stop_video_recording()
                    return
                self._video_operation_done(status)
            elif state == "failed":
                if self.clip_audio is not None and not self.busy:
                    self._stop_video_recording(cancel=True)
                    return
                message = format_video_status(status)
                self._log(message)
                # A take can fail after playback started, when no worker is
                # waiting for it. Surface the reason instead of only the log.
                self._error("Recording", RuntimeError(status.get("error") or message))
            self._last_video_state = state
        active = state in ACTIVE_STATES
        if (state in ("failed", "cancelled") and not self.busy
                and ((getattr(self, "_pov_project", None) is not None
                      and getattr(self.controller, "_pov_active", False))
                     or getattr(self.controller, "_export_resolution", None) is not None
                     or getattr(self.controller, "_export_timing", None) is not None)):
            self._layer_queue = []
            self._auto_finish_layered = False
            self._submit("Restoring recording settings", self.video_export.stop)
        ready = recording_ready(controller_status)
        controller_message = str(controller_status.get("message") or "")
        if controller_status.get("playing") or "Playing native camera path" in controller_message:
            self._take_playing_seen = True
        if (active and getattr(self, "_auto_finish_layered", False) and state == "recording"
                and getattr(self, "_take_playing_seen", False)
                and not self.busy and not self.playing
                and "Native shot finished" in controller_message):
            # A layered take ends with its authored path. Finishing here writes
            # the manifest, preview and shot sidecar instead of leaving the
            # recorder open until the game closes.
            self._auto_finish_layered = False
            self._stop_video_recording()
        _set_var(self.video_status_text,
                 "Launch a replay to record video." if state == "idle" and not ready
                 else format_video_status(status))
        _set_widget_state(self.video_start_button,
                          "normal" if ready and not active and not self.busy else "disabled")
        screenshot_ui.refresh(self, ready and not active and not self.busy)
        can_stop = state in ("starting", "recording") and not self.busy
        _set_widget_state(self.video_stop_button, "normal" if can_stop else "disabled")
        # While Players frames are still encoding the same button cancels that instead.
        processing = getattr(self, '_player_encode_cancel', None) is not None
        cancel_text = "Cancel processing" if processing else "Discard recording"
        if str(self.video_cancel_button.cget("text")) != cancel_text:
            self.video_cancel_button.configure(text=cancel_text)
        _set_widget_state(self.video_cancel_button, "normal" if can_stop or processing else "disabled")
        if processing:
            _set_var(self.video_status_text,
                     "Building Players MOV: %d frames converted (%s)." %
                     (self._player_encode_progress, getattr(self, '_player_resource_mode', 'Background')))
        entry_state = "disabled" if active or self.busy else "normal"
        for widget in (self.video_path_entry, self.video_browse_button,
                       getattr(self, "ffmpeg_path_entry", None), getattr(self, "ffmpeg_browse_button", None),
                       getattr(self, "players_scratch_entry", None), getattr(self, "players_scratch_browse", None)):
            if widget is not None:
                _set_widget_state(widget, entry_state)
        combo_state = "disabled" if active or self.busy else "readonly"
        for widget in (self.video_fps_combo, self.video_bitrate_combo,
                       getattr(self, "video_source_combo", None),
                       getattr(self, "video_pov_duration_combo", None),
                       getattr(self, "video_codec_combo", None),
                       getattr(self, "video_format_combo", None),
                       getattr(self, "player_processing_combo", None),
                       getattr(self, "video_speed_combo", None)):
            if widget is not None:
                _set_widget_state(widget, combo_state)
        check_state = "disabled" if active or self.busy else "normal"
        for widget in (self.video_game_audio_check, self.video_reconstructed_audio_check, getattr(self, "video_ae_camera_check", None),
                       getattr(self, "video_usd_camera_check", None)):
            if widget is not None:
                _set_widget_state(widget, check_state)
        _set_widget_state(self.reshade_configure_button,
                          "normal" if ready and not active and not self.busy and not self.playing
                          else "disabled")
        _set_widget_state(self.reshade_forget_button,
                          "normal" if not active and not self.busy and not self.playing else "disabled")
        self._refresh_reshade(controller_status, active)
        self._advance_layer_pipeline()

    def _refresh_reshade(self, controller_status, video_active=False):
        bridge = (None if controller_status.get('game_running') is False
                  else self.controller._native_bridge())
        ready = recording_ready(controller_status)
        try:
            shade = bridge.media_status().get("reshade", {}) if bridge is not None else {}
            state = int(shade.get("state", 0))
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            # Optional media telemetry must not stop the camera/session UI or
            # repeat the same failure in the log ten times every second.
            message = "ReShade status unavailable: " + str(exc)
            _set_var(self.reshade_status_text, message)
            _set_widget_state(self.reshade_disable_button, "disabled")
            _set_widget_state(self.reshade_configure_button, "disabled")
            signature = (id(bridge), message)
            if getattr(self, "_last_reshade_status_error", None) != signature:
                self._last_reshade_status_error = signature
                self._log(message)
            return
        self._last_reshade_status_error = None
        from dolly.reshade_setup import runtime_issue
        from dolly.runtime import application_root
        widget = getattr(self, "reshade_runtime_path", None)
        selection = (widget.get() if widget is not None
                     else self.app_settings.reshade_runtime_path).strip()
        issue = runtime_issue(selection, application_root=application_root()) if selection else None
        # Selecting a runtime must be possible without a live session; the
        # worker explains that a DirectX 11 replay is still required if pressed.
        _set_widget_state(
            self.reshade_configure_button,
            "normal" if selection and issue is None and not video_active and not self.busy
            and not self.playing else "disabled")
        if state == 3:
            _set_var(self.reshade_status_text,
                     "ReShade unavailable: " + str(shade.get("message") or "Runtime loading failed."))
        elif state == 2:
            binding = self.app_settings.reshade_binding
            menu = "Menu open." if shade.get("open") else (f"{binding.label} opens its menu." if binding else "Set its menu shortcut in Controls.")
            _set_var(self.reshade_status_text, "ReShade ready. " + menu)
        elif state == 1:
            _set_var(self.reshade_status_text, "Loading ReShade…")
        elif not self.busy:
            if issue:
                _set_var(self.reshade_status_text, "ReShade unavailable: " + issue)
            elif selection and ready:
                _set_var(self.reshade_status_text, "ReShade runtime selected. Press Enable ReShade to load it.")
            elif selection:
                _set_var(self.reshade_status_text, "ReShade runtime selected. Launch a replay through Dolly, "
                                                   "then press Enable ReShade (a live session enables it automatically).")
            else:
                _set_var(self.reshade_status_text, "ReShade disabled. Choose a runtime DLL to enable its in-game menu.")
        _set_widget_state(self.reshade_disable_button,
                          "normal" if ready and state in (1, 2) and not self.busy and not video_active else "disabled")
        # A selected runtime is opt-in and remembered across launches. Make one
        # attempt per bridge; a bad DLL must never create a retry/modal loop.
        if (ready and not self.busy and not self.playing and not video_active
                and self.app_settings.reshade_runtime_path
                and getattr(self, "_auto_reshade_bridge", None) is not bridge):
            self._auto_reshade_bridge = bridge
            self._configure_reshade(automatic=True)

    def _browse_reshade(self):
        path = filedialog.askopenfilename(parent=self.root, title="Choose the ReShade runtime", filetypes=(("ReShade runtime", "*.dll"),))
        if path:
            self._select_reshade_runtime(path)

    def _select_reshade_runtime(self, selected):
        """Remember a chosen runtime immediately and explain the next step.

        Browsing used to fill only the entry: the path was never saved, the
        status text never changed, and a user could reasonably conclude the DLL
        was rejected. Persisting here also lets the next session auto-enable it.
        """
        from dolly.reshade_setup import runtime_issue
        from dolly.runtime import application_root
        text = str(selected).strip()
        self.reshade_runtime_path.set(text)
        issue = runtime_issue(text, application_root=application_root()) if text else None
        if issue:
            self.reshade_status_text.set("ReShade unavailable: " + issue)
            self._log("ReShade: " + issue)
            return False
        settings = replace(self.app_settings, reshade_runtime_path=text)
        try:
            save_settings(settings)
        except OSError as exc:
            self.reshade_status_text.set("Could not save the ReShade runtime selection: " + str(exc))
            return False
        self.app_settings = settings
        bridge = self.controller._native_bridge()
        if recording_ready(self.controller.status()) and bridge is not None:
            self.reshade_status_text.set("ReShade runtime selected. Enabling it now…")
            self._configure_reshade(automatic=True)
        else:
            self.reshade_status_text.set("ReShade runtime selected. Launch a replay through Dolly, "
                                         "then press Enable ReShade (a live session enables it automatically).")
        return True

    def _browse_reshade_library(self):
        path = filedialog.askdirectory(parent=self.root, title="Choose the ReShade FX library folder")
        if path:
            self._select_reshade_library(path)

    def _select_reshade_library(self, selected):
        """Point ReShade at a chosen FX library folder.

        The folder is merged into Dolly's private ReShade configuration, so it
        survives updates and applies the next time the runtime loads. This is
        the fix ReShade's menu asks for when it cannot find effect files.
        """
        from dolly.reshade_setup import library_issue
        issue = library_issue(selected)
        if issue:
            self.reshade_status_text.set("ReShade unavailable: " + issue)
            self._log("ReShade: " + issue)
            return
        def operation():
            from dolly.reshade_setup import prepare_config
            from dolly.settings import reshade_config_path
            return prepare_config(reshade_config_path(), library_root=selected)
        def complete(summary):
            if summary.get("library_shaders") is None and summary.get("library_textures") is None:
                self.reshade_status_text.set(
                    "No shader files were found in that folder; ReShade was not changed.")
                return
            message = ("ReShade FX library saved. It loads with the next ReShade start; "
                       "restart the replay if ReShade is already open.")
            self.reshade_status_text.set(message)
            self._log(message)
        self._submit("Choosing the ReShade FX library", operation, complete)

    def _configure_reshade(self, automatic=False):
        def operation():
            from dolly.reshade_setup import runtime_issue
            from dolly.runtime import application_root
            from dolly.settings import reshade_config_path
            selected = self.app_settings.reshade_runtime_path if automatic else self.reshade_runtime_path.get().strip()
            issue = runtime_issue(selected, application_root=application_root()) if selected else None
            if issue:
                raise ValueError(issue)
            path = Path(selected).expanduser().absolute()
            if path.suffix.lower() != ".dll":
                raise ValueError("Choose the 64-bit ReShade runtime DLL.")
            settings = replace(self.app_settings, reshade_runtime_path=str(path))
            config = reshade_config_path()
            self._auto_reshade_bridge = self.controller._native_bridge()
            def configure():
                bridge = self.controller._native_bridge()
                if bridge is None or not recording_ready(self.controller.status()):
                    raise RuntimeError("Launch a DirectX 11 replay through Dolly before enabling ReShade.")
                from dolly.reshade_setup import discover_library, prepare_config
                summary = prepare_config(config, library_root=discover_library(path))
                bridge.configure_reshade(str(path), str(config))
                save_settings(settings)
                return summary
            def complete(summary):
                self.app_settings = settings
                if summary.get("shaders") is None and summary.get("textures") is None:
                    self.reshade_status_text.set(
                        "ReShade requested, but no FX library was found. Use Browse FX library... "
                        "to point it at your shaders.")
                else:
                    self.reshade_status_text.set("ReShade requested. Dolly's shader library and editable presets are ready in its menu.")
            self._submit("Enabling ReShade", configure, complete)
        if automatic:
            try:
                operation()
            except (RuntimeError, ValueError, OSError) as exc:
                self.reshade_status_text.set("ReShade unavailable: " + str(exc))
                self._log("ReShade: " + str(exc))
        else:
            self._guard("ReShade", operation)

    def _disable_reshade(self):
        def operation():
            bridge = self.controller._native_bridge()
            if bridge is None:
                return
            self._auto_reshade_bridge = bridge
            self._submit("Disabling ReShade", bridge.disable_reshade)
        self._guard("ReShade", operation)

    def _forget_reshade(self):
        def operation():
            if self.video_export.status().get("state") in ACTIVE_STATES:
                raise RuntimeError("Finish recording before changing ReShade.")
            settings = replace(self.app_settings, reshade_runtime_path="")
            bridge = self.controller._native_bridge()
            self._auto_reshade_bridge = bridge
            def forget():
                warning = ""
                if bridge is not None:
                    try:
                        bridge.disable_reshade()
                    except (RuntimeError, ValueError, OSError) as exc:
                        warning = "Current ReShade could not be disabled: " + str(exc)
                save_settings(settings)
                return warning
            def complete(warning):
                self.app_settings = settings
                self.reshade_runtime_path.set("")
                message = "Runtime forgotten. ReShade will not load on the next launch."
                if warning:
                    message += " " + warning
                    self._log(warning)
                self.reshade_status_text.set(message)
                self.status_text.set(message)
            self._submit("Forgetting ReShade runtime", forget, complete)
        self._guard("ReShade", operation)

    def _open_advanced_startup(self):
        self.advanced_startup_dialog.deiconify()
        self.advanced_startup_dialog.lift()

    def _check_game_build(self):
        """Hash the installed modules and report Native camera compatibility."""
        from dolly import compatibility
        from dolly.launcher import LaunchError, game_pins, validate_game
        path = self.game_path.get().strip()
        if not path:
            self.native_status_text.set("Choose the Deadlock folder first.")
            return
        try:
            paths = validate_game(path)
            report = compatibility.scan_game_modules(paths.game_dir, pins=game_pins())
        except (compatibility.CompatibilityError, LaunchError, OSError) as exc:
            self.native_status_text.set(str(exc))
            return
        self.native_status_text.set(report.describe())
        self._log(report.details())

    def _build_advanced_startup(self):
        dialog = tk.Toplevel(self.root)
        self.advanced_startup_dialog = dialog
        chrome.style_title_bar(dialog)
        dialog.title("Advanced launch · Deadlock Dolly")
        dialog.configure(bg=BG)
        dialog.geometry(self._window_size(1080, 662))
        dialog.transient(self.root)
        dialog.protocol("WM_DELETE_WINDOW", dialog.withdraw)
        tab = ttk.Frame(dialog, padding=self._px((24, 22, 24, 24)))
        tab.pack(fill="both", expand=True)
        tab.columnconfigure(0, weight=1)
        tk.Label(tab, text="Advanced launch · Deadlock Dolly", bg=BG, fg=TEXT,
                 font=fonts.font(dialog, "page_title"), anchor="w").grid(
                     row=0, column=0, sticky="ew", pady=self._px((0, 15)))
        intro = ttk.Frame(tab, style="Rounded.Card.TFrame", padding=self._px(20))
        intro.grid(row=1, column=0, sticky="ew")
        intro.columnconfigure(1, weight=1)
        ttk.Label(intro, text="Local Replay Session", style="CardTitle.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=self._px((0, 13)))
        ttk.Label(intro, text="Deadlock executable", style="CardMuted.TLabel").grid(row=1, column=0, sticky="w", padx=self._px((0, 14)))
        ttk.Entry(intro, textvariable=self.game_path, style="Card.TEntry").grid(row=1, column=1, sticky="ew")
        ttk.Button(intro, text="Browse…", style="Card.TButton", command=self._browse_game).grid(row=1, column=2, padx=self._px((9, 0)))
        ttk.Label(intro, text="Replay file · .dem", style="CardMuted.TLabel").grid(row=2, column=0, sticky="w", padx=self._px((0, 14)), pady=self._px((10, 0)))
        ttk.Entry(intro, textvariable=self.demo_path, style="Card.TEntry").grid(row=2, column=1, sticky="ew", pady=self._px((10, 0)))
        ttk.Button(intro, text="Browse…", style="Card.TButton", command=self._browse_demo).grid(row=2, column=2, padx=self._px((9, 0)), pady=self._px((10, 0)))
        connection = ttk.Frame(intro, style="Card.TFrame")
        connection.grid(row=3, column=0, columnspan=3, sticky="ew", pady=self._px((12, 0)))
        ttk.Label(connection, text="Console link", style="CardMuted.TLabel").pack(side="left", padx=self._px((0, 9)))
        ttk.Combobox(connection, textvariable=self.protocol, values=("Netconsole", "VConsole"), state="readonly", width=self._chars(13), style="Card.TCombobox").pack(side="left")
        ttk.Label(connection, text="Developer mode  ·  -insecure", style="CardMuted.TLabel").pack(side="right")
        driver = ttk.Frame(intro, style="Card.TFrame")
        driver.grid(row=4, column=0, columnspan=3, sticky="ew", pady=self._px((10, 0)))
        ttk.Label(driver, text="Camera driver", style="CardMuted.TLabel").pack(side="left", padx=self._px((0, 9)))
        self.camera_driver_combo = ttk.Combobox(driver, textvariable=self.camera_driver,
            values=("Native (experimental)", "Console (legacy)"), state="readonly", width=self._chars(23), style="Card.TCombobox")
        self.camera_driver_combo.pack(side="left")
        ttk.Label(driver, text="Choose before launch. Native follows rendered views.",
                  style="CardMuted.TLabel").pack(side="left", padx=self._px((12, 0)))
        compat = ttk.Frame(intro, style="Card.TFrame")
        compat.grid(row=5, column=0, columnspan=3, sticky="ew", pady=self._px((10, 0)))
        self.native_status_text = tk.StringVar(
            value="Check the installed Deadlock build against Dolly's reviewed Native camera profiles.")
        ttk.Label(compat, textvariable=self.native_status_text, style="CardMuted.TLabel",
                  wraplength=self._px(790), justify="left").pack(side="left", fill="x", expand=True)
        ttk.Button(compat, text="Check game build", style="Card.TButton", command=self._check_game_build).pack(side="right", padx=self._px((12, 0)))
        flow = ttk.Frame(tab, padding=self._px((0, 14)))
        flow.grid(row=2, column=0, sticky="ew")
        for column in range(3):
            flow.columnconfigure(column, weight=1, uniform="startup")
        steps = (
            ("01  Open the hideout", "Launch, then wait for the pre-lobby to load.",
             (("launch_button", "Launch hideout", self._launch, "Card.Primary.TButton"),
              ("connect_button", "Connect", self._connect, "Card.TButton"))),
            ("02  Prepare the replay", "Initialize the unlocker before loading a demo.",
             (("initialize_button", "Initialize unlocker", self._initialize_unlocker, "Card.TButton"),
              ("load_replay_button", "Load replay", self._load_replay, "Card.TButton"))),
            ("03  Start creating", "Once the replay loads, check camera support.",
             (("probe_button", "Check camera support", self._probe, "Card.TButton"),
              ("disconnect_button", "Disconnect", lambda: self._session_operation("Disconnecting", self.controller.disconnect), "Card.TButton"))),
        )
        for column, (title, subtitle, buttons) in enumerate(steps):
            card = ttk.Frame(flow, style="Rounded.Card.TFrame", padding=self._px(20))
            card.grid(row=0, column=column, sticky="nsew", padx=self._px((0 if column == 0 else 5, 0 if column == 2 else 5)))
            card.columnconfigure(0, weight=1)
            ttk.Label(card, text=title, style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
            ttk.Label(card, text=subtitle, style="CardMuted.TLabel", wraplength=self._px(240)).grid(row=1, column=0, sticky="w", pady=self._px((6, 12)))
            for row, (attribute, label, command, style) in enumerate(buttons, 2):
                button = ttk.Button(card, text=label, command=command, style=style,
                                    state="normal" if attribute == "launch_button" else "disabled")
                button.grid(row=row, column=0, sticky="ew", pady=self._px((0, 6)))
                setattr(self, attribute, button)
        note = ttk.Frame(tab, style="Rounded.Card.TFrame", padding=self._px(20))
        note.grid(row=3, column=0, sticky="ew")
        tab.bind("<Configure>", lambda event: note.grid() if event.height >= self._px(465) else note.grid_remove())
        ttk.Label(note, text="Frame → Capture → Fly → Capture", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(note, text="Open Cameras to capture your free-camera view, shape the framing curve, and play the complete shot.\n"
                  "The replay resumes with the path; HUD visibility returns after playback. Close this session before playing normally.",
                  style="CardMuted.TLabel", wraplength=self._px(900), justify="left").pack(anchor="w", pady=self._px((6, 0)))

        dialog.withdraw()

    def _refresh_replays(self):
        folder = self.replay_folder.get().strip()
        if not folder:
            self.replay_entries = []
            self._filter_replays()
            self.replay_summary.set("Choose the folder containing your .dem replays.")
            return
        def finished(entries):
            self.replay_entries = entries
            self._filter_replays()
        self._submit("Reading replay folder", lambda: discover_replays(folder), finished)

    def _filter_replays(self):
        from dolly.replays import sorted_replay_indices
        search = self.replay_search.get().strip().casefold()
        selected_path = self.demo_path.get()
        self.replay_tree.delete(*self.replay_tree.get_children())
        total = 0
        for index in sorted_replay_indices(self.replay_entries, self.replay_sort.get()):
            entry = self.replay_entries[index]
            if search and search not in entry.name.casefold():
                continue
            size = f"{entry.size_bytes / (1024 * 1024):.1f} MB"
            modified = datetime.fromtimestamp(entry.modified_ns / 1e9).strftime("%Y-%m-%d, %H:%M")
            self.replay_tree.insert("", "end", iid=str(index), values=(entry.name, size, modified))
            if str(entry.path) == selected_path:
                self.replay_tree.selection_set(str(index))
            total += 1
        self.replay_summary.set(f"{total} replay{'s' if total != 1 else ''}" + (" matching your search" if search else ""))

    def _select_replay(self, _event=None):
        selection = self.replay_tree.selection()
        if selection:
            self.demo_path.set(str(self.replay_entries[int(selection[0])].path))

    def _use_selected_replay(self):
        self._select_replay()
        if self.demo_path.get().strip():
            self._select_page(self.setup_tab)
            self.status_text.set("Replay selected. Press START to begin.")

    def _browse_replay_folder(self):
        folder = filedialog.askdirectory(parent=self.root, title="Choose replay folder", initialdir=self.replay_folder.get() or None)
        if folder:
            self.replay_folder.set(folder)
            self._refresh_replays()

    def _build_keybinds(self):
        from .ui.controls_page import ControlsActions, ControlsPage
        groups = {
            "Camera": ("capture", "replace", "play_path", "stop", "previous_view", "next_view",
                       "flight", "level_view"),
            "Replay": ("play_pause", "seek_back", "seek_forward"),
            "Interface": ("panel", "game_ui", "framing_grid", "object_picker", "reshade"),
            "Movement": ACTION_ORDER[12:],
        }
        records = [(action, "Toggle ReShade menu" if action == "reshade" else ACTION_LABELS[action], group)
                   for group, actions in groups.items() for action in actions
                   if action != "object_picker" or OBJECT_PICKER_ENABLED]
        commands = ControlsActions(
            change_binding=self._record_binding, clear_binding=self._clear_editor_binding,
            reset_binding=self._reset_editor_binding, reset_all=self._reset_editor_bindings,
            save_movement=self._save_movement_settings, save_sensitivity=self._save_mouse_sensitivity)
        self.controls_view = ControlsPage(self.controls_tab, records, commands,
            movement_speed=self.editor_move_speed, mouse_sensitivity=self.editor_sensitivity,
            feedback=self.binding_feedback)
        self._refresh_bindings()

    def _refresh_bindings(self, selected=None):
        if hasattr(self, "controls_view"):
            self.controls_view.refresh({**self.app_settings.action_bindings,
                                        "reshade": self.app_settings.reshade_binding})

    def _save_editor_binding(self, action, binding):
        from .ui.binding_capture import binding_settings
        if self.busy or self.playing:
            message = "Stop playback and finish the current operation before changing shortcuts."
            self.binding_feedback.set(message)
            self.status_text.set(message)
            return False
        try:
            settings = binding_settings(self.app_settings, action, binding)
        except ValueError as error:
            self.binding_feedback.set(str(error))
            self.status_text.set(str(error))
            return False
        if settings == self.app_settings:
            self.binding_feedback.set("This shortcut is already set.")
            self.status_text.set("This shortcut is already set.")
            return True
        label = "Toggle ReShade menu" if action == "reshade" else ACTION_LABELS[action]
        return self._persist_preferences(settings, f"{label}: {binding.label if binding else 'Unbound'}.")

    def _clear_editor_binding(self, action):
        self._save_editor_binding(action, None)

    def _reset_editor_binding(self, action):
        from .settings import DEFAULT_RESHADE_BINDING
        default = DEFAULT_RESHADE_BINDING if action == "reshade" else default_action_bindings()[action]
        self._save_editor_binding(action, default)

    def _show_listening(self, action):
        """The Controls keycap of ``action`` in its Listening face (None: no keycap listens)."""
        view = getattr(self, "controls_view", None)
        if view is None:
            return
        try:
            view.set_listening(action)
        except tk.TclError:
            pass   # the page is closing with the window

    def _record_binding(self, action="capture"):
        from .ui.binding_capture import BindingCapture, SideMouseCapture, key_from_event, modifiers_from_event
        if self.busy or self.playing:
            message = "Stop playback and finish the current operation before changing shortcuts."
            self.binding_feedback.set(message)
            self.status_text.set(message)
            return
        if self.binding_dialog is not None and self.binding_dialog.winfo_exists():
            self.binding_dialog.lift()
            return
        capture = BindingCapture(self.app_settings, action)
        self._binding_capture = capture
        dialog = self.binding_dialog = tk.Toplevel(self.root)
        chrome.style_title_bar(dialog)
        dialog.title("Change shortcut")
        dialog.configure(bg=BG)
        # The shortcut's keycap on Controls shows Listening while this dialog is open.
        self._show_listening(action)
        dialog.bind("<Destroy>", lambda event: self._show_listening(None)
                    if event.widget is dialog and self.binding_dialog in (None, dialog) else None, add="+")
        width, height = self._window_dimensions(480, 358)
        # Centred on the editor, kept with its frame inside the monitor's work area.
        left, top, right, bottom = chrome.work_area(self.root)
        frame_width, frame_height = chrome.frame_size(menu=False)
        x = max(left, min(self.root.winfo_rootx() + (self.root.winfo_width()-width)//2,
                          right-width-frame_width))
        y = max(top, min(self.root.winfo_rooty() + (self.root.winfo_height()-height)//2,
                         bottom-height-frame_height))
        dialog.geometry(f"{width}x{height}+{x}+{y}")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        body = ttk.Frame(dialog, padding=self._px((24, 22, 24, 20)))
        body.pack(fill="both", expand=True)
        label = "Toggle ReShade menu" if action == "reshade" else ACTION_LABELS[action]
        tk.Label(body, text="Change shortcut", bg=BG, fg=TEXT,
                 font=fonts.font(dialog, "page_title"), anchor="w").pack(fill="x", pady=self._px((0, 15)))
        buttons = ttk.Frame(body)
        buttons.pack(side="bottom", fill="x")
        card = ttk.Frame(body, style="Rounded.Card.TFrame", padding=self._px(20))
        card.pack(fill="x")
        ttk.Label(card, text=label, style="CardTitle.TLabel").pack(anchor="w", pady=self._px((0, 15)))
        value = tk.StringVar(dialog)
        feedback = tk.StringVar(dialog)
        keycap = tk.Frame(card, bg=FIELD, highlightbackground=ui_theme.TOKENS["accent"],
                          highlightthickness=max(1, self._px(1)))
        keycap.pack(fill="x", pady=self._px((0, 15)))
        tk.Label(keycap, textvariable=value, bg=FIELD, fg=TEXT,
                 font=fonts.font(dialog, "body"), anchor="center").pack(fill="x", pady=self._px(8))
        feedback_box = ttk.Frame(card, style="Card.TFrame", height=self._px(45))
        feedback_box.pack(fill="x", pady=self._px((0, 15)))
        feedback_box.pack_propagate(False)
        ttk.Label(feedback_box, textvariable=feedback, style="CardMuted.TLabel",
                  wraplength=self._px(390)).pack(fill="both", expand=True)
        capture_tag = "DollyBindingCapture" + str(dialog.winfo_id())
        side_mouse = None
        escape_modifiers = 0
        focus_after = None
        def close():
            nonlocal side_mouse, focus_after
            capture.cancel()
            if focus_after is not None:
                dialog.after_cancel(focus_after)
                focus_after = None
            if side_mouse is not None:
                side_mouse.stop()
                side_mouse = None
            dialog.unbind_class(capture_tag, "<KeyPress>")
            dialog.unbind_class(capture_tag, "<KeyRelease>")
            dialog.unbind_class(capture_tag, "<ButtonPress>")
            if self.binding_dialog is dialog:
                self.binding_dialog = None
                self._show_listening(None)
            if dialog.winfo_exists():
                dialog.destroy()
        def refresh():
            nonlocal side_mouse
            if capture.ready:
                value.set(capture.candidate.label)
                feedback.set("Ready to save. Choose Record again to try another shortcut.")
            else:
                value.set("Press a shortcut...")
                feedback.set(capture.error or "Press a key or mouse button, with any modifiers. Escape cancels. F7 is reserved for the console.")
            save.configure(state="normal" if capture.ready else "disabled")
            again.configure(state="normal" if capture.ready else "disabled")
            if capture.ready and side_mouse is not None:
                side_mouse.stop()
            elif capture.listening:
                if side_mouse is None:
                    def side_button(key, modifiers):
                        if capture.listening:
                            capture.press(key, modifiers)
                            refresh()
                    side_mouse = SideMouseCapture(dialog, side_button)
                if not side_mouse.running:
                    side_mouse.start()
        def record_again():
            nonlocal capture, escape_modifiers
            escape_modifiers = 0
            capture = self._binding_capture = BindingCapture(self.app_settings, action)
            refresh()
            dialog.focus_set()
        def save_shortcut():
            try:
                settings = capture.settings_for(self.app_settings)
            except ValueError as error:
                feedback.set(str(error))
                save.configure(state="disabled")
                return
            if settings == self.app_settings:
                self.binding_feedback.set("Shortcut unchanged.")
                close()
            elif self._persist_preferences(settings, f"{label}: {capture.candidate.label}."):
                close()
            else:
                feedback.set("Finish the current operation and stop playback before saving.")
        save = ttk.Button(buttons, text="Save shortcut", command=save_shortcut, style="Primary.TButton")
        save.pack(side="right")
        ttk.Button(buttons, text="Cancel", command=close).pack(side="right", padx=self._px((0, 10)))
        capture_actions = ttk.Frame(card, style="Card.TFrame")
        capture_actions.pack(anchor="w")
        again = ttk.Button(capture_actions, text="Record again", command=record_again, style="Card.TButton")
        again.pack(side="left", padx=self._px((0, 10)))
        def use_escape():
            nonlocal capture, escape_modifiers
            modifiers, escape_modifiers = escape_modifiers, 0
            if side_mouse is not None and side_mouse.keyboard_blocked():
                capture.focus_lost()
                feedback.set("Focus this window and release the Windows key before recording a shortcut.")
                return
            if capture.ready:
                capture = self._binding_capture = BindingCapture(self.app_settings, action)
            capture.bind_escape(modifiers)
            refresh()
        escape_button = ttk.Button(capture_actions, text="Use Escape as the shortcut", command=use_escape,
                                   style="Card.TButton")
        escape_button.pack(side="left")
        def escape_pressed(event):
            nonlocal escape_modifiers
            escape_modifiers = modifiers_from_event(event)
        escape_button.bind("<ButtonPress-1>", escape_pressed)
        escape_button.bind("<KeyPress>", escape_pressed)
        def record(event):
            key = key_from_event(event)
            mouse = str(event.type) in ("4", "ButtonPress")
            if key == "Escape":
                close()
                return "break"
            if not capture.listening:
                if mouse:
                    return None if key is None else "break"
                modifiers = modifiers_from_event(event)
                if key == "Tab" and modifiers in (0, 4):
                    return
                if key in ("Space", "Enter") and not modifiers and isinstance(event.widget, ttk.Button):
                    return
                return "break"
            if str(event.type) in ("3", "KeyRelease"):
                capture.release(key)
            elif not mouse or key is not None:
                if not mouse and side_mouse is not None and side_mouse.keyboard_blocked():
                    capture.focus_lost()
                    capture.error = "Focus this window and release the Windows key before recording a shortcut."
                else:
                    capture.press(key, modifiers_from_event(event))
            refresh()
            # Primary clicks operate Cancel/Save; keyboard events belong only
            # to the recorder, before ttk or the application's Ctrl+S handlers.
            return None if mouse and key is None else "break"
        for sequence in ("<KeyPress>", "<KeyRelease>", "<ButtonPress>"):
            dialog.bind_class(capture_tag, sequence, record)
        def add_tag(widget):
            widget.bindtags((capture_tag, *widget.bindtags()))
            for child in widget.winfo_children():
                add_tag(child)
        add_tag(dialog)
        def check_focus():
            nonlocal focus_after
            focus_after = None
            target = dialog.focus_get()
            while target is not None and target is not dialog:
                target = getattr(target, "master", None)
            if target is None:
                capture.focus_lost()
        def focus_out(_event):
            nonlocal focus_after
            if focus_after is None:
                focus_after = dialog.after_idle(check_focus)
        dialog.bind("<FocusOut>", focus_out, add="+")
        dialog.protocol("WM_DELETE_WINDOW", close)
        dialog.grab_set()
        dialog.focus_set()
        refresh()

    def _reset_editor_bindings(self):
        if not self.busy and not self.playing and messagebox.askyesno("Restore bindings", "Restore all editor shortcuts to their defaults?", parent=self.root):
            from dolly.settings import DEFAULT_RESHADE_BINDING
            settings = replace(self.app_settings, capture_binding=DEFAULT_BINDING,
                               action_bindings=default_action_bindings(), reshade_binding=DEFAULT_RESHADE_BINDING)
            self._persist_preferences(settings, "Default editor shortcuts restored.")

    def _save_movement_settings(self):
        def operation():
            settings = replace(self.app_settings,
                movement_speed=_finite(self.editor_move_speed.get(), "Movement speed"),
                mouse_sensitivity=_finite(self.editor_sensitivity.get(), "Mouse sensitivity"))
            self._persist_preferences(settings, "Movement settings saved.")
        self._guard("Movement settings", operation)

    def _save_mouse_sensitivity(self):
        def operation():
            settings = replace(self.app_settings,
                mouse_sensitivity=_finite(self.editor_sensitivity.get(), "Mouse sensitivity"))
            self._persist_preferences(settings, f"Mouse sensitivity saved ({settings.mouse_sensitivity:.3g}).")
        self._guard("Mouse sensitivity", operation)

    def _persist_preferences(self, settings, message):
        if self.busy or self.playing:
            self.status_text.set("Finish the current operation and stop playback before saving preferences.")
            return False
        settings = replace(settings, game_path=self.game_path.get().strip(),
                           demo_path=self.demo_path.get().strip(), replay_folder=self.replay_folder.get().strip())
        capture_changed = settings.action_bindings.get("capture") != self.app_settings.action_bindings.get("capture")
        old_helper = self.capture_hotkey if capture_changed else None
        if old_helper is not None:
            self.capture_generation += 1
            self.hotkey_enabled.set(False)
        def save():
            if old_helper is not None:
                old_helper.stop()
            save_settings(settings)
        def complete(_result):
            if old_helper is not None:
                self.capture_hotkey = None
            self.app_settings = settings
            self.capture_binding = settings.capture_binding
            self.hotkey_label.set(self._capture_binding_label())
            self._refresh_bindings()
            self.binding_feedback.set(message)
            self.status_text.set(message)
            editor_session.configure(self)
        return self._submit("Saving preferences", save, complete)

    def _open_launch_options(self):
        dialog = tk.Toplevel(self.root)
        chrome.style_title_bar(dialog)
        dialog.title("Launch options · Deadlock Dolly")
        dialog.configure(bg=BG)
        dialog.geometry(self._window_size(720, 354))
        dialog.transient(self.root)
        body = ttk.Frame(dialog, padding=self._px((24, 22, 24, 20)))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        tk.Label(body, text="Launch options · Deadlock Dolly", bg=BG, fg=TEXT,
                 font=fonts.font(dialog, "page_title"), anchor="w").grid(
                     row=0, column=0, sticky="ew", pady=self._px((0, 15)))
        card = ttk.Frame(body, style="Rounded.Card.TFrame", padding=self._px(20))
        card.grid(row=1, column=0, sticky="ew")
        card.columnconfigure(0, weight=1)
        ttk.Label(card, text="Launch Options", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        flags = ttk.Frame(card, style="Card.TFrame")
        flags.grid(row=1, column=0, sticky="w", pady=self._px((15, 15)))
        ttk.Label(flags, text="Always included:", style="Card.TLabel").pack(side="left", padx=self._px((0, 6)))
        for flag in ("-dev", "-insecure", "-console", "-dx11"):
            tk.Label(flags, text=flag, bg=FIELD, fg=TEXT, font=(MONO, -self._px(12)),
                     padx=self._px(8), pady=self._px(3)).pack(side="left", padx=self._px((0, 6)))
        ttk.Label(card, text="Additional display options", style="CardMuted.TLabel").grid(row=2, column=0, sticky="w", pady=self._px((0, 8)))
        value = tk.StringVar(value=self.launch_options.get())
        ttk.Entry(card, textvariable=value, style="Card.TEntry").grid(row=3, column=0, sticky="ew")
        ttk.Label(card, text="Examples: -windowed -w 1920 -h 1080\nReplay loading, console connection and security settings are managed by Dolly.", style="CardMuted.TLabel", wraplength=self._px(620)).grid(row=4, column=0, sticky="w", pady=self._px((15, 0)))
        def save():
            def operation():
                text = value.get().strip()
                parse_launch_options(text)
                candidate = replace(self.app_settings, launch_options=text)
                self.launch_options.set(text)
                if self._persist_preferences(candidate, "Launch options saved for the next session."):
                    dialog.destroy()
            self._guard("Launch options", operation)
        ttk.Button(body, text="Save options", style="Primary.TButton", command=save).grid(row=2, column=0, sticky="e", pady=self._px((24, 0)))

    def _start_editing_session(self):
        def operation():
            if self.controller.status().get("startup_stage") == "waiting_game_screen":
                return self._resume_startup()
            game, demo = self.game_path.get().strip(), self.demo_path.get().strip()
            if not game or not demo:
                raise ValueError("Choose your Deadlock executable and a .dem replay first.")
            from .lite_mode import launch_options_for_mode
            # Discard the retired UI flag from older preferences. Our rebuilt
            # helper uses the same editor for every normal native launch.
            options = launch_options_for_mode(self.launch_options.get().strip(), False)
            parse_launch_options(options)
            from .graphics_profiles_ui import launch_options as graphics_launch_options
            graphics_options = graphics_launch_options(self)
            settings = replace(self.app_settings, game_path=game, demo_path=demo,
                               replay_folder=self.replay_folder.get().strip(), launch_options=self.launch_options.get().strip())
            cancel = threading.Event()
            protocol = "vconsole" if self.protocol.get() == "VConsole" else "netcon"
            native = self.camera_driver.get() == "Native (experimental)"
            allow_mods = (self.allow_mod_overrides.get()
                          if hasattr(self, "allow_mod_overrides")
                          else bool(getattr(self.app_settings, "allow_mod_overrides", False)))
            mod_options = {"allow_mod_overrides": True} if allow_mods else {}
            if getattr(self.app_settings, "nvidia_dlss", False):
                mod_options["nvidia_dlss"] = True
            self._disable_external_input()
            self.attach_fields = None
            def start():
                if not native and cancel.is_set():
                    raise RuntimeError("Console startup cancelled before launch.")
                save_settings(settings)
                if native:
                    return self.controller.start_editing(game, demo, protocol=protocol, native=True,
                        launch_options=options, cancel_event=cancel,
                        **mod_options, **graphics_options)
                if cancel.is_set():
                    raise RuntimeError("Console startup cancelled before launch.")
                return self.controller.launch(game, demo, protocol=protocol, native=False,
                    launch_options=options, **mod_options, **graphics_options)
            def complete(result):
                self.app_settings = settings
                if native:
                    self._editing_started(result)
                else:
                    self.startup_cancel = None
                    self.startup_bar.stop()
                    self._session_result(result)
                    self._open_advanced_startup()
                    message = ("Console startup cancelled. The launched game remains open."
                               if cancel.is_set() else
                               "Console startup: wait for the hideout, then Connect, Initialize unlocker, "
                               "Load replay and Check camera support in Startup controls.")
                    self.startup_progress.set(message)
                    self.status_text.set(message)
            if self._submit("Starting replay editor", start, complete):
                self.startup_cancel = cancel
                self.startup_progress.set("Launching Deadlock and preparing the hideout…")
                self.startup_bar.start(15)
        self._guard("Start replay editor", operation)

    def _resume_startup(self):
        cancel = threading.Event()
        demo = self.demo_path.get().strip()
        if self._submit("Checking startup again",
                        lambda: self.controller.resume_startup(demo, cancel_event=cancel),
                        self._editing_started):
            self.startup_cancel = cancel
            self.startup_progress.set("Rechecking the existing hideout and preload…")
            self.startup_bar.start(15)

    def _editing_started(self, result):
        self.startup_cancel = None
        self.startup_bar.stop()
        # Keep the driver the user selected; the live indicator reports which
        # backend the running session actually uses.
        self.startup_progress.set("Replay paused and ready. Return to Deadlock to frame your first camera.")
        self.status_text.set("Use your editor shortcut to open the in-game panel. F7 opens the console.")
        graphics = self.controller._startup_evidence.get("graphics_profile", {})
        if graphics.get("warning"):
            self.status_text.set(graphics["warning"])
            self._log(json.dumps(graphics, indent=2))
        editor_session.configure(self)
        self._submit("Preparing attach camera fields", self._query_attach_fields,
                     self._attach_fields_complete)
        if not hasattr(self, "full_editor") or self.full_editor.get():
            self._select_page(self.camera_tab)

    def _query_attach_fields(self):
        """Worker: read the attach schema offsets once; failure only disables attach."""
        try:
            return attach_camera.query_field_offsets(self.controller)
        except Exception as exc:
            LOG.info("Attach camera fields unavailable: %s", exc)
            return None

    def _attach_fields_complete(self, fields):
        self.attach_fields = fields
        if fields:
            LOG.info("Attach camera schema fields ready: %s", fields)
        editor_session.configure(self)

    def _cancel_startup(self):
        if self.startup_cancel is not None:
            self.startup_cancel.set()
            self.startup_progress.set("Cancelling startup and restoring the editing session…")
            self.cancel_startup_button.configure(state="disabled")

    def _disable_external_input(self):
        """Prevent legacy polling from competing with native editor input."""
        self.capture_generation += 1
        self.hotkey_enabled.set(False)
        if self.capture_hotkey is not None:
            self.capture_hotkey.stop()
            self.capture_hotkey = None

    def _tree(self, parent, columns, labels, widths=None, height=6):
        """Only data lists scroll; editor panels retain their available width.

        The holder is a plain panel frame (a ttk frame repaints its whole area on
        every expose, PLAN-5 PR4). On a kit card no ttk ``Card.`` frame sits above
        the rail for the theme's surface matching to find, so the rail names its
        card style itself.
        """
        frame = tk.Frame(parent, background=PANEL, borderwidth=0, highlightthickness=0, takefocus=0)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse", height=height)
        for index, (column, label) in enumerate(zip(columns, labels)):
            tree.heading(column, text=label)
            width = widths[index] if widths else self._px(80)
            tree.column(column, width=width, minwidth=self._px(45), anchor="e", stretch=True)
        tree.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview, style="Card.Vertical.TScrollbar")
        yscroll.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=yscroll.set)
        return frame, tree

    def _attach_point_picker(self):
        """"Bone Picker..." chosen as the attach point: keep the key's own point, open the picker.

        The Camera page calls this from its ``attach_point`` trace; the dropdown goes back
        to the selected camera's current point (or Eyes) while the picker runs.
        """
        index = self._selection_index(self.camera_tree)
        point = "Eyes"
        if index is not None and 0 <= index < len(self.project.keyframes):
            current = self.project.keyframes[index].attach
            if current:
                point = current.point.title()
        self.attach_point.set(point)
        self.root.after_idle(self._open_bone_picker)

    # Citadel DOF (the game's own depth of field) authors the same shot values
    # as the Effects tracks and applies them through the existing preview.
    def _citadel_playhead(self):
        try:
            value = float(self.shot_time.get())
        except (TypeError, ValueError):
            value = 0.0
        return max(0.0, value if math.isfinite(value) else 0.0)

    @staticmethod
    def _focus_to_slider(value):
        return math.log(1.0 + max(0.0, float(value))) / math.log(10001.0)

    @staticmethod
    def _slider_to_focus(value):
        return math.exp(min(1.0, max(0.0, float(value))) * math.log(10001.0)) - 1.0

    def _refresh_citadel_dof(self):
        if not hasattr(self, "citadel_dof_checkbox") or not hasattr(self, "citadel_enabled"):
            return
        from dolly.editor_dof import citadel_values_at
        enabled, sensor, focus = citadel_values_at(self.project, self._citadel_playhead())
        self.citadel_enabled.set(enabled)
        self.citadel_sensor.set(sensor)
        self.citadel_sensor_text.set(f"Sensor size · {sensor:.2f}")
        self.citadel_focus_internal.set(self._focus_to_slider(focus))
        self.citadel_focus_text.set(f"Focus distance · {focus:.0f} in")

    def _commit_citadel_dof(self, control, value, label):
        if self.busy or self.playing:
            self.status_text.set("Pause playback and finish the current operation before editing DOF.")
            self._refresh_citadel_dof()
            return
        from dolly.editor_dof import edited_citadel_project
        at = self._citadel_playhead()
        try:
            candidate = edited_citadel_project(self.project, at, control, value)
        except ValueError as exc:
            self._error("Citadel DOF", exc)
            self._refresh_citadel_dof()
            return
        def complete(_result):
            self.project = candidate
            self._mark_dirty()
            self._refresh_tracks()
            self._refresh_fixed()
            self.status_text.set(label + " applied at the paused moment.")
        self._submit("Applying Citadel DOF", lambda: self.controller.apply(candidate, at), complete)

    def _toggle_citadel_dof(self):
        self._commit_citadel_dof(0, bool(self.citadel_enabled.get()), "Citadel DOF")

    def _citadel_sensor_changed(self):
        try:
            value = float(self.citadel_sensor.get())
        except (TypeError, ValueError):
            return
        self.citadel_sensor_text.set(f"Sensor size · {value:.2f}")

    def _citadel_sensor_commit(self):
        value = min(3.0, max(.5, round(float(self.citadel_sensor.get()) / .05) * .05))
        self.citadel_sensor.set(value)
        self.citadel_sensor_text.set(f"Sensor size · {value:.2f}")
        self._commit_citadel_dof(1, value, "Sensor size")

    def _citadel_focus_changed(self):
        focus = self._slider_to_focus(self.citadel_focus_internal.get())
        self.citadel_focus_text.set(f"Focus distance · {focus:.0f} in")

    def _citadel_focus_commit(self):
        focus = min(10000.0, max(0.0, round(self._slider_to_focus(self.citadel_focus_internal.get()))))
        self.citadel_focus_internal.set(self._focus_to_slider(focus))
        self.citadel_focus_text.set(f"Focus distance · {focus:.0f} in")
        self._commit_citadel_dof(2, focus, "Focus distance")

    def _session_operation(self, label, function):
        if self.busy and function in (self.controller.stop, self.controller.pause):
            if self.controller.cancel_replay_recovery():
                self.status_text.set("Cancelling replay preparation…")
                return True
        if self.busy:
            self.status_text.set("Please wait for the current operation to finish.")
            return False
        if function == self.controller.stop:
            function = lambda: self.controller.stop(preserve_speed=True)
            return self._submit(label, function, self._restore_completed)
        return self._submit(label, function)

    def _restore_completed(self, _result=None):
        status = self.controller.status()
        pending = bool(status.get("health_panel_restore_pending"))
        if pending and status.get("connected"):
            # Surface the held panel for every camera backend. The saved setting
            # is only restored once a valid hero view exists, so tell the user how
            # to finish it instead of leaving the panel silently hidden.
            self._show_restore_guidance()
        elif pending:
            # Keep it visible even after the game connection is gone.
            self.status_text.set(
                "The game health panel is still hidden. Reconnect to Deadlock and use Stop / restore to finish.")
        else:
            dialog = getattr(self, "restore_guidance_dialog", None)
            if dialog is not None and dialog.winfo_exists():
                dialog.destroy()
                self.status_text.set("Health panel restored to its saved setting.")
            self.restore_guidance_dialog = None

    def _show_restore_guidance(self):
        dialog = getattr(self, "restore_guidance_dialog", None)
        if dialog is not None and dialog.winfo_exists():
            dialog.lift()
            return
        dialog = tk.Toplevel(self.root)
        self.restore_guidance_dialog = dialog
        chrome.style_title_bar(dialog)
        dialog.title("Restore the game health panel")
        dialog.configure(bg=BG)
        dialog.transient(self.root)
        body = ttk.Frame(dialog, padding=self._px((24, 22, 24, 20)))
        body.pack(fill="both", expand=True)
        tk.Label(body, text="Restore the game health panel", bg=BG, fg=TEXT,
                 font=fonts.font(dialog, "page_title"), anchor="w").pack(
                     fill="x", pady=self._px((0, 15)))
        card = ttk.Frame(body, style="Rounded.Card.TFrame", padding=self._px(20))
        card.pack(fill="x")
        ttk.Label(card, text="One More Step to Restore", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(card, wraplength=self._px(448), justify="left", style="Card.TLabel", text=(
            "The health and ability panel is still hidden because the game is in free camera. "
            "Dolly is keeping your original setting until a hero view is ready."
        )).pack(fill="x", pady=self._px((14, 14)))
        for number, instruction in enumerate((
            "Open replay controls below.",
            "Select a hero and wait for the camera to follow that hero.",
            "Return to Dolly and click Restore after selection.",
        ), start=1):
            row = ttk.Frame(card, style="Card.TFrame")
            row.pack(fill="x", pady=self._px((0, 8 if number < 3 else 0)))
            tk.Label(row, text=f"{number}.", bg=ui_theme.TOKENS["button"], fg=TEXT,
                     font=fonts.font(dialog, "small"), width=2).pack(side="left", padx=self._px((0, 10)))
            ttk.Label(row, text=instruction, style="CardMuted.TLabel", wraplength=self._px(410),
                      justify="left").pack(side="left", fill="x", expand=True)
        ttk.Label(body, wraplength=self._px(490), style="Muted.TLabel", text=(
            "You can leave this window open. Restore checks the hero view before showing the panel."
        )).pack(fill="x", pady=self._px((15, 20)))
        actions = ttk.Frame(body)
        actions.pack(anchor="e")
        ttk.Button(actions, text="Open replay controls",
                   command=self._open_restore_replay_controls).pack(side="left")
        ttk.Button(actions, text="Restore after selection", style="Primary.TButton", command=lambda:
                   self._session_operation("Restoring game controls", self.controller.stop)
                   ).pack(side="left", padx=self._px((10, 0)))

    def _open_restore_replay_controls(self):
        def open_controls():
            self.controller.toggle_console(False)
            self.controller.toggle_game_ui(True)
        def focused(_result):
            pid = self.controller.game_pid()
            if pid is not None:
                focus_window(pid)
            self.status_text.set("Select a hero in Deadlock, then return here and click Restore after selection.")
        return self._submit("Opening replay controls", open_controls, focused)

    def _open_coordinates(self):
        if self.coordinates_dialog is not None and self.coordinates_dialog.winfo_exists():
            self.coordinates_dialog.lift()
            return
        dialog = tk.Toplevel(self.root)
        self.coordinates_dialog = dialog
        chrome.style_title_bar(dialog)
        dialog.title("Camera coordinates & shot timing")
        dialog.configure(bg=BG)
        dialog.geometry(self._window_size(620, 658))
        dialog.minsize(*self._window_dimensions(580, 600))
        dialog.transient(self.root)
        body = ttk.Frame(dialog, padding=self._px((24, 22, 24, 20)))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        tk.Label(body, text="Camera coordinates & shot timing", bg=BG, fg=TEXT,
                 font=fonts.font(dialog, "page_title"), anchor="w").grid(
                     row=0, column=0, sticky="ew", pady=self._px((0, 15)))
        camera_card = ttk.Frame(body, style="Rounded.Card.TFrame", padding=self._px(20))
        camera_card.grid(row=1, column=0, sticky="ew", pady=self._px((0, 15)))
        camera_card.columnconfigure((0, 1), weight=1, uniform="coordinate")
        ttk.Label(camera_card, text="Camera Coordinates", style="CardTitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=self._px((0, 15)))
        self.key_entries = {}
        for index, (field, label) in enumerate(zip(FIELDS, FIELD_LABELS)):
            cell = ttk.Frame(camera_card, style="Card.TFrame")
            cell.grid(row=1 + index // 2, column=index % 2, sticky="ew", padx=self._px((0, 20 if index % 2 == 0 else 0)), pady=self._px((0, 10)))
            cell.columnconfigure(0, minsize=self._px(104))
            cell.columnconfigure(1, weight=1)
            ttk.Label(cell, text=label, style="CardMuted.TLabel").grid(row=0, column=0, sticky="w", padx=self._px((0, 10)))
            entry = ttk.Entry(cell, textvariable=self.key_vars[field], width=1, style="Card.TEntry")
            entry.grid(row=0, column=1, sticky="ew")
            self.key_entries[field] = entry
        actions = ttk.Frame(camera_card, style="Card.TFrame")
        actions.grid(row=5, column=0, columnspan=2, sticky="w", pady=self._px((4, 14)))
        ttk.Button(actions, text="Update selected", style="Card.TButton", command=self._update_key).pack(side="left", padx=self._px((0, 10)))
        ttk.Button(actions, text="Add entered camera", style="Card.TButton", command=self._add_key).pack(side="left")
        timing_card = ttk.Frame(body, style="Rounded.Card.TFrame", padding=self._px(20))
        timing_card.grid(row=2, column=0, sticky="ew")
        timing_card.columnconfigure((0, 1), weight=1, uniform="timing")
        ttk.Label(timing_card, text="Shot Timing & Movement", style="CardTitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=self._px((0, 15)))
        for index, (label, variable, values) in enumerate((("Start replay tick", self.start_tick, None), ("Ticks / second", self.tick_rate, None),
                                                         ("Position curve", self.interpolation, ("smooth", "spline", "linear")),
                                                         ("Rotation", self.rotation, ("shortest", "unwrapped")))):
            cell = ttk.Frame(timing_card, style="Card.TFrame")
            cell.grid(row=1 + index // 2, column=index % 2, sticky="ew", padx=self._px((0, 20 if index % 2 == 0 else 0)), pady=self._px((0, 10)))
            cell.columnconfigure(0, minsize=self._px(104))
            cell.columnconfigure(1, weight=1)
            ttk.Label(cell, text=label, style="CardMuted.TLabel").grid(row=0, column=0, sticky="w", padx=self._px((0, 10)))
            widget = (ttk.Combobox(cell, textvariable=variable, values=values, state="readonly", width=1, style="Card.TCombobox")
                      if values else ttk.Entry(cell, textvariable=variable, width=1, style="Card.TEntry"))
            widget.grid(row=0, column=1, sticky="ew")
        actions = ttk.Frame(timing_card, style="Card.TFrame")
        actions.grid(row=3, column=0, columnspan=2, sticky="ew", pady=self._px((5, 0)))
        ttk.Button(actions, text="Use current replay tick", style="Card.TButton", command=self._use_current_tick).pack(side="left")
        ttk.Button(actions, text="Retime to replay", style="Card.TButton", command=self._retime_to_replay).pack(side="left", padx=self._px(10))
        ttk.Button(actions, text="Apply settings", style="Card.TButton", command=self._apply_options).pack(side="left")
        ttk.Label(body, text="Captured positions stay in view coordinates. Height is Z.\nBank uses Dolly’s last applied value; capture starts with bank 0.",
                  style="Muted.TLabel", wraplength=self._px(570)).grid(row=3, column=0, sticky="w", pady=self._px((15, 20)))
        ttk.Button(body, text="Done", style="Primary.TButton", command=dialog.destroy).grid(row=4, column=0, sticky="e")

    def _toggle_coordinates(self):
        self._open_coordinates()

    def _capture_mode_changed(self, _event=None):
        replay_timing = self.capture_mode.get() == "Replay timing"
        self.segment_entry.configure(state="disabled" if replay_timing else "normal")
        self.capture_hint.set(
            "Replay timing: capture pauses the replay and records its current time."
            if replay_timing else "Timed shot: fly between views; spacing sets their arrival times.")
        self.status_text.set(self.capture_hint.get())

    def _build_fixed_dialog(self):
        """The Fixed camera values window (opened from Look's "Fixed values…")."""
        self.fixed_dialog = tk.Toplevel(self.root)
        chrome.style_title_bar(self.fixed_dialog)
        self.fixed_dialog.title("Fixed camera values")
        self.fixed_dialog.geometry(self._window_size(820, 360))
        self.fixed_dialog.minsize(*self._window_dimensions(740, 300))
        self.fixed_dialog.configure(bg=BG)
        self.fixed_dialog.transient(self.root)
        self.fixed_dialog.protocol("WM_DELETE_WINDOW", self.fixed_dialog.withdraw)
        body = ttk.Frame(self.fixed_dialog, padding=self._px((24, 22, 24, 24)))
        body.pack(fill="both", expand=True)
        body.rowconfigure(1, weight=1)
        body.columnconfigure(0, weight=1)
        tk.Label(body, text="Fixed camera values", bg=BG, fg=TEXT,
                 font=fonts.font(self.fixed_dialog, "page_title"), anchor="w").grid(
                     row=0, column=0, columnspan=2, sticky="ew", pady=self._px((0, 15)))
        setup = ttk.Frame(body, style="Rounded.Card.TFrame", padding=self._px(20))
        setup.grid(row=1, column=0, sticky="nsew", padx=self._px((0, 15)))
        setup.rowconfigure(1, weight=1)
        setup.columnconfigure(0, weight=1)
        ttk.Label(setup, text="Fixed Values", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w", pady=self._px((0, 15)))
        frame, self.setup_tree = self._tree(setup, ("name", "value"), ("Variable", "Value"), self._px((250, 80)), height=6)
        self.setup_tree.column("name", anchor="w")
        frame.grid(row=1, column=0, sticky="nsew")
        fixed_form = ttk.Frame(body, style="Rounded.Card.TFrame", padding=self._px(20),
                               width=self._px(280), height=self._px(152))
        fixed_form.grid(row=1, column=1, sticky="new")
        fixed_form.grid_propagate(False)
        fixed_form.columnconfigure(0, weight=1)
        ttk.Entry(fixed_form, textvariable=self.setup_name, width=1, style="Card.TEntry").grid(row=0, column=0, sticky="ew")
        ttk.Entry(fixed_form, textvariable=self.setup_value, width=1, style="Card.TEntry").grid(row=1, column=0, sticky="ew", pady=self._px((10, 0)))
        actions = ttk.Frame(fixed_form, style="Card.TFrame")
        actions.grid(row=2, column=0, sticky="e", pady=self._px((10, 0)))
        ttk.Button(actions, text="Remove", style="Card.TButton", command=self._remove_fixed).pack(side="left")
        ttk.Button(actions, text="Set value", style="Card.Primary.TButton", command=self._set_fixed).pack(side="left", padx=self._px((10, 0)))
        self.setup_tree.bind("<<TreeviewSelect>>", self._select_fixed)
        self.fixed_dialog.withdraw()

    def _particles_changed(self):
        def operation():
            self._sync_options()
            self.controller.set_native_particles(
                self.project.confetti_enabled,
                self.project.confetti_spawn_height,
                self.project.confetti_despawn_on_ground,
                self.project.particles_preset,
                self.project.particles_intensity,
            )
            label = PARTICLE_LABELS.get(self.project.particles_preset,
                                        self.project.particles_preset)
            if not self.project.confetti_enabled:
                self.status_text.set(f"{label} disabled for this shot.")
                return
            message = (f"{label} enabled at {self.project.confetti_spawn_height:g} units, "
                       f"intensity {self.project.particles_intensity:g}x")
            if self.project.particles_preset == PARTICLE_DEFAULT:
                message += (". Despawns on ground." if self.project.confetti_despawn_on_ground
                            else ". Remains on the ground.")
            self.status_text.set(message)
        self._guard("Particles", operation)

    def _show_fixed_values(self):
        self.fixed_dialog.deiconify()
        self.fixed_dialog.lift()

    def _worker_loop(self):
        self.operations.run(self._operation_failure_kind)

    def _operation_failure_kind(self):
        layered = getattr(self, "_base_capture", None) is not None
        if not layered:
            return "error"
        # Only run recording recovery when a capture actually engaged. A layered
        # run can fail in its preflight (for example the Players disk-space
        # check) before any recorder or replay work, and there is nothing to
        # recover then; doing so only delays the error and can confuse state.
        exporter = getattr(self, "video_export", None)
        controller = getattr(self, "controller", None)
        engaged = ((exporter is not None and exporter.status().get("state") in ACTIVE_STATES)
                   or (controller is not None and controller.status().get("playing"))
                   or (controller is not None
                       and getattr(controller, "_recording_replay", None) is not None))
        if engaged:
            self._recover_export_failure()
            return "export_error"
        self._clear_layer_pipeline()
        return "error"

    def _recover_game_config(self):
        def complete(restored):
            messagebox.showinfo("Game configuration",
                f"Recovered or cleaned {len(restored)} session(s)." if restored else
                "No pending recovery or temporary-folder cleanup is needed.", parent=self.root)
        # recover_pending verifies that Deadlock is closed and retains any
        # conflicting user/Steam changes. The usual worker owns this action.
        self._submit("Recovering game configuration", recover_pending, complete)

    def _submit(self, label, function, callback=None):
        return self.operations.submit(
            label, function, callback,
            blocked=bool(getattr(self, "_bone_picker_context", None)),
            started=lambda name: self.busy_text.set(name + "\u2026"),
            rejected=lambda message: self.status_text.set(message),
        )

    def _enqueue_log(self, *parts):
        self.operations.enqueue_log(*parts)

    def _log(self, text):
        # An idle callback can report a startup error before the activity-log
        # window exists (for example when the window manager processes idle
        # work while a Toplevel is being created). Preserve it until then.
        if not hasattr(self, "log_widget"):
            if not hasattr(self, "_early_log"):
                self._early_log = []
            self._early_log.append(text)
            return
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", str(text).rstrip() + "\n")
        if int(self.log_widget.index("end-1c").split(".")[0]) > 1200:
            self.log_widget.delete("1.0", "200.0")
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")

    def _poll(self):
        if self.closed:
            return
        try:
            self._poll_once()
        except Exception:
            LOG.exception("Dolly's interface poll failed; the window will keep updating.")
        finally:
            if not self.closed:
                self.root.after(self._poll_delay(), self._poll)

    def _poll_delay(self):
        """Poll fast only while a session or work is active."""
        if (getattr(self, "playing", False) or getattr(self, "busy", False)
                or getattr(self, "startup_cancel", None) is not None
                or getattr(self, "_poll_connected", False)):
            return 100
        return 250

    def _poll_once(self):
        if self.closed:
            return
        for kind, label, payload in self.operations.pending_events():
            if kind == "capture_hotkey":
                self._handle_capture_hotkey(payload)
            elif kind == "log":
                self._log(payload)
            elif kind in ("error", "export_error"):
                self.busy = False
                self.busy_text.set("")
                if getattr(self, "_closing", False):
                    # The user asked to close Dolly and a teardown step (such as
                    # Stop) failed, often because the game was already closed
                    # from its own window. Still run the file-level close and
                    # exit rather than leaving the window open behind an error.
                    LOG.warning("Close-time failure; finishing the close anyway: %s", payload)
                    self._log(f"{label}: {payload}")
                    self._close_after_failure()
                    return
                if kind == "export_error":
                    self._clear_layer_pipeline()
                cancelled = self.startup_cancel is not None and self.startup_cancel.is_set()
                if self.startup_cancel is not None:
                    self.startup_cancel = None
                    self.startup_bar.stop()
                    self.startup_progress.set("Startup cancelled." if cancelled else str(payload))
                if cancelled:
                    self.status_text.set("Startup cancelled. You can choose a replay and try again.")
                    self._log(str(payload))
                else:
                    self._report_operation_error(label, payload)
            elif kind == "done":
                self.busy = False
                self.busy_text.set("")
                result, callback = payload
                self.status_text.set(label + " complete.")
                if callback:
                    try:
                        callback(result)
                    except Exception as exc:
                        self._error(label, exc)
                if self.closed:
                    return
        try:
            status = self.controller.status()
            self._handle_game_exit(status)
            self.playing = bool(status.get("playing"))
            connected = bool(status.get("connected"))
            self._poll_connected = connected
            stage = str(status.get("startup_stage", "not_launched"))
            stage_labels = {
                "not_launched": "Game not launched",
                "waiting_hideout": "Waiting for hideout",
                "waiting_preload_intro": "Preparing Deadlock intro",
                "waiting_game_screen": "Dismiss the game screen, then check startup again",
                "preload_unavailable": "Deadlock intro/preload not detected",
                "waiting_replay_camera": "Waiting for replay intro camera",
                "preloading": "Preloading map and shaders",
                "preload_ready": "Preload complete — opening replay",
                "waiting_console": "Connecting to Deadlock",
                "initializing_unlocker": "Preparing camera commands",
                "checking_camera": "Checking camera support",
                "entering_editor": "Starting paused editor",
                "editing_ready": "Paused editor ready",
                "connected": "Connected — initialize in hideout",
                "unlocker_ready": "Unlocker ready — load replay",
                "loading_replay": "Replay requested — wait for loading",
                "recovering_replay": "Preparing replay",
                "replay_ready": "Replay ready",
                "failed": "Session needs attention",
                "game_closed": "Game closed",
            }
            session_label = stage_labels.get(stage, "Game connected" if connected else "Game not connected")
            if not connected and stage in ("connected", "unlocker_ready", "loading_replay", "replay_ready"):
                session_label = "Disconnected — reconnect to continue"
            _set_var(self.session_text, "Playing camera path" if self.playing else session_label)
            if self.startup_cancel is not None and not self.startup_cancel.is_set():
                _set_var(self.startup_progress, str(status.get("message") or session_label))
            _set_widget_state(self.play_replay_button,
                              "normal" if not self.busy and not self.playing else "disabled")
            self.play_replay_button.configure(
                text="Check startup again" if stage == "waiting_game_screen" else "Play replay")
            _set_widget_state(self.cancel_startup_button,
                              "normal" if self.startup_cancel is not None
                              and not self.startup_cancel.is_set() else "disabled")
            unlocker_ready = bool(status.get("unlocker_ready"))
            available = not self.busy and not self.playing
            _set_widget_state(self.speed_combo, "normal" if available else "disabled")
            _set_widget_state(self.rate_combo, "readonly" if available else "disabled")
            running = bool(status.get("game_running"))
            driver_state = "disabled" if running or self.busy else "readonly"
            _set_widget_state(self.camera_driver_combo, driver_state)
            home_driver_combo = getattr(self, "home_camera_driver_combo", None)
            if home_driver_combo is not None:
                _set_widget_state(home_driver_combo, driver_state)
            native = status.get("camera_backend") == "native" if running else self.camera_driver.get() == "Native (experimental)"
            self._set_driver_indicator(status.get("camera_backend"), running)
            _set_widget_state(self.smoothing_combo, "disabled" if native or self.playing else "readonly")
            if native and getattr(self, "_smoothing_packed", False):
                self.smoothing_row.pack_forget()
                self._smoothing_packed = False
            elif not native and not getattr(self, "_smoothing_packed", False):
                # Back in its place in Playback options: 10 px below the row above it.
                self.smoothing_row.pack(fill="x", pady=(self._px(10), 0))
                self._smoothing_packed = True
            if hasattr(self, "camera_curve_workspace"):
                recording = self.video_export.status().get("state") if self.video_export is not None else None
                self.camera_curve_workspace.set_enabled(available and recording not in ACTIVE_STATES
                    and not getattr(self, "_layer_queue", None) and not getattr(self, "_active_layer_take", None)
                    and not getattr(self, "_bone_picker_context", None) and not getattr(self, "preview_attach", False))
            else:
                self.aspect_curve.set_enabled(available)
                if hasattr(self, "rotation_curve"):
                    self.rotation_curve.set_enabled(available)
                if hasattr(self, "position_curve"):
                    self.position_curve.set_enabled(available)
            startup_buttons = (
                (self.launch_button, available),
                (self.connect_button, available and not connected and stage not in ("not_launched", "game_closed")),
                (self.initialize_button, available and connected and not unlocker_ready),
                (self.load_replay_button, available and connected and unlocker_ready and stage != "loading_replay"),
                (self.probe_button, available and connected and unlocker_ready and stage in ("loading_replay", "replay_ready", "failed")),
                (self.disconnect_button, not self.busy and connected),
            )
            for button, enabled in startup_buttons:
                _set_widget_state(button, "normal" if enabled else "disabled")
            capture_available = available and connected and stage in ("replay_ready", "editing_ready")
            for button in self.capture_buttons:
                _set_widget_state(button, "normal" if capture_available else "disabled")
            message = str(status.get("message") or "")
            if message and message != self._last_controller_message:
                self._last_controller_message = message
                self.status_text.set(message)
            if not self.busy:
                tick = status.get("tick")
                _set_var(self.busy_text, f"Replay tick {int(tick):,}" if tick is not None else "")
            if self.playing and not self.dragging:
                self._set_time(float(status.get("time", self.shot_time.get())))
        except Exception as exc:
            self._log("Status unavailable: " + str(exc))
        else:
            try:
                self._refresh_video(status)
                self._refresh_discord_presence(status)
                self._last_video_poll_error = None
            except (RuntimeError, ValueError, TypeError, OSError) as exc:
                message = "Media status unavailable: " + str(exc)
                self.video_status_text.set(message)
                if getattr(self, "_last_video_poll_error", None) != message:
                    self._last_video_poll_error = message
                    self._log(message)
            self._check_paused_memory(status)
        self._refresh_renderer_pressure()
        try:
            editor_session.poll(self)
            capture_state = "disabled" if self.native_editor_active else "normal"
            for checkbox in getattr(self, "capture_hotkey_checkboxes", ()):
                _set_widget_state(checkbox, capture_state)
            if self.native_editor_active:
                binding = self.app_settings.action_bindings.get("capture")
                _set_var(self.hotkey_enabled, binding is not None)
                _set_var(self.hotkey_label, "Native capture · " + (binding.label if binding else "Unbound"))
            else:
                _set_var(self.hotkey_label, self._capture_binding_label())
        except Exception as exc:
            self._log("Editor connection unavailable: " + str(exc))
        if getattr(self, "_dropped_logs", 0):
            dropped, self._dropped_logs = self._dropped_logs, 0
            self._log(f"Interface log backlog dropped {dropped} line(s) to stay responsive.")
        self._check_capture_listener()

    def _check_paused_memory(self, status):
        """Warn once when the paused engine keeps committing memory.

        The first user's pause-time OOM reproduced on every camera backend, so
        this is a read-only early warning, never an automatic action. It only
        reads counters for the PID Dolly launched and owns.
        """
        watch = getattr(self, "_memory_watch", None)
        if watch is None:
            return
        getter = getattr(self.controller, "game_pid", None)
        pid = getter() if callable(getter) else None
        paused = (bool(status.get("game_running")) and not status.get("playing")
                  and status.get("tick") is not None)
        warning = watch.observe(pid, paused=paused, tick=status.get("tick"))
        if warning:
            self._log(warning)
            self.status_text.set(warning)

    def _refresh_renderer_pressure(self):
        """Warn before the engine's DX11 buffer queue hits its fatal capacity.

        The native overlay reacts on its own by pausing in-game drawing; this
        only surfaces a human-readable message so a silent crash becomes a
        recoverable warning.
        """
        controller = getattr(self, "controller", None)
        getter = getattr(controller, "_native_bridge", None)
        if not callable(getter):
            return
        bridge = getter()
        latest = getattr(bridge, "latest_graphics", None)
        if bridge is None or not callable(latest):
            return
        try:
            sample = latest() or {}
        except (RuntimeError, ValueError, TypeError, OSError):
            return
        pending = sample.get("pending_count")
        if not isinstance(pending, int) or pending < 20000:
            return
        capacity = sample.get("capacity")
        capacity = capacity if isinstance(capacity, int) and capacity > 0 else 32767
        self.status_text.set(
            f"Renderer buffer queue near capacity ({pending:,}/{capacity:,}). "
            "Dolly paused its in-game drawing; stop replay skipping or playback to recover.")

    def _handle_game_exit(self, status):
        session = getattr(self.controller,'_session',None)
        if status.get('game_running'):
            self._last_game_session = session
            return
        if session is None or getattr(self,'_last_game_session',None) is not session:
            return
        if getattr(self,'_game_exit_shown',None) is not session:
            self._game_exit_shown = session
            self.playing = False
            self.preview_attach = False
            self._bone_picker_context = None
            self._attach_snap_pending = False
            self._attach_snap_started = None
            self._attach_snap_context = None
            self.native_editor_active = False
            self._native_editor_bridge = self._native_visualization_bridge = None
            self._native_visualization_cache = None
            if self.root.state() == 'iconic':
                self._restore_from_taskbar()
            if self.startup_cancel is not None:
                self.startup_cancel.set()
        if not self.busy and getattr(self,'_game_exit_cleanup',None) is not session:
            if getattr(self,'_base_capture',None) is not None:
                self._clear_layer_pipeline()
            if self._submit('Clearing closed game session',self.controller.recover_dead_session):
                self._game_exit_cleanup = session

    def _restore_from_taskbar(self):
        """Bring the minimized window back, activated as a click on its taskbar button does.

        USER-ADDITIONS UPDATE 20: Tk still treats Dolly's frameless window as override-redirect
        and would show it without activating it, so Windows restores it
        (:meth:`.window_chrome.WindowChrome.restore`); a window with the native frame deiconifies.
        """
        window = getattr(self, "window_chrome", None)
        if window is not None and window.active:
            window.restore()
        else:
            self.root.deiconify()

    def _set_driver_indicator(self, backend, running):
        """Show the camera driver actually in use, live once a session runs."""
        indicator = getattr(self, "driver_indicator", None)
        if indicator is None:
            return
        driver = getattr(self, "camera_driver", None)
        selected = driver.get() if driver is not None else "Native (experimental)"
        if running and backend in ("native", "console"):
            active = backend
        else:
            active = "native" if selected == "Native (experimental)" else "console"
        if active == "native":
            _set_var(indicator, "DRIVER · NATIVE")
            style = "Pill.TLabel"
        else:
            _set_var(indicator, "DRIVER · CONSOLE LEGACY")
            style = "PillConsole.TLabel"
        label = getattr(self, "driver_indicator_label", None)
        if label is not None and label.cget("style") != style:
            label.configure(style=style)

    def _report_operation_error(self, title, exc):
        """Deadlock's missing intro/preload gets an actionable manual load.

        Only one error dialog is allowed at a time. The native dialog runs a
        nested Tk wait, so the interface poll keeps firing while it is open;
        without this guard a burst of failures (for example a Native DOF failure
        on every slider move) would stack modal dialogs and leave the window
        disabled behind them, forcing a hard close.
        """
        if isinstance(exc, StartupAcknowledgementRequired) and not self.closed:
            # The user handles the stock game screen. This action must never
            # substitute the explicit unverified replay load for normal checks.
            self.status_text.set(str(exc))
            self.startup_progress.set(str(exc))
            self._log(f"{title}: {exc}")
        elif isinstance(exc, PreloadUnavailableError) and not self.closed:
            self._error(title, exc, offer_unverified_replay=True)
        else:
            self._error(title, exc)

    def _offer_unverified_replay(self, title, exc):
        text = str(exc) or type(exc).__name__
        self.status_text.set(text[:240])
        self._log(f"{title}: {text}")
        if messagebox.askyesno("Deadlock intro/preload not detected",
                               text + "\n\nLoad the selected replay now without the preload check?",
                               parent=self.root):
            self._load_replay()

    def _error(self, title, exc, *, offer_unverified_replay=False):
        """Show one error dialog at a time; further errors are logged only.

        Every error path funnels through here, so two modal dialogs can never
        stack. The native dialog disables the owner window while it is open; if a
        second dialog ran at the same time, the two enable/disable cycles could
        interleave and leave the main window permanently disabled (which reads as
        an unclickable, "not responding" Dolly). The dialog is also topmost, so
        it stays visible over a fullscreen game. When one is already open, or
        Dolly is closing, the message is only logged and shown in the status.
        """
        text = str(exc) or type(exc).__name__
        # Remembered first, so the footer status trace already sees an error.
        self._status_error_text = text[:240]
        self.status_text.set(text[:240])
        self._log(f"{title}: {text}")
        if self.closed:
            return
        if getattr(self, "_error_dialog_open", False):
            return
        self._error_dialog_open = True
        try:
            if offer_unverified_replay:
                self._offer_unverified_replay(title, exc)
            else:
                dialogs.show_error(self.root, title, text)
        finally:
            self._error_dialog_open = False

    def _guard(self, title, function):
        if self.busy or self.playing:
            self.status_text.set("Stop path playback and finish the current operation before editing the shot.")
            return None
        try:
            return function()
        except Exception as exc:
            self._error(title, exc)
            return None

    def _discovered(self, path):
        if path and not self.game_path.get():
            self.game_path.set(str(path))
        if self.game_path.get() and not self.replay_folder.get():
            self.replay_folder.set(str(find_replay_folder(self.game_path.get())))
        self._request_display_fonts()
        self.status_text.set("Choose a replay and press START to begin.")
        if self.replay_folder.get() and Path(self.replay_folder.get()).is_dir():
            self._refresh_replays()

    def _browse_game(self):
        path = filedialog.askopenfilename(parent=self.root, title="Find Deadlock executable", filetypes=(("Deadlock executable", "*.exe"), ("All files", "*.*")))
        if path:
            self.game_path.set(path)
            self.replay_folder.set(str(find_replay_folder(path)))
            if Path(self.replay_folder.get()).is_dir():
                self._refresh_replays()

    def _browse_demo(self):
        path = filedialog.askopenfilename(parent=self.root, title="Choose local replay", filetypes=(("Deadlock replay", "*.dem"),))
        if path:
            self.demo_path.set(path)
            self.replay_folder.set(str(Path(path).parent))

    def _repair_selected_replay(self):
        """Offer an experimental repair as a new file; leave the source alone."""
        if self.busy:
            self.status_text.set("Please wait for the current operation to finish.")
            return
        selected = self.demo_path.get().strip()
        if not selected:
            self._error("Replay repair", ValueError("Choose a replay to repair first."))
            return
        source = Path(selected)
        destination = filedialog.asksaveasfilename(
            parent=self.root, title="Save experimental repaired replay",
            initialdir=str(source.parent), initialfile=source.stem + "-repaired.dem",
            defaultextension=".dem", filetypes=(("Deadlock replay", "*.dem"),))
        if not destination:
            return
        from .replay_repair import repair_replay

        def finished(path):
            self.status_text.set("Replay copy saved. Select it to try it in Deadlock.")
            messagebox.showinfo(
                "Replay copy saved",
                "The experimental copy was saved to:\n" + str(path) +
                "\n\nThe original replay was not changed. Open the copy to test whether it plays.",
                parent=self.root)

        self._submit("Repairing replay", lambda: repair_replay(source, Path(destination)), finished)

    def _launch(self):
        game, demo = self.game_path.get().strip(), self.demo_path.get().strip()
        if not game or not demo:
            self._error("Choose game and replay", ValueError("Select the Deadlock executable and a local .dem replay first."))
            return
        protocol = "vconsole" if self.protocol.get() == "VConsole" else "netcon"
        native = self.camera_driver.get() == "Native (experimental)"
        from .graphics_profiles_ui import launch_options as graphics_launch_options
        try:
            graphics_options = graphics_launch_options(self)
        except (OSError, ValueError, RuntimeError) as exc:
            self._error("Graphics profiles", exc)
            return
        allow_mods = (self.allow_mod_overrides.get()
                      if hasattr(self, "allow_mod_overrides")
                      else bool(getattr(self.app_settings, "allow_mod_overrides", False)))
        mod_options = {"allow_mod_overrides": True} if allow_mods else {}
        if getattr(self.app_settings, "nvidia_dlss", False):
            mod_options["nvidia_dlss"] = True
        self._submit("Launching hideout", lambda: self.controller.launch(game, demo, protocol=protocol, native=native,
                     **mod_options, **graphics_options), self._session_result)

    def _session_result(self, result):
        if result is not None:
            self._log(json.dumps(result, indent=2, default=str))
        self.status_text.set("Game launched. Wait for the pre-lobby / hideout to fully load, then connect and initialize the unlocker.")

    def _connect(self):
        self._submit("Connecting to game", self.controller.connect, lambda result: self._show_result(result, "Connected. In the fully loaded pre-lobby / hideout, click Initialize unlocker in hideout."))

    def _initialize_unlocker(self):
        self._submit("Initializing unlocker in hideout", self.controller.initialize_unlocker,
                     lambda result: self._show_result(result, "Unlocker initialized. Click Load replay, then wait for the replay to finish loading."))

    def _load_replay(self):
        self._submit("Loading selected replay", self.controller.load_replay,
                     lambda result: self._show_result(result, "Replay requested. Wait for the replay to finish loading, then click Check camera support."))

    def _probe(self):
        self._submit("Checking camera support", self.controller.probe, self._probe_result)

    def _show_result(self, result, status):
        if result is not None:
            self._log(json.dumps(result, indent=2, default=str))
        self.status_text.set(status)

    def _probe_result(self, result):
        self._log(json.dumps(result, indent=2, default=str))
        message = result.get("message") if isinstance(result, dict) else None
        self.status_text.set(str(message or "Camera support check finished. Open the activity log for the results."))
        if isinstance(result, dict) and result.get("graphics_profile", {}).get("warning"):
            self.status_text.set(result["graphics_profile"]["warning"])
        self._offer_replay_tick_rate(result)

    def _read_standard_aspect(self):
        raw = self.standard_aspect.get().strip()
        if raw in ASPECT_PRESETS:
            value = ASPECT_PRESETS[raw]
        elif ":" in raw:
            parts = raw.split(":")
            if len(parts) != 2:
                raise ValueError("Normal aspect must be a ratio such as 16:9 or a number such as 1.77778.")
            width, height = (_finite(part, "Normal aspect") for part in parts)
            if width <= 0 or height <= 0:
                raise ValueError("Normal aspect dimensions must be greater than zero.")
            value = width / height
        else:
            value = _finite(raw, "Normal aspect")
        if not .5 <= value <= 4:
            raise ValueError("Normal aspect must be between 0.5 and 4.")
        return value

    def _refresh_curve(self):
        if not any(hasattr(self, name) for name in ("aspect_curve", "rotation_curve", "position_curve")):
            return
        selected_index = self._selection_index(self.camera_tree)
        shot_time = getattr(self, "shot_time", None)
        current_time = float(shot_time.get()) if shot_time is not None else 0.0
        if hasattr(self, "camera_curve_workspace"):
            self.camera_curve_workspace.set_project(self.project, selected_index, current_time)
            return
        if hasattr(self, "position_curve"):
            self.position_curve.set_project(self.project, selected_index=selected_index,
                                            current_time=current_time)
        if hasattr(self, "rotation_curve"):
            self.rotation_curve.set_project(self.project, selected_index=selected_index,
                                            current_time=current_time)
        if hasattr(self, "aspect_curve"):
            self.aspect_curve.set_project(self.project, selected_index=selected_index,
                                          current_time=current_time)

    def _preview_camera_curve(self, project, gesture=None):
        """Offer a temporary path guide without committing or moving the view."""
        from dolly import curve_preview
        curve_preview.offer_desktop(self, project, gesture)

    def _select_curve_key(self, index):
        if self.busy or self.playing or not 0 <= index < len(self.project.keyframes):
            return
        self.camera_tree.selection_set(str(index))
        self.camera_tree.see(str(index))
        self._select_key()

    def _change_curve_key(self, index, time, value):
        def operation():
            if not 0 <= index < len(self.project.keyframes):
                raise ValueError("That camera is no longer present in this shot.")
            # The framing graph changes framing only; replay timing is edited
            # explicitly in the inspector, never incidentally by a graph drag.
            keys = copy.deepcopy(self.project.keyframes)
            key = keys[index]
            key.aspect_ratio = _finite(str(value), "Aspect ratio")
            self._commit_camera(keys, key.time)
            self.status_text.set(f"Camera {index + 1} framing set to {key.aspect_ratio:.4f}. Preview to check the view.")
        self._guard("Edit framing curve", operation)
        self._refresh_curve()

    def _change_unified_curve(self, index, channel, value):
        """Admission shared by the desktop lanes; keep ordinary edit/history paths."""
        from dolly.curve_workspace import LABELS, valid_value
        from dolly.path import channel_value
        if not self._shot_edit_ready():
            self._refresh_curve()
            return
        if (channel not in LABELS or not isinstance(index, int) or isinstance(index, bool)
                or not 0 <= index < len(self.project.keyframes) or not valid_value(channel, value)):
            raise ValueError("Choose an existing camera and a valid curve value.")
        key = self.project.keyframes[index]
        if key.source != "free":
            raise ValueError("Attached cameras follow their target. Edit attachment offsets instead.")
        if value == channel_value(key, channel):
            return
        previous_group = getattr(self, "_history_edit_group", None)
        self._history_edit_group = None
        try:
            if channel in POSITION_CHANNELS:
                self._change_position_curve(index, channel, value)
            elif channel in CURVE_CHANNELS:
                self._change_rotation_curve(index, channel, value)
            else:
                self._change_curve_key(index, key.time, value)
        finally:
            self._history_edit_group = previous_group

    def _change_camera_tangent(self, index, channel, mode, incoming=None, outgoing=None):
        """Commit one XYZ handle gesture through the normal shot/history path."""
        if not self._shot_edit_ready():
            self._refresh_curve()
            return False

        def operation():
            if (not isinstance(index, int) or isinstance(index, bool)
                    or not 0 <= index < len(self.project.keyframes)
                    or channel not in POSITION_CHANNELS):
                raise ValueError("Choose an existing camera and X, Y or Z.")
            if self.project.interpolation != "smooth":
                raise ValueError("Arrival and departure handles are editable in Smooth interpolation.")
            key = self.project.keyframes[index]
            if key.source != "free":
                raise ValueError("Attached cameras follow their target. Edit a free camera's handles.")
            if self._selection_index(self.camera_tree) != index:
                raise ValueError("The selected camera changed. Select its handles again.")
            candidate = copy.deepcopy(self.project)
            if not candidate.set_camera_tangent(index, channel, mode,
                                                 incoming=incoming, outgoing=outgoing):
                return False
            candidate.validate()
            previous_group = getattr(self, "_history_edit_group", None)
            self._history_edit_group = None
            try:
                self._commit_project(candidate, key.time)
            finally:
                self._history_edit_group = previous_group
            self.status_text.set(
                f"Camera {index + 1:02d} {channel.upper()} arrival/departure: {mode.title()}. "
                "Preview to check the move.")
            return True

        try:
            return self._guard("Edit arrival/departure", operation)
        finally:
            self._refresh_curve()

    def _change_position_curve(self, index, channel, value):
        """Commit one authored world-coordinate edit without retiming or seeking."""
        if not self._shot_edit_ready():
            self._refresh_curve()
            return

        def operation():
            if channel not in POSITION_CHANNELS:
                raise ValueError("Choose X, Y or Z for the position graph.")
            if (not isinstance(index, int) or isinstance(index, bool)
                    or not 0 <= index < len(self.project.keyframes)):
                raise ValueError("That camera is no longer present in this shot.")
            key = self.project.keyframes[index]
            if key.source != "free":
                raise ValueError("Attached cameras follow their target. Edit a free-camera key's position.")
            position = _finite(str(value), f"Camera {channel.upper()} position")
            if abs(position) > POSITION_LIMIT:
                raise ValueError("Camera position must be between -100,000,000 and 100,000,000 world units.")
            if position == getattr(key, channel):
                return
            keys = copy.deepcopy(self.project.keyframes)
            setattr(keys[index], channel, position)
            previous_group = getattr(self, "_history_edit_group", None)
            self._history_edit_group = None
            try:
                self._commit_camera(keys, key.time)
            finally:
                self._history_edit_group = previous_group
            self.status_text.set(f"Camera {index + 1} {channel.upper()} set to {position:g} world units.")

        self._guard("Edit position curve", operation)
        self._refresh_curve()

    def _change_rotation_curve(self, index, channel, value):
        if channel not in CURVE_CHANNELS:
            return

        def operation():
            if not 0 <= index < len(self.project.keyframes):
                raise ValueError("That camera is no longer present in this shot.")
            keys = copy.deepcopy(self.project.keyframes)
            key = keys[index]
            degrees = _finite(str(value), f"Rotation curve {channel}")
            setattr(key, "curve_" + channel, degrees)
            self._commit_camera(keys, key.time)
            self.status_text.set(f"Camera {index + 1} {channel} curve set to {degrees:.2f}°. "
                                 "Preview to check the view.")

        self._guard("Edit rotation curve", operation)
        self._refresh_curve()

    def _reset_rotation_curve(self, channel):
        if channel not in CURVE_CHANNELS:
            return
        if not self._shot_edit_ready():
            self._refresh_curve()
            return

        def operation():
            keys, changed = shot_commands.reset_rotation(self.project.keyframes, channel)
            if changed:
                self._commit_camera(keys)
                self.status_text.set(f"Reset the {channel} curve to the camera keys.")

        self._guard("Reset rotation curve", operation)
        self._refresh_curve()

    def _redraw_curves(self):
        """Coalesce graph redraw bursts (wheel/pan/edits) into one idle pass."""
        if getattr(self, "_curves_redraw_pending", False):
            return
        schedule = getattr(self.root, "after_idle", None)
        if not callable(schedule):
            self._redraw_curves_now()
            return
        self._curves_redraw_pending = True
        schedule(self._redraw_curves_now)

    def _redraw_curves_now(self):
        self._curves_redraw_pending = False
        if hasattr(self, "camera_curve_workspace"):
            self.camera_curve_workspace.redraw()
            return
        for widget in (getattr(self, "position_curve", None), getattr(self, "rotation_curve", None),
                       getattr(self, "aspect_curve", None)):
            if widget is not None:
                widget._redraw()

    def _reset_aspect(self):
        def operation():
            index = self._selection_index(self.camera_tree)
            if index is None:
                raise ValueError("Select a camera to reset its framing.")
            self._sync_options()
            keys = shot_commands.reset_aspect(self.project, index)
            self._commit_camera(keys, keys[index].time)
            self.status_text.set("Selected camera reset to normal aspect.")
        self._guard("Reset framing", operation)

    def _apply_options(self):
        self._guard("Shot settings", self._sync_options)

    def _sync_options(self):
        def numeric(variable, original, label):
            raw = variable.get()
            value = _finite(raw, label)
            # A refreshed text field displays a rounded number. Merely syncing
            # that field must not introduce an edit or erase Redo after Undo.
            return original if raw.strip() == _number(original) else value
        tick = numeric(self.start_tick, self.project.start_tick, "Shot start tick")
        rate = numeric(self.tick_rate, self.project.tick_rate, "Ticks per second")
        if tick < 0 or int(tick) != tick:
            raise ValueError("Shot start tick must be a non-negative whole number.")
        if rate <= 0:
            raise ValueError("Ticks per second must be greater than zero.")
        confetti_enabled = getattr(self, "confetti_enabled", None)
        confetti_height = getattr(self, "confetti_spawn_height", None)
        confetti_despawn = getattr(self, "confetti_despawn_on_ground", None)
        preset_variable = getattr(self, "particles_preset", None)
        intensity_variable = getattr(self, "particles_intensity", None)
        aspect = self._read_standard_aspect()
        aspect_display = next((label for label, value in ASPECT_PRESETS.items()
                               if abs(value - self.project.standard_aspect) < 1e-7),
                              _number(self.project.standard_aspect))
        if self.standard_aspect.get().strip() == aspect_display:
            aspect = self.project.standard_aspect
        values = dict(start_tick=int(tick), tick_rate=rate, interpolation=self.interpolation.get(),
                      rotation_mode=self.rotation.get(), standard_aspect=aspect,
                      lens_interpolation=self.lens_interpolation.get(),
                      confetti_enabled=(bool(confetti_enabled.get()) if confetti_enabled is not None
                                        else self.project.confetti_enabled),
                      confetti_spawn_height=(numeric(confetti_height, self.project.confetti_spawn_height, "Particle spawn height")
                                             if confetti_height is not None else self.project.confetti_spawn_height),
                      confetti_despawn_on_ground=(bool(confetti_despawn.get()) if confetti_despawn is not None
                                                  else self.project.confetti_despawn_on_ground),
                      particles_preset=(LABEL_TO_ID.get(preset_variable.get(),
                                                        self.project.particles_preset)
                                        if preset_variable is not None
                                        else self.project.particles_preset),
                      particles_intensity=(numeric(intensity_variable, self.project.particles_intensity, "Particle intensity")
                                           if intensity_variable is not None
                                           else self.project.particles_intensity))
        changed = any(getattr(self.project, name) != value for name, value in values.items())
        if changed:
            candidate = copy.deepcopy(self.project)
            for name, value in values.items():
                setattr(candidate, name, value)
            # Settings are validated even before the first camera is captured.
            candidate.validate()
            self.project = candidate
            self._mark_dirty()
            self._refresh_curve()
        self.controller.standard_aspect = self.project.standard_aspect
        self.status_text.set("Shot timing and framing are ready.")

    def _snapshot(self):
        still = screenshot_ui.snapshot(self)
        if still is not None:
            return still
        self._sync_options()
        candidate = copy.deepcopy(self.project)
        candidate.validate()
        if not candidate.keyframes:
            raise ValueError("Capture or add at least one camera keyframe first.")
        return candidate

    def _selection_index(self, tree):
        return shot_commands.selection_index(tree.selection())

    def _read_key(self):
        values = {field: _finite(self.key_vars[field].get(), label) for field, label in zip(FIELDS, FIELD_LABELS)}
        return Keyframe(**values)

    def _commit_camera(self, keys, select_time=None):
        self._commit_project(shot_commands.commit_camera(self.project, keys), select_time)

    def _commit_project(self, candidate, select_time=None):
        self.project = candidate
        self._mark_dirty()
        self._refresh_keys(select_time)
        history = getattr(self, "shot_history", None)
        if history is not None:
            history.select(select_time)

    def _add_key(self):
        def operation():
            key = self._read_key()
            if any(existing.time == key.time for existing in self.project.keyframes):
                raise ValueError("A keyframe already exists at that shot time. Select it and use Update view.")
            self._commit_camera([*self.project.keyframes, key], key.time)
        self._guard("Add keyframe", operation)

    def _update_key(self):
        def operation():
            index = self._selection_index(self.camera_tree)
            if index is None:
                raise ValueError("Select a camera keyframe to update.")
            key = self._read_key()
            existing = self.project.keyframes[index]
            key.fov = existing.fov  # Legacy metadata stays intact.
            key.lens_scale = existing.lens_scale
            key.source = existing.source
            key.source_blend = existing.source_blend
            key.attach = copy.deepcopy(existing.attach)
            key.curve_pitch = existing.curve_pitch
            key.curve_yaw = existing.curve_yaw
            key.curve_roll = existing.curve_roll
            key.tangents = copy.deepcopy(existing.tangents)
            keys = list(self.project.keyframes)
            if any(i != index and existing.time == key.time for i, existing in enumerate(keys)):
                raise ValueError("Another keyframe already exists at that shot time.")
            keys[index] = key
            self._commit_camera(keys, key.time)
        self._guard("Update keyframe", operation)

    def _delete_key(self):
        if not self._shot_edit_ready():
            return
        index = self._selection_index(self.camera_tree)
        if index is not None and 0 <= index < len(self.project.keyframes):
            def operation():
                candidate = shot_commands.delete_camera(self.project, index)
                rebased = index == 0 and bool(candidate.keyframes)
                selected_time = (candidate.keyframes[min(index, len(candidate.keyframes) - 1)].time
                                 if candidate.keyframes else None)
                self._commit_project(candidate, selected_time)
                if rebased:
                    # Deleting the leading camera moved the shot start to the
                    # new first camera; reflect the new anchor and shifted
                    # effect times.
                    self.start_tick.set(_number(candidate.start_tick))
                    self._refresh_tracks()
                if not candidate.keyframes:
                    self._set_time(0)
                    self.preview_attach = False
                self.status_text.set(
                    f"Camera {index + 1:02d} deleted. The shot now starts at the new first camera."
                    if rebased else f"Camera {index + 1:02d} deleted. Undo restores it.")
            self._guard("Delete keyframe", operation)

    def _select_key(self, _event=None):
        index = self._selection_index(self.camera_tree)
        if index is not None and index < len(self.project.keyframes):
            key = self.project.keyframes[index]
            history = getattr(self, "shot_history", None)
            if history is not None:
                history.select(key.time)
            for field in FIELDS:
                self.key_vars[field].set(_number(getattr(key, field)))
            self._set_time(key.time)
            if hasattr(self, "selected_text"):
                self.selected_text.set(f"Camera {index + 1:02d}")
            self._refresh_curve()
            self._refresh_attach_card(key)

    def _refresh_attach_card(self, key):
        if not hasattr(self, "attach_enabled"):
            return
        self.attach_enabled.set(key.source == "attach")
        self.source_blend.set(_number(key.source_blend))
        attach = key.attach if isinstance(key.attach, AttachKey) else None
        if attach is None:
            for variable in self.attach_vars.values():
                variable.set("0")
            self.attach_smoothing.set("0")
            self.attach_lock_vertical_aim.set(False)
            self.attach_hide.set(True)
            self.attach_clearance.set("Exact offset")
            self.attach_point.set("Eyes")
            self.attach_bone.set("head")
            self.attach_bone_combo.configure(state="disabled")
            return
        for variable, value in zip(self.attach_vars.values(), attach.offset):
            variable.set(_number(value))
        self.attach_smoothing.set(_number(attach.smoothing))
        self.attach_lock_vertical_aim.set(attach.lock_vertical_aim)
        self.attach_hide.set(bool(attach.hide_body))
        self.attach_clearance.set("Automatic clearance" if attach.clearance_mode == "auto" else "Exact offset")
        self.attach_point.set(attach.point.title())
        self.attach_bone.set(attach.bone or "head")
        self.attach_bone_combo.configure(state="normal" if attach.point == "bone" else "disabled")
        for label, player in self._attach_players.items():
            if (player.get("handle") == attach.handle
                    and str(player.get("model_path") or "") == attach.model):
                self.attach_player.set(label)
                break

    def _attach_roster_changed(self, roster):
        """Update the live player picker from the native roster block."""
        if not hasattr(self, "attach_player_combo"):
            return
        players = roster.get("players", []) if isinstance(roster, dict) else []
        available = bool(roster.get("available")) if isinstance(roster, dict) else False
        self._attach_players = {}
        values = []
        for player in players:
            if not isinstance(player, dict):
                continue
            path = str(player.get("model_path") or "")
            label = "%s · %d" % (attach_camera.hero_name(path),
                                 int(player.get("entity_index", 0) or 0))
            self._attach_players[label] = player
            values.append(label)
        if not available:
            values = ["(live schema offsets unavailable)"]
        elif not values:
            values = ["(no players in the loaded replay)"]
        if list(self.attach_player_combo.cget("values")) != values:
            current = self.attach_player.get()
            self.attach_player_combo.configure(values=values)
            self.attach_player.set(current if current in values else values[0])
        if not available:
            self.attach_note.configure(text="The session has not published attach offsets yet.")
        elif players:
            self.attach_note.configure(text="Live roster: %d player(s)." % len(players))
        else:
            self.attach_note.configure(text="No players in the loaded replay yet.")

    def _attach_bones_changed(self, bones):
        if not hasattr(self, "attach_bone_combo"):
            return
        player = self._attach_players.get(self.attach_player.get())
        from .native_effects import model_token
        matches = (isinstance(bones, dict) and player and
                   bones.get("handle") == player.get("handle") and
                   bones.get("model") == model_token(player.get("model_path", "")))
        self.attach_bone_combo.configure(values=bones.get("names", ()) if matches else ())

    def _attach_result_changed(self, result):
        """Apply native snap / attached-fly offsets to the selected attach key."""
        preview = bool(getattr(self, "preview_attach", False))
        snap_pending = bool(getattr(self, "_attach_snap_pending", False))
        if not (preview or snap_pending) or (preview and snap_pending):
            return
        index = self._selection_index(self.camera_tree)
        if index is None or not 0 <= index < len(self.project.keyframes):
            return
        key = self.project.keyframes[index]
        if key.source != "attach" or not isinstance(key.attach, AttachKey):
            return
        from .native_effects import model_token
        bridge = self.controller._native_bridge()
        if snap_pending and not editor_session.snap_is_current(self, bridge):
            editor_session.cancel_snap(self, "Snap cancelled because the selected camera changed.")
            return
        if (not bridge or result.get("config_sequence") != bridge._editor_attach_sequence or
                result.get("handle") != key.attach.handle or
                (key.attach.entity_id and result.get("entity_index") != key.attach.entity_id) or
                result.get("model") != model_token(key.attach.model) or
                result.get("point") != ("eyes", "weapon", "bone").index(key.attach.point)):
            return
        self._attach_snap_pending = False
        self._attach_snap_started = None
        self._attach_snap_context = None
        offsets = tuple(float(value) for value in result.get("offset") or ())
        if len(offsets) != 6:
            return
        if snap_pending:
            self.status_text.set("Snap complete. Choose Attach here to preview.")
        if offsets == tuple(key.attach.offset) and (not snap_pending or key.attach.orientation_mode == "rigid"):
            return
        candidate = copy.deepcopy(self.project)
        candidate.keyframes[index].attach.offset = offsets
        if snap_pending:
            candidate.keyframes[index].attach.orientation_mode = "rigid"
        candidate.validate()
        self.project = candidate
        self._mark_dirty()
        self._refresh_attach_card(candidate.keyframes[index])

    def _apply_attach(self, apply_all):
        def operation():
            index = self._selection_index(self.camera_tree)
            if index is None:
                raise ValueError("Select a camera keyframe first.")
            candidate = copy.deepcopy(self.project)
            targets = range(len(candidate.keyframes)) if apply_all else (index,)
            attach = None
            if self.attach_enabled.get():
                player = self._attach_players.get(self.attach_player.get())
                if player is None:
                    raise ValueError("Choose a live player from the roster first.")
                model_path = str(player.get("model_path") or "")
                if not model_path:
                    raise ValueError("The selected player has no readable model identity yet.")
                offsets = tuple(_finite(self.attach_vars[name].get(), "Attach offset")
                                for name in ("x", "y", "z", "pitch", "yaw", "roll"))
                selected_attach = candidate.keyframes[index].attach
                lock_vertical_aim = bool(self.attach_lock_vertical_aim.get())
                changing_lock = any(
                    isinstance(candidate.keyframes[target].attach, AttachKey) and
                    candidate.keyframes[target].attach.lock_vertical_aim != lock_vertical_aim
                    for target in targets)
                if changing_lock and (getattr(self, "_attach_snap_pending", False) or
                                      not self._shot_edit_ready()):
                    raise ValueError("Detach and finish Snap or recording before changing vertical aim lock.")
                attach = AttachKey(handle=int(player.get("handle", 0)),
                                   entity_id=int(player.get("entity_index", 0) or 0),
                                   model=model_path,
                                   point=self.attach_point.get().lower(),
                                   bone=self.attach_bone.get().strip() if self.attach_point.get() == "Bone" else "",
                                   offset=offsets,
                                   smoothing=_finite(self.attach_smoothing.get(), "Attach smoothing"),
                                   hide_body=bool(self.attach_hide.get()),
                                   clearance_mode="auto" if self.attach_clearance.get() == "Automatic clearance" else "exact",
                                   orientation_mode=selected_attach.orientation_mode
                                   if isinstance(selected_attach, AttachKey) else "legacy",
                                   lock_vertical_aim=lock_vertical_aim)
            for target in targets:
                candidate.keyframes[target].source_blend = float(self.source_blend.get())
                candidate.keyframes[target].source = "attach" if attach else "free"
                candidate.keyframes[target].attach = copy.deepcopy(attach)
            candidate.validate()
            self.project = candidate
            self._mark_dirty()
            self._refresh_keys(self.project.keyframes[index].time)
        self._guard("Apply attach camera", operation)

    def _publish_editor(self):
        """Publish desktop editor state to the native panel; Tk updates itself."""
        editor_session.configure(self)

    def _open_bone_picker(self):
        from .bone_picker import open_picker, start_attached_view
        def open_or_start():
            if self.project.keyframes:
                return open_picker(self, publish=self._publish_editor)
            player = self._attach_players.get(self.attach_player.get())
            if not player:
                raise ValueError("Choose a player before opening Bone Picker.")
            def target(key):
                key.source = "attach"
                key.attach = AttachKey(handle=int(player.get("handle", 0)),
                    entity_id=int(player.get("entity_index", 0) or 0),
                    model=str(player.get("model_path") or ""))
            start_attached_view(self, target, publish=self._publish_editor)
        self._guard("Bone Picker", open_or_start)

    def _cancel_bone_picker(self):
        from .bone_picker import close_picker
        self._guard("Bone Picker", lambda: close_picker(self, publish=self._publish_editor))

    def _update_export_camera_note(self):
        label = getattr(self, "export_camera_note", None)
        if label is None:
            return
        if hasattr(self, "video_pov_controls"):
            single_attach = len(self.project.keyframes) == 1 and self.project.keyframes[0].source == "attach"
            if self.video_source.get() == "Player POV" or single_attach:
                self.video_pov_controls.pack(fill="x", before=self.video_camera_options)
            else:
                self.video_pov_controls.pack_forget()
        if hasattr(self, "video_source") and self.video_source.get() == "Player POV":
            label.configure(text="Camera: Deadlock's selected spectator view. The saved camera path is unchanged.")
        else:
            label.configure(text="Camera: " + attach_camera.camera_summary(self.project))

    def _load_app_settings(self):
        try:
            settings = load_settings()
            for warning in settings.migration_warnings:
                self._enqueue_log(warning)
            if settings.migration_warnings:
                self.root.after(300, lambda text="\n\n".join(settings.migration_warnings): messagebox.showinfo("Keybind settings", text, parent=self.root))
            return settings
        except (ValueError, OSError) as exc:
            self._enqueue_log(f"Preferences could not be loaded; using defaults. {exc}")
            self.root.after(200, lambda detail=str(exc): messagebox.showwarning(
                "Dolly preferences", "Dolly could not load your preferences, so this launch uses defaults (capture: Ctrl+Alt+K).\n"
                "Open Controls to review and save your shortcuts.\n\n" + detail, parent=self.root))
            return AppSettings()

    def _capture_binding_label(self):
        if self.app_settings.action_bindings.get("capture") is None:
            return "In-game capture · Unbound"
        return "In-game capture · " + self.capture_binding.label

    def _show_keybinds(self, action="capture"):
        self._select_page(self.controls_tab)
        self.controls_view.reveal_action(action)

    def _open_capture_binding(self):
        self._show_keybinds("capture")

    def _check_capture_listener(self):
        """Reflect an unexpected input-worker exit once, without a modal loop."""
        helper = self.capture_hotkey
        if (self.closed or self.busy or not self.hotkey_enabled.get()
                or helper is None or helper.running):
            return
        self.capture_generation += 1
        self.hotkey_enabled.set(False)
        detail = helper.last_error or "The input listener stopped."
        message = f"In-game capture stopped: {detail} Enable it again to retry."
        self.status_text.set(message[:240])
        self._log(message)
        LOG.warning(message)
        # Retain the helper so the next enable or shutdown can finish cleanup.

    def _handle_capture_hotkey(self, generation=None):
        # A queued press from a disabled or replaced listener must never become
        # a later camera capture after another worker operation completes.
        if (self.closed or not self.hotkey_enabled.get()
                or generation != self.capture_generation
                or self.capture_hotkey is None or not self.capture_hotkey.running
                or not self.capture_hotkey.is_game_focused()):
            return
        if not self.busy and not self.playing and self.root.grab_current() is None:
            self.capture_here()
        else:
            self.status_text.set("Capture shortcut ignored while Dolly is busy. Finish the current operation, then try again.")

    def _change_capture_listener(self, binding, enabled, *, persist=False, on_applied=None):
        if getattr(self, "native_editor_active", False):
            if persist:
                try:
                    settings = self.app_settings.with_capture_binding(binding)
                except ValueError as exc:
                    self._error("Capture binding", exc)
                    return False
                submitted = self._persist_preferences(settings, "Native capture binding saved.")
                if submitted and on_applied:
                    on_applied()
                return submitted
            self.hotkey_enabled.set(bool(self.app_settings.action_bindings.get("capture")))
            self.status_text.set("Native capture uses your Controls settings while the in-game editor has control.")
            return False
        if self.closed or self.busy:
            self.hotkey_enabled.set(bool(self.capture_hotkey and self.capture_hotkey.running))
            self.status_text.set("Finish the current operation before changing the capture shortcut.")
            return False
        previous_settings = self.app_settings
        old_helper = self.capture_hotkey
        self.capture_generation += 1
        generation = self.capture_generation
        # Invalidate all queued presses before the worker stops the listener.
        self.hotkey_enabled.set(False)
        def change():
            settings = previous_settings
            saved = False
            if old_helper is not None:
                try:
                    old_helper.stop()
                except Exception as exc:
                    return settings, old_helper, False, exc, saved
            if persist:
                try:
                    settings = previous_settings.with_capture_binding(binding)
                    save_settings(settings)
                    saved = True
                except Exception as exc:
                    return previous_settings, None, False, exc, saved
            if not enabled:
                return settings, None, True, None, saved
            helper = None
            try:
                helper = CaptureHotkey(
                    lambda: self.events.put(("capture_hotkey", "", generation)),
                    self.controller.game_pid, binding=binding)
                helper.start()
            except Exception as exc:
                if helper is not None:
                    try:
                        helper.stop()
                    except Exception:
                        LOG.exception("Capture shortcut cleanup failed after enable error")
                        return settings, helper, False, exc, saved
                return settings, None, False, exc, saved
            return settings, helper, True, None, saved
        def changed(result):
            settings, helper, succeeded, error, saved = result
            self.app_settings = settings
            self.capture_binding = settings.capture_binding
            self.hotkey_label.set(self._capture_binding_label())
            self._refresh_bindings()
            self.capture_hotkey = helper
            self.hotkey_enabled.set(bool(succeeded and enabled))
            if on_applied:
                on_applied()
            if error is not None:
                prefix = "Binding saved, but in-game capture is disabled. " if saved else "In-game capture is disabled. "
                self._error("Capture binding", RuntimeError(prefix + str(error)))
            elif enabled:
                self.status_text.set(binding.label + " captures a view while your Dolly-launched Deadlock window is focused.")
            else:
                self.status_text.set("Capture binding saved. Enable in-game capture to use it." if persist else "In-game capture shortcut disabled.")
        return self._submit("Saving capture binding" if persist else ("Enabling capture shortcut" if enabled else "Disabling capture shortcut"), change, changed)

    def _apply_capture_binding(self, binding, on_applied=None):
        if self.playing:
            self.status_text.set("Stop path playback before changing the capture binding.")
            return False
        return self._change_capture_listener(binding, self.hotkey_enabled.get(), persist=True, on_applied=on_applied)

    def _toggle_capture_hotkey(self):
        if self.app_settings.action_bindings.get("capture") is None:
            self.hotkey_enabled.set(False)
            self.status_text.set("Assign a Capture camera shortcut in Controls first.")
            return
        self._change_capture_listener(self.capture_binding, self.hotkey_enabled.get())

    def capture_here(self):
        """UI-thread entry point shared by the capture button and global hotkey."""
        self._capture_view("append" if self.project.keyframes else "start")

    def _capture(self):
        self.capture_here()

    def _start_path_here(self):
        self._capture_view("start")

    def _replace_camera_here(self):
        self._capture_view("replace")

    @staticmethod
    def _valid_tick_rate(value):
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and value > 0)

    def _resolve_replay_tick_rate(self, candidate, *, adopt=False):
        """Use the loaded replay's own ticks/second for replay-timed capture.

        The replay file's CDemoFileInfo is authoritative (32 and 64 tick
        recordings are both current). A camera-less shot adopts the rate
        silently; an existing path is retimed with confirmation so two time
        bases never mix. Declining blocks the capture instead of writing a
        camera against the wrong clock. Returns the rate the capture must use.
        """
        try:
            detected = self.controller.replay_tick_rate()
        except Exception:
            LOG.exception("Could not read the loaded replay's tick rate")
            detected = None
        if not self._valid_tick_rate(detected) or tick_rates_match(detected, candidate.tick_rate):
            return float(candidate.tick_rate)
        advice = tick_rate_advice(detected, candidate.tick_rate)
        if not adopt and candidate.keyframes:
            key = (round(float(detected), 3), round(float(candidate.tick_rate), 3))
            if key not in self._tick_rate_prompted:
                self._tick_rate_prompted.add(key)
                if messagebox.askyesno("Replay tick rate",
                                       advice + "\n\nRetime this shot to the replay and continue?",
                                       parent=self.root):
                    adopt = True
            if not adopt:
                raise ValueError(advice)
        candidate.retime(float(detected))
        self.status_text.set(f"This replay runs at {float(detected):.3g} ticks/second; the shot now uses it.")
        return float(candidate.tick_rate)

    def _offer_replay_tick_rate(self, result=None):
        """Check the loaded replay against the open shot and offer a retime."""
        detected = result.get("replay_tick_rate") if isinstance(result, dict) else None
        if not self._valid_tick_rate(detected):
            try:
                detected = self.controller.replay_tick_rate()
            except Exception:
                LOG.exception("Could not read the loaded replay's tick rate")
                return
        if not self._valid_tick_rate(detected) or tick_rates_match(detected, self.project.tick_rate):
            return
        candidate = copy.deepcopy(self.project)
        try:
            self._resolve_replay_tick_rate(candidate, adopt=not candidate.keyframes)
        except ValueError as exc:
            self.status_text.set(str(exc))
            return
        if tick_rates_match(candidate.tick_rate, self.project.tick_rate):
            return
        self.project = candidate
        self._refresh_project()
        self._mark_dirty()
        self._set_time(0)
        self.status_text.set(f"Shot retimed to this replay's {float(candidate.tick_rate):.3g} ticks/second. "
                             "Camera and effect times now match the replay.")

    def _capture_view(self, action, native_snapshot=None, *, replacement_confirmed=False):
        if getattr(self, "_bone_picker_context", None):
            self.status_text.set("Finish or cancel Bone Picker before capturing a camera.")
            return
        if action == "append" and not self.project.keyframes:
            action = "start"
        def operation():
            self._sync_options()
            original = self.project
            candidate = copy.deepcopy(original)
            replay_timing = self.capture_mode.get() == "Replay timing"
            selected = self._selection_index(self.camera_tree) if action == "replace" else None
            if action == "replace" and selected is None:
                raise ValueError("Select the view you want to replace, then frame its new position in the game.")
            capture_rate = candidate.tick_rate
            pause_after_capture = self.app_settings.pause_after_capture
            if replay_timing and action != "replace":
                capture_rate = self._resolve_replay_tick_rate(candidate, adopt=(action == "start"))
            if action == "start":
                time = 0.0
            elif action == "replace":
                time = candidate.keyframes[selected].time
            else:
                seconds = _finite(self.segment_seconds.get(), "Seconds between views") if not replay_timing else 0.0
                if not replay_timing and seconds <= 0:
                    raise ValueError("Seconds between views must be greater than zero.")
                time = candidate.keyframes[-1].time + seconds
            def capture():
                self.controller.standard_aspect = candidate.standard_aspect
                if native_snapshot is not None:
                    key, tick = self.controller.capture_native_snapshot(
                        native_snapshot, time=time, pause=pause_after_capture)
                    if replay_timing and action != "replace":
                        key.time = 0.0 if action == "start" else (tick - candidate.start_tick) / capture_rate
                    return key, tick
                if replay_timing and action != "replace":
                    start_tick = None if action == "start" else candidate.start_tick
                    key = self.controller.capture_at_replay(start_tick, capture_rate)
                else:
                    key = self.controller.capture(time)
                return key, self.controller.status().get("tick")
            def captured(result):
                key, tick = result
                if self.project is not original:
                    raise ValueError("The shot changed during capture. Capture again to add this view to the current shot.")
                if action == "start":
                    if candidate.keyframes and not replacement_confirmed and not messagebox.askyesno(
                            "Start a new camera path?",
                            "Replace the current camera path with this captured view? Existing lens and depth-of-field tracks will be kept.",
                            parent=self.root):
                        return
                shot_commands.finish_capture(candidate, action, key, tick, selected)
                self.project = candidate
                self.start_tick.set(_number(candidate.start_tick))
                self.tick_rate.set(_number(candidate.tick_rate))
                self.interpolation.set(candidate.interpolation)
                self._mark_dirty()
                self._refresh_keys(key.time)
                self._set_time(key.time)
                if not hasattr(self, "full_editor") or self.full_editor.get():
                    self._select_page(self.camera_tab)
                verb = "Replaced" if action == "replace" else "Captured"
                self.status_text.set(f"{verb} view {candidate.keyframes.index(key) + 1} at {key.time:g} seconds. "
                                     "Fly to the next position and use Add camera here.")
                if action == "start" and tick is None:
                    self.status_text.set("Starting view captured, but the replay start tick is unavailable. "
                                         "Export diagnostics before normal playback, or select Frozen preview for a paused-scene path.")
            self._submit("Capturing free-camera view", capture, captured)
        self._guard("Capture view", operation)

    def _use_current_tick(self):
        if self.busy or self.playing:
            self.status_text.set("Stop playback and finish the current operation before changing shot timing.")
            return
        self._submit("Reading replay tick", self.controller.current_tick, self._set_start_tick)

    def _retime_to_replay(self):
        """Explicit one-step repair for a shot authored at another replay rate."""
        if self.busy or self.playing:
            self.status_text.set("Stop playback and finish the current operation before retiming the shot.")
            return
        detected = None
        try:
            detected = self.controller.replay_tick_rate()
        except Exception:
            LOG.exception("Could not read the loaded replay's tick rate")
        if not self._valid_tick_rate(detected):
            self.status_text.set("Load a replay through Dolly first; its tick rate is read from the replay file.")
            return
        if tick_rates_match(detected, self.project.tick_rate):
            self.status_text.set(f"This shot already matches the replay's {float(detected):.3g} ticks/second.")
            return
        if not messagebox.askyesno(
                "Retime shot",
                f"Replay: {float(detected):.3g} ticks/second\nShot: {float(self.project.tick_rate):.3g} ticks/second\n\n"
                "Retime this shot so its camera and effect times match the replay?",
                parent=self.root):
            return
        candidate = copy.deepcopy(self.project)
        candidate.retime(float(detected))
        self.project = candidate
        self.tick_rate.set(_number(candidate.tick_rate))
        self._refresh_project()
        self._mark_dirty()
        self._set_time(0)
        self.status_text.set(f"Shot retimed to this replay's {float(detected):.3g} ticks/second. "
                             "Camera and effect times now match the replay.")

    def _set_start_tick(self, tick):
        self.start_tick.set(str(int(tick)))
        self._sync_options()
        self.status_text.set(f"Shot start set to replay tick {int(tick):,}.")

    def _apply_selected(self):
        index = self._selection_index(self.camera_tree)
        if index is None:
            self._error("Apply selected view", ValueError("Select a camera keyframe first."))
            return
        self._set_time(self.project.keyframes[index].time)
        self._apply_frame()

    def _current_time(self):
        time = _finite(self.time_text.get(), "Shot time")
        if time < 0:
            raise ValueError("Shot time must be non-negative.")
        self._set_time(time)
        return time

    def _apply_frame(self):
        def operation():
            project, time = self._snapshot(), self._current_time()
            self._submit("Previewing frame", lambda: self.controller.apply(project, time))
        self._guard("Preview frame", operation)

    def _seek(self):
        def operation():
            project, time = self._snapshot(), self._current_time()
            self._submit("Seeking replay", lambda: self.controller.seek(project, time))
        self._guard("Seek replay", operation)

    def _set_seek_relief(self):
        """Toggle the native render relief that prevents seek-time overload."""
        self.controller.set_seek_relief(self.seek_relief.get())

    def _apply_playback_speed(self):
        """Apply the desktop speed to the running or paused replay."""
        if self.closed or self.busy:
            return
        try:
            speed = _finite(self.speed.get(), "Playback speed")
        except ValueError:
            return
        if not .05 <= speed <= 4:
            return
        if not self.controller.status().get("connected"):
            return
        if speed == getattr(self, "_live_playback_speed", None):
            return
        if self._submit("Applying playback speed",
                        lambda: self.controller.set_playback_speed(speed)):
            self._live_playback_speed = speed

    def _play(self, *, project=None):
        def operation():
            # One last chance to repair a shot authored at another replay rate
            # before the path runs; the controller only warns from here.
            if project is None:
                self._offer_replay_tick_rate()
                playback_project = self._snapshot()
            else:
                playback_project = copy.deepcopy(project)
            # Play shot always runs the complete shot, independently of the
            # selected key or the cursor used for single-frame previews.
            speed = _finite(self.speed.get(), "Playback speed")
            if not .05 <= speed <= 4:
                raise ValueError("Playback speed must be between 0.05 and 4.")
            rate, frozen, hide_hud = int(self.rate.get()), self.frozen.get(), self.hide_hud.get()
            if rate not in (30, 60, 120):
                raise ValueError("Choose a playback update rate of 30, 60, or 120.")
            smoothing = self.smoothing.get().lower()
            smoothing_window(smoothing)
            submitted = self._submit("Starting shot playback", lambda: self.controller.play(
                playback_project, time=0.0, speed=speed, rate=rate, frozen=frozen, hide_hud=hide_hud,
                smoothing=smoothing))
            if submitted:
                self._set_time(0.0)
        self._guard("Play shot", operation)

    def _slide(self, value):
        self.time_text.set(f"{float(value):.3f}".rstrip("0").rstrip("."))
        if hasattr(self, "aspect_curve"):
            self.aspect_curve.set_current_time(float(value))
        if hasattr(self, "rotation_curve"):
            self.rotation_curve.set_current_time(float(value))
        if hasattr(self, "position_curve"):
            self.position_curve.set_current_time(float(value))

    def _timeline_limit(self, time=0.0):
        """Keep the scrubber on the authored shot, not a fixed 10-second tail."""
        duration = max(0.0, float(self.project.duration))
        if not self.project.keyframes:
            return max(10.0, duration, float(time))
        return max(1.0, duration, min(max(0.0, float(time)), duration))

    def _set_time(self, time):
        self.slider.configure(to=self._timeline_limit(time))
        self.shot_time.set(time)
        self.time_text.set(_number(time))
        if hasattr(self, "aspect_curve"):
            self.aspect_curve.set_current_time(float(time))
        if hasattr(self, "rotation_curve"):
            self.rotation_curve.set_current_time(float(time))
        if hasattr(self, "position_curve"):
            self.position_curve.set_current_time(float(time))
        if not getattr(self, "playing", False):
            self._refresh_citadel_dof()

    def _selected_track(self):
        index = self._selection_index(self.track_tree)
        if index is None or index >= len(self.project.tracks):
            raise ValueError("Select a camera-variable track first.")
        return index, self.project.tracks[index]

    def _track_settings(self, keys):
        raw_restore = self.restore_value.get().strip()
        return CvarTrack(name=self.track_name.get().strip(), keys=keys, interpolation=self.track_mode.get(),
                         restore_value=parse_cvar_value(self.track_name.get().strip(), raw_restore, "Restore value") if raw_restore else None)

    def _commit_tracks(self, tracks, selected=None):
        candidate = copy.deepcopy(self.project)
        candidate.tracks = tracks
        candidate.validate()
        self.project = candidate
        self._mark_dirty()
        self._refresh_tracks(selected)

    def _create_track(self):
        def operation():
            # A new track begins with the entered key, so it is valid immediately.
            key = TrackKey(time=_finite(self.track_time.get(), "Shot seconds"), value=parse_cvar_value(self.track_name.get().strip(), self.track_value.get(), "Camera value"))
            track = self._track_settings([key])
            if any(existing.name == track.name for existing in self.project.tracks):
                raise ValueError("That variable already has a track. Select it to add keys or update settings.")
            self._commit_tracks([*self.project.tracks, track], len(self.project.tracks))
        self._guard("Create camera track", operation)

    def _update_track(self):
        def operation():
            index, existing = self._selected_track()
            track = self._track_settings(copy.deepcopy(existing.keys))
            tracks = copy.deepcopy(self.project.tracks)
            tracks[index] = track
            self._commit_tracks(tracks, index)
        self._guard("Update track settings", operation)

    def _remove_track(self):
        def operation():
            index, _ = self._selected_track()
            self._commit_tracks([track for i, track in enumerate(self.project.tracks) if i != index])
        self._guard("Remove camera track", operation)

    def _select_track(self, _event=None):
        self.value_tree.delete(*self.value_tree.get_children())
        try:
            _, track = self._selected_track()
        except ValueError:
            return
        self.track_name.set(track.name)
        self.track_mode.set(track.interpolation)
        self.restore_value.set("" if track.restore_value is None else format_cvar_value(track.restore_value))
        for index, key in enumerate(track.keys):
            self.value_tree.insert("", "end", iid=str(index), values=(_number(key.time), format_cvar_value(key.value)))

    def _select_value(self, _event=None):
        index = self._selection_index(self.value_tree)
        if index is None:
            return
        try:
            _, track = self._selected_track()
            key = track.keys[index]
        except (ValueError, IndexError):
            return
        self.track_time.set(_number(key.time))
        self.track_value.set(format_cvar_value(key.value))

    def _put_value(self):
        def operation():
            index, existing = self._selected_track()
            key = TrackKey(time=_finite(self.track_time.get(), "Shot seconds"), value=parse_cvar_value(existing.name, self.track_value.get(), "Camera value"))
            tracks = copy.deepcopy(self.project.tracks)
            tracks[index].keys = sorted([*(item for item in existing.keys if item.time != key.time), key], key=lambda item: item.time)
            self._commit_tracks(tracks, index)
        self._guard("Add camera-variable key", operation)

    def _delete_value(self):
        def operation():
            track_index, track = self._selected_track()
            index = self._selection_index(self.value_tree)
            if index is None:
                raise ValueError("Select a value key to delete.")
            if len(track.keys) <= 1:
                raise ValueError("A track needs at least one key. Use Remove selected track to remove it entirely.")
            tracks = copy.deepcopy(self.project.tracks)
            tracks[track_index].keys = [key for i, key in enumerate(track.keys) if i != index]
            self._commit_tracks(tracks, track_index)
        self._guard("Delete camera-variable key", operation)

    def _set_fixed(self):
        def operation():
            candidate = copy.deepcopy(self.project)
            name = self.setup_name.get().strip()
            candidate.setup_values[name] = parse_cvar_value(name, self.setup_value.get(), "Fixed camera value")
            candidate.validate()
            self.project = candidate
            self._mark_dirty()
            self._refresh_fixed()
        self._guard("Set fixed camera value", operation)

    def _remove_fixed(self):
        if self.busy or self.playing:
            self.status_text.set("Stop playback and finish the current operation before editing the shot.")
            return
        selected = self.setup_tree.selection()
        if selected:
            name = self.setup_tree.item(selected[0], "values")[0]
            self.project.setup_values.pop(name, None)
            self._mark_dirty()
            self._refresh_fixed()

    def _select_fixed(self, _event=None):
        selected = self.setup_tree.selection()
        if selected:
            name, value = self.setup_tree.item(selected[0], "values")
            self.setup_name.set(name)
            self.setup_value.set(value)

    def _dof_preset(self):
        def operation():
            candidate = copy.deepcopy(self.project)
            end = candidate.duration if candidate.duration > 0 else 5.0
            values = {"r_citadel_depthoffield_enable": 1.0, "r_depth_of_field": 1.0}
            tracks = [
                CvarTrack("r_citadel_depthoffield_focus_distance", [TrackKey(0.0, 200.0), TrackKey(end, 600.0)], "smooth"),
                CvarTrack("r_citadel_depthoffield_aperture_diameter", [TrackKey(0.0, 0.1), TrackKey(end, 0.6)], "smooth"),
            ]
            names = {track.name for track in candidate.tracks}
            if names.intersection(track.name for track in tracks):
                if not messagebox.askyesno("Replace depth-of-field tracks?", "Replace the existing focus and aperture tracks with the preset?", parent=self.root):
                    return
            preset_names = {track.name for track in tracks}
            candidate.tracks = [track for track in candidate.tracks if track.name not in preset_names] + tracks
            candidate.setup_values.update(values)
            candidate.validate()
            self.project = candidate
            self._mark_dirty()
            self._refresh_tracks()
            self._refresh_fixed()
            self.status_text.set("Depth-of-field preset added. Focus and aperture values are editable; verify the look in your replay.")
        self._guard("Depth-of-field preset", operation)

    def _range_dof_preset(self):
        def operation():
            candidate = copy.deepcopy(self.project)
            name = "r_dof_override_ranges"
            if any(track.name == name for track in candidate.tracks):
                if not messagebox.askyesno("Replace range DOF track?", "Replace the existing four-component range track with the preset?", parent=self.root):
                    return
            end = candidate.duration if candidate.duration > 0 else 5.0
            # Verified generic DOF defaults; four zeros disables this override.
            from .editor_dof import DEFAULT_RANGES
            ranges = DEFAULT_RANGES
            track = CvarTrack(name, [TrackKey(0.0, ranges), TrackKey(end, ranges)], "smooth")
            candidate.tracks = [existing for existing in candidate.tracks if existing.name != name] + [track]
            candidate.setup_values.update({"r_depth_of_field": 1.0, "r_dof_override": 1.0})
            candidate.validate()
            self.project = candidate
            self._mark_dirty()
            self._refresh_tracks(len(candidate.tracks) - 1)
            self._refresh_fixed()
            self.status_text.set("Range DOF added. Edit four numbers: near blurry, near crisp, far crisp, far blurry.")
        self._guard("Range depth-of-field preset", operation)

    def _refresh_project(self):
        self.start_tick.set(_number(self.project.start_tick))
        self.tick_rate.set(_number(self.project.tick_rate))
        self.interpolation.set(self.project.interpolation)
        self.rotation.set(self.project.rotation_mode)
        standard_label = next((label for label, value in ASPECT_PRESETS.items()
                               if abs(value - self.project.standard_aspect) < 1e-7), _number(self.project.standard_aspect))
        self.standard_aspect.set(standard_label)
        self.lens_interpolation.set(self.project.lens_interpolation)
        if hasattr(self, "confetti_enabled"):
            self.confetti_enabled.set(self.project.confetti_enabled)
            self.confetti_spawn_height.set(_number(self.project.confetti_spawn_height))
            self.confetti_despawn_on_ground.set(self.project.confetti_despawn_on_ground)
        if hasattr(self, "particles_preset"):
            self.particles_preset.set(PARTICLE_LABELS.get(self.project.particles_preset,
                                                          self.project.particles_preset))
            self.particles_intensity.set(_number(self.project.particles_intensity))
        self.controller.standard_aspect = self.project.standard_aspect
        self._refresh_keys()
        self._refresh_tracks()
        self._refresh_fixed()
        self._title()
        if hasattr(self, "native_editor_active"):
            editor_session.configure(self)

    def _refresh_keys(self, selected_time=None):
        self.camera_tree.delete(*self.camera_tree.get_children())
        for index, key in enumerate(self.project.keyframes):
            self.camera_tree.insert("", "end", iid=str(index), values=(f"Camera {index + 1:02d}", *((f"{key.aspect_ratio:.4f}".rstrip("0").rstrip(".") if field == "aspect_ratio" else _number(getattr(key, field))) for field in FIELDS)))
            if selected_time is not None and key.time == selected_time:
                self.camera_tree.selection_set(str(index))
                self.camera_tree.see(str(index))
        self.slider.configure(to=self._timeline_limit())
        if hasattr(self, "path_summary"):
            self.path_summary.set(f"{len(self.project.keyframes)} cameras · {self.project.duration:.2f} s")
        if hasattr(self, "selected_text") and not self.camera_tree.selection():
            self.selected_text.set("No camera selected")
        view = getattr(self, "camera_view", None)
        if view is not None:
            view.fit_camera_rows()   # the Cameras table follows the shot (3 to 8 rows, as the bento allows)
        self._refresh_curve()
        self._update_export_camera_note()
        if selected_time is not None:
            self._select_key()

    def _refresh_tracks(self, selected=None):
        self.track_tree.delete(*self.track_tree.get_children())
        for index, track in enumerate(self.project.tracks):
            self.track_tree.insert("", "end", iid=str(index), values=(track.name,))
        if selected is not None and selected < len(self.project.tracks):
            self.track_tree.selection_set(str(selected))
            self.track_tree.see(str(selected))
        self._select_track()
        self.slider.configure(to=self._timeline_limit())

    def _refresh_fixed(self):
        self.setup_tree.delete(*self.setup_tree.get_children())
        for index, (name, value) in enumerate(sorted(self.project.setup_values.items())):
            self.setup_tree.insert("", "end", iid=str(index), values=(name, format_cvar_value(value)))
        self._refresh_citadel_dof()

    def _title(self):
        marker = " *" if self.dirty else ""
        self.project_text.set(self.project.name + marker)
        self.root.title(f"Deadlock Dolly — {self.project.name}{marker}")
        if getattr(self, "title_bar", None) is not None:
            self.title_bar.set_title(self.project.name + marker)

    def _mark_dirty(self):
        history = getattr(self, "shot_history", None)
        if history is not None:
            index = self._selection_index(self.camera_tree)
            selected_time = (self.project.keyframes[index].time
                             if index is not None and 0 <= index < len(self.project.keyframes) else None)
            history.record(self.project, selected_time,
                           merge_key=getattr(self, "_history_edit_group", None))
        self.dirty = True
        self._title()
        if hasattr(self, "native_editor_active"):
            editor_session.configure(self)

    def _shot_edit_ready(self):
        video = getattr(self, "video_export", None)
        recording_state = video.status().get("state") if video is not None else None
        if recording_state in ACTIVE_STATES:
            self.status_text.set("Wait for the recording to finish saving before editing cameras."
                                 if recording_state == "finalizing" else
                                 "Finish recording before editing cameras.")
            return False
        if getattr(self, "_layer_queue", None) or getattr(self, "_active_layer_take", None):
            self.status_text.set("Finish recording before editing cameras.")
            return False
        if (self.busy or self.playing or getattr(self, "_pending_capture", None)
                or getattr(self, "_bone_picker_context", None) or getattr(self, "preview_attach", False)):
            self.status_text.set("Stop playback/recording and detach any live bone preview before editing the shot.")
            return False
        return True

    def _refresh_history_menu(self):
        history = self.shot_history
        self.edit_menu.entryconfigure(0, state="normal" if history.can_undo else "disabled")
        self.edit_menu.entryconfigure(1, state="normal" if history.can_redo else "disabled")

    def _history_shortcut(self, event, redo=False):
        if event.widget.winfo_class() in ("Entry", "TEntry", "Text", "Spinbox", "TSpinbox", "TCombobox"):
            return None
        (self.redo_shot if redo else self.undo_shot)()
        return "break"

    def _move_shot_history(self, redo=False):
        shot_commands.move_history(
            redo, ready=self._shot_edit_ready, sync=self._sync_options,
            move=lambda direction: self.shot_history.move(direction),
            restore=self._restore_shot_history,
            empty=lambda direction: self.status_text.set("Nothing to redo." if direction else "Nothing to undo."),
        )

    def _restore_shot_history(self, state, redo):
        self.project = state.project
        self.dirty = self.shot_history.is_dirty(self.project)
        self._native_editor_config_cache = None
        self._native_attach_cache = None
        self._native_camera_selected = None
        self._refreshing_shot_history = True
        try:
            self._refresh_project()
            self._refresh_keys(state.selected_time)
            self._set_time(state.selected_time or 0)
        finally:
            self._refreshing_shot_history = False
        editor_session.configure(self)
        self.status_text.set("Shot edit redone." if redo else "Shot edit undone.")

    def undo_shot(self):
        self._guard("Undo shot edit", self._move_shot_history)

    def redo_shot(self):
        self._guard("Redo shot edit", lambda: self._move_shot_history(True))

    def _allow_discard(self):
        if getattr(self, "_saving_shot", False):
            return False
        if self.playing or self.busy:
            messagebox.showinfo("Finish the current operation", "Stop path playback and wait for the current operation before changing projects.", parent=self.root)
            return False
        try:
            self._sync_options()
        except ValueError:
            # Invalid uncommitted timing fields still deserve a discard prompt.
            self.dirty = True
        if not self.dirty:
            return True
        choice = messagebox.askyesnocancel("Save changes?", f"Save changes to {self.project.name}?", parent=self.root)
        if choice is None:
            return False
        return self.save() if choice else True

    def new(self):
        if not self._allow_discard():
            return
        self.project = Project(name="Untitled shot", keyframes=[], tracks=[],
                               interpolation=self.interpolation.get())
        self.shot_history.reset(self.project)
        self._native_camera_page = 0
        self.file_path = None
        self.dirty = False
        self._refresh_project()
        self._set_time(0)

    def open(self):
        if not self._allow_discard():
            return
        path = filedialog.askopenfilename(parent=self.root, title="Open camera shot", filetypes=(("Dolly shot", "*.json"), ("All files", "*.*")))
        if not path:
            return
        def operation():
            project = Project.load(path)
            # Project.load enforces the file-size limit before this migration notice.
            legacy_project = json.loads(Path(path).read_text(encoding="utf-8")).get("version") == 1
            project.validate()
            self.project = project
            self.shot_history.reset(self.project)
            self._native_camera_page = 0
            self.file_path = Path(path)
            self.dirty = False
            self._refresh_project()
            self._set_time(0)
            if self.full_editor.get():
                self._select_page(self.camera_tab)
            if legacy_project:
                self.status_text.set("Shot imported: camera positions and timing are preserved. Framing starts at normal aspect; old FOV values are kept as inactive metadata.")
            else:
                self.status_text.set(f"Opened {Path(path).name}.")
            self._offer_replay_tick_rate()
        self._guard("Open shot", operation)

    def save(self, save_as=False):
        if getattr(self, "_saving_shot", False):
            LOG.info("Save shot ignored: a save dialog is already open.")
            return False
        # Worker operations publish edits on Tk's thread. Saving the currently
        # visible shot does not require an idle game or worker queue.
        self._saving_shot = True
        LOG.info("Save shot requested: save_as=%s busy=%s existing_file=%s",
                 save_as, self.busy, self.file_path is not None)
        try:
            self._sync_options()
            project = copy.deepcopy(self.project)
            path = self.file_path
            if save_as or path is None:
                filename = "".join(character if character.isalnum() or character in "-_ " else "_" for character in project.name).strip() or "shot"
                LOG.info("Opening Save camera shot dialog.")
                chosen = filedialog.asksaveasfilename(parent=self.root, title="Save camera shot", defaultextension=".json",
                                                       initialfile=filename + ".json", filetypes=(("Dolly shot", "*.json"),))
                if not chosen:
                    LOG.info("Save shot cancelled.")
                    return False
                path = Path(chosen)
            project.save(path)
            self.shot_history.mark_saved(project)
            self.file_path = path
            # Native events/worker completions can edit the shot while Tk's
            # modal picker is running. Those later changes remain unsaved.
            self.dirty = self.shot_history.is_dirty(self.project)
            self._title()
            self.status_text.set(f"Saved {path.name}." + (" Newer edits are still unsaved." if self.dirty else ""))
            LOG.info("Saved camera shot: %s; newer_edits=%s", path, self.dirty)
            return True
        except Exception as exc:
            LOG.exception("Save shot failed")
            self._error("Save shot", exc)
            return False
        finally:
            self._saving_shot = False

    def rename(self):
        if self.busy or self.playing:
            self.status_text.set("Stop playback and finish the current operation before renaming the shot.")
            return
        name = simpledialog.askstring("Rename shot", "Shot name", initialvalue=self.project.name, parent=self.root)
        if name and name.strip():
            self.project.name = name.strip()
            self._mark_dirty()

    def _diagnostics(self):
        path = filedialog.asksaveasfilename(parent=self.root, title="Export diagnostics", defaultextension=".zip",
                                           initialfile="dolly-diagnostics.zip", filetypes=(("Diagnostic archive", "*.zip"),))
        if path:
            self._submit("Exporting diagnostics", lambda: self.controller.export_diagnostics(path),
                         lambda result: self.status_text.set(f"Diagnostics exported to {Path(result or path).name}."))

    def _check_dof(self):
        game_path = self.game_path.get().strip()
        self._submit("Checking Native DOF support", lambda: self.controller.dof_preflight(game_path),
                     self._dof_checked)

    def _dof_checked(self, report):
        if not report:
            return
        messagebox.showinfo("Native DOF support", dof_preflight.format_report(report), parent=self.root)
        self.status_text.set(dof_preflight.summary(report))

    def _toggle_log(self):
        if self.show_log.get():
            self.log_dialog.deiconify()
            self.log_dialog.lift()
        else:
            self.log_dialog.withdraw()

    def _close_log(self):
        self.show_log.set(False)
        self.log_dialog.withdraw()

    def _on_close(self):
        if self.busy:
            messagebox.showinfo("Operation in progress", "Wait for the current operation to finish before closing Dolly.", parent=self.root)
            return
        # Record the user's intent to close now, so a failure in any teardown
        # step (for example the game already exited) still closes the window
        # instead of stacking an error dialog behind an unclosable app.
        self._closing = True
        if self.playing:
            self._submit("Stopping before closing", self.controller.stop, lambda _result: self._finish_close())
        else:
            self._finish_close()

    def _finish_close(self):
        self.playing = False
        if not self._allow_discard():
            # The user chose to keep editing; cancel the pending close intent.
            self._closing = False
            return
        editor_session.close(self)
        self._submit("Closing replay connection", self.controller.close, lambda _result: self._destroy())

    def _close_after_failure(self):
        """Finish a requested close after a teardown step failed.

        The controller close restores gameinfo and hands off deployment cleanup;
        it must still run, but even if it reports a problem the window closes.
        Run it on the worker when possible and fall back to a direct close so the
        window can never be left open.
        """
        self.playing = False
        self.busy = False
        if self._submit("Closing replay connection", self.controller.close,
                        lambda _result: self._destroy()):
            return
        try:
            self.controller.close()
        except Exception:  # noqa: BLE001 - the window must close regardless
            LOG.exception("Ignoring controller close failure while closing the window")
        self._destroy()

    def _destroy(self):
        presence = getattr(self, "_discord_presence", None)
        if presence is not None:
            try:
                presence.close()
            except Exception:
                LOG.debug("Discord presence shutdown unavailable", exc_info=True)
        editor_session.close(self)
        self.capture_generation += 1
        self.hotkey_enabled.set(False)
        if self.capture_hotkey is not None:
            try:
                self.capture_hotkey.stop()
            except Exception:
                LOG.exception("Capture shortcut cleanup failed during editor shutdown")
            self.capture_hotkey = None
        self.closed = True
        self.operations.shutdown()
        # A page switch may still have its window fit waiting for idle.
        for name in ("_fit_job", "_refit_job"):
            job = getattr(self, name, None)
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except tk.TclError:
                    pass
                setattr(self, name, None)
        self.root.destroy()


def main():
    # Before the first window: Windows then draws Dolly at the display's real
    # DPI (sharp) and Tk reports it, so every design size scales by ui_scale.
    LOG.info("DPI aware: %s", enable_dpi_awareness())
    root = tk.Tk()
    app = None
    try:
        app = DollyApp(root)
        app.start_discord_presence()
        from .update_ui import UpdateUI
        app.update_manager = UpdateUI(app, skip_startup="--updated" in sys.argv)
        root.mainloop()
    finally:
        # USER-ADDITIONS UPDATE 20: Windows must never call Dolly's window procedure once Python
        # stops running the window, and a failed start must not leave it invisible.
        window_chrome.release(root)
        if app is not None and app._discord_presence is not None:
            app._discord_presence.close()


if __name__ == "__main__":
    main()
