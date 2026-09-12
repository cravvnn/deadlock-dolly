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
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from dolly import editor_session
from dolly.editor_actions import ACTION_LABELS, ACTION_ORDER, EDITOR_KEY_CHOICES, EditorBinding, default_action_bindings, validate_action_bindings
from dolly.replays import discover_replays, find_replay_folder, parse_launch_options
from dolly.bindings import CaptureBinding, DEFAULT_BINDING, KEY_CHOICES
from dolly.branding import apply_window_icon
from dolly.controller import Controller
from dolly.curve import AspectCurve
from dolly.hotkey import CaptureHotkey
from dolly.launcher import discover_game, recover_pending
from dolly.navigation import CameraMotion
from dolly.navigation_input import CameraInput
from dolly.path import CvarTrack, Keyframe, Project, TrackKey, parse_cvar_value, format_cvar_value
from dolly.settings import AppSettings, load_settings, save_settings
from dolly.smoothing import smoothing_window
from dolly.video_export import (ACTIVE_STATES, BITRATE_PRESETS, CODEC_BY_KEY, CODEC_CHOICES,
                                CODEC_LABEL_TO_KEY, DEFAULT_CODEC_KEY, VideoExport, VideoOptions,
                                bundled_ffmpeg_path, default_video_path, format_video_status,
                                recording_ready)


FIELDS = ("time", "x", "y", "z", "pitch", "yaw", "roll", "aspect_ratio")
FIELD_LABELS = ("Shot seconds", "X", "Y", "Height · Z", "Pitch °", "Yaw °", "Bank °", "Aspect ratio")
BG = "#10151c"
PANEL = "#191f28"
TEXT = "#e8edf3"
MUTED = "#8f9eae"
ACCENT = "#64d6c3"
LOG = logging.getLogger("dolly")
ASPECT_PRESETS = {"16:9": 16 / 9, "16:10": 16 / 10, "21:9": 21 / 9, "4:3": 4 / 3}



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


def _clear_committed_combobox_selection(event):
    """Remove Tk's automatic text highlight after choosing a dropdown item.

    Leave focus, the insertion cursor, and ordinary typing/drag selection
    alone. Waiting for idle lets the platform's combobox binding finish.
    """
    widget = event.widget
    def clear():
        try:
            widget.selection_clear()
        except tk.TclError:
            pass  # The option's callback may have closed its dialog.
    widget.after_idle(clear)


def _binding_event_key(event):
    number = getattr(event, "num", None)
    if isinstance(number, int):
        # Tk on Windows uses button 4/5 for XBUTTON1/2; users on other Tk
        # backends can always select the explicit Mouse4/Mouse5 presets.
        return {2: "MiddleMouse", 4: "Mouse4", 5: "Mouse5", 8: "Mouse4", 9: "Mouse5"}.get(number)
    key = str(getattr(event, "keysym", ""))
    aliases = {"space": "Space", "Return": "Enter", "Prior": "PageUp", "Next": "PageDown", "Control_L": "Ctrl", "Control_R": "Ctrl", "Shift_L": "Shift", "Shift_R": "Shift", "Alt_L": "Alt", "Alt_R": "Alt"}
    key = aliases.get(key, key.upper() if len(key) == 1 else key)
    return key if key in EDITOR_KEY_CHOICES or key == "F7" else None


class DollyApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        apply_window_icon(root)
        self.root.title("Deadlock Dolly — Untitled shot")
        self.ui_scale = max(1.0, float(self.root.tk.call("tk", "scaling")) / (96 / 72))
        self.root.geometry(self._window_size(1180, 800))
        self.root.minsize(*self._window_dimensions(1000, 700))
        self.events: queue.Queue = queue.Queue()
        self.jobs: queue.Queue = queue.Queue()
        self.closed = False
        self.busy = False
        self.dirty = False
        self.file_path: Path | None = None
        self.project = Project(name="Untitled shot", keyframes=[], tracks=[])
        self.controller = Controller(log_callback=self._enqueue_log)
        self.video_export = VideoExport(self.controller)
        self.worker = threading.Thread(target=self._worker_loop, name="dolly-ui-operations", daemon=True)
        self.worker.start()
        self.game_path = tk.StringVar()
        self.demo_path = tk.StringVar()
        self.protocol = tk.StringVar(value="Netconsole")
        self.camera_driver = tk.StringVar(value="Native (experimental)")
        self.status_text = tk.StringVar(value="Choose a replay and press Play replay to begin editing.")
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
        self.show_coordinates = tk.BooleanVar(value=False)
        self.app_settings = self._load_app_settings()
        self.video_path = tk.StringVar(value=str(default_video_path(self.project.name)))
        self.video_fps = tk.StringVar(value="60")
        self.video_bitrate = tk.StringVar(value="20 Mbps")
        self.video_codec = tk.StringVar(value=CODEC_BY_KEY[DEFAULT_CODEC_KEY][0])
        self.video_fixed_step = tk.BooleanVar(value=False)
        self.video_export_speed = tk.StringVar(value="1")
        _bundled_ffmpeg = bundled_ffmpeg_path()
        self.ffmpeg_path = tk.StringVar(value=str(_bundled_ffmpeg) if _bundled_ffmpeg else "")
        self.video_status_text = tk.StringVar(value="Launch a replay to record video.")
        self.reshade_runtime_path = tk.StringVar(value=getattr(self.app_settings, "reshade_runtime_path", ""))
        self.reshade_status_text = tk.StringVar(value="Choose the ReShade runtime to enable its in-game menu.")
        self.game_path.set(self.app_settings.game_path)
        self.demo_path.set(self.app_settings.demo_path)
        self.replay_folder = tk.StringVar(value=self.app_settings.replay_folder)
        self.launch_options = tk.StringVar(value=self.app_settings.launch_options)
        self.editor_move_speed = tk.StringVar(value=_number(self.app_settings.movement_speed))
        self.editor_sensitivity = tk.StringVar(value=_number(self.app_settings.mouse_sensitivity))
        self.replay_search = tk.StringVar()
        self.replay_summary = tk.StringVar(value="Choose your replay folder.")
        self.startup_progress = tk.StringVar(value="Ready when you are.")
        self.startup_cancel = None
        self.native_editor_active = False
        self.replay_entries = []
        self.binding_action = tk.StringVar()
        self.binding_key = tk.StringVar()
        self.binding_ctrl = tk.BooleanVar(value=False)
        self.binding_alt = tk.BooleanVar(value=False)
        self.binding_shift = tk.BooleanVar(value=False)
        self.binding_feedback = tk.StringVar(value="Select an action to change its shortcut.")
        self.capture_binding = self.app_settings.capture_binding
        self.hotkey_enabled = tk.BooleanVar(value=False)
        self.hotkey_label = tk.StringVar(value=self._capture_binding_label())
        self.capture_hotkey = None
        self.capture_generation = 0
        self.binding_dialog = None
        self.paused_dialog = None
        self.paused_input = None
        self.paused_cancel = None
        self.paused_requested = False
        self.paused_run_options = (240.0, 60.0)
        self.capture_hint = tk.StringVar(value="Move the replay free camera, then capture each view. Capture pauses a playing replay; the views form a smooth spline.")
        self.show_log = tk.BooleanVar(value=False)
        self.playing = False
        self.dragging = False
        self._last_controller_message = ""
        self._style()
        self._build_menu()
        self._build_window()
        self._refresh_project()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Control-s>", lambda _event: self.save())
        self.root.bind("<Control-o>", lambda _event: self.open())
        self.root.bind("<Control-n>", lambda _event: self.new())
        self.root.after(80, self._poll)
        self._submit("Looking for Deadlock", discover_game, self._discovered)

    def _window_dimensions(self, width, height):
        return (min(round(width * self.ui_scale), self.root.winfo_screenwidth() - 40),
                min(round(height * self.ui_scale), self.root.winfo_screenheight() - 80))

    def _window_size(self, width, height):
        width, height = self._window_dimensions(width, height)
        return f"{width}x{height}"

    def _style(self):
        self.root.configure(bg=BG)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Card.TLabel", background=PANEL)
        style.configure("CardMuted.TLabel", background=PANEL, foreground=MUTED)
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
        style.configure("Section.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("CardTitle.TLabel", background=PANEL, font=("Segoe UI", 11, "bold"))
        style.configure("Accent.TLabel", foreground=ACCENT)
        style.configure("Pill.TLabel", background="#1d3a36", foreground=ACCENT,
                        font=("Segoe UI", 9, "bold"), padding=(9, 3))
        style.configure("PillConsole.TLabel", background="#3a3320", foreground="#e8c76a",
                        font=("Segoe UI", 9, "bold"), padding=(9, 3))
        style.configure("TButton", background="#28323f", foreground=TEXT, padding=(10, 6), borderwidth=0)
        style.map("TButton", background=[("active", "#374757"), ("disabled", "#202731")],
                  foreground=[("disabled", "#617082")])
        style.configure("Primary.TButton", background=ACCENT, foreground="#092620", font=("Segoe UI", 10, "bold"))
        style.map("Primary.TButton", background=[("active", "#8ee7d9"), ("disabled", "#24433f")],
                  foreground=[("disabled", "#799e98")])
        style.configure("Quiet.TButton", background=PANEL, padding=(8, 5))
        style.configure("TMenubutton", background=PANEL, foreground=MUTED, padding=(5, 2), borderwidth=0)
        style.configure("TEntry", fieldbackground="#0e131a", foreground=TEXT, insertcolor=TEXT,
                        bordercolor="#34404d", lightcolor="#34404d", darkcolor="#34404d", padding=5)
        style.configure("TCombobox", fieldbackground="#0e131a", background="#28323f", foreground=TEXT,
                        arrowcolor=MUTED, borderwidth=0, padding=4)
        style.map("TCombobox", fieldbackground=[("readonly", "#0e131a")], foreground=[("readonly", TEXT)])
        style.configure("TCheckbutton", background=BG, foreground=MUTED, padding=0)
        style.map("TCheckbutton", background=[("active", BG)], foreground=[("active", TEXT)])
        style.configure("TNotebook", background=BG, borderwidth=0, bordercolor=BG, lightcolor=BG, darkcolor=BG, tabmargins=(0, 0, 0, 6))
        style.configure("TNotebook.Tab", background=BG, foreground=MUTED, padding=(17, 9), borderwidth=0, bordercolor=BG, lightcolor=BG, darkcolor=BG)
        style.map("TNotebook.Tab", background=[("selected", PANEL)], foreground=[("selected", ACCENT)])
        style.layout("TNotebook.Tab", [("Notebook.padding", {"sticky": "nswe", "children": [("Notebook.label", {"sticky": "nswe"})]})])
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT,
                        rowheight=31, borderwidth=0, lightcolor=PANEL, darkcolor=PANEL)
        style.configure("Treeview.Heading", background="#222b37", foreground=MUTED, bordercolor="#222b37", lightcolor="#222b37", darkcolor="#222b37", padding=(8, 7),
                        relief="flat", borderwidth=0, font=("Segoe UI", 9))
        style.map("Treeview", background=[("selected", "#274e4b")], foreground=[("selected", "#e4fffa")])
        style.configure("Vertical.TScrollbar", background="#354150", troughcolor=PANEL, arrowcolor=MUTED,
                        borderwidth=0, arrowsize=12, relief="flat")
        style.configure("Horizontal.TScale", background=BG, troughcolor="#283442", sliderlength=16,
                        sliderthickness=14, borderwidth=0, lightcolor=ACCENT, darkcolor=ACCENT)
        style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor="#283442", bordercolor=PANEL,
                        lightcolor=ACCENT, darkcolor=ACCENT, thickness=5, borderwidth=0)
        style.configure("TLabelframe", background=BG, bordercolor="#303c49")
        style.configure("TLabelframe.Label", foreground=MUTED)
        self.root.option_add("*TCombobox*Listbox.background", "#10151c")
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.bind_class("TCombobox", "<<ComboboxSelected>>",
                             _clear_committed_combobox_selection, add="+")

    def _build_menu(self):
        menu = tk.Menu(self.root)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="New shot", accelerator="Ctrl+N", command=self.new)
        file_menu.add_command(label="Open shot…", accelerator="Ctrl+O", command=self.open)
        file_menu.add_command(label="Save", accelerator="Ctrl+S", command=self.save)
        file_menu.add_command(label="Save as…", command=lambda: self.save(save_as=True))
        file_menu.add_command(label="Rename shot…", command=self.rename)
        file_menu.add_separator()
        file_menu.add_command(label="Keybinds…", command=self._show_keybinds)
        file_menu.add_command(label="Paused camera…", command=self._open_paused_camera)
        file_menu.add_command(label="Export diagnostics…", command=self._diagnostics)
        file_menu.add_command(label="Recover game configuration…", command=self._recover_game_config)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)
        menu.add_cascade(label="File", menu=file_menu)
        self.root.config(menu=menu)

    def _build_window(self):
        outer = ttk.Frame(self.root, padding=(16, 12, 16, 9))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)
        heading = ttk.Frame(outer)
        heading.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        heading.columnconfigure(0, weight=1)
        ttk.Label(heading, text="DEADLOCK DOLLY", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(heading, textvariable=self.project_text, style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))
        status_row = ttk.Frame(heading)
        status_row.grid(row=0, column=1, sticky="e")
        self.driver_indicator_label = ttk.Label(status_row, textvariable=self.driver_indicator, style="Pill.TLabel")
        self.driver_indicator_label.pack(side="left", padx=(0, 10))
        ttk.Label(status_row, textvariable=self.session_text, style="Accent.TLabel").pack(side="left")
        ttk.Label(heading, textvariable=self.busy_text, style="Muted.TLabel").grid(row=1, column=1, sticky="e")
        self.notebook = ttk.Notebook(outer)
        self.notebook.grid(row=1, column=0, sticky="nsew")
        self.setup_tab = ttk.Frame(self.notebook)
        self.camera_tab = ttk.Frame(self.notebook)
        self.cvar_tab = ttk.Frame(self.notebook)
        self.replays_tab = ttk.Frame(self.notebook)
        self.keybinds_tab = ttk.Frame(self.notebook)
        self.export_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.setup_tab, text="Home")
        self.notebook.add(self.replays_tab, text="Replays")
        self.notebook.add(self.keybinds_tab, text="Keybinds")
        self.notebook.add(self.camera_tab, text="Cameras")
        self.notebook.add(self.cvar_tab, text="Effects")
        self.notebook.add(self.export_tab, text="Export")
        self._build_setup()
        self._build_replays()
        self._build_keybinds()
        self._build_camera()
        self._build_cvars()
        self._build_export()
        self._build_timeline(outer)
        self.notebook.bind("<<NotebookTabChanged>>", self._tab_changed)
        self._tab_changed()
        footer = ttk.Frame(outer)
        footer.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        footer.columnconfigure(0, weight=1)
        self.status_label = ttk.Label(footer, textvariable=self.status_text, style="Muted.TLabel", wraplength=680)
        self.status_label.grid(row=0, column=0, sticky="w", padx=(0, 12))
        footer.bind("<Configure>", lambda event: self.status_label.configure(wraplength=max(250, event.width - 265)))
        ttk.Checkbutton(footer, text="Log", variable=self.show_log, command=self._toggle_log).grid(row=0, column=1)
        ttk.Button(footer, text="Export diagnostics", command=self._diagnostics, style="Quiet.TButton").grid(row=0, column=2, padx=(10, 0))
        self.log_dialog = tk.Toplevel(self.root)
        self.log_dialog.title("Dolly activity log")
        self.log_dialog.geometry(self._window_size(850, 300))
        self.log_dialog.configure(bg=BG)
        self.log_dialog.transient(self.root)
        self.log_dialog.protocol("WM_DELETE_WINDOW", self._close_log)
        self.log_frame = ttk.Frame(self.log_dialog, padding=12)
        self.log_frame.pack(fill="both", expand=True)
        self.log_widget = tk.Text(self.log_frame, height=3, bg="#10151c", fg=MUTED, relief="flat",
                                  font=("Consolas", 9), wrap="word", state="disabled")
        self.log_widget.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(self.log_frame, command=self.log_widget.yview)
        scroll.pack(side="right", fill="y")
        self.log_widget.configure(yscrollcommand=scroll.set)
        self.log_dialog.withdraw()

    def _build_setup(self):
        tab = self.setup_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(2, weight=1)
        card = ttk.Frame(tab, style="Card.TFrame", padding=20)
        card.grid(row=0, column=0, sticky="ew")
        card.columnconfigure(1, weight=1)
        ttk.Label(card, text="REPLAY", style="CardTitle.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        note = ttk.Label(card, text="Open a local replay in the paused camera editor.", style="CardMuted.TLabel", wraplength=800)
        note.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 18))
        card.bind("<Configure>", lambda e: note.configure(wraplength=max(200, e.width - 40)))
        for row, label, variable, command in ((2, "Deadlock", self.game_path, self._browse_game), (3, "Replay", self.demo_path, self._browse_demo)):
            ttk.Label(card, text=label, style="CardMuted.TLabel", width=11).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=(0, 10))
            ttk.Entry(card, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=(0, 10))
            ttk.Button(card, text="Browse…", command=command).grid(row=row, column=2, padx=(10, 0), pady=(0, 10))
        driver = ttk.Frame(card, style="Card.TFrame")
        driver.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(0, 12))
        ttk.Label(driver, text="Camera driver", style="CardMuted.TLabel").pack(side="left", padx=(0, 12))
        self.home_camera_driver_combo = ttk.Combobox(driver, textvariable=self.camera_driver,
            values=("Native (experimental)", "Console (legacy)"), state="readonly", width=23)
        self.home_camera_driver_combo.pack(side="left")
        ttk.Label(driver, text="Choose before launch. Native follows rendered views.",
                  style="CardMuted.TLabel").pack(side="left", padx=(12, 0))
        actions = ttk.Frame(card, style="Card.TFrame")
        actions.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        self.play_replay_button = ttk.Button(actions, text="▶  Play replay", style="Primary.TButton", command=self._start_editing_session)
        self.play_replay_button.pack(side="left")
        self.cancel_startup_button = ttk.Button(actions, text="Cancel startup", style="Quiet.TButton", command=self._cancel_startup, state="disabled")
        self.cancel_startup_button.pack(side="left", padx=(10, 0))
        ttk.Button(actions, text="Replay library →", style="Quiet.TButton", command=lambda: self.notebook.select(self.replays_tab)).pack(side="right")
        progress = ttk.Frame(tab, style="Card.TFrame", padding=(20, 14))
        progress.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        progress.columnconfigure(0, weight=1)
        self.startup_label = ttk.Label(progress, textvariable=self.startup_progress, style="CardMuted.TLabel", wraplength=800)
        self.startup_label.grid(row=0, column=0, sticky="w")
        progress.bind("<Configure>", lambda e: self.startup_label.configure(wraplength=max(200, e.width - 40)))
        self.startup_bar = ttk.Progressbar(progress, mode="indeterminate")
        self.startup_bar.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        footer = ttk.Frame(tab, padding=(4, 16))
        footer.grid(row=3, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, text="DirectX 11  ·  Local replay editing  ·  F7 console", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(footer, text="Launch options…", style="Quiet.TButton", command=self._open_launch_options).grid(row=0, column=1)
        ttk.Button(footer, text="Troubleshooting…", style="Quiet.TButton", command=self._open_advanced_startup).grid(row=0, column=2, padx=(8, 0))
        self._build_advanced_startup()

    def _tab_changed(self, _event=None):
        if not hasattr(self, "timeline_frame"):
            return
        selected = self.notebook.select()
        if selected in (str(self.camera_tab), str(self.cvar_tab)):
            self.timeline_frame.grid()
        else:
            self.timeline_frame.grid_remove()

    def _build_export(self):
        tab = self.export_tab
        tab.columnconfigure(0, weight=1)
        card = ttk.Frame(tab, style="Card.TFrame", padding=18)
        card.grid(row=0, column=0, sticky="ew")
        card.columnconfigure(1, weight=1)
        ttk.Label(card, text="VIDEO", style="CardTitle.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        note = ttk.Label(card, text="Record the normal scene to MP4 at the current game resolution. Real-time capture · SDR · video only.",
                         style="CardMuted.TLabel", wraplength=860)
        note.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 14))
        card.bind("<Configure>", lambda event: note.configure(wraplength=max(220, event.width - 36)))
        ttk.Label(card, text="Output file", style="CardMuted.TLabel").grid(row=2, column=0, sticky="w", padx=(0, 12))
        self.video_path_entry = ttk.Entry(card, textvariable=self.video_path)
        self.video_path_entry.grid(row=2, column=1, sticky="ew")
        self.video_browse_button = ttk.Button(card, text="Browse…", command=self._browse_video)
        self.video_browse_button.grid(row=2, column=2, padx=(10, 0))
        options = ttk.Frame(card, style="Card.TFrame")
        options.grid(row=3, column=0, columnspan=3, sticky="w", pady=(12, 0))
        ttk.Label(options, text="Video FPS", style="CardMuted.TLabel").pack(side="left", padx=(0, 8))
        self.video_fps_combo = ttk.Combobox(options, textvariable=self.video_fps, values=("30", "60", "120", "300", "600"), state="readonly", width=5)
        self.video_fps_combo.pack(side="left", padx=(0, 18))
        ttk.Label(options, text="Bitrate", style="CardMuted.TLabel").pack(side="left", padx=(0, 8))
        self.video_bitrate_combo = ttk.Combobox(options, textvariable=self.video_bitrate, values=tuple(BITRATE_PRESETS), state="readonly", width=10)
        self.video_bitrate_combo.pack(side="left", padx=(0, 18))
        ttk.Label(options, text="Encoder", style="CardMuted.TLabel").pack(side="left", padx=(0, 8))
        self.video_codec_combo = ttk.Combobox(options, textvariable=self.video_codec,
                                              values=tuple(label for _key, label, *_ in CODEC_CHOICES),
                                              state="readonly", width=30)
        self.video_codec_combo.pack(side="left", padx=(0, 18))
        self.video_fixed_checkbox = ttk.Checkbutton(options, text="Fixed-step export (frame-accurate)",
                                                    variable=self.video_fixed_step)
        self.video_fixed_checkbox.pack(side="left", padx=(0, 18))
        ttk.Label(options, text="Export speed", style="CardMuted.TLabel").pack(side="left", padx=(0, 6))
        self.video_speed_combo = ttk.Combobox(options, textvariable=self.video_export_speed,
                                              values=("0.05", "0.1", "0.25", "0.5", "1", "2", "4"),
                                              width=5)
        self.video_speed_combo.pack(side="left")
        ttk.Label(card, text="FFmpeg", style="CardMuted.TLabel").grid(row=4, column=0, sticky="w", padx=(0, 12), pady=(12, 0))
        self.ffmpeg_path_entry = ttk.Entry(card, textvariable=self.ffmpeg_path)
        self.ffmpeg_path_entry.grid(row=4, column=1, sticky="ew", pady=(12, 0))
        self.ffmpeg_browse_button = ttk.Button(card, text="Browse…", command=self._browse_ffmpeg)
        self.ffmpeg_browse_button.grid(row=4, column=2, padx=(10, 0), pady=(12, 0))
        actions = ttk.Frame(card, style="Card.TFrame")
        actions.grid(row=5, column=0, columnspan=3, sticky="w", pady=(14, 0))
        self.video_start_button = ttk.Button(actions, text="Start recording", style="Primary.TButton", command=self._start_video_recording)
        self.video_start_button.pack(side="left", padx=(0, 8))
        self.video_stop_button = ttk.Button(actions, text="Finish recording", command=self._stop_video_recording, state="disabled")
        self.video_stop_button.pack(side="left", padx=(0, 8))
        self.video_cancel_button = ttk.Button(actions, text="Discard recording", style="Quiet.TButton", command=lambda: self._stop_video_recording(cancel=True), state="disabled")
        self.video_cancel_button.pack(side="left")
        ttk.Label(card, textvariable=self.video_status_text, style="CardMuted.TLabel", wraplength=850).grid(row=6, column=0, columnspan=3, sticky="w", pady=(12, 0))
        ttk.Label(tab, text="Start recording, return to Deadlock, then press F5 to play the shot. Use Finish recording in either interface to save. Encoders using FFmpeg need the ffmpeg.exe path above; the bundled build fills it automatically.",
                  style="Muted.TLabel", wraplength=900).grid(row=1, column=0, sticky="w", padx=4, pady=(10, 14))
        shade = ttk.Frame(tab, style="Card.TFrame", padding=18)
        shade.grid(row=2, column=0, sticky="ew")
        shade.columnconfigure(1, weight=1)
        ttk.Label(shade, text="RESHADE", style="CardTitle.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(shade, text="Optional effects and presets. Set the menu shortcut in Keybinds.", style="CardMuted.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 12))
        ttk.Label(shade, text="Runtime DLL", style="CardMuted.TLabel").grid(row=2, column=0, sticky="w", padx=(0, 12))
        self.reshade_path_entry = ttk.Entry(shade, textvariable=self.reshade_runtime_path)
        self.reshade_path_entry.grid(row=2, column=1, sticky="ew")
        self.reshade_browse_button = ttk.Button(shade, text="Browse…", command=self._browse_reshade)
        self.reshade_browse_button.grid(row=2, column=2, padx=(10, 0))
        actions = ttk.Frame(shade, style="Card.TFrame")
        actions.grid(row=3, column=0, columnspan=3, sticky="w", pady=(12, 0))
        self.reshade_configure_button = ttk.Button(actions, text="Enable ReShade", command=self._configure_reshade)
        self.reshade_configure_button.pack(side="left", padx=(0, 10))
        self.reshade_disable_button = ttk.Button(actions, text="Disable for this session", style="Quiet.TButton", command=self._disable_reshade, state="disabled")
        self.reshade_disable_button.pack(side="left", padx=(0, 10))
        self.reshade_forget_button = ttk.Button(actions, text="Forget runtime", style="Quiet.TButton", command=self._forget_reshade)
        self.reshade_forget_button.pack(side="left", padx=(0, 10))
        ttk.Button(actions, text="Keybinds…", style="Quiet.TButton", command=lambda: self.notebook.select(self.keybinds_tab)).pack(side="left")
        ttk.Label(shade, textvariable=self.reshade_status_text, style="CardMuted.TLabel", wraplength=850).grid(row=4, column=0, columnspan=3, sticky="w", pady=(10, 0))

    def _browse_video(self):
        if self.busy or self.video_export.status().get("state") in ACTIVE_STATES:
            return
        current = Path(self.video_path.get())
        path = filedialog.asksaveasfilename(parent=self.root, title="Record video", defaultextension=".mp4",
                                          initialdir=str(current.parent), initialfile=current.name,
                                          filetypes=(("MP4 video", "*.mp4"),), confirmoverwrite=False)
        if path:
            self.video_path.set(path)

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

    def _start_video_recording(self):
        if self.busy:
            self.status_text.set("Finish the current operation before starting a recording.")
            return
        try:
            codec_key = CODEC_LABEL_TO_KEY.get(self.video_codec.get(), DEFAULT_CODEC_KEY)
            options = VideoOptions(Path(self.video_path.get().strip()), int(self.video_fps.get()),
                                   BITRATE_PRESETS[self.video_bitrate.get()], codec=codec_key,
                                   ffmpeg_path=self.ffmpeg_path.get().strip() or None,
                                   fixed_step=bool(self.video_fixed_step.get()),
                                   speed=float(self.video_export_speed.get())).validated()
        except (ValueError, KeyError, OSError) as exc:
            self._error("Record video", exc)
            return
        self._submit("Starting video recording", lambda: self.video_export.start(options), self._video_operation_done)

    def _stop_video_recording(self, cancel=False):
        if self.busy:
            self.status_text.set("Finish the current operation before stopping the recording.")
            return
        self._submit("Discarding recording" if cancel else "Finishing video recording",
                     lambda: self.video_export.stop(cancel=cancel), self._video_operation_done)

    def _video_operation_done(self, status):
        self.video_status_text.set(format_video_status(status))
        self.status_text.set(self.video_status_text.get())
        self._last_video_state = status.get("state", "idle")
        if status.get("state") == "completed" and self.video_export.output_path:
            self._log("Video saved to " + str(self.video_export.output_path))
            self.video_path.set(str(default_video_path(self.project.name, self.video_export.output_path.parent)))

    def _refresh_video(self, controller_status):
        status = self.video_export.status()
        state = status.get("state", "idle")
        previous = getattr(self, "_last_video_state", "idle")
        if state != previous:
            if state == "completed":
                self._video_operation_done(status)
            elif state == "failed":
                self._log(format_video_status(status))
            self._last_video_state = state
        active = state in ACTIVE_STATES
        ready = recording_ready(controller_status)
        self.video_status_text.set("Launch a replay to record video." if state == "idle" and not ready else format_video_status(status))
        self.video_start_button.configure(state="normal" if ready and not active and not self.busy else "disabled")
        can_stop = state in ("starting", "recording") and not self.busy
        self.video_stop_button.configure(state="normal" if can_stop else "disabled")
        self.video_cancel_button.configure(state="normal" if can_stop else "disabled")
        for widget in (self.video_path_entry, self.video_browse_button,
                       getattr(self, "ffmpeg_path_entry", None), getattr(self, "ffmpeg_browse_button", None)):
            if widget is not None:
                widget.configure(state="disabled" if active or self.busy else "normal")
        for widget in (self.video_fps_combo, self.video_bitrate_combo,
                       getattr(self, "video_codec_combo", None),
                       getattr(self, "video_speed_combo", None)):
            if widget is not None:
                widget.configure(state="disabled" if active or self.busy else "readonly")
        self.reshade_configure_button.configure(state="normal" if ready and not active and not self.busy and not self.playing else "disabled")
        self.reshade_forget_button.configure(state="normal" if not active and not self.busy and not self.playing else "disabled")
        self._refresh_reshade(controller_status, active)

    def _refresh_reshade(self, controller_status, video_active=False):
        bridge = self.controller._native_bridge()
        ready = recording_ready(controller_status)
        try:
            shade = bridge.media_status().get("reshade", {}) if bridge is not None else {}
            state = int(shade.get("state", 0))
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            # Optional media telemetry must not stop the camera/session UI or
            # repeat the same failure in the log ten times every second.
            message = "ReShade status unavailable: " + str(exc)
            self.reshade_status_text.set(message)
            self.reshade_disable_button.configure(state="disabled")
            self.reshade_configure_button.configure(state="disabled")
            signature = (id(bridge), message)
            if getattr(self, "_last_reshade_status_error", None) != signature:
                self._last_reshade_status_error = signature
                self._log(message)
            return
        self._last_reshade_status_error = None
        if state == 3:
            self.reshade_status_text.set("ReShade unavailable: " + str(shade.get("message") or "Runtime loading failed."))
        elif state == 2:
            binding = self.app_settings.reshade_binding
            menu = "Menu open." if shade.get("open") else (f"{binding.label} opens its menu." if binding else "Set its menu shortcut in Keybinds.")
            self.reshade_status_text.set("ReShade ready. " + menu)
        elif state == 1:
            self.reshade_status_text.set("Loading ReShade…")
        elif not self.busy:
            self.reshade_status_text.set("ReShade disabled. Choose a runtime DLL to enable its in-game menu.")
        self.reshade_disable_button.configure(state="normal" if ready and state in (1, 2) and not self.busy and not video_active else "disabled")
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
            self.reshade_runtime_path.set(path)

    def _configure_reshade(self, automatic=False):
        def operation():
            from dolly.settings import reshade_config_path
            selected = self.app_settings.reshade_runtime_path if automatic else self.reshade_runtime_path.get().strip()
            path = Path(selected).expanduser().absolute()
            if path.suffix.lower() != ".dll" or not path.is_file():
                raise ValueError("Choose the 64-bit ReShade runtime DLL.")
            settings = replace(self.app_settings, reshade_runtime_path=str(path))
            config = reshade_config_path()
            self._auto_reshade_bridge = self.controller._native_bridge()
            def configure():
                bridge = self.controller._native_bridge()
                if bridge is None or not recording_ready(self.controller.status()):
                    raise RuntimeError("Launch a DirectX 11 replay through Dolly before enabling ReShade.")
                config.parent.mkdir(parents=True, exist_ok=True)
                bridge.configure_reshade(str(path), str(config))
                save_settings(settings)
            def complete(_result):
                self.app_settings = settings
                self.reshade_status_text.set("ReShade requested. Use its menu shortcut to choose effects and presets.")
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
        from dolly.launcher import LaunchError, validate_game
        path = self.game_path.get().strip()
        if not path:
            self.native_status_text.set("Choose the Deadlock folder first.")
            return
        try:
            paths = validate_game(path)
            report = compatibility.scan_game_modules(paths.game_dir)
        except (compatibility.CompatibilityError, LaunchError, OSError) as exc:
            self.native_status_text.set(str(exc))
            return
        self.native_status_text.set(report.describe())
        self._log(report.details())

    def _build_advanced_startup(self):
        dialog = tk.Toplevel(self.root)
        self.advanced_startup_dialog = dialog
        dialog.title("Advanced launch · Deadlock Dolly")
        dialog.configure(bg=BG)
        dialog.geometry(self._window_size(1050, 650))
        dialog.transient(self.root)
        dialog.protocol("WM_DELETE_WINDOW", dialog.withdraw)
        tab = ttk.Frame(dialog, padding=16)
        tab.pack(fill="both", expand=True)
        tab.columnconfigure(0, weight=1)
        intro = ttk.Frame(tab, style="Card.TFrame", padding=16)
        intro.grid(row=0, column=0, sticky="ew")
        intro.columnconfigure(1, weight=1)
        ttk.Label(intro, text="LOCAL REPLAY SESSION", style="CardTitle.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 13))
        ttk.Label(intro, text="Deadlock executable", style="CardMuted.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 14))
        ttk.Entry(intro, textvariable=self.game_path).grid(row=1, column=1, sticky="ew")
        ttk.Button(intro, text="Browse…", command=self._browse_game).grid(row=1, column=2, padx=(9, 0))
        ttk.Label(intro, text="Replay file · .dem", style="CardMuted.TLabel").grid(row=2, column=0, sticky="w", padx=(0, 14), pady=(10, 0))
        ttk.Entry(intro, textvariable=self.demo_path).grid(row=2, column=1, sticky="ew", pady=(10, 0))
        ttk.Button(intro, text="Browse…", command=self._browse_demo).grid(row=2, column=2, padx=(9, 0), pady=(10, 0))
        connection = ttk.Frame(intro, style="Card.TFrame")
        connection.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        ttk.Label(connection, text="Console link", style="CardMuted.TLabel").pack(side="left", padx=(0, 9))
        ttk.Combobox(connection, textvariable=self.protocol, values=("Netconsole", "VConsole"), state="readonly", width=13).pack(side="left")
        ttk.Label(connection, text="Developer mode  ·  -insecure", style="CardMuted.TLabel").pack(side="right")
        driver = ttk.Frame(intro, style="Card.TFrame")
        driver.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        ttk.Label(driver, text="Camera driver", style="CardMuted.TLabel").pack(side="left", padx=(0, 9))
        self.camera_driver_combo = ttk.Combobox(driver, textvariable=self.camera_driver,
            values=("Native (experimental)", "Console (legacy)"), state="readonly", width=23)
        self.camera_driver_combo.pack(side="left")
        ttk.Label(driver, text="Choose before launch. Native follows rendered views.",
                  style="CardMuted.TLabel").pack(side="left", padx=(12, 0))
        compat = ttk.Frame(intro, style="Card.TFrame")
        compat.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        self.native_status_text = tk.StringVar(
            value="Check the installed Deadlock build against Dolly's reviewed Native camera profiles.")
        ttk.Label(compat, textvariable=self.native_status_text, style="CardMuted.TLabel",
                  wraplength=790, justify="left").pack(side="left", fill="x", expand=True)
        ttk.Button(compat, text="Check game build", command=self._check_game_build).pack(side="right", padx=(12, 0))
        flow = ttk.Frame(tab, padding=(0, 14))
        flow.grid(row=1, column=0, sticky="ew")
        for column in range(3):
            flow.columnconfigure(column, weight=1, uniform="startup")
        steps = (
            ("01  Open the hideout", "Launch, then wait for the pre-lobby to load.",
             (("launch_button", "Launch hideout", self._launch, "Primary.TButton"),
              ("connect_button", "Connect", self._connect, "TButton"))),
            ("02  Prepare the replay", "Initialize the unlocker before loading a demo.",
             (("initialize_button", "Initialize unlocker", self._initialize_unlocker, "TButton"),
              ("load_replay_button", "Load replay", self._load_replay, "TButton"))),
            ("03  Start creating", "Once the replay loads, check camera support.",
             (("probe_button", "Check camera support", self._probe, "TButton"),
              ("disconnect_button", "Disconnect", lambda: self._session_operation("Disconnecting", self.controller.disconnect), "Quiet.TButton"))),
        )
        for column, (title, subtitle, buttons) in enumerate(steps):
            card = ttk.Frame(flow, style="Card.TFrame", padding=13)
            card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 5, 0 if column == 2 else 5))
            card.columnconfigure(0, weight=1)
            ttk.Label(card, text=title, style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
            ttk.Label(card, text=subtitle, style="CardMuted.TLabel", wraplength=240).grid(row=1, column=0, sticky="w", pady=(6, 12))
            for row, (attribute, label, command, style) in enumerate(buttons, 2):
                button = ttk.Button(card, text=label, command=command, style=style,
                                    state="normal" if attribute == "launch_button" else "disabled")
                button.grid(row=row, column=0, sticky="ew", pady=(0, 6))
                setattr(self, attribute, button)
        note = ttk.Frame(tab, style="Card.TFrame", padding=(16, 12))
        note.grid(row=2, column=0, sticky="ew")
        tab.bind("<Configure>", lambda event: note.grid() if event.height >= round(465 * self.ui_scale) else note.grid_remove())
        ttk.Label(note, text="FRAME → CAPTURE → FLY → CAPTURE", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(note, text="Open Cameras to capture your free-camera view, shape the framing curve, and play the complete shot.\n"
                  "The replay resumes with the path; HUD visibility returns after playback. Close this session before playing normally.",
                  style="CardMuted.TLabel", wraplength=900, justify="left").pack(anchor="w", pady=(6, 0))

        dialog.withdraw()

    def _build_replays(self):
        tab = self.replays_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(2, weight=1)
        folder = ttk.Frame(tab, padding=(0, 8, 0, 12))
        folder.grid(row=0, column=0, sticky="ew")
        folder.columnconfigure(1, weight=1)
        ttk.Label(folder, text="Replay folder", style="Muted.TLabel").grid(row=0, column=0, padx=(0, 12))
        ttk.Entry(folder, textvariable=self.replay_folder).grid(row=0, column=1, sticky="ew")
        ttk.Button(folder, text="Browse…", command=self._browse_replay_folder).grid(row=0, column=2, padx=(10, 0))
        ttk.Button(folder, text="Refresh", command=self._refresh_replays).grid(row=0, column=3, padx=(8, 0))
        search = ttk.Frame(tab)
        search.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        search.columnconfigure(1, weight=1)
        ttk.Label(search, text="Find replay", style="Muted.TLabel").grid(row=0, column=0, padx=(0, 12))
        ttk.Entry(search, textvariable=self.replay_search).grid(row=0, column=1, sticky="ew")
        self.replay_search.trace_add("write", lambda *_args: self._filter_replays())
        table, self.replay_tree = self._tree(tab, ("name", "size", "modified"), ("Replay", "Size", "Modified"), (470, 100, 190), height=8)
        table.grid(row=2, column=0, sticky="nsew")
        self.replay_tree.column("name", anchor="w", minwidth=170)
        self.replay_tree.column("size", stretch=False)
        self.replay_tree.column("modified", stretch=False)
        self.replay_tree.bind("<<TreeviewSelect>>", self._select_replay)
        self.replay_tree.bind("<Double-1>", lambda _e: self._use_selected_replay())
        bottom = ttk.Frame(tab, padding=(0, 12))
        bottom.grid(row=3, column=0, sticky="ew")
        bottom.columnconfigure(0, weight=1)
        ttk.Label(bottom, textvariable=self.replay_summary, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(bottom, text="Use selected replay", style="Primary.TButton", command=self._use_selected_replay).grid(row=0, column=1)
        ttk.Button(bottom, text="Browse another file…", command=self._browse_demo).grid(row=0, column=2, padx=(8, 0))

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
        search = self.replay_search.get().strip().casefold()
        selected_path = self.demo_path.get()
        self.replay_tree.delete(*self.replay_tree.get_children())
        total = 0
        for index, entry in enumerate(self.replay_entries):
            if search and search not in entry.name.casefold():
                continue
            size = f"{entry.size_bytes / (1024 * 1024):.1f} MB"
            modified = datetime.fromtimestamp(entry.modified_ns / 1e9).strftime("%Y-%m-%d  %H:%M")
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
            self.notebook.select(self.setup_tab)
            self.status_text.set("Replay selected. Press Play replay to begin.")

    def _browse_replay_folder(self):
        folder = filedialog.askdirectory(parent=self.root, title="Choose replay folder", initialdir=self.replay_folder.get() or None)
        if folder:
            self.replay_folder.set(folder)
            self._refresh_replays()

    def _build_keybinds(self):
        tab = self.keybinds_tab
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=0, minsize=300)
        tab.rowconfigure(1, weight=1)
        title = ttk.Frame(tab, padding=(0, 8, 0, 12))
        title.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(title, text="MAKE THE CONTROLS YOURS", style="Section.TLabel").pack(anchor="w")
        ttk.Label(title, text="Bindings are saved across launches. F7 always opens the console and releases camera input.", style="Muted.TLabel").pack(anchor="w", pady=(5, 0))
        table, self.bindings_tree = self._tree(tab, ("action", "binding"), ("Action", "Shortcut"), (310, 160), height=9)
        table.grid(row=1, column=0, sticky="nsew", padx=(0, 14))
        self.bindings_tree.column("action", anchor="w")
        self.bindings_tree.column("binding", anchor="w")
        self.bindings_tree.bind("<<TreeviewSelect>>", self._select_binding)
        edit = ttk.Frame(tab, style="Card.TFrame", padding=14)
        edit.grid(row=1, column=1, sticky="nsew")
        edit.columnconfigure(0, weight=1)
        ttk.Label(edit, textvariable=self.binding_action, style="CardTitle.TLabel", wraplength=260).grid(row=0, column=0, sticky="w", pady=(0, 12))
        ttk.Label(edit, text="Key or mouse button", style="CardMuted.TLabel").grid(row=1, column=0, sticky="w", pady=(0, 5))
        self.binding_chooser = ttk.Combobox(edit, textvariable=self.binding_key, values=("Unbound", *EDITOR_KEY_CHOICES), state="readonly")
        self.binding_chooser.grid(row=2, column=0, sticky="ew")
        self.binding_record_button = ttk.Button(edit, text="Press a key / mouse button…", command=self._record_binding)
        self.binding_record_button.grid(row=3, column=0, sticky="ew", pady=(8, 10))
        mods = ttk.Frame(edit, style="Card.TFrame")
        mods.grid(row=4, column=0, sticky="w")
        for label, var in (("Ctrl", self.binding_ctrl), ("Alt", self.binding_alt), ("Shift", self.binding_shift)):
            ttk.Checkbutton(mods, text=label, variable=var).pack(side="left", padx=(0, 14))
        presets = ttk.Frame(edit, style="Card.TFrame")
        presets.grid(row=5, column=0, sticky="ew", pady=(12, 8))
        for label, key in (("Mouse 4", "Mouse4"), ("Mouse 5", "Mouse5")):
            ttk.Button(presets, text=label, style="Quiet.TButton", command=lambda k=key: self._set_binding_fields(EditorBinding(k))).pack(side="left", padx=(0, 8))
        self.binding_save_button = ttk.Button(edit, text="Save binding", style="Primary.TButton", command=self._save_selected_binding)
        self.binding_save_button.grid(row=6, column=0, sticky="ew", pady=(2, 8))
        ttk.Label(edit, textvariable=self.binding_feedback, style="CardMuted.TLabel", wraplength=260).grid(row=7, column=0, sticky="nw", pady=(4, 14))
        edit.rowconfigure(7, weight=1)
        ttk.Button(edit, text="Restore default bindings", style="Quiet.TButton", command=self._reset_editor_bindings).grid(row=8, column=0, sticky="ew")
        motion = ttk.Frame(tab, padding=(0, 14))
        motion.grid(row=2, column=0, columnspan=2, sticky="ew")
        for label, variable in (("Move speed", self.editor_move_speed), ("Mouse sensitivity", self.editor_sensitivity)):
            ttk.Label(motion, text=label, style="Muted.TLabel").pack(side="left", padx=(0, 8))
            ttk.Entry(motion, textvariable=variable, width=7).pack(side="left", padx=(0, 18))
        ttk.Button(motion, text="Save movement settings", command=self._save_movement_settings).pack(side="left")
        self._refresh_bindings()

    def _refresh_bindings(self, selected=None):
        if not hasattr(self, "bindings_tree"):
            return
        selected = selected or next(iter(self.bindings_tree.selection()), ACTION_ORDER[0])
        self.bindings_tree.delete(*self.bindings_tree.get_children())
        for action in ACTION_ORDER:
            binding = self.app_settings.action_bindings.get(action)
            self.bindings_tree.insert("", "end", iid=action, values=(ACTION_LABELS[action], binding.label if binding else "Unbound"))
        binding = self.app_settings.reshade_binding
        self.bindings_tree.insert("", "end", iid="reshade", values=("Toggle ReShade menu", binding.label if binding else "Unbound"))
        self.bindings_tree.selection_set(selected)
        self._select_binding()

    def _select_binding(self, _event=None):
        selected = self.bindings_tree.selection()
        if not selected:
            return
        action = selected[0]
        self.binding_action.set("Toggle ReShade menu" if action == "reshade" else ACTION_LABELS[action])
        self._set_binding_fields(self.app_settings.reshade_binding if action == "reshade" else self.app_settings.action_bindings.get(action))
        self.binding_feedback.set("Choose a shortcut, then save. Conflicts are shown before anything changes.")

    def _set_binding_fields(self, binding):
        self.binding_key.set(binding.key if binding else "Unbound")
        self.binding_ctrl.set(bool(binding and binding.ctrl))
        self.binding_alt.set(bool(binding and binding.alt))
        self.binding_shift.set(bool(binding and binding.shift))

    def _record_binding(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Press a shortcut")
        dialog.configure(bg=BG)
        dialog.geometry(self._window_size(460, 200))
        dialog.transient(self.root)
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=22)
        body.pack(fill="both", expand=True)
        label = ttk.Label(body, text="Press a keyboard key or a side mouse button.\nEscape cancels; F7 is reserved for the console.", style="Muted.TLabel", wraplength=400)
        label.pack(anchor="w", pady=(0, 16))
        ttk.Button(body, text="Cancel", command=dialog.destroy).pack(anchor="e")
        def record(event):
            key = _binding_event_key(event)
            if key == "Escape":
                dialog.destroy()
                return "break"
            if key is None:
                return "break"
            if key in ("Ctrl", "Alt", "Shift"):
                if str(event.type) not in ("3", "KeyRelease"):
                    return "break"
                self._set_binding_fields(EditorBinding(key))
                dialog.destroy()
                return "break"
            if key == "F7":
                label.configure(text="F7 is reserved for the console. Choose another shortcut.")
                return "break"
            state = int(getattr(event, "state", 0))
            self._set_binding_fields(EditorBinding(key, bool(state & 0x4), bool(state & 0x20000 or state & 0x8), bool(state & 0x1)))
            dialog.destroy()
            return "break"
        dialog.bind("<KeyPress>", record)
        dialog.bind("<KeyRelease>", record)
        dialog.bind("<ButtonPress>", record)
        dialog.after(100, dialog.focus_force)

    def _save_selected_binding(self):
        def operation():
            selected = self.bindings_tree.selection()
            if not selected:
                return
            key = self.binding_key.get()
            binding = None if key == "Unbound" else EditorBinding(key, self.binding_ctrl.get(), self.binding_alt.get(), self.binding_shift.get())
            if selected[0] == "reshade":
                settings = self.app_settings.with_reshade_binding(binding)
            else:
                bindings = dict(self.app_settings.action_bindings)
                bindings[selected[0]] = binding
                settings = self.app_settings.with_action_bindings(validate_action_bindings(bindings))
            self._persist_preferences(settings, "Binding saved. Your in-game controls use this shortcut.")
        self._guard("Keybind", operation)

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
        dialog.title("Launch options · Deadlock Dolly")
        dialog.configure(bg=BG)
        dialog.geometry(self._window_size(690, 350))
        dialog.transient(self.root)
        body = ttk.Frame(dialog, padding=22)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        ttk.Label(body, text="LAUNCH OPTIONS", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(body, text="Always included: -dev  -insecure  -console  -dx11", style="Accent.TLabel").grid(row=1, column=0, sticky="w", pady=(12, 8))
        ttk.Label(body, text="Additional display options", style="Muted.TLabel").grid(row=2, column=0, sticky="w", pady=(6, 5))
        value = tk.StringVar(value=self.launch_options.get())
        ttk.Entry(body, textvariable=value).grid(row=3, column=0, sticky="ew")
        ttk.Label(body, text="Examples: -windowed -w 1920 -h 1080\nReplay loading, console connection and security settings are managed by Dolly.", style="Muted.TLabel", wraplength=620).grid(row=4, column=0, sticky="w", pady=(9, 16))
        def save():
            def operation():
                text = value.get().strip()
                parse_launch_options(text)
                candidate = replace(self.app_settings, launch_options=text)
                self.launch_options.set(text)
                if self._persist_preferences(candidate, "Launch options saved for the next session."):
                    dialog.destroy()
            self._guard("Launch options", operation)
        ttk.Button(body, text="Save options", style="Primary.TButton", command=save).grid(row=5, column=0, sticky="e")

    def _start_editing_session(self):
        def operation():
            game, demo = self.game_path.get().strip(), self.demo_path.get().strip()
            if not game or not demo:
                raise ValueError("Choose your Deadlock executable and a .dem replay first.")
            options = self.launch_options.get().strip()
            parse_launch_options(options)
            settings = replace(self.app_settings, game_path=game, demo_path=demo,
                               replay_folder=self.replay_folder.get().strip(), launch_options=options)
            cancel = threading.Event()
            protocol = "vconsole" if self.protocol.get() == "VConsole" else "netcon"
            native = self.camera_driver.get() == "Native (experimental)"
            self._close_paused_camera(stop=False)
            self._disable_external_input()
            def start():
                save_settings(settings)
                return self.controller.start_editing(game, demo, protocol=protocol, native=native,
                    launch_options=options, cancel_event=cancel)
            def complete(result):
                self.app_settings = settings
                self._editing_started(result)
            if self._submit("Starting replay editor", start, complete):
                self.startup_cancel = cancel
                self.startup_progress.set("Launching Deadlock and preparing the hideout…")
                self.startup_bar.start(15)
        self._guard("Start replay editor", operation)

    def _editing_started(self, result):
        self.startup_cancel = None
        self.startup_bar.stop()
        # Keep the driver the user selected; the live indicator reports which
        # backend the running session actually uses.
        self.startup_progress.set("Replay paused and ready. Return to Deadlock to frame your first camera.")
        self.status_text.set("Use your editor shortcut to open the in-game panel. F7 opens the console.")
        editor_session.configure(self)
        self.notebook.select(self.camera_tab)

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
        self._close_paused_camera(stop=False)

    def _tree(self, parent, columns, labels, widths=None, height=6):
        """Only data lists scroll; editor panels retain their available width."""
        frame = ttk.Frame(parent, style="Card.TFrame")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse", height=height)
        for index, (column, label) in enumerate(zip(columns, labels)):
            tree.heading(column, text=label)
            width = widths[index] if widths else 80
            tree.column(column, width=width, minwidth=45, anchor="e", stretch=True)
        tree.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=yscroll.set)
        return frame, tree

    def _build_camera(self):
        tab = self.camera_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(2, weight=1)
        capture = ttk.Frame(tab)
        capture.grid(row=0, column=0, sticky="ew", pady=(2, 9))
        self.capture_buttons = []
        for text, command, style in (("Start path here", self._start_path_here, "TButton"),
                                      ("+ Capture camera", self.capture_here, "Primary.TButton"),
                                      ("Replace selected", self._replace_camera_here, "TButton")):
            button = ttk.Button(capture, text=text, command=command, style=style)
            button.pack(side="left", padx=(0, 7))
            self.capture_buttons.append(button)
        self.capture_hotkey_checkbox = ttk.Checkbutton(capture, textvariable=self.hotkey_label, variable=self.hotkey_enabled,
                        command=self._toggle_capture_hotkey)
        self.capture_hotkey_checkbox.pack(side="right")
        timing = ttk.Frame(tab)
        timing.grid(row=1, column=0, sticky="ew", pady=(0, 11))
        ttk.Label(timing, text="Capture timing", style="Muted.TLabel").pack(side="left", padx=(0, 7))
        mode = ttk.Combobox(timing, textvariable=self.capture_mode,
                           values=("Replay timing", "Timed shot"), state="readonly", width=13)
        mode.pack(side="left")
        mode.bind("<<ComboboxSelected>>", self._capture_mode_changed)
        ttk.Label(timing, text="Spacing", style="Muted.TLabel").pack(side="left", padx=(14, 6))
        self.segment_entry = ttk.Entry(timing, textvariable=self.segment_seconds, width=5,
                                       state="disabled" if self.capture_mode.get() == "Replay timing" else "normal")
        self.segment_entry.pack(side="left")
        ttk.Label(timing, text="s", style="Muted.TLabel").pack(side="left", padx=(4, 12))
        ttk.Checkbutton(timing, text="Renderer relief", variable=self.seek_relief,
                        command=self._set_seek_relief).pack(side="left")
        ttk.Label(timing, textvariable=self.path_summary, style="Muted.TLabel").pack(side="right")
        ttk.Button(timing, text="Keybinds…", style="Quiet.TButton",
                   command=self._show_keybinds).pack(side="right", padx=(0, 14))
        ttk.Button(timing, text="Paused camera…", style="Quiet.TButton",
                   command=self._open_paused_camera).pack(side="right", padx=(0, 7))
        workspace = ttk.Frame(tab)
        workspace.grid(row=2, column=0, sticky="nsew")
        workspace.columnconfigure(0, weight=1)
        workspace.columnconfigure(1, weight=0, minsize=280)
        workspace.rowconfigure(0, weight=1)
        left = ttk.Frame(workspace)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=3, minsize=112)
        left.rowconfigure(1, weight=2, minsize=148)
        table_frame, self.camera_tree = self._tree(left, ("view", *FIELDS),
                                                  ("Camera", *FIELD_LABELS), height=3)
        table_frame.grid(row=0, column=0, sticky="nsew")
        self.camera_tree.configure(displaycolumns=("view", "time", "aspect_ratio", "roll"))
        self.camera_tree.column("view", anchor="w", width=145, minwidth=95)
        self.camera_tree.column("time", width=110, minwidth=88)
        self.camera_tree.column("aspect_ratio", width=110, minwidth=84)
        self.camera_tree.column("roll", width=85, minwidth=60)
        self.camera_tree.heading("time", text="Arrive · seconds")
        self.camera_tree.heading("aspect_ratio", text="Aspect ratio")
        self.camera_tree.heading("roll", text="Bank °")
        self.camera_tree.bind("<<TreeviewSelect>>", self._select_key)
        self.camera_tree.bind("<Double-1>", lambda _event: self.lens_entry.focus_set())
        self.camera_tree.bind("<Delete>", lambda _event: self._delete_key())
        graph = ttk.Frame(left, style="Card.TFrame", padding=(10, 8, 10, 5))
        graph.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        graph.columnconfigure(0, weight=1)
        graph.rowconfigure(1, weight=1)
        graph_header = ttk.Frame(graph, style="Card.TFrame")
        graph_header.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(graph_header, text="FRAMING CURVE", style="CardTitle.TLabel").pack(side="left")
        curve_mode = ttk.Combobox(graph_header, textvariable=self.lens_interpolation,
                                 values=("smooth", "linear", "step"), state="readonly", width=9)
        curve_mode.pack(side="right")
        curve_mode.bind("<<ComboboxSelected>>", lambda _event: self._apply_options())
        self.aspect_curve = AspectCurve(graph, on_change=self._change_curve_key, on_select=self._select_curve_key)
        self.aspect_curve.grid(row=1, column=0, sticky="nsew")
        inspector = ttk.Frame(workspace)
        inspector.grid(row=0, column=1, sticky="nsew")
        inspector.columnconfigure(0, weight=1)
        inspector.rowconfigure(1, weight=1)
        selected = ttk.Frame(inspector, style="Card.TFrame", padding=12)
        selected.grid(row=0, column=0, sticky="ew")
        selected.columnconfigure(1, weight=1)
        title = ttk.Frame(selected, style="Card.TFrame")
        title.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Label(title, textvariable=self.selected_text, style="CardTitle.TLabel").pack(side="left")
        more = ttk.Menubutton(title, text="More")
        more.pack(side="right")
        actions_menu = tk.Menu(more, tearoff=False, bg=PANEL, fg=TEXT, activebackground="#274e4b", activeforeground=TEXT)
        actions_menu.add_command(label="Coordinates / timing…", command=self._open_coordinates)
        actions_menu.add_separator()
        actions_menu.add_command(label="Delete selected camera", command=self._delete_key)
        more.configure(menu=actions_menu)
        ttk.Label(selected, text="Aspect ratio", style="CardMuted.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 9))
        self.lens_entry = ttk.Entry(selected, textvariable=self.key_vars["aspect_ratio"], width=11)
        self.lens_entry.grid(row=1, column=1, sticky="ew")
        self.lens_entry.bind("<Return>", lambda _event: self._update_key())
        pair = ttk.Frame(selected, style="Card.TFrame")
        pair.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        pair.columnconfigure(0, weight=1)
        pair.columnconfigure(1, weight=1)
        for column, (field, label) in enumerate((("time", "Arrive · seconds"), ("roll", "Bank · degrees"))):
            ttk.Label(pair, text=label, style="CardMuted.TLabel").grid(row=0, column=column, sticky="w", pady=(0, 4), padx=(0, 8 if column == 0 else 0))
            entry = ttk.Entry(pair, textvariable=self.key_vars[field], width=10)
            entry.grid(row=1, column=column, sticky="ew", padx=(0, 8 if column == 0 else 0))
            entry.bind("<Return>", lambda _event: self._update_key())
        standard = ttk.Frame(selected, style="Card.TFrame")
        standard.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 8))
        ttk.Label(standard, text="Normal", style="CardMuted.TLabel").pack(side="left", padx=(0, 7))
        standard_combo = ttk.Combobox(standard, textvariable=self.standard_aspect, values=tuple(ASPECT_PRESETS), width=8)
        standard_combo.pack(side="left")
        standard_combo.bind("<<ComboboxSelected>>", lambda _event: self._apply_options())
        standard_combo.bind("<Return>", lambda _event: self._apply_options())
        ttk.Button(standard, text="Reset", style="Quiet.TButton", command=self._reset_aspect).pack(side="right")
        buttons = ttk.Frame(selected, style="Card.TFrame")
        buttons.grid(row=4, column=0, columnspan=2, sticky="ew")
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        ttk.Button(buttons, text="Update camera", command=self._update_key).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(buttons, text="Preview", command=self._apply_selected).grid(row=0, column=1, sticky="ew", padx=(4, 0))
        view = ttk.Frame(inspector, style="Card.TFrame", padding=(10, 8))
        view.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        ttk.Label(view, text="PATH · TOP DOWN", style="CardMuted.TLabel").pack(anchor="w", pady=(0, 5))
        self.canvas = tk.Canvas(view, width=230, height=55, background=PANEL, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _event: self._draw_path())

    def _open_paused_camera(self):
        if getattr(self, "native_editor_active", False):
            def open_panel():
                self.controller.enter_native_flight()
                bridge = self.controller._native_bridge()
                if bridge is None:
                    raise RuntimeError("The native editor disconnected. Reconnect the replay to open its controls.")
                bridge.configure_editor(owner="panel")
            def opened(_result):
                editor_session.configure(self)
                self.status_text.set("Paused camera controls are open inside Deadlock. Return to the game to frame your view.")
            self._submit("Opening in-game camera controls", open_panel, opened)
            return
        if self.paused_dialog is not None and self.paused_dialog.winfo_exists():
            self.paused_dialog.lift()
            return
        dialog = tk.Toplevel(self.root)
        self.paused_dialog = dialog
        self.paused_input = CameraInput(self.controller.game_pid)
        self.paused_requested = False
        self.paused_cancel = None
        self.paused_game_enabled = tk.BooleanVar(value=False)
        self.paused_move_speed = tk.StringVar(value="240")
        self.paused_turn_speed = tk.StringVar(value="60")
        self.paused_status = tk.StringVar(value="Start controls to hold the current replay moment.")
        self.paused_view_text = tk.StringVar(value=self.selected_text.get())
        dialog.title("Paused camera — Deadlock Dolly")
        dialog.configure(bg=BG)
        dialog.geometry(self._window_size(610, 660))
        dialog.minsize(*self._window_dimensions(590, 650))
        dialog.transient(self.root)
        # Deliberately nonmodal: Deadlock and the capture shortcut remain usable.
        dialog.protocol("WM_DELETE_WINDOW", self._close_paused_camera)
        body = ttk.Frame(dialog, padding=18)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        ttk.Label(body, text="FRAME A PAUSED MOMENT", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(body, text="Move the free camera or switch saved views while paused.\n"
                  "Switching keeps this replay moment; camera arrival times are ignored.",
                  style="Muted.TLabel", wraplength=round(550 * self.ui_scale), justify="left").grid(row=1, column=0, sticky="w", pady=(7, 13))
        saved = ttk.Frame(body, style="Card.TFrame", padding=12)
        saved.grid(row=2, column=0, sticky="ew")
        ttk.Label(saved, textvariable=self.paused_view_text, style="CardTitle.TLabel").pack(anchor="w", pady=(0, 8))
        row = ttk.Frame(saved, style="Card.TFrame")
        row.pack(fill="x")
        self.paused_switch_buttons = []
        for label, delta in (("← Previous", -1), ("Next →", 1), ("Apply selected", 0)):
            button = ttk.Button(row, text=label, command=lambda step=delta: self._switch_paused_camera(step))
            button.pack(side="left", padx=(0, 7))
            self.paused_switch_buttons.append(button)
        controls = ttk.Frame(body)
        controls.grid(row=3, column=0, sticky="ew", pady=(14, 8))
        self.paused_start_button = ttk.Button(controls, text="Start camera controls", style="Primary.TButton", command=self._start_paused_controls)
        self.paused_start_button.pack(side="left", padx=(0, 8))
        self.paused_stop_button = ttk.Button(controls, text="Stop controls", command=self._stop_paused_controls)
        self.paused_stop_button.pack(side="left")
        self.paused_capture_button = ttk.Button(controls, text="Capture view", command=self.capture_here)
        self.paused_capture_button.pack(side="right")
        settings = ttk.Frame(body)
        settings.grid(row=4, column=0, sticky="ew", pady=(0, 9))
        ttk.Label(settings, text="Move units / s", style="Muted.TLabel").pack(side="left", padx=(0, 6))
        self.paused_move_entry = ttk.Entry(settings, textvariable=self.paused_move_speed, width=6)
        self.paused_move_entry.pack(side="left", padx=(0, 15))
        ttk.Label(settings, text="Turn degrees / s", style="Muted.TLabel").pack(side="left", padx=(0, 6))
        self.paused_turn_entry = ttk.Entry(settings, textvariable=self.paused_turn_speed, width=6)
        self.paused_turn_entry.pack(side="left")
        ttk.Checkbutton(body, text="Enable keyboard flight while Deadlock is focused", variable=self.paused_game_enabled,
                        command=self._toggle_paused_game_input).grid(row=5, column=0, sticky="w", pady=(0, 10))
        self.paused_pad = pad = ttk.Frame(body, style="Card.TFrame", padding=12, takefocus=True)
        pad.grid(row=6, column=0, sticky="ew")
        pad.columnconfigure(0, weight=1)
        pad.columnconfigure(1, weight=1)
        ttk.Label(pad, text="HOLD TO MOVE", style="CardMuted.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))
        ttk.Label(pad, text="HOLD TO LOOK", style="CardMuted.TLabel").grid(row=0, column=1, sticky="w", padx=(14, 0), pady=(0, 8))
        self.paused_hold_buttons = []
        for column, keys in enumerate(((("Forward · W", "w"), ("Back · S", "s"), ("Left · A", "a"),
                                         ("Right · D", "d"), ("Up · Space", "space"), ("Down · Ctrl", "ctrl")),
                                       (("Look up · ↑", "up"), ("Look down · ↓", "down"),
                                        ("Look left · ←", "left"), ("Look right · →", "right")))):
            group = ttk.Frame(pad, style="Card.TFrame")
            group.grid(row=1, column=column, sticky="new", padx=(0 if column == 0 else 14, 0))
            for slot in range(2):
                group.columnconfigure(slot, weight=1, uniform="move_buttons")
            for index, (label, key) in enumerate(keys):
                button = ttk.Button(group, text=label, state="disabled", takefocus=False)
                button.grid(row=index // 2, column=index % 2, sticky="ew", padx=(0, 5 if index % 2 == 0 else 0), pady=(0, 6))
                button.bind("<ButtonPress-1>", lambda event, name=key: self._paused_button_press(event, name))
                button.bind("<ButtonRelease-1>", self._paused_buttons_release)
                self.paused_hold_buttons.append(button)
        self.paused_focus_button = ttk.Button(pad, text="Focus keyboard controls", style="Quiet.TButton", command=pad.focus_set)
        self.paused_focus_button.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        pad.bind("<FocusIn>", lambda _event: self.paused_input.set_gui_focus(True) if self.paused_input else None)
        pad.bind("<FocusOut>", lambda _event: self._paused_input_blur())
        pad.bind("<KeyPress>", lambda event: self._paused_key(event, True))
        pad.bind("<KeyRelease>", lambda event: self._paused_key(event, False))
        dialog.bind("<ButtonRelease-1>", self._paused_buttons_release, add="+")
        dialog.bind("<Unmap>", lambda event: self._paused_input_blur() if event.widget is dialog else None)
        ttk.Label(body, text="WASD move · Space / Ctrl height · Arrows look · Shift 4× speed\n"
                  "Escape stops controls. Use Timed shot to capture several views here.",
                  style="Muted.TLabel", wraplength=round(550 * self.ui_scale), justify="left").grid(row=7, column=0, sticky="w", pady=(10, 8))
        ttk.Label(body, textvariable=self.paused_status, style="Accent.TLabel", wraplength=round(550 * self.ui_scale)).grid(row=8, column=0, sticky="w")
        ttk.Label(body, text="Stop keeps this view paused. Main Stop / restore also resets camera variables.",
                  style="Muted.TLabel", wraplength=round(550 * self.ui_scale)).grid(row=9, column=0, sticky="w", pady=(7, 0))
        self._poll_paused_camera(self.controller.status())

    def _paused_input_blur(self):
        helper = getattr(self, "paused_input", None)
        if helper is not None:
            helper.set_gui_focus(False)
            helper.clear()
        for button in getattr(self, "paused_hold_buttons", ()):
            try:
                button.state(["!pressed"])
            except tk.TclError:
                pass

    def _paused_button_press(self, event, key):
        if not self.paused_requested or self.busy:
            return "break"
        self.paused_pad.focus_set()
        self.paused_input.set_gui_focus(True)
        self.paused_input.press(key)
        event.widget.state(["pressed"])
        return "break"

    def _paused_buttons_release(self, _event=None):
        helper = getattr(self, "paused_input", None)
        if helper is not None:
            helper.clear()
        for button in getattr(self, "paused_hold_buttons", ()):
            try:
                button.state(["!pressed"])
            except tk.TclError:
                pass
        return "break"

    def _paused_key(self, event, pressed):
        helper = getattr(self, "paused_input", None)
        if helper is None:
            return None
        # These bindings belong only to the explicitly focused control pad.
        # Typing speeds, names, and project values never moves the camera.
        key = event.keysym.lower()
        if key not in ("w", "a", "s", "d", "space", "ctrl", "shift", "left", "right", "up", "down", "escape",
                       "control_l", "control_r", "shift_l", "shift_r", "alt_l", "alt_r", "super_l", "super_r"):
            return None
        # Releases must still clear a held key when a console operation starts.
        if not pressed:
            helper.release(key)
        elif key == "escape" and self.paused_requested:
            self._stop_paused_controls()
        elif not self.paused_requested or self.busy:
            helper.clear()
            return None
        else:
            helper.press(key)
        return "break"

    def _toggle_paused_game_input(self):
        try:
            self.paused_input.set_game_enabled(self.paused_game_enabled.get())
        except (OSError, RuntimeError) as exc:
            self.paused_game_enabled.set(False)
            self._error("In-game camera controls", exc)

    def _paused_options(self):
        move = _finite(self.paused_move_speed.get(), "Move speed")
        turn = _finite(self.paused_turn_speed.get(), "Turn speed")
        if not 0 < move <= 10000 or not 0 < turn <= 720:
            raise ValueError("Move speed must be greater than 0 and at most 10,000; turn speed must be greater than 0 and at most 720.")
        return move, turn

    def _paused_source(self):
        helper = self.paused_input
        previous = getattr(self, "paused_cancel", None)
        if previous is not None:
            previous.set()
        cancel = threading.Event()
        self.paused_cancel = cancel
        def sample():
            return CameraMotion(stop=True) if cancel.is_set() else helper.sample()
        return sample, cancel

    def _start_paused_controls(self):
        if getattr(self, "native_editor_active", False):
            self._submit("Starting native paused flight", self.controller.enter_native_flight)
            return
        def operation():
            move, turn = self._paused_options()
            helper = self.paused_input
            helper.clear()
            source, cancel = self._paused_source()
            def start():
                if cancel.is_set():
                    return
                try:
                    self.controller.begin_paused_camera(cancelled=cancel.is_set)
                except Exception:
                    if cancel.is_set():
                        return
                    raise
                if not cancel.is_set():
                    self.controller.start_paused_flight(source, move_speed=move, turn_speed=turn)
            def started(_result):
                if self.paused_input is helper and not cancel.is_set():
                    self.paused_pad.focus_set()
            if self._submit("Starting paused camera controls", start, started):
                self.paused_run_options = (move, turn)
                self.paused_requested = True
        self._guard("Paused camera controls", operation)

    def _resume_paused_controls(self):
        if not self.paused_requested or self.paused_input is None:
            return
        move, turn = self.paused_run_options
        self.paused_input.clear()
        source, cancel = self._paused_source()
        self._submit("Resuming paused camera controls", lambda: None if cancel.is_set() else self.controller.start_paused_flight(
            source, move_speed=move, turn_speed=turn))

    def _stop_paused_controls(self):
        self.paused_requested = False
        cancel = getattr(self, "paused_cancel", None)
        if cancel is not None:
            cancel.set()
        self._paused_input_blur()
        if not self.busy:
            self._submit("Stopping paused camera controls", self.controller.stop_paused_flight)

    def _switch_paused_camera(self, step=0):
        def operation():
            project = self._snapshot()
            index = self._selection_index(self.camera_tree)
            if index is None:
                index = len(project.keyframes) - 1 if step < 0 else 0
            elif step:
                index = (index + step) % len(project.keyframes)
            key = project.keyframes[index]
            helper = self.paused_input
            helper.clear()
            resume = self.paused_requested
            move, turn = self.paused_run_options
            source, cancel = self._paused_source()
            def switch():
                if cancel.is_set():
                    return
                try:
                    frame = self.controller.select_paused_camera(project, key.time, cancelled=cancel.is_set)
                except Exception:
                    if cancel.is_set():
                        return
                    raise
                if resume and not cancel.is_set():
                    self.controller.start_paused_flight(source, move_speed=move, turn_speed=turn)
                return frame
            def switched(_result):
                if self.paused_input is not helper or cancel.is_set():
                    return
                self.camera_tree.selection_set(str(index))
                self.camera_tree.see(str(index))
                self._select_key()
                self.paused_view_text.set(f"Camera {index + 1:02d} · current replay moment")
            self._submit("Switching camera at the paused moment", switch, switched)
        self._guard("Switch paused camera", operation)

    def _poll_paused_camera(self, status):
        if getattr(self, "paused_dialog", None) is None:
            return
        active = bool(status.get("paused_flight"))
        if not self.busy and not active:
            self.paused_requested = False
            self._paused_input_blur()
        tick = status.get("paused_tick")
        if active:
            text = "Camera controls active"
        elif status.get("paused_camera"):
            text = "Camera held · controls stopped"
        else:
            text = "Start controls to hold the current replay moment."
        if tick is not None:
            text += f" · replay tick {int(tick):,}"
        self.paused_status.set(text)
        available = not self.busy and not self.playing and bool(status.get("connected"))
        for button in self.paused_switch_buttons:
            button.configure(state="normal" if available and self.project.keyframes else "disabled")
        self.paused_start_button.configure(state="normal" if available and not active else "disabled")
        self.paused_stop_button.configure(state="normal" if self.paused_requested or active else "disabled")
        self.paused_capture_button.configure(state="normal" if available else "disabled")
        for button in self.paused_hold_buttons:
            button.configure(state="normal" if available and active else "disabled")
        self.paused_focus_button.configure(state="normal" if available and active else "disabled")
        for entry in (self.paused_move_entry, self.paused_turn_entry):
            entry.configure(state="disabled" if self.busy or active else "normal")
        if not self.paused_requested and not active:
            self.paused_view_text.set(self.selected_text.get())

    def _close_paused_camera(self, stop=True):
        helper = getattr(self, "paused_input", None)
        if helper is None:
            return
        self.paused_requested = False
        cancel = getattr(self, "paused_cancel", None)
        if cancel is not None:
            cancel.set()
        helper.close()
        self.paused_input = None
        dialog = self.paused_dialog
        self.paused_dialog = None
        if dialog is not None:
            dialog.destroy()
        self.paused_hold_buttons = []
        if stop and not self.busy and not self.closed:
            self._submit("Stopping paused camera controls", self.controller.stop_paused_flight)

    def _session_operation(self, label, function):
        if self.busy:
            self.status_text.set("Please wait for the current operation to finish.")
            return False
        self._close_paused_camera(stop=False)
        return self._submit(label, function)

    def _open_coordinates(self):
        if self.coordinates_dialog is not None and self.coordinates_dialog.winfo_exists():
            self.coordinates_dialog.lift()
            return
        dialog = tk.Toplevel(self.root)
        self.coordinates_dialog = dialog
        dialog.title("Camera coordinates & shot timing")
        dialog.configure(bg=BG)
        dialog.geometry(self._window_size(560, 530))
        dialog.minsize(*self._window_dimensions(530, 500))
        dialog.transient(self.root)
        body = ttk.Frame(dialog, padding=18)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        ttk.Label(body, text="Camera coordinates", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        self.key_entries = {}
        for index, (field, label) in enumerate(zip(FIELDS, FIELD_LABELS)):
            cell = ttk.Frame(body)
            cell.grid(row=1 + index // 2, column=index % 2, sticky="ew", padx=(0, 14 if index % 2 == 0 else 0), pady=(0, 8))
            cell.columnconfigure(1, weight=1)
            ttk.Label(cell, text=label, style="Muted.TLabel", width=13).grid(row=0, column=0, sticky="w")
            entry = ttk.Entry(cell, textvariable=self.key_vars[field], width=12)
            entry.grid(row=0, column=1, sticky="ew")
            self.key_entries[field] = entry
        actions = ttk.Frame(body)
        actions.grid(row=5, column=0, columnspan=2, sticky="w", pady=(4, 14))
        ttk.Button(actions, text="Update selected", command=self._update_key).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Add entered camera", command=self._add_key).pack(side="left")
        ttk.Label(body, text="Shot timing & movement", style="Section.TLabel").grid(row=6, column=0, columnspan=2, sticky="w", pady=(4, 10))
        for index, (label, variable, values) in enumerate((("Start replay tick", self.start_tick, None), ("Ticks / second", self.tick_rate, None),
                                                         ("Position curve", self.interpolation, ("smooth", "linear")),
                                                         ("Rotation", self.rotation, ("shortest", "unwrapped")))):
            cell = ttk.Frame(body)
            cell.grid(row=7 + index // 2, column=index % 2, sticky="ew", padx=(0, 14 if index % 2 == 0 else 0), pady=(0, 8))
            cell.columnconfigure(1, weight=1)
            ttk.Label(cell, text=label, style="Muted.TLabel", width=13).grid(row=0, column=0, sticky="w")
            widget = (ttk.Combobox(cell, textvariable=variable, values=values, state="readonly", width=11)
                      if values else ttk.Entry(cell, textvariable=variable, width=12))
            widget.grid(row=0, column=1, sticky="ew")
        actions = ttk.Frame(body)
        actions.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        ttk.Button(actions, text="Use current replay tick", command=self._use_current_tick).pack(side="left")
        ttk.Button(actions, text="Apply settings", command=self._apply_options).pack(side="left", padx=8)
        ttk.Button(actions, text="Done", command=dialog.destroy).pack(side="right")
        ttk.Label(body, text="Captured positions stay in view coordinates. Height is Z.\nBank uses Dolly’s last applied value; capture starts with bank 0.",
                  style="Muted.TLabel", wraplength=510).grid(row=10, column=0, columnspan=2, sticky="w", pady=(14, 0))

    def _toggle_coordinates(self):
        self._open_coordinates()

    def _capture_mode_changed(self, _event=None):
        replay_timing = self.capture_mode.get() == "Replay timing"
        self.segment_entry.configure(state="disabled" if replay_timing else "normal")
        self.capture_hint.set(
            "Replay timing: capture pauses the replay and records its current time."
            if replay_timing else "Timed shot: fly between views; spacing sets their arrival times.")
        self.status_text.set(self.capture_hint.get())

    def _build_cvars(self):
        tab = self.cvar_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        intro = ttk.Frame(tab)
        intro.grid(row=0, column=0, sticky="ew", pady=(2, 10))
        ttk.Label(intro, text="CAMERA VARIABLES", style="Section.TLabel").pack(side="left")
        ttk.Checkbutton(intro, text="Renderer relief during seeks", variable=self.seek_relief,
                        command=self._set_seek_relief).pack(side="left", padx=(18, 0))
        ttk.Button(intro, text="Fixed values…", command=self._show_fixed_values).pack(side="right", padx=(8, 0))
        ttk.Button(intro, text="+ Citadel DOF", command=self._dof_preset).pack(side="right", padx=(8, 0))
        ttk.Button(intro, text="+ Range DOF", command=self._range_dof_preset).pack(side="right")
        body = ttk.Frame(tab)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=2, minsize=255)
        body.columnconfigure(1, weight=4, minsize=485)
        body.rowconfigure(0, weight=1)
        left = ttk.Frame(body, style="Card.TFrame", padding=10)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        ttk.Label(left, text="ANIMATED TRACKS", style="CardMuted.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))
        frame, self.track_tree = self._tree(left, ("name",), ("Variable",), (240,), height=3)
        self.track_tree.column("name", anchor="w", minwidth=120)
        frame.grid(row=1, column=0, sticky="nsew")
        ttk.Button(left, text="Remove track", style="Quiet.TButton", command=self._remove_track).grid(row=2, column=0, sticky="w", pady=(7, 0))
        right = ttk.Frame(body, style="Card.TFrame", padding=12)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)
        properties = ttk.Frame(right, style="Card.TFrame")
        properties.grid(row=0, column=0, sticky="ew")
        properties.columnconfigure(0, weight=1)
        ttk.Label(properties, text="Variable name", style="CardMuted.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(properties, textvariable=self.track_name, width=30).grid(row=1, column=0, sticky="ew")
        settings = ttk.Frame(right, style="Card.TFrame")
        settings.grid(row=1, column=0, sticky="ew", pady=8)
        ttk.Label(settings, text="Curve", style="CardMuted.TLabel").pack(side="left", padx=(0, 6))
        ttk.Combobox(settings, textvariable=self.track_mode, values=("step", "linear", "smooth"), state="readonly", width=8).pack(side="left")
        ttk.Label(settings, text="Restore · optional", style="CardMuted.TLabel").pack(side="left", padx=(15, 6))
        ttk.Entry(settings, textvariable=self.restore_value, width=18).pack(side="left")
        track_controls = ttk.Frame(right, style="Card.TFrame")
        track_controls.grid(row=2, column=0, sticky="w", pady=(0, 8))
        ttk.Button(track_controls, text="Create track", command=self._create_track).pack(side="left")
        ttk.Button(track_controls, text="Update settings", command=self._update_track).pack(side="left", padx=7)
        frame, self.value_tree = self._tree(right, ("time", "value"), ("Shot seconds", "Value"), (115, 180), height=2)
        frame.grid(row=3, column=0, sticky="nsew")
        value_controls = ttk.Frame(right, style="Card.TFrame")
        value_controls.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        value_controls.columnconfigure(1, weight=1)
        ttk.Label(value_controls, text="Time", style="CardMuted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(value_controls, textvariable=self.track_time, width=9).grid(row=0, column=1, sticky="w", padx=(8, 12), pady=(0, 6))
        ttk.Label(value_controls, text="Value", style="CardMuted.TLabel").grid(row=1, column=0, sticky="w")
        ttk.Entry(value_controls, textvariable=self.track_value, width=23).grid(row=1, column=1, sticky="ew", padx=(8, 12))
        ttk.Button(value_controls, text="Add / replace", command=self._put_value).grid(row=0, column=2, sticky="ew", pady=(0, 6))
        ttk.Button(value_controls, text="Delete", style="Quiet.TButton", command=self._delete_value).grid(row=1, column=2, sticky="ew")
        ttk.Label(right, text="Range DOF: near blurry · near crisp · far crisp · far blurry. Enter four numbers separated by spaces.",
                  style="CardMuted.TLabel", wraplength=430).grid(row=5, column=0, sticky="w", pady=(8, 0))
        self.track_tree.bind("<<TreeviewSelect>>", self._select_track)
        self.value_tree.bind("<<TreeviewSelect>>", self._select_value)
        self.value_tree.bind("<Delete>", lambda _event: self._delete_value())
        self.fixed_dialog = tk.Toplevel(self.root)
        self.fixed_dialog.title("Fixed camera values")
        self.fixed_dialog.geometry(self._window_size(780, 300))
        self.fixed_dialog.minsize(*self._window_dimensions(740, 270))
        self.fixed_dialog.configure(bg=BG)
        self.fixed_dialog.transient(self.root)
        self.fixed_dialog.protocol("WM_DELETE_WINDOW", self.fixed_dialog.withdraw)
        setup = ttk.Frame(self.fixed_dialog, style="Card.TFrame", padding=12)
        setup.pack(fill="both", expand=True)
        setup.rowconfigure(1, weight=1)
        setup.columnconfigure(0, weight=1)
        ttk.Label(setup, text="FIXED VALUES", style="CardMuted.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6))
        frame, self.setup_tree = self._tree(setup, ("name", "value"), ("Variable", "Value"), (250, 80), height=2)
        self.setup_tree.column("name", anchor="w")
        frame.grid(row=1, column=0, sticky="nsew", padx=(0, 12))
        fixed_form = ttk.Frame(setup, style="Card.TFrame")
        fixed_form.grid(row=1, column=1, sticky="n")
        ttk.Entry(fixed_form, textvariable=self.setup_name, width=26).grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Entry(fixed_form, textvariable=self.setup_value, width=26).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(7, 0))
        ttk.Button(fixed_form, text="Set value", command=self._set_fixed).grid(row=2, column=0, sticky="w", pady=(7, 0))
        ttk.Button(fixed_form, text="Remove", style="Quiet.TButton", command=self._remove_fixed).grid(row=2, column=1, pady=(7, 0))
        self.setup_tree.bind("<<TreeviewSelect>>", self._select_fixed)
        self.fixed_dialog.withdraw()

    def _show_fixed_values(self):
        self.fixed_dialog.deiconify()
        self.fixed_dialog.lift()

    def _build_timeline(self, parent):
        frame = ttk.Frame(parent, padding=(0, 10, 0, 0))
        self.timeline_frame = frame
        frame.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        frame.columnconfigure(0, weight=1)
        timeline = ttk.Frame(frame)
        timeline.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        timeline.columnconfigure(0, weight=1)
        self.slider = ttk.Scale(timeline, from_=0, to=10, variable=self.shot_time, command=self._slide)
        self.slider.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        self.slider.bind("<ButtonPress-1>", lambda _event: setattr(self, "dragging", True))
        self.slider.bind("<ButtonRelease-1>", lambda _event: setattr(self, "dragging", False))
        ttk.Entry(timeline, textvariable=self.time_text, width=9).grid(row=0, column=1)
        ttk.Label(timeline, text="s", style="Muted.TLabel").grid(row=0, column=2, padx=(6, 0))
        controls = ttk.Frame(frame)
        controls.grid(row=1, column=0, sticky="ew")
        for text, command, style in (("▶  Play shot", self._play, "Primary.TButton"),
                                     ("Pause", lambda: self._session_operation("Pausing", self.controller.pause), "TButton"),
                                     ("Stop / restore", lambda: self._session_operation("Stopping", self.controller.stop), "TButton"),
                                     ("Preview frame", self._apply_frame, "Quiet.TButton"),
                                     ("Seek replay", self._seek, "Quiet.TButton")):
            ttk.Button(controls, text=text, command=command, style=style).pack(side="left", padx=(0, 6))
        self.speed_combo = ttk.Combobox(controls, textvariable=self.speed,
                                       values=("0.05", "0.1", "0.25", "0.5", "1", "2", "4"), width=5)
        self.speed_combo.pack(side="right")
        ttk.Label(controls, text="Speed", style="Muted.TLabel").pack(side="right", padx=(9, 6))
        options = ttk.Frame(frame)
        options.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Checkbutton(options, text="Hide HUD", variable=self.hide_hud).pack(side="left")
        ttk.Checkbutton(options, text="Frozen preview", variable=self.frozen).pack(side="left", padx=(16, 0))
        ttk.Label(options, text="Play shot resumes the replay from the first camera.", style="Muted.TLabel").pack(side="left", padx=(18, 0))
        self.rate_combo = ttk.Combobox(options, textvariable=self.rate,
                                      values=("30", "60", "120"), state="readonly", width=5)
        self.rate_combo.pack(side="right")
        ttk.Label(options, text="Updates / s", style="Muted.TLabel").pack(side="right", padx=(9, 6))
        smoothing = ttk.Frame(frame)
        smoothing.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(smoothing, text="Smoothing", style="Muted.TLabel").pack(side="left", padx=(0, 6))
        self.smoothing_combo = ttk.Combobox(smoothing, textvariable=self.smoothing,
                     values=("Off", "Light", "Balanced", "Strong"), state="readonly", width=10)
        self.smoothing_combo.pack(side="left")
        ttk.Label(smoothing, text="Light 80 ms · Balanced 160 ms · Strong 280 ms (real time)",
                  style="Muted.TLabel").pack(side="left", padx=(12, 0))
        ttk.Label(frame, text="Native synchronizes camera + supported DOF curves. Updates / s changes monitoring only; smoothing is for Console.",
                  style="Muted.TLabel").grid(row=4, column=0, sticky="w", pady=(3, 0))

    def _worker_loop(self):
        while True:
            job = self.jobs.get()
            if job is None:
                return
            label, function, callback = job
            try:
                result = function()
            except Exception as exc:
                LOG.exception("Operation failed: %s", label)
                self.events.put(("error", label, exc))
            else:
                self.events.put(("done", label, (result, callback)))

    def _recover_game_config(self):
        def complete(restored):
            messagebox.showinfo("Game configuration",
                f"Recovered or cleaned {len(restored)} session(s)." if restored else
                "No pending recovery or temporary-folder cleanup is needed.", parent=self.root)
        # recover_pending verifies that Deadlock is closed and retains any
        # conflicting user/Steam changes. The usual worker owns this action.
        self._submit("Recovering game configuration", recover_pending, complete)

    def _submit(self, label, function, callback=None):
        if self.closed:
            return False
        if self.busy:
            self.status_text.set("Please wait for the current operation to finish.")
            return False
        self.busy = True
        self.busy_text.set(label + "…")
        self.jobs.put((label, function, callback))
        return True

    def _enqueue_log(self, *parts):
        self.events.put(("log", "", " ".join(str(part) for part in parts)))

    def _log(self, text):
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", str(text).rstrip() + "\n")
        if int(self.log_widget.index("end-1c").split(".")[0]) > 1200:
            self.log_widget.delete("1.0", "200.0")
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")

    def _poll(self):
        if self.closed:
            return
        for _ in range(100):
            try:
                kind, label, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "capture_hotkey":
                self._handle_capture_hotkey(payload)
            elif kind == "log":
                self._log(payload)
            elif kind == "error":
                self.busy = False
                self.busy_text.set("")
                cancelled = self.startup_cancel is not None and self.startup_cancel.is_set()
                if self.startup_cancel is not None:
                    self.startup_cancel = None
                    self.startup_bar.stop()
                    self.startup_progress.set("Startup cancelled." if cancelled else str(payload))
                if cancelled:
                    self.status_text.set("Startup cancelled. You can choose a replay and try again.")
                    self._log(str(payload))
                else:
                    self._error(label, payload)
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
            self.playing = bool(status.get("playing"))
            self._poll_paused_camera(status)
            connected = bool(status.get("connected"))
            stage = str(status.get("startup_stage", "not_launched"))
            stage_labels = {
                "not_launched": "Game not launched",
                "waiting_hideout": "Waiting for hideout",
                "waiting_console": "Connecting to Deadlock",
                "initializing_unlocker": "Preparing camera commands",
                "checking_camera": "Checking camera support",
                "entering_editor": "Starting paused editor",
                "editing_ready": "Paused editor ready",
                "connected": "Connected — initialize in hideout",
                "unlocker_ready": "Unlocker ready — load replay",
                "loading_replay": "Replay requested — wait for loading",
                "replay_ready": "Replay ready",
                "failed": "Startup needs attention",
                "game_closed": "Game closed",
            }
            session_label = stage_labels.get(stage, "Game connected" if connected else "Game not connected")
            if not connected and stage in ("connected", "unlocker_ready", "loading_replay", "replay_ready"):
                session_label = "Disconnected — reconnect to continue"
            self.session_text.set("Playing camera path" if self.playing else session_label)
            if self.startup_cancel is not None and not self.startup_cancel.is_set():
                self.startup_progress.set(str(status.get("message") or session_label))
            self.play_replay_button.configure(state="normal" if not self.busy and not self.playing else "disabled")
            self.cancel_startup_button.configure(state="normal" if self.startup_cancel is not None and not self.startup_cancel.is_set() else "disabled")
            unlocker_ready = bool(status.get("unlocker_ready"))
            available = not self.busy and not self.playing
            self.speed_combo.configure(state="normal" if available else "disabled")
            self.rate_combo.configure(state="readonly" if available else "disabled")
            running = bool(status.get("game_running"))
            driver_state = "disabled" if running or self.busy else "readonly"
            self.camera_driver_combo.configure(state=driver_state)
            home_driver_combo = getattr(self, "home_camera_driver_combo", None)
            if home_driver_combo is not None:
                home_driver_combo.configure(state=driver_state)
            native = status.get("camera_backend") == "native" if running else self.camera_driver.get() == "Native (experimental)"
            self._set_driver_indicator(status.get("camera_backend"), running)
            self.smoothing_combo.configure(state="disabled" if native or self.playing else "readonly")
            self.aspect_curve.set_enabled(available)
            startup_buttons = (
                (self.launch_button, available),
                (self.connect_button, available and not connected and stage not in ("not_launched", "game_closed")),
                (self.initialize_button, available and connected and not unlocker_ready),
                (self.load_replay_button, available and connected and unlocker_ready and stage != "loading_replay"),
                (self.probe_button, available and connected and unlocker_ready and stage in ("loading_replay", "replay_ready", "failed")),
                (self.disconnect_button, not self.busy and connected),
            )
            for button, enabled in startup_buttons:
                button.configure(state="normal" if enabled else "disabled")
            for button in self.capture_buttons:
                button.configure(state="normal" if available and connected and stage in ("replay_ready", "editing_ready") else "disabled")
            message = str(status.get("message") or "")
            if message and message != self._last_controller_message:
                self._last_controller_message = message
                self.status_text.set(message)
            if not self.busy:
                tick = status.get("tick")
                self.busy_text.set(f"Replay tick {int(tick):,}" if tick is not None else "")
            if self.playing and not self.dragging:
                self._set_time(float(status.get("time", self.shot_time.get())))
        except Exception as exc:
            self._log("Status unavailable: " + str(exc))
        else:
            try:
                self._refresh_video(status)
                self._last_video_poll_error = None
            except (RuntimeError, ValueError, TypeError, OSError) as exc:
                message = "Media status unavailable: " + str(exc)
                self.video_status_text.set(message)
                if getattr(self, "_last_video_poll_error", None) != message:
                    self._last_video_poll_error = message
                    self._log(message)
        self._refresh_renderer_pressure()
        try:
            editor_session.poll(self)
            self.capture_hotkey_checkbox.configure(state="disabled" if self.native_editor_active else "normal")
            if self.native_editor_active:
                binding = self.app_settings.action_bindings.get("capture")
                self.hotkey_enabled.set(binding is not None)
                self.hotkey_label.set("Native capture · " + (binding.label if binding else "Unbound"))
            else:
                self.hotkey_label.set(self._capture_binding_label())
        except Exception as exc:
            self._log("Editor connection unavailable: " + str(exc))
        self._check_capture_listener()
        self.root.after(100, self._poll)

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
            indicator.set("DRIVER · NATIVE")
            style = "Pill.TLabel"
        else:
            indicator.set("DRIVER · CONSOLE LEGACY")
            style = "PillConsole.TLabel"
        label = getattr(self, "driver_indicator_label", None)
        if label is not None and label.cget("style") != style:
            label.configure(style=style)

    def _error(self, title, exc):
        text = str(exc) or type(exc).__name__
        self.status_text.set(text[:240])
        self._log(f"{title}: {text}")
        messagebox.showerror(title, text, parent=self.root)

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
        self.status_text.set("Choose a replay and press Play replay to begin.")
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

    def _launch(self):
        game, demo = self.game_path.get().strip(), self.demo_path.get().strip()
        if not game or not demo:
            self._error("Choose game and replay", ValueError("Select the Deadlock executable and a local .dem replay first."))
            return
        protocol = "vconsole" if self.protocol.get() == "VConsole" else "netcon"
        native = self.camera_driver.get() == "Native (experimental)"
        self._close_paused_camera(stop=False)
        self._submit("Launching hideout", lambda: self.controller.launch(game, demo, protocol=protocol, native=native), self._session_result)

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
        self._close_paused_camera(stop=False)
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
        if hasattr(self, "aspect_curve"):
            self.aspect_curve.set_project(self.project, selected_index=self._selection_index(self.camera_tree),
                                          current_time=float(self.shot_time.get()))

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

    def _reset_aspect(self):
        def operation():
            index = self._selection_index(self.camera_tree)
            if index is None:
                raise ValueError("Select a camera to reset its framing.")
            self._sync_options()
            keys = copy.deepcopy(self.project.keyframes)
            keys[index].aspect_ratio = self.project.standard_aspect
            self._commit_camera(keys, keys[index].time)
            self.status_text.set("Selected camera reset to normal aspect.")
        self._guard("Reset framing", operation)

    def _apply_options(self):
        self._guard("Shot settings", self._sync_options)

    def _sync_options(self):
        tick = _finite(self.start_tick.get(), "Shot start tick")
        rate = _finite(self.tick_rate.get(), "Ticks per second")
        if tick < 0 or int(tick) != tick:
            raise ValueError("Shot start tick must be a non-negative whole number.")
        if rate <= 0:
            raise ValueError("Ticks per second must be greater than zero.")
        values = dict(start_tick=int(tick), tick_rate=rate, interpolation=self.interpolation.get(),
                      rotation_mode=self.rotation.get(), standard_aspect=self._read_standard_aspect(),
                      lens_interpolation=self.lens_interpolation.get())
        changed = any(getattr(self.project, name) != value for name, value in values.items())
        if changed:
            candidate = copy.deepcopy(self.project)
            for name, value in values.items():
                setattr(candidate, name, value)
            # Settings are validated even before the first camera is captured.
            candidate.validate()
            self.project = candidate
            self._mark_dirty()
            self._draw_path()
            self._refresh_curve()
        self.controller.standard_aspect = self.project.standard_aspect
        self.status_text.set("Shot timing and framing are ready.")

    def _snapshot(self):
        self._sync_options()
        candidate = copy.deepcopy(self.project)
        candidate.validate()
        if not candidate.keyframes:
            raise ValueError("Capture or add at least one camera keyframe first.")
        return candidate

    def _selection_index(self, tree):
        selected = tree.selection()
        return int(selected[0]) if selected else None

    def _read_key(self):
        values = {field: _finite(self.key_vars[field].get(), label) for field, label in zip(FIELDS, FIELD_LABELS)}
        return Keyframe(**values)

    def _commit_camera(self, keys, select_time=None):
        candidate = copy.deepcopy(self.project)
        candidate.keyframes = sorted(keys, key=lambda key: key.time)
        if candidate.keyframes:
            candidate.validate()
        self.project = candidate
        self._mark_dirty()
        self._refresh_keys(select_time)

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
            key.fov = self.project.keyframes[index].fov  # Legacy metadata stays intact.
            keys = list(self.project.keyframes)
            if any(i != index and existing.time == key.time for i, existing in enumerate(keys)):
                raise ValueError("Another keyframe already exists at that shot time.")
            keys[index] = key
            self._commit_camera(keys, key.time)
        self._guard("Update keyframe", operation)

    def _delete_key(self):
        index = self._selection_index(self.camera_tree)
        if index is not None:
            self._guard("Delete keyframe", lambda: self._commit_camera([key for i, key in enumerate(self.project.keyframes) if i != index]))

    def _select_key(self, _event=None):
        index = self._selection_index(self.camera_tree)
        if index is not None and index < len(self.project.keyframes):
            key = self.project.keyframes[index]
            for field in FIELDS:
                self.key_vars[field].set(_number(getattr(key, field)))
            self._set_time(key.time)
            if hasattr(self, "selected_text"):
                self.selected_text.set(f"Camera {index + 1:02d}")
            self._refresh_curve()

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
                "Open Keybinds to review and save your shortcuts.\n\n" + detail, parent=self.root))
            return AppSettings()

    def _capture_binding_label(self):
        if self.app_settings.action_bindings.get("capture") is None:
            return "In-game capture · Unbound"
        return "In-game capture · " + self.capture_binding.label

    def _show_keybinds(self, action="capture"):
        self.notebook.select(self.keybinds_tab)
        self.bindings_tree.selection_set(action)
        self.bindings_tree.see(action)
        self._select_binding()

    def _open_capture_binding(self):
        if self.busy or self.playing:
            self.status_text.set("Finish the current operation and stop path playback before changing the capture binding.")
            return
        if self.binding_dialog is not None and self.binding_dialog.winfo_exists():
            self.binding_dialog.lift()
            return
        dialog = tk.Toplevel(self.root)
        self.binding_dialog = dialog
        dialog.title("Capture binding")
        dialog.configure(bg=BG)
        dialog.geometry(self._window_size(500, 530))
        dialog.minsize(*self._window_dimensions(480, 520))
        dialog.transient(self.root)
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=20)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        ttk.Label(body, text="Capture from the game", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(body, text="Choose a keyboard key or mouse button, then add any required modifiers.",
                  style="Muted.TLabel", wraplength=430).grid(row=1, column=0, sticky="w", pady=(8, 16))
        key = tk.StringVar(value=self.capture_binding.key)
        ctrl = tk.BooleanVar(value=self.capture_binding.ctrl)
        alt = tk.BooleanVar(value=self.capture_binding.alt)
        shift = tk.BooleanVar(value=self.capture_binding.shift)
        ttk.Label(body, text="Key or button", style="Muted.TLabel").grid(row=2, column=0, sticky="w", pady=(0, 5))
        chooser = ttk.Combobox(body, textvariable=key, values=KEY_CHOICES, state="readonly", width=24)
        chooser.grid(row=3, column=0, sticky="ew")
        modifiers = ttk.Frame(body)
        modifiers.grid(row=4, column=0, sticky="w", pady=(12, 16))
        ttk.Label(modifiers, text="Required", style="Muted.TLabel").pack(side="left", padx=(0, 14))
        for text, variable in (("Ctrl", ctrl), ("Alt", alt), ("Shift", shift)):
            ttk.Checkbutton(modifiers, text=text, variable=variable).pack(side="left", padx=(0, 22))
        presets = ttk.Frame(body)
        presets.grid(row=5, column=0, sticky="w")
        def preset(binding):
            key.set(binding.key)
            ctrl.set(binding.ctrl)
            alt.set(binding.alt)
            shift.set(binding.shift)
        for label, binding in (("Mouse 4", CaptureBinding("Mouse4", False, False)),
                               ("Mouse 5", CaptureBinding("Mouse5", False, False)),
                               ("Middle", CaptureBinding("MiddleMouse", False, False)),
                               ("Default", DEFAULT_BINDING)):
            ttk.Button(presets, text=label, width=8, style="Quiet.TButton", command=lambda chosen=binding: preset(chosen)).pack(side="left", padx=(0, 8))
        ttk.Label(body, text="Mouse 4 / 5 are the usual side buttons. Leave modifiers unchecked to capture while holding movement keys. If your mouse software remaps a button, select its mapped key.\n\n"
                  "Capture only works while the Dolly-launched game is focused. The button still reaches the game; avoid a conflicting game action.",
                  style="Muted.TLabel", wraplength=430, justify="left").grid(row=6, column=0, sticky="w", pady=(16, 12))
        ttk.Label(body, text="Your binding is saved for future launches. Enable in-game capture separately each time.",
                  style="Muted.TLabel", wraplength=430).grid(row=7, column=0, sticky="w")
        actions = ttk.Frame(body)
        actions.grid(row=8, column=0, sticky="ew", pady=(16, 0))
        def close():
            if dialog.winfo_exists():
                dialog.destroy()
            if self.binding_dialog is dialog:
                self.binding_dialog = None
        def apply():
            try:
                binding = CaptureBinding(key.get(), ctrl.get(), alt.get(), shift.get())
            except ValueError as exc:
                self._error("Capture binding", exc)
                return
            if self._apply_capture_binding(binding, close):
                apply_button.configure(state="disabled")
        apply_button = ttk.Button(actions, text="Save binding", style="Primary.TButton", command=apply)
        apply_button.pack(side="right")
        ttk.Button(actions, text="Cancel", style="Quiet.TButton", command=close).pack(side="right", padx=(0, 8))
        dialog.protocol("WM_DELETE_WINDOW", close)
        dialog.bind("<Escape>", lambda _event: close())
        chooser.focus_set()

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
            self.status_text.set("Native capture uses your Keybinds settings while the in-game editor has control.")
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
            self.status_text.set("Assign a Capture camera shortcut in Keybinds first.")
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

    def _capture_view(self, action, native_snapshot=None):
        if action == "append" and not self.project.keyframes:
            action = "start"
        def operation():
            self._sync_options()
            original = self.project
            resume_paused = bool(getattr(self, "paused_requested", False))
            paused_session = getattr(self, "paused_input", None)
            if paused_session is not None:
                paused_session.clear()
            candidate = copy.deepcopy(original)
            replay_timing = self.capture_mode.get() == "Replay timing"
            selected = self._selection_index(self.camera_tree) if action == "replace" else None
            if action == "replace" and selected is None:
                raise ValueError("Select the view you want to replace, then frame its new position in the game.")
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
                    key, tick = self.controller.capture_native_snapshot(native_snapshot, time=time)
                    if replay_timing and action != "replace":
                        key.time = 0.0 if action == "start" else (tick - candidate.start_tick) / candidate.tick_rate
                    return key, tick
                if replay_timing and action != "replace":
                    start_tick = None if action == "start" else candidate.start_tick
                    key = self.controller.capture_at_replay(start_tick, candidate.tick_rate)
                else:
                    key = self.controller.capture(time)
                return key, self.controller.status().get("tick")
            def captured(result):
                key, tick = result
                if self.project is not original:
                    raise ValueError("The shot changed during capture. Capture again to add this view to the current shot.")
                if action == "start":
                    if candidate.keyframes and not messagebox.askyesno(
                            "Start a new camera path?",
                            "Replace the current camera path with this captured view? Existing lens and depth-of-field tracks will be kept.",
                            parent=self.root):
                        return
                    candidate.keyframes = [key]
                    if tick is not None:
                        candidate.start_tick = int(tick)
                    candidate.interpolation = "smooth"
                elif action == "replace":
                    candidate.keyframes[selected] = key
                else:
                    if any(existing.time == key.time for existing in candidate.keyframes):
                        raise ValueError("This replay moment already has a view. Advance the replay, use Replace selected camera, "
                                         "or choose Timed shot to add several views while paused.")
                    candidate.keyframes.append(key)
                candidate.keyframes.sort(key=lambda item: item.time)
                candidate.validate()
                self.project = candidate
                self.start_tick.set(_number(candidate.start_tick))
                self.interpolation.set(candidate.interpolation)
                self._mark_dirty()
                self._refresh_keys(key.time)
                self._set_time(key.time)
                self.notebook.select(self.camera_tab)
                verb = "Replaced" if action == "replace" else "Captured"
                self.status_text.set(f"{verb} view {candidate.keyframes.index(key) + 1} at {key.time:g} seconds. "
                                     "Fly to the next position and use Add camera here.")
                if action == "start" and tick is None:
                    self.status_text.set("Starting view captured, but the replay start tick is unavailable. "
                                         "Export diagnostics before normal playback, or select Frozen preview for a paused-scene path.")
                if resume_paused and self.paused_requested and self.paused_input is paused_session:
                    self._resume_paused_controls()
            self._submit("Capturing free-camera view", capture, captured)
        self._guard("Capture view", operation)

    def _use_current_tick(self):
        if self.busy or self.playing:
            self.status_text.set("Stop playback and finish the current operation before changing shot timing.")
            return
        self._submit("Reading replay tick", self.controller.current_tick, self._set_start_tick)

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
            self._close_paused_camera(stop=False)
            self._submit("Previewing frame", lambda: self.controller.apply(project, time))
        self._guard("Preview frame", operation)

    def _seek(self):
        def operation():
            project, time = self._snapshot(), self._current_time()
            self._close_paused_camera(stop=False)
            self._submit("Seeking replay", lambda: self.controller.seek(project, time))
        self._guard("Seek replay", operation)

    def _set_seek_relief(self):
        """Toggle the native render relief that prevents seek-time overload."""
        self.controller.set_seek_relief(self.seek_relief.get())

    def _play(self):
        def operation():
            project = self._snapshot()
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
            self._close_paused_camera(stop=False)
            submitted = self._submit("Starting shot playback", lambda: self.controller.play(
                project, time=0.0, speed=speed, rate=rate, frozen=frozen, hide_hud=hide_hud,
                smoothing=smoothing))
            if submitted:
                self._set_time(0.0)
        self._guard("Play shot", operation)

    def _slide(self, value):
        self.time_text.set(f"{float(value):.3f}".rstrip("0").rstrip("."))
        self._draw_path()
        if hasattr(self, "aspect_curve"):
            self.aspect_curve.set_current_time(float(value))

    def _set_time(self, time):
        self.slider.configure(to=max(10.0, float(self.project.duration), time))
        self.shot_time.set(time)
        self.time_text.set(_number(time))
        self._draw_path()
        if hasattr(self, "aspect_curve"):
            self.aspect_curve.set_current_time(float(time))

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
            ranges = (-100.0, 0.0, 180.0, 2000.0)
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
        self.slider.configure(to=max(10.0, float(self.project.duration)))
        if hasattr(self, "path_summary"):
            self.path_summary.set(f"{len(self.project.keyframes)} cameras · {self.project.duration:.2f} s")
        if hasattr(self, "selected_text") and not self.camera_tree.selection():
            self.selected_text.set("No camera selected")
        self._draw_path()
        self._refresh_curve()
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
        self.slider.configure(to=max(10.0, float(self.project.duration)))

    def _refresh_fixed(self):
        self.setup_tree.delete(*self.setup_tree.get_children())
        for index, (name, value) in enumerate(sorted(self.project.setup_values.items())):
            self.setup_tree.insert("", "end", iid=str(index), values=(name, format_cvar_value(value)))

    def _draw_path(self):
        if not hasattr(self, "canvas"):
            return
        canvas = self.canvas
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 20 or height < 20:
            return
        if height < 80:
            canvas.create_text(width / 2, height / 2, text="Enlarge the window for a path overview",
                               fill=MUTED, font=("Segoe UI", 9), width=max(100, width - 20), justify="center")
            return
        keys = self.project.keyframes
        if not keys:
            canvas.create_text(width / 2, height / 2, text="Capture a view\nto begin a path", fill=MUTED,
                               font=("Segoe UI", 10), justify="center")
            return
        points = [(key.x, key.y) for key in keys]
        sample = []
        if len(keys) > 1:
            start, end = keys[0].time, keys[-1].time
            try:
                for i in range(81):
                    state = self.project.evaluate(start + (end - start) * i / 80)
                    sample.append((state["x"], state["y"]))
            except (ValueError, KeyError, TypeError):
                sample = points
        all_points = points + sample
        x0, x1 = min(p[0] for p in all_points), max(p[0] for p in all_points)
        y0, y1 = min(p[1] for p in all_points), max(p[1] for p in all_points)
        scale = min(max(1, width - 44) / max(1, x1 - x0), max(1, height - 44) / max(1, y1 - y0))
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        def xy(point):
            return (width / 2 + (point[0] - cx) * scale, height / 2 - (point[1] - cy) * scale)
        if len(sample) > 1:
            canvas.create_line(*[coordinate for point in sample for coordinate in xy(point)], fill="#578e87", width=2)
        for i, point in enumerate(points):
            x, y = xy(point)
            canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill=ACCENT, outline="")
            canvas.create_text(x + 8, y - 8, text=str(i + 1), fill=TEXT, anchor="w", font=("Segoe UI", 9))
        try:
            state = self.project.evaluate(self.shot_time.get())
            x, y = xy((state["x"], state["y"]))
            canvas.create_oval(x - 6, y - 6, x + 6, y + 6, outline="#f1cb89", width=2)
            yaw = math.radians(state["yaw"])
            canvas.create_line(x, y, x + math.cos(yaw) * 18, y - math.sin(yaw) * 18, fill="#f1cb89", width=2, arrow="last")
        except (ValueError, KeyError, TypeError):
            pass

    def _title(self):
        marker = " *" if self.dirty else ""
        self.project_text.set(self.project.name + marker)
        self.root.title(f"Deadlock Dolly — {self.project.name}{marker}")

    def _mark_dirty(self):
        self.dirty = True
        self._title()
        if hasattr(self, "native_editor_active"):
            editor_session.configure(self)

    def _allow_discard(self):
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
        self._close_paused_camera()
        self.project = Project(name="Untitled shot", keyframes=[], tracks=[])
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
            self._close_paused_camera()
            self.project = project
            self.file_path = Path(path)
            self.dirty = False
            self._refresh_project()
            self._set_time(0)
            self.notebook.select(self.camera_tab)
            if legacy_project:
                self.status_text.set("Shot imported: camera positions and timing are preserved. Framing starts at normal aspect; old FOV values are kept as inactive metadata.")
            else:
                self.status_text.set(f"Opened {Path(path).name}.")
        self._guard("Open shot", operation)

    def save(self, save_as=False):
        if self.busy:
            self.status_text.set("Finish the current operation before saving the shot.")
            return False
        try:
            self._sync_options()
            path = self.file_path
            if save_as or path is None:
                filename = "".join(character if character.isalnum() or character in "-_ " else "_" for character in self.project.name).strip() or "shot"
                chosen = filedialog.asksaveasfilename(parent=self.root, title="Save camera shot", defaultextension=".json",
                                                       initialfile=filename + ".json", filetypes=(("Dolly shot", "*.json"),))
                if not chosen:
                    return False
                path = Path(chosen)
            self.project.save(path)
            self.file_path = path
            self.dirty = False
            self._title()
            self.status_text.set(f"Saved {path.name}.")
            return True
        except Exception as exc:
            self._error("Save shot", exc)
            return False

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
        if self.playing:
            self._submit("Stopping before closing", self.controller.stop, lambda _result: self._finish_close())
        else:
            self._finish_close()

    def _finish_close(self):
        self.playing = False
        if not self._allow_discard():
            return
        editor_session.close(self)
        self._close_paused_camera(stop=False)
        self._submit("Closing replay connection", self.controller.close, lambda _result: self._destroy())

    def _destroy(self):
        editor_session.close(self)
        self._close_paused_camera(stop=False)
        self.capture_generation += 1
        self.hotkey_enabled.set(False)
        if self.capture_hotkey is not None:
            try:
                self.capture_hotkey.stop()
            except Exception:
                LOG.exception("Capture shortcut cleanup failed during editor shutdown")
            self.capture_hotkey = None
        self.closed = True
        self.jobs.put(None)
        self.root.destroy()


def main():
    root = tk.Tk()
    DollyApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
