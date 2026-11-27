"""Scene page view: the shot's weather, the desktop twin of the in-game Scene drawer.

Built on the page kit (:mod:`.kit_page`) like Look: the same two columns and 20 px bento
gutters, with the shared Shot Playback card pinned at the bottom. The cards follow the
in-game drawer's sections one to one, so a setting has the same name and place in both:
Weather (preset, intensity, build-up and gusts) beside Precipitation (flake size and
density), then Wind (direction, speed and hero wakes) beside Ground and Haze (coating, height ceiling
and the Effects layer toggle), then Lens (water on the camera lens while it rains).

Each slider shows its value in its caption, reports while it moves and commits on release
(or on a key), like the Citadel DOF sliders on Look. While the preset is Off the controls
below it are disabled, as the drawer greys them.

Both editors change the same per-shot ``Project.weather``: an edit in game updates this
page and an edit here reaches the game straight away.

Wiring lives in ``gui_layout.py``; this module never imports the application controller.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import math
import tkinter as tk
from tkinter import ttk

from . import art, fonts
from .kit_controls import KitSlider
from .kit_page import ColumnSpec, KitPage

TITLE = "Scene"
SUBTITLE = "Weather for the shot: wind, snow, rain, haze and ground coating, drawn by Dolly in game."
SCENE_COLUMNS = ColumnSpec(main=613, side=297, gap=20, main_min=520, side_min=297)
BENTO_GAP = 20
CAPTION_MIN = 120

WEATHER_INFO = "Saved with the shot. Playback, scrubbing and export match exactly."
QUALITY_INFO = "Weather detail for live preview and video exports. High adds finer sand, reflections and puddle ripples."
WIND_INFO = "Moves haze, flakes and drifts around the map. Direction is where the wind blows toward."
PRECIP_INFO = "Snow, rain, sand and ash flakes. Fog and haze presets have none."
GROUND_INFO = "Coating settles where open sky is above; haze rises to the ceiling."
BUILD_UP_INFO = "Seconds from the shot start for snow, sand or wetness to settle. 0 starts settled."
HERO_INFO = "Heroes part the wind, haze and flakes around them."
EFFECTS_INFO = "Weather is always in Color and World, never in Players or Depth."
LENS_INFO = ("Water drops land on the lens while the shot rains, run down and leave trails. "
             "Under a roof no new drops land. Drawn on Color and World only.")
TRAILS_INFO = ("Heroes leave footprints in snow, sand, dust and ash. Dolly reads where they walk "
               "from the replay when you open it, and keeps it in its own file beside the map "
               "data. The replay itself is never changed.")
TRAILS_NONE = "Hero tracks: appear as the shot plays."
OFF_NOTICE = "Pick a preset to turn on wind, flakes and haze."
ON_NOTICE = "Also in game: F8 → Scene. Both edit the same shot."


@dataclass(frozen=True)
class Control:
    """One weather slider: its caption, variable, range and how its value reads."""
    key: str
    label: str
    low: float
    high: float
    fmt: str            # str.format spec for the caption value, e.g. "{:.2f}"
    unit: str = ""
    widest: str = ""    # the widest caption value, so the slider never moves
    info: str = ""      # the caption's info bubble, if any


@dataclass(frozen=True)
class SceneState:
    preset: tk.Variable
    values: dict        # Control.key -> tk.DoubleVar
    include_in_effects: tk.Variable
    hero_wakes: tk.Variable
    lens_rain: tk.Variable
    preset_labels: tuple
    controls: dict      # card title -> tuple[Control, ...]
    hero_tracks: tk.Variable | None = None
    quality: tk.Variable | None = None
    quality_labels: tuple = ("Standard", "High")


@dataclass(frozen=True)
class SceneActions:
    weather_changed: Callable[..., object]


class ScenePage:
    def __init__(self, parent, state: SceneState, commands: SceneActions):
        page = self.scene_page = KitPage(parent, columns=SCENE_COLUMNS, pinned=True, fade=True)
        page.pack(fill="both", expand=True)
        s = page.scale
        page.CARD_GAP = BENTO_GAP
        main, side = page.columns()
        self.sliders: dict[str, KitSlider] = {}
        self.captions: dict[str, tk.StringVar] = {}
        self._state = state
        body = fonts.font(page, "body")

        def caption_width(controls):
            widest = max(body.measure(self._caption(c, c.widest or c.fmt.format(c.high))) for c in controls)
            return max(CAPTION_MIN, math.ceil(widest / s))

        def sliders(card, controls, first_top=None):
            width = caption_width(controls)
            for index, control in enumerate(controls):
                variable = state.values[control.key]
                text = self.captions[control.key] = tk.StringVar(page, self._caption(control, variable.get()))
                variable.trace_add("write", lambda *_a, c=control, v=variable, t=text:
                                   t.set(self._caption(c, self._safe(v, c))))
                row = card.row(top=first_top if index == 0 else 5)
                row.label(textvariable=text, width=width, info=control.info or None)
                row.gap(10)
                slider = row.widget(
                    KitSlider(row.host, from_=control.low, to=control.high, variable=variable,
                              command=lambda _value: None), width="fill")
                slider.bind("<ButtonRelease-1>", lambda _event: commands.weather_changed(), add="+")
                slider.bind("<KeyRelease>", lambda _event: commands.weather_changed(), add="+")
                self.sliders[control.key] = slider

        weather = self.weather_card = main.add_card("Weather", info=WEATHER_INFO)
        row = weather.row()
        row.label("Preset", width=55)
        row.gap(10)
        self.preset_combo = row.widget(
            ttk.Combobox(row.host, textvariable=state.preset, values=tuple(state.preset_labels),
                         state="readonly", style="Kit.TCombobox"), width="fill")
        self.preset_combo.bind("<<ComboboxSelected>>", lambda _event: commands.weather_changed(), add="+")
        quality_var = state.quality if state.quality is not None else tk.StringVar(page, state.quality_labels[0])
        qrow = weather.row(top=8)
        qrow.label("Quality", width=55, info=QUALITY_INFO)
        qrow.gap(10)
        self.quality_combo = qrow.widget(
            ttk.Combobox(qrow.host, textvariable=quality_var, values=tuple(state.quality_labels),
                         state="readonly", style="Kit.TCombobox"), width="fill")
        self.quality_combo.bind("<<ComboboxSelected>>", lambda _event: commands.weather_changed(), add="+")
        self.notice = tk.StringVar(page, OFF_NOTICE)
        weather.row(height=18).label(textvariable=self.notice, tone="dim")
        sliders(weather, state.controls["Weather"])

        precip = self.precip_card = side.add_card("Precipitation", info=PRECIP_INFO)
        sliders(precip, state.controls["Precipitation"])

        wind = self.wind_card = main.add_card("Wind", info=WIND_INFO)
        sliders(wind, state.controls["Wind"])
        row = wind.row(top=10, height=18)
        self.hero_checkbox = row.widget(
            ttk.Checkbutton(row.host, text="Hero wakes", variable=state.hero_wakes,
                            command=commands.weather_changed, style="Kit.TCheckbutton"))
        row.gap(5)
        row.info(HERO_INFO)

        ground = self.ground_card = side.add_card("Ground and Haze", info=GROUND_INFO)
        sliders(ground, state.controls["Ground and Haze"])
        row = ground.row(top=10, height=18)
        self.effects_checkbox = row.widget(
            ttk.Checkbutton(row.host, text="Include in Effects layer", variable=state.include_in_effects,
                            command=commands.weather_changed, style="Kit.TCheckbutton"))
        row.gap(5)
        row.info(EFFECTS_INFO)
        row = ground.row(top=10, height=18)
        tracks = state.hero_tracks if state.hero_tracks is not None else tk.BooleanVar(page, True)
        self.tracks_checkbox = row.widget(
            ttk.Checkbutton(row.host, text="Hero tracks", variable=tracks,
                            command=commands.weather_changed, style="Kit.TCheckbutton"))
        row.gap(5)
        row.info(TRAILS_INFO)
        self.trails_status = tk.StringVar(page, TRAILS_NONE)
        ground.row(top=5, height=18).label(textvariable=self.trails_status, tone="dim")

        lens = self.lens_card = main.add_card("Lens", info=LENS_INFO)
        row = lens.row(height=18)
        self.lens_checkbox = row.widget(
            ttk.Checkbutton(row.host, text="Rain on the lens", variable=state.lens_rain,
                            command=commands.weather_changed, style="Kit.TCheckbutton"))
        sliders(lens, state.controls["Lens"], first_top=10)

        page.bento([((weather,), (precip,)), ((wind, lens), (ground,))])
        self.playback_host = page.host_card("Shot Playback", where="dock", art=art.playback_art)
        page.watch_focus()

    @staticmethod
    def _safe(variable, control):
        try:
            return float(variable.get())
        except (tk.TclError, ValueError):
            return control.low

    @staticmethod
    def _safe_flag(variable) -> bool:
        try:
            return bool(variable.get())
        except (tk.TclError, ValueError):
            return False

    @staticmethod
    def _caption(control: Control, value) -> str:
        text = value if isinstance(value, str) else control.fmt.format(float(value))
        return f"{control.label} · {text}{control.unit}"

    def set_enabled(self, enabled: bool) -> None:
        """Grey every control below the preset while the weather is Off (the drawer does the same)."""
        # The lens sliders also wait for their own switch, as the drawer greys them.
        lens_on = enabled and self._safe_flag(self._state.lens_rain)
        lens_keys = {control.key for control in self._state.controls.get("Lens", ())}
        for key, slider in self.sliders.items():
            on = lens_on if key in lens_keys else enabled
            slider.configure(state="normal" if on else "disabled")
        for box in (self.hero_checkbox, self.effects_checkbox, self.lens_checkbox,
                    self.tracks_checkbox):
            box.state(["!disabled"] if enabled else ["disabled"])
        self.quality_combo.configure(state="readonly" if enabled else "disabled")
        self.notice.set(ON_NOTICE if enabled else OFF_NOTICE)
