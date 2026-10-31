"""Look page view (Figma [design reference]): animated tracks, the track editor, particles, Citadel depth of field.

Built on the page kit (:mod:`.kit_page`, PLAN-5 3.2): Animated Tracks and Track
Editor in the main column, Particles, Citadel Depth of Field and Fixed Values in
the side column, and the shared Shot Playback card (``app.timeline_frame``, adopted
by :attr:`LookPage.playback_host`) pinned at the bottom like on Camera: the cards
above it scroll together, with the scroll-edge fade (USER-ADDITIONS UPDATE 7, 8).

The cards are a bento grid (UPDATE 8): Animated Tracks beside Particles, then Track
Editor beside Citadel Depth of Field over Fixed Values; neighbours share their top and
bottom edges with 20 px gutters. Track Editor's two buttons sit on its title line, so
its height matches the two cards beside it exactly; the 5 px Animated Tracks lacks
beside Particles go to its table. Below the two-column width the cards stack in one
column. (Kara chose the bento over Replay's one-column look after seeing both.)

Labels, units and every card button are drawn on the cards' canvases; fields,
dropdowns, checkboxes, tables and the two DOF sliders are real widgets placed on them.

Where today's Look controls went: the "CAMERA VARIABLES", "PARTICLES" and
"ANIMATED TRACKS" labels are the header and card titles; the Range DOF help line is
the Value field's info bubble (same text); the Fixed values dialog stays the app's
Toplevel, opened by "Fixed values…"; the Citadel DOF card moved here from Camera
(N12; the app's ``_citadel_*`` helpers read the variables this page creates).

Wiring lives in ``gui_layout.py``; this module never imports the application
controller or the export backend.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import math
import tkinter as tk
from tkinter import ttk

from . import art, fonts, kit_fields
from .kit_controls import CanvasInfoIcon, KitSlider
from .kit_page import ColumnSpec, KitPage
from .scale import px

TITLE = "Look"
SUBTITLE = "Animate camera variables over the shot, set fixed values and add particles."
LOOK_COLUMNS = ColumnSpec(main=613, side=297, gap=20, main_min=520, side_min=297)
BENTO_GAP = 20              # the bento's gutters, between columns and between rows
RANGE_INFO = "Range DOF: near blurry · near crisp · far crisp · far blurry. Enter four numbers separated by spaces."
FIXED_INFO = ("Fixed values… opens a separate dialog on Look. These values are applied for the shot; "
              "a track with the same name overrides the fixed value while the shot runs.")
CURVES = ("step", "linear", "smooth")
HEIGHT_UNIT = "units (100–1500)"
# The DOF captions change with the value; their column fits the widest one, so the
# sliders never move (look.md F7).
CAPTION_WIDTH = 135
SENSOR_WIDEST = "Sensor size · 3.00"
FOCUS_WIDEST = "Focus distance · 10000 in"
# Both tables show two rows and scroll inside (PLAN-5 Q12): the kit table's 26 px
# heading and 36 px rows (Library.Treeview), so the card never waits for the table.
TABLE_ROWS = 2
TABLE_HEADING = 26
TABLE_ROW = 36


@dataclass(frozen=True)
class LookState:
    confetti_enabled: tk.Variable
    particles_preset: tk.Variable
    particles_intensity: tk.Variable
    confetti_spawn_height: tk.Variable
    confetti_despawn_on_ground: tk.Variable
    track_name: tk.Variable
    track_mode: tk.Variable
    restore_value: tk.Variable
    track_time: tk.Variable
    track_value: tk.Variable
    particle_labels: tuple
    intensity_unit: str


@dataclass(frozen=True)
class LookActions:
    range_dof_preset: Callable[..., object]
    citadel_dof_preset: Callable[..., object]
    remove_track: Callable[..., object]
    select_track: Callable[..., object]
    create_track: Callable[..., object]
    update_track: Callable[..., object]
    select_value: Callable[..., object]
    put_value: Callable[..., object]
    delete_value: Callable[..., object]
    particles_changed: Callable[..., object]
    toggle_citadel_dof: Callable[..., object]
    sensor_changed: Callable[..., object]
    sensor_commit: Callable[..., object]
    focus_changed: Callable[..., object]
    focus_commit: Callable[..., object]
    show_fixed_values: Callable[..., object]
    tree: Callable[..., object]


class LookPage:
    def __init__(self, parent, state: LookState, commands: LookActions):
        page = self.look_page = KitPage(parent, columns=LOOK_COLUMNS, pinned=True, fade=True)
        page.pack(fill="both", expand=True)
        s = page.scale
        page.CARD_GAP = BENTO_GAP
        main, side = page.columns()
        # Citadel DOF lives here now; the app's helpers read and write these (today's defaults).
        self.citadel_enabled = tk.BooleanVar(page, False)
        self.citadel_sensor = tk.DoubleVar(page, 1.0)
        self.citadel_sensor_text = tk.StringVar(page, "Sensor size · 1.00")
        self.citadel_focus_internal = tk.DoubleVar(page, 0.0)
        self.citadel_focus_text = tk.StringVar(page, "Focus distance · 200 in")

        # The table's last row keeps 10 px under its text, so the card closes 10 px below it.
        tracks = self.tracks_card = main.add_card("Animated Tracks", bottom=10)
        row = tracks.row()
        self.range_dof_button = row.button("+ Range DOF", commands.range_dof_preset)
        row.gap(10)
        self.citadel_dof_button = row.button("+ Citadel DOF", commands.citadel_dof_preset)
        row.push()
        self.remove_track_button = row.button("Remove track", commands.remove_track, kind="quiet")
        self.track_table, self.track_tree, self.track_block = self._table(tracks, commands.tree, ("name",),
                                                                          ("Variable",), (571,))
        self.track_tree.column("name", minwidth=px(s, 120))
        self.track_tree.bind("<<TreeviewSelect>>", commands.select_track)

        editor = self.editor_card = main.add_card("Track Editor")
        # The two buttons share the title line, so the card is as tall as the two beside it.
        row = editor.trailing
        self.create_track_button = row.button("Create track", commands.create_track)
        row.gap(10)
        self.update_track_button = row.button("Update settings", commands.update_track)
        row = editor.row()
        row.label("Variable name")
        row.gap(10)
        self.track_name_entry = row.widget(
            ttk.Entry(row.host, textvariable=state.track_name, style="Kit.TEntry"), width="fill")
        self.track_name_cover = kit_fields.elide_at_rest(self.track_name_entry, state.track_name)
        row = editor.row()
        row.label("Curve")
        row.gap(10)
        self.track_mode_combo = row.widget(
            ttk.Combobox(row.host, textvariable=state.track_mode, values=CURVES, state="readonly",
                         style="Kit.TCombobox"), width=120)
        row.gap(20)
        row.label("Restore · optional")
        row.gap(10)
        self.restore_entry = row.widget(
            ttk.Entry(row.host, textvariable=state.restore_value, style="Kit.TEntry"), width="fill")
        self.value_table, self.value_tree, self.value_block = self._table(editor, commands.tree, ("time", "value"),
                                                                          ("Shot seconds", "Value"), (120, 451))
        self.value_tree.bind("<<TreeviewSelect>>", commands.select_value)
        self.value_tree.bind("<Delete>", lambda _event: commands.delete_value())
        # 15 below the table's last text: its row keeps 10 px under it.
        row = editor.row(top=5)
        row.label("Time")
        row.gap(10)
        self.track_time_entry = row.widget(
            ttk.Entry(row.host, textvariable=state.track_time, style="Kit.TEntry"), width=80)
        row.gap(20)
        row.label("Value", info=RANGE_INFO)
        self.range_info = next(control for control in row.controls() if isinstance(control, CanvasInfoIcon))
        row.gap(10)
        self.track_value_entry = row.widget(
            ttk.Entry(row.host, textvariable=state.track_value, style="Kit.TEntry"), width="fill")
        row.gap(20)
        self.put_value_button = row.button("Add / Replace", commands.put_value)
        row.gap(10)
        self.delete_value_button = row.button("Delete", commands.delete_value, kind="quiet")

        particles = self.particles_card = side.add_card("Particles")
        row = particles.row()
        self.confetti_checkbox = row.widget(
            ttk.Checkbutton(row.host, text="Enable", variable=state.confetti_enabled,
                            command=commands.particles_changed, style="Kit.TCheckbutton"))
        row.gap(10)
        self.particles_combo = row.widget(
            ttk.Combobox(row.host, textvariable=state.particles_preset, values=tuple(state.particle_labels),
                         state="readonly", style="Kit.TCombobox"), width="fill")
        self.particles_combo.bind("<<ComboboxSelected>>", lambda _event: commands.particles_changed(), add="+")
        # Numeric fields fit their widest valid value (look.md F6); a typed value applies on Return or leaving.
        numbers = []
        for label, variable, widest, design, unit in (
                ("Intensity", state.particles_intensity, "0.25", 32, state.intensity_unit),
                ("Height", state.confetti_spawn_height, "1500", 49, HEIGHT_UNIT)):
            row = particles.row()
            row.label(label, width=55)
            row.gap(10)
            entry = ttk.Entry(row.host, textvariable=variable, style="Kit.TEntry")
            row.widget(entry, width=kit_fields.number_width(entry, widest, design) / s)
            entry.bind("<Return>", lambda _event: commands.particles_changed(), add="+")
            entry.bind("<FocusOut>", lambda _event: commands.particles_changed(), add="+")
            row.gap(5)
            row.label(unit, tone="dim")
            numbers.append(entry)
        self.intensity_entry, self.spawn_height_entry = numbers
        row = particles.row(height=18)
        self.despawn_checkbox = row.widget(
            ttk.Checkbutton(row.host, text="Despawn on ground", variable=state.confetti_despawn_on_ground,
                            command=commands.particles_changed, style="Kit.TCheckbutton"))

        dof = self.dof_card = side.add_card("Citadel Depth of Field")
        row = dof.row(height=18)
        self.citadel_dof_checkbox = row.widget(
            ttk.Checkbutton(row.host, text="Enable DOF", variable=self.citadel_enabled,
                            command=commands.toggle_citadel_dof, style="Kit.TCheckbutton"))
        body = fonts.font(page, "body")
        caption = max(CAPTION_WIDTH, math.ceil(max(body.measure(SENSOR_WIDEST), body.measure(FOCUS_WIDEST)) / s))
        # Sliders report while they move and commit on release, as the ttk scales did.
        row = dof.row()
        row.label(textvariable=self.citadel_sensor_text, width=caption)
        row.gap(10)
        self.citadel_sensor_scale = row.widget(
            KitSlider(row.host, from_=.5, to=3.0, variable=self.citadel_sensor,
                      command=lambda _value: commands.sensor_changed()), width="fill")
        self.citadel_sensor_scale.bind("<ButtonRelease-1>", lambda _event: commands.sensor_commit())
        row = dof.row(top=5)
        row.label(textvariable=self.citadel_focus_text, width=caption)
        row.gap(10)
        self.citadel_focus_scale = row.widget(
            KitSlider(row.host, from_=0.0, to=1.0, variable=self.citadel_focus_internal,
                      command=lambda _value: commands.focus_changed()), width="fill")
        self.citadel_focus_scale.bind("<ButtonRelease-1>", lambda _event: commands.focus_commit())

        fixed = self.fixed_card = side.add_card("Fixed Values", info=FIXED_INFO)
        self.fixed_values_button = fixed.row().button("Fixed values…", commands.show_fixed_values)
        page.bento([((tracks,), (particles,)), ((editor,), (dof, fixed))],
                   grow={tracks: self.track_block, editor: self.value_block})

        # Shot Playback (the app's timeline_frame) is pinned here while Look shows, with its
        # background art; made last so it is last in Tab order too.
        self.playback_host = page.host_card("Shot Playback", where="dock", art=art.playback_art)
        page.watch_focus()

    @staticmethod
    def _table(card, tree, columns, labels, widths):
        """A kit table at the end of ``card``: ``TABLE_ROWS`` rows, left-aligned, its own rail only while needed.

        Headings and rows start on the content edge (the table bleeds left by Tk's cell
        indent); the last column takes the rest of the width.
        """
        s = card.page.scale
        sizes = [px(s, width) for width in widths]
        sizes[-1] += kit_fields.TREE_CELL_INSET
        table, widget = tree(card.host, columns, labels, tuple(sizes), height=TABLE_ROWS)
        height = px(s, TABLE_HEADING) + TABLE_ROWS * px(s, TABLE_ROW)
        block = card.block(table, top=15, height=height / s, bleed_left=kit_fields.TREE_CELL_INSET)
        widget.configure(style="Kit.Treeview")
        for column in columns:
            widget.column(column, anchor="w", stretch=column == columns[-1])
            widget.heading(column, anchor="w")
        # The table sits on a card canvas, where the theme's restyler finds no card frame.
        bar = next((child for child in table.winfo_children() if isinstance(child, ttk.Scrollbar)), None)
        if bar is not None:
            try:
                ttk.Style(bar).layout("Card.Vertical.TScrollbar")
                bar.configure(style="Card.Vertical.TScrollbar")
            except tk.TclError:
                pass   # a standalone page without the shell theme
        kit_fields.hide_idle_scrollbar(table, widget)
        return table, widget, block
