"""Export page (Figma [design reference]): explicit variables/actions, with locally owned widgets.

The page is a :class:`.kit_page.KitPage` of kit cards in one column like Replay's
(USER-ADDITIONS UPDATE 13): Camera Source, Destination, Encoder & Quality, Capture,
Output Passes, Screenshot, FFmpeg Runtime and Recording, 613 design px wide, 15 apart.
The whole page scrolls as one (no pinned card). Every card button is canvas-drawn
(N21); fields, dropdowns and checkboxes are kit ttk widgets placed on their card. The
old help labels are info bubbles with the same text. ``screenshot_ui.build`` fills the
Screenshot card, so this module never imports the export backend.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import tkinter as tk
from tkinter import ttk

from .. import edition
from . import kit_fields
from .kit_page import KitPage

TITLE = "Export"
SUBTITLE = "Set the camera source, output file and passes, then record the shot as video or take a high-res still."

POV_INFO = ("Select a hero with Game Follow or F9, then pause at the start. F8: return to Export. "
            "HUD hides automatically.")
AUDIO_INFO = "Reconstructed audio installs a verified helper automatically on first use."
PROCESSING_INFO = ("Background leaves more CPU time for editing while building the Players MOV. "
                   "Faster modes use more CPU.")
SCRATCH_INFO = ("Blank uses the output folder. Raw Players files need a fast drive with free space. "
                "Failed or cancelled captures are kept in their own folders.")
SCREENSHOT_INFO = ("Captures the current paused view: plate.png, hero_alpha.png, hero_rgba.png, "
                   "hero_isolated.png and depth.exr in one folder beside the output file. "
                   "Hero = every player and their equipment, no NPCs.")
PASSES_INFO = "Color video always records first. Ticked passes are extra takes recorded automatically after it."
DEPTH_INFO = ("Depth and its extra passes use 100% render scale; your scale is restored afterward. "
              "This can make export slower.")
RECORDING_INFO = ("With passes ticked: Record video records the color take. When it finishes, Dolly records "
                  "each ticked pass in turn; you do not need to press Finish again.")

SOURCES = ("Camera path", "Player POV")
POV_SECONDS = ("1", "2", "5", "10", "15", "30", "60", "120")
PROCESSING_MODES = ("Background", "Balanced", "Fast")
FPS_CHOICES = ("30", "60", "120", "300", "600")
SPEED_CHOICES = ("0.05", "0.1", "0.25", "0.5", "1", "2", "4")


@dataclass(frozen=True)
class ExportState:
    ffmpeg_path: tk.Variable
    video_bitrate: tk.Variable
    video_codec: tk.Variable
    video_format: tk.Variable
    player_processing: tk.Variable
    players_scratch: tk.Variable
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
    video_ae_camera: tk.Variable
    video_usd_camera: tk.Variable
    codec_choices: tuple[str, ...]
    format_choices: tuple[str, ...]
    bitrate_choices: tuple[str, ...]


@dataclass(frozen=True)
class ExportActions:
    browse_ffmpeg: Callable[..., object]
    browse_players_scratch: Callable[..., object]
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


def _dropdown(row, variable, values, width="fill"):
    return row.widget(ttk.Combobox(row.host, textvariable=variable, values=values, state="readonly",
                                   style="Kit.TCombobox"), width=width)


def _check(row, text, variable, command=None):
    options = {"command": command} if command is not None else {}
    return row.widget(ttk.Checkbutton(row.host, text=text, variable=variable, style="Kit.TCheckbutton", **options))


def _entry(row, variable):
    entry = row.widget(ttk.Entry(row.host, textvariable=variable, style="Kit.TEntry"), width="fill")
    kit_fields.elide_at_rest(entry, variable)
    return entry


class ExportPage:
    def __init__(self, parent, state: ExportState, commands: ExportActions):
        page = self.export_page = KitPage(parent)
        page.pack(fill="both", expand=True)

        card = self.camera_source_card = page.add_card("Camera Source")
        row = card.row()
        row.label("Source")
        row.gap(10)
        self.video_source_combo = _dropdown(row, state.video_source, SOURCES)
        self.video_source_combo.bind("<<ComboboxSelected>>", commands.video_source_changed)
        # Shown by the app for Player POV (or a single attach key) through pack()/pack_forget().
        row = self.video_pov_controls = card.row()
        row.label("Replay seconds", info=POV_INFO)
        row.gap(10)
        self.video_pov_duration_combo = _dropdown(row, state.video_pov_duration, POV_SECONDS, width=80)
        row.set_visible(False)
        row = self.video_camera_options = card.row(height=18)
        self.video_ae_camera_check = _check(row, "Export After Effects camera (.jsx)", state.video_ae_camera)
        row.gap(20)
        self.video_usd_camera_check = _check(row, "Export USD camera (.usda)", state.video_usd_camera)

        card = self.video_destination_card = page.add_card("Destination")
        row = card.row()
        row.label("Format")
        row.gap(10)
        self.video_format_combo = _dropdown(row, state.video_format, state.format_choices)
        row = card.row()
        row.label("Output file")
        row.gap(10)
        self.video_path_entry = _entry(row, state.video_path)
        row.gap(10)
        self.video_browse_button = row.button("Browse...", commands.browse_video)
        row.gap(10)
        row.button("Open output folder", commands.open_output_folder)
        row = card.row(height=18)
        self.video_game_audio_check = _check(row, "Include game audio", state.video_game_audio)
        row.gap(20)
        self.video_reconstructed_audio_check = _check(row, "Include reconstructed audio",
                                                      state.video_reconstructed_audio)
        row.gap(5)
        row.info(AUDIO_INFO)

        card = self.encoder_card = page.add_card("Encoder & Quality")
        combos = []
        for label, info, variable, values in (("Encoder", None, state.video_codec, state.codec_choices),
                                              ("Bitrate", None, state.video_bitrate, state.bitrate_choices),
                                              ("Players processing", PROCESSING_INFO, state.player_processing,
                                               PROCESSING_MODES)):
            row = card.row()
            row.label(label, width=145, info=info)
            row.gap(10)
            combos.append(_dropdown(row, variable, values))
        self.video_codec_combo, self.video_bitrate_combo, self.player_processing_combo = combos
        row = card.row()
        row.label("Players scratch folder", width=145, info=SCRATCH_INFO)
        row.gap(10)
        self.players_scratch_entry = _entry(row, state.players_scratch)
        # Blank means the output folder; the hint never becomes the value.
        kit_fields.placeholder(self.players_scratch_entry, state.players_scratch, "Output folder")
        row.gap(10)
        self.players_scratch_browse = row.button("Browse scratch folder...", commands.browse_players_scratch)

        card = self.export_capture_card = page.add_card("Capture")
        combos = []
        for label, variable, values in (("Video FPS", state.video_fps, FPS_CHOICES),
                                        ("Export speed", state.video_export_speed, SPEED_CHOICES)):
            row = card.row()
            row.label(label, width=80)
            row.gap(10)
            combos.append(_dropdown(row, variable, values))
        self.video_fps_combo, self.video_speed_combo = combos
        self.video_fixed_checkbox = _check(card.row(height=18), "Fixed-step export (frame-accurate)",
                                           state.video_fixed_step)

        card = self.export_passes_card = page.add_card("Output Passes", info=PASSES_INFO)
        row = card.row(height=18)
        self.video_depth_checkbox = _check(row, "Depth master (.mov)", state.video_depth, commands.depth_toggled)
        row.gap(5)
        row.info(DEPTH_INFO)
        row = card.row(height=18)
        row.gap(17)
        self.video_depth_exr_checkbox = _check(row, "EXR sequence (float)", state.video_depth_exr)
        # The sequence needs the depth master; the app keeps this in step with
        # depth changes from either UI.
        self.video_depth_exr_checkbox.configure(state="normal" if state.video_depth.get() else "disabled")
        self.video_layer_checkboxes = []
        for index, (title, variable) in enumerate((("World layer", state.video_layer_world),
                                                   ("Players layer (alpha)", state.video_layer_players),
                                                   ("Effects layer (alpha)", state.video_layer_effects))):
            row = card.row(height=18, top=15 if index == 0 else None)
            self.video_layer_checkboxes.append(_check(row, title, variable, commands.layer_toggled))

        # Filled by screenshot_ui.build (it owns the still's widgets and variables).
        self.screenshot_card = page.add_card("Screenshot" if edition.is_environment() else "Screenshot (High-Res Still)",
                                             info="Capture a PNG of the paused view." if edition.is_environment() else SCREENSHOT_INFO)

        card = self.ffmpeg_card = page.add_card("FFmpeg Runtime")
        row = card.row()
        row.label("Executable")
        row.gap(10)
        self.ffmpeg_path_entry = _entry(row, state.ffmpeg_path)
        flow = card.flow()
        self.ffmpeg_browse_button = flow.button("Browse FFmpeg...", commands.browse_ffmpeg)
        flow.button("Save path", commands.save_ffmpeg_preference)
        flow.button("Use bundled FFmpeg", commands.use_bundled_ffmpeg)

        # The last card of the page: it scrolls with the rest (N1).
        card = self.recording_card = page.add_card("Recording", info=RECORDING_INFO)
        row = card.row()
        row.button("Play shot", commands.play)
        row.gap(10)
        self.video_start_button = row.button("Record video", commands.start_video_recording, kind="primary")
        row.gap(10)
        self.video_stop_button = row.button("Finish recording", commands.stop_video_recording, state="disabled")
        row.gap(10)
        self.video_cancel_button = row.button("Discard recording", commands.discard_recording, state="disabled")
        card.text(textvariable=state.video_status_text, top=15)
        self.export_camera_note = card.text("Camera: Free path", top=4)

        # gui_layout still hands screenshot_ui.build "export_page.body" until it passes card= (PLAN-5 3.3).
        page.body = self.screenshot_card
        page.watch_focus()
