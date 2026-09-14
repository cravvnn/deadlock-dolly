"""Desktop layout: a simple library, with the complete editor on demand."""
from __future__ import annotations

import os
from pathlib import Path
import tkinter as tk
from tkinter import ttk

GAP = 14


class ScrollPage(ttk.Frame):
    """One vertical scroll region; wheel events stay within this page."""
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, background="#11171c", highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.body = ttk.Frame(self.canvas, padding=(16, 12))
        self.window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.window, width=e.width))
        self.body.bind("<Configure>", self._resize)
        self.bind_id = self.winfo_toplevel().bind("<MouseWheel>", self._wheel, add="+")
        self.bind("<Destroy>", self._destroyed, add="+")

    def _resize(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _wheel(self, event):
        target = event.widget
        while target is not None and target is not self:
            if isinstance(target, (ttk.Treeview, tk.Text)):
                return
            target = getattr(target, "master", None)
        if target is self and self.canvas.yview() != (0.0, 1.0):
            self.scroll_page_wheel(event)
            return "break"

    def scroll_page_wheel(self, event):
        amount = -int(event.delta / 120) if event.delta else (-1 if getattr(event, "num", 0) == 4 else 1)
        self.canvas.yview_scroll(amount, "units")

    def _destroyed(self, event):
        if event.widget is self:
            self.winfo_toplevel().unbind("<MouseWheel>", self.bind_id)

    def reveal(self, widget):
        self.update_idletasks()
        y = widget.winfo_rooty() - self.body.winfo_rooty()
        self.canvas.yview_moveto(max(0, y - GAP) / max(1, self.body.winfo_height()))


def surface_style(parent):
    name = parent.cget("style") or "TFrame"
    if "Card." in name:
        return "Card.TFrame"
    background = ttk.Style(parent).lookup(name, "background")
    return "Card.TFrame" if background == ttk.Style(parent).lookup("Card.TFrame", "background") else "TFrame"


class Disclosure(ttk.Frame):
    def __init__(self, parent, title):
        frame_style = surface_style(parent)
        super().__init__(parent, style=frame_style)
        self.title = title
        self.opened = False
        self.toggle = ttk.Button(self, text=">  " + title, command=self.flip, style="Disclosure.Card.TButton" if frame_style == "Card.TFrame" else "Disclosure.TButton")
        self.toggle.pack(anchor="w")
        self.body = ttk.Frame(self, style=frame_style, padding=(0, 8, 0, 0))

    def flip(self):
        self.set_open(not self.opened)

    def set_open(self, value=True):
        self.opened = value
        self.toggle.configure(text=("v  " if value else ">  ") + self.title)
        if value:
            self.body.pack(fill="both", expand=True)
        else:
            self.body.pack_forget()


def card(parent, title):
    frame = ttk.Frame(parent, style="Rounded.Card.TFrame", padding=14)
    frame.pack(fill="x", pady=(0, GAP))
    ttk.Label(frame, text=title, style="CardTitle.TLabel").pack(anchor="w", pady=(0, GAP))
    return frame


def field(parent, label, variable, *, values=None):
    frame = ttk.Frame(parent, style=surface_style(parent))
    frame.pack(fill="x", pady=(0, GAP))
    ttk.Label(frame, text=label, style="CardMuted.TLabel" if surface_style(parent) == "Card.TFrame" else "Muted.TLabel").pack(anchor="w", pady=(0, 6))
    widget = (ttk.Combobox(frame, textvariable=variable, values=values, state="readonly",
                            style="Card.TCombobox" if surface_style(parent) == "Card.TFrame" else "TCombobox")
              if values is not None else ttk.Entry(frame, textvariable=variable,
                                                style="Card.TEntry" if surface_style(parent) == "Card.TFrame" else "TEntry"))
    widget.pack(fill="x")
    return widget


def actions(parent, specs, columns=3):
    """Bounded wrapping rows, never an unbounded horizontal pack of buttons."""
    frame = ttk.Frame(parent, style=surface_style(parent))
    frame.pack(fill="x")
    buttons = []
    for i, spec in enumerate(specs):
        label, command, *style = spec
        button_style = style[0] if style else "TButton"
        if surface_style(parent) == "Card.TFrame":
            button_style = "Card." + button_style
        b = ttk.Button(frame, text=label, command=command, style=button_style)
        b.grid(row=i // columns, column=i % columns, sticky="w", padx=(0, 10 if i % columns < columns-1 else 0), pady=(0, 10))
        frame.columnconfigure(i % columns, weight=0)
        buttons.append(b)
    return buttons


def disclosure(parent, title):
    d = Disclosure(parent, title)
    d.pack(fill="x", pady=(0, GAP))
    return d


def build_library(app):
    page = ScrollPage(app.setup_tab)
    page.pack(fill="both", expand=True)
    app.library_page = page
    body = page.body
    ttk.Label(body, text="Replays & shots", style="Section.TLabel").pack(anchor="w", pady=(0, GAP))
    pair = ttk.Frame(body)
    pair.pack(fill="both", expand=True, pady=(0, GAP))
    for col in (0, 1):
        pair.columnconfigure(col, weight=1, uniform="library")
    left = ttk.Frame(pair, style="Rounded.Card.TFrame", padding=14)
    right = ttk.Frame(pair, style="Rounded.Card.TFrame", padding=14)
    left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
    right.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
    for frame, title in ((left, "Replay library"), (right, "Selected replay")):
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        ttk.Label(frame, text=title, style="CardTitle.TLabel").grid(row=0, column=0, sticky="w", pady=(0, GAP))
    search = ttk.Frame(left, style="Card.TFrame")
    search.grid(row=1, column=0, sticky="ew", pady=(0, GAP))
    field(search, "Find replay", app.replay_search)
    app.replay_search.trace_add("write", lambda *_: app._filter_replays())
    table, app.replay_tree = app._tree(left, ("name", "size", "modified"),
                                      ("Replay", "Size", "Modified"), (220, 75, 100), height=3)
    table.grid(row=2, column=0, sticky="nsew", pady=(0, GAP))
    app.replay_tree.column("name", anchor="w", minwidth=100)
    app.replay_tree.column("size", minwidth=50)
    app.replay_tree.column("modified", minwidth=70)
    app.replay_tree.bind("<<TreeviewSelect>>", app._select_replay)
    app.replay_tree.bind("<Double-1>", lambda _e: app._use_selected_replay())
    buttons = ttk.Frame(left, style="Card.TFrame")
    buttons.grid(row=3, column=0, sticky="ew")
    actions(buttons, (("Browse .dem...", app._browse_demo), ("Refresh", app._refresh_replays)), 2)
    app.selected_replay_text = tk.StringVar()
    selected = ttk.Frame(right, style="Card.TFrame")
    selected.grid(row=1, column=0, sticky="ew", pady=(0, GAP))
    ttk.Label(selected, text="Replay file", style="CardMuted.TLabel").pack(anchor="w", pady=(0, 6))
    filename = ttk.Label(selected, textvariable=app.selected_replay_text, style="Card.TLabel", padding=(0, 7))
    filename.pack(fill="x", pady=(0, GAP))
    def update_name(*_):
        app.selected_replay_text.set(Path(app.demo_path.get()).name if app.demo_path.get() else "Choose a replay")
    app.demo_path.trace_add("write", update_name)
    update_name()
    info = ttk.Frame(right, style="Card.TFrame")
    info.grid(row=2, column=0, sticky="new", pady=(0, GAP))
    label = ttk.Label(info, text="Open in the paused in-game camera editor.", style="CardMuted.TLabel", wraplength=320)
    label.pack(anchor="w", pady=(0, GAP))
    right.bind("<Configure>", lambda e: (filename.configure(wraplength=max(180, e.width-36)), label.configure(wraplength=max(180, e.width-36))))
    launch = disclosure(info, "Launch setup")
    field(launch.body, "Deadlock executable", app.game_path)
    actions(launch.body, (("Browse game...", app._browse_game),), 1)
    app.home_camera_driver_combo = field(launch.body, "Camera driver", app.camera_driver,
                                        values=("Native (experimental)", "Console (legacy)"))
    launch_row = ttk.Frame(right, style="Card.TFrame")
    launch_row.grid(row=3, column=0, sticky="ew", pady=(0, 6))
    app.play_replay_button = ttk.Button(launch_row, text="Open replay in Dolly", style="Card.Primary.TButton", command=app._start_editing_session)
    app.play_replay_button.pack(side="left")
    app.library_capture_toggle = ttk.Checkbutton(
        launch_row, textvariable=app.hotkey_label, variable=app.hotkey_enabled,
        command=app._toggle_capture_hotkey)
    app.library_capture_toggle.pack(side="left", padx=(14, 0))
    app.capture_hotkey_checkboxes.append(app.library_capture_toggle)
    ttk.Label(right, text="In-game camera capture follows this switch; set the shortcut in Keybinds.",
              style="CardMuted.TLabel", wraplength=320).grid(row=4, column=0, sticky="w", pady=(0, 10))
    progress = card(body, "Session")
    app.startup_label = ttk.Label(progress, textvariable=app.startup_progress, style="CardMuted.TLabel", wraplength=780)
    app.startup_label.pack(fill="x", pady=(0, GAP))
    app.startup_bar = ttk.Progressbar(progress, mode="indeterminate")
    app.startup_bar.pack(fill="x", pady=(0, GAP))
    app.cancel_startup_button = actions(progress, (("Cancel startup", app._cancel_startup),
        ("Stop / restore", lambda: app._session_operation("Stopping", app.controller.stop))), 2)[0]
    app.cancel_startup_button.configure(state="disabled")
    shot = card(body, "Shot files")
    ttk.Label(shot, textvariable=app.project_text, style="Card.TLabel").pack(anchor="w", pady=(0, 6))
    ttk.Label(shot, textvariable=app.path_summary, style="CardMuted.TLabel").pack(anchor="w", pady=(0, GAP))
    shot.pack_configure(before=progress)
    actions(shot, (("New shot", app.new), ("Open shot...", app.open), ("Save", app.save),
                   ("Save as...", lambda: app.save(save_as=True)), ("Rename shot...", app.rename)))
    ttk.Label(body, text="F8 panel - Frame cameras, edit the lens, and record inside Deadlock.", style="Muted.TLabel").pack(anchor="w")
    app._build_advanced_startup()


def build_settings(app):
    page = ScrollPage(app.settings_tab)
    page.pack(fill="both", expand=True)
    app.settings_page = page
    body = page.body
    ttk.Label(body, text="Settings", style="Section.TLabel").pack(anchor="w", pady=(0, GAP))
    updates = card(body, "Updates")
    app.auto_updates = tk.BooleanVar(value=app.app_settings.auto_updates)
    app.update_status = tk.StringVar(value="Checks published Latest releases; experimental pre-releases are ignored.")
    ttk.Checkbutton(updates, style="Card.TCheckbutton", text="Download and install updates automatically when idle", variable=app.auto_updates,
                    command=app._save_update_preference).pack(anchor="w", pady=(0, GAP))
    app.update_check_button = actions(updates, (("Check for updates", app._check_updates),), 1)[0]
    ttk.Label(updates, textvariable=app.update_status, wraplength=700, style="CardMuted.TLabel").pack(fill="x")
    game = card(body, "Game & files")
    field(game, "Deadlock executable", app.game_path)
    actions(game, (("Browse game...", app._browse_game),), 1)
    field(game, "Replay folder", app.replay_folder)
    actions(game, (("Browse folder...", app._browse_replay_folder), ("Save paths", app._save_layout_paths)), 2)
    actions(game, (("Display launch options...", app._open_launch_options),), 1)
    app.controls_disclosure = disclosure(body, "Controls & keybinds")
    app.keybinds_tab = app.controls_disclosure.body
    app._build_keybinds()
    app.reshade_card = shade = card(body, "ReShade")
    app.reshade_path_entry = field(shade, "Runtime DLL", app.reshade_runtime_path)
    app.reshade_browse_button = actions(shade, (("Browse runtime...", app._browse_reshade),), 1)[0]
    buttons = actions(shade, (("Enable ReShade", app._configure_reshade),
                    ("Disable for this session", app._disable_reshade), ("Forget runtime", app._forget_reshade)))
    app.reshade_configure_button, app.reshade_disable_button, app.reshade_forget_button = buttons
    app.reshade_disable_button.configure(state="disabled")
    actions(shade, (("Menu shortcut...", lambda: app._show_keybinds("reshade")),), 1)
    ttk.Label(shade, textvariable=app.reshade_status_text, style="CardMuted.TLabel", wraplength=760).pack(fill="x")
    tools = disclosure(body, "Troubleshooting & recovery")
    actions(tools.body, (("Startup controls...", app._open_advanced_startup),
                         ("Recover configuration...", app._recover_game_config),
                         ("Export diagnostics", app._diagnostics)))
    ttk.Checkbutton(tools.body, text="Show log", variable=app.show_log, command=app._toggle_log).pack(anchor="w", pady=(0, GAP))


def build_export(app):
    from .video_export import CODEC_CHOICES, BITRATE_PRESETS
    recording_holder = ttk.Frame(app.export_tab, padding=(16, GAP, 16, 0))
    recording_holder.pack(side="bottom", fill="x")
    page = ScrollPage(app.export_tab)
    page.pack(fill="both", expand=True)
    app.export_page = page
    recording_holder.configure(padding=(16, GAP, 16 + page.scrollbar.winfo_reqwidth(), 0))
    body = page.body
    ttk.Label(body, text="Export", style="Section.TLabel").pack(anchor="w", pady=(0, GAP))
    destination = card(body, "Destination")
    app.video_path_entry = field(destination, "Output file", app.video_path)
    app.video_browse_button = actions(destination, (("Browse...", app._browse_video),
                              ("Open output folder", app._open_output_folder)), 2)[0]
    options = ttk.Frame(body)
    options.pack(fill="x", pady=(0, GAP))
    for i in (0, 1):
        options.columnconfigure(i, weight=1, uniform="export")
    left = ttk.Frame(options)
    right = ttk.Frame(options)
    left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
    right.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
    passes = card(left, "Output passes")
    ttk.Label(passes, text="Color video always records first. Ticked passes are extra takes recorded automatically after it.",
              style="CardMuted.TLabel", wraplength=320).pack(anchor="w", pady=(0, GAP))
    app.video_depth_checkbox = ttk.Checkbutton(passes, style="Card.TCheckbutton", text="Depth master (.mov)", variable=app.video_depth, command=app._depth_toggled)
    app.video_depth_checkbox.pack(anchor="w", pady=(0, GAP))
    app.video_depth_exr_checkbox = ttk.Checkbutton(passes, style="Card.TCheckbutton", text="EXR sequence (float)", variable=app.video_depth_exr)
    app.video_depth_exr_checkbox.pack(anchor="w", pady=(0, GAP))
    app.video_layer_checkboxes = []
    for title, var in (("World layer", app.video_layer_world), ("Players layer (alpha)", app.video_layer_players), ("Effects layer (alpha)", app.video_layer_effects)):
        cb = ttk.Checkbutton(passes, style="Card.TCheckbutton", text=title, variable=var, command=app._layer_toggled)
        cb.pack(anchor="w", pady=(0, GAP))
        app.video_layer_checkboxes.append(cb)
    capture = card(right, "Capture")
    passes.pack_configure(fill="both", expand=True)
    capture.pack_configure(fill="both", expand=True)
    app.export_passes_card, app.export_capture_card = passes, capture
    app.video_fps_combo = field(capture, "Video FPS", app.video_fps, values=("30", "60", "120", "300", "600"))
    app.video_speed_combo = field(capture, "Export speed", app.video_export_speed, values=("0.05", "0.1", "0.25", "0.5", "1", "2", "4"))
    app.video_fixed_checkbox = ttk.Checkbutton(capture, style="Card.TCheckbutton", text="Fixed-step export (frame-accurate)", variable=app.video_fixed_step)
    app.video_fixed_checkbox.pack(anchor="w", pady=(0, GAP))
    quality = disclosure(capture, "Encoder & quality")
    app.video_codec_combo = field(quality.body, "Encoder", app.video_codec, values=tuple(c[1] for c in CODEC_CHOICES))
    app.video_bitrate_combo = field(quality.body, "Bitrate", app.video_bitrate, values=tuple(BITRATE_PRESETS))
    controls = card(recording_holder, "Recording")
    buttons = actions(controls, (("Play shot", app._play), ("Record video", app._start_video_recording, "Primary.TButton"),
                                 ("Finish recording", app._stop_video_recording),
                                 ("Discard recording", lambda: app._stop_video_recording(cancel=True))), 4)
    app.video_start_button, app.video_stop_button, app.video_cancel_button = buttons[1:]
    app.video_stop_button.configure(state="disabled")
    app.video_cancel_button.configure(state="disabled")
    ttk.Label(controls, textvariable=app.video_status_text, style="CardMuted.TLabel", wraplength=760).pack(fill="x")
    ttk.Label(controls, text="With passes ticked: Record video records the color take. When it finishes, Dolly records each ticked pass in turn; you do not need to press Finish again.",
              style="CardMuted.TLabel", wraplength=760).pack(fill="x")
    runtime = disclosure(body, "FFmpeg runtime")
    app.ffmpeg_path_entry = field(runtime.body, "Executable", app.ffmpeg_path)
    app.ffmpeg_browse_button = actions(runtime.body, (("Browse FFmpeg...", app._browse_ffmpeg), ("Save path", app._save_ffmpeg_preference),
        ("Use bundled FFmpeg", app._use_bundled_ffmpeg)), 3)[0]
