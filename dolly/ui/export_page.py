"""Export page view: explicit variables/actions, with locally owned widgets."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import tkinter as tk
from tkinter import ttk

from .widgets import GAP, ScrollPage, actions, card, disclosure, field


@dataclass(frozen=True)
class ExportState:
    ffmpeg_path: tk.Variable
    video_bitrate: tk.Variable
    video_codec: tk.Variable
    video_depth: tk.Variable
    video_depth_exr: tk.Variable
    video_export_speed: tk.Variable
    video_fixed_step: tk.Variable
    video_fps: tk.Variable
    video_game_audio: tk.Variable
    video_layer_effects: tk.Variable
    video_layer_players: tk.Variable
    video_layer_world: tk.Variable
    video_path: tk.Variable
    video_pov_duration: tk.Variable
    video_reconstructed_audio: tk.Variable
    video_source: tk.Variable
    video_status_text: tk.Variable
    codec_choices: tuple[str, ...]
    bitrate_choices: tuple[str, ...]


@dataclass(frozen=True)
class ExportActions:
    browse_ffmpeg: Callable[..., object]
    browse_video: Callable[..., object]
    depth_toggled: Callable[..., object]
    layer_toggled: Callable[..., object]
    open_output_folder: Callable[..., object]
    play: Callable[..., object]
    save_ffmpeg_preference: Callable[..., object]
    start_video_recording: Callable[..., object]
    stop_video_recording: Callable[..., object]
    use_bundled_ffmpeg: Callable[..., object]
    video_source_changed: Callable[..., object]
    discard_recording: Callable[..., object]


class ExportPage:
    def __init__(self, parent, state: ExportState, commands: ExportActions):
        recording_holder = ttk.Frame(parent, padding=(16, GAP, 16, 0))
        recording_holder.pack(side="bottom", fill="x")
        page = ScrollPage(parent)
        page.pack(fill="both", expand=True)
        self.export_page = page
        recording_holder.configure(padding=(16, GAP, 16 + page.scrollbar.winfo_reqwidth(), 0))
        body = page.body
        ttk.Label(body, text="EXPORT", style="Section.TLabel").pack(anchor="w", pady=(0, GAP))
        source = card(body, "Camera source")
        self.video_source_combo = field(source, "Source", state.video_source, values=("Camera path", "Player POV"))
        self.video_source_combo.bind("<<ComboboxSelected>>", commands.video_source_changed)
        self.video_pov_controls = ttk.Frame(source)
        self.video_pov_duration_combo = field(self.video_pov_controls, "Replay seconds", state.video_pov_duration,
                                           values=("1", "2", "5", "10", "15", "30", "60", "120"))
        ttk.Label(self.video_pov_controls,
                  text="F9: select a hero and pause at the start. F8: return to Export. HUD hides automatically.",
                  style="CardMuted.TLabel", wraplength=760).pack(fill="x")
        destination = card(body, "Destination")
        self.video_destination_card = destination
        self.video_path_entry = field(destination, "Output file", state.video_path)
        self.video_browse_button = actions(destination, (("Browse...", commands.browse_video),
                                  ("Open output folder", commands.open_output_folder)), 2)[0]
        audio_options = ttk.Frame(destination, style="Card.TFrame")
        audio_options.pack(anchor="w", pady=(0, GAP))
        self.video_game_audio_check = ttk.Checkbutton(
            audio_options, style="Card.TCheckbutton", text="Include game audio",
            variable=state.video_game_audio)
        self.video_game_audio_check.pack(side="left", padx=(0, 18))
        self.video_reconstructed_audio_check = ttk.Checkbutton(
            audio_options, style="Card.TCheckbutton", text="Include reconstructed audio",
            variable=state.video_reconstructed_audio)
        self.video_reconstructed_audio_check.pack(side="left")
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
        self.video_depth_checkbox = ttk.Checkbutton(passes, style="Card.TCheckbutton", text="Depth master (.mov)", variable=state.video_depth, command=commands.depth_toggled)
        self.video_depth_checkbox.pack(anchor="w", pady=(0, GAP))
        ttk.Label(passes, text="Depth and its extra passes use 100% render scale; your scale is restored afterward. This can make export slower.",
                  style="CardMuted.TLabel", wraplength=320).pack(anchor="w", pady=(0, GAP))
        self.video_depth_exr_checkbox = ttk.Checkbutton(passes, style="Card.TCheckbutton", text="EXR sequence (float)", variable=state.video_depth_exr)
        self.video_depth_exr_checkbox.pack(anchor="w", pady=(0, GAP))
        self.video_layer_checkboxes = []
        for title, var in (("World layer", state.video_layer_world), ("Players layer (alpha)", state.video_layer_players), ("Effects layer (alpha)", state.video_layer_effects)):
            cb = ttk.Checkbutton(passes, style="Card.TCheckbutton", text=title, variable=var, command=commands.layer_toggled)
            cb.pack(anchor="w", pady=(0, GAP))
            self.video_layer_checkboxes.append(cb)
        capture = card(right, "Capture")
        passes.pack_configure(fill="both", expand=True)
        capture.pack_configure(fill="both", expand=True)
        self.export_passes_card, self.export_capture_card = passes, capture
        self.video_fps_combo = field(capture, "Video FPS", state.video_fps, values=("30", "60", "120", "300", "600"))
        self.video_speed_combo = field(capture, "Export speed", state.video_export_speed, values=("0.05", "0.1", "0.25", "0.5", "1", "2", "4"))
        self.video_fixed_checkbox = ttk.Checkbutton(capture, style="Card.TCheckbutton", text="Fixed-step export (frame-accurate)", variable=state.video_fixed_step)
        self.video_fixed_checkbox.pack(anchor="w", pady=(0, GAP))
        quality = disclosure(capture, "Encoder & quality")
        self.video_codec_combo = field(quality.body, "Encoder", state.video_codec, values=state.codec_choices)
        self.video_bitrate_combo = field(quality.body, "Bitrate", state.video_bitrate, values=state.bitrate_choices)
        controls = card(recording_holder, "Recording")
        buttons = actions(controls, (("Play shot", commands.play), ("Record video", commands.start_video_recording, "Primary.TButton"),
                                     ("Finish recording", commands.stop_video_recording),
                                     ("Discard recording", commands.discard_recording)), 4)
        self.video_start_button, self.video_stop_button, self.video_cancel_button = buttons[1:]
        self.video_stop_button.configure(state="disabled")
        self.video_cancel_button.configure(state="disabled")
        ttk.Label(controls, textvariable=state.video_status_text, style="CardMuted.TLabel", wraplength=760).pack(fill="x")
        self.export_camera_note = ttk.Label(controls, text="Camera: Free path", style="CardMuted.TLabel",
                                           wraplength=760)
        self.export_camera_note.pack(fill="x")
        ttk.Label(controls, text="With passes ticked: Record video records the color take. When it finishes, Dolly records each ticked pass in turn; you do not need to press Finish again.",
                  style="CardMuted.TLabel", wraplength=760).pack(fill="x")
        runtime = disclosure(body, "FFmpeg runtime")
        self.ffmpeg_path_entry = field(runtime.body, "Executable", state.ffmpeg_path)
        self.ffmpeg_browse_button = actions(runtime.body, (("Browse FFmpeg...", commands.browse_ffmpeg), ("Save path", commands.save_ffmpeg_preference),
            ("Use bundled FFmpeg", commands.use_bundled_ffmpeg)), 3)[0]
