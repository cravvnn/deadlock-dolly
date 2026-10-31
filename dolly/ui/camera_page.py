"""Camera page view (Figma [design reference]): capture, the camera list, curves, the selected camera and attachment.

Built on the page kit (:mod:`.kit_page`): cards in two columns that scroll together with a fade at
the scroll edges, and the Shot Playback card pinned in the page's dock (PLAN-5 3.1; Kara's
comment #5). Wiring lives in ``gui_layout.py``; this module never imports the application
controller or the export backend.

The cards are a bento, like Look (USER-ADDITIONS UPDATE 17): Capture, Cameras and Camera
Curves on the left (613 px), Selected Camera and Attach Camera on the right (297 px), 20 px
gutters, both columns starting and ending on the same lines. The right column sets the
height and the left one fills it with what can grow: whole rows of the Cameras table first
(one per camera, 3 to 8, then it scrolls inside), then the curve graphs (240 px at least).
A left column much taller than the right one (stacked graphs) leaves the right one at its
own height instead of stretching Attach Camera into an empty card. Below 460 px of left
column the page is one column (PLAN-5 3.0.2), every card at its own height.

Only the cards of Kara's frame are built (UPDATE 17): the old Path · Top Down overview is
gone. The shared Shot Playback frame (:mod:`.playback_card`) is adopted into
``playback_host`` by the app whenever the page is shown. Every card button is
canvas-drawn (N21); labels, hints and help bubbles are items on the card canvases.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import tkinter as tk
from tkinter import ttk

from .. import ui_theme
from ..curve import TimelineView
from ..curve_workspace import CameraCurveWorkspace
from . import art
from .kit_card import KitBlock
from .kit_controls import KitSlider
from .kit_fields import TREE_CELL_INSET, hide_idle_scrollbar
from .kit_page import ColumnSpec, KitPage
from .scale import px

TITLE = "Camera"
SUBTITLE = "Capture cameras from the replay, then edit their timing, curves and attachment."
CAMERA_COLUMNS = ColumnSpec(main=613, side=297, gap=20, main_min=460, side_min=297)
ATTACH_INFO = "Player point of view or weapon camera"
CLEARANCE_INFO = ("Automatic clearance keeps head POV close and forward; other bones use wider spacing. "
                  "Animated limbs may still cross the view.")
CURVES_INFO = "Drag keys | Up/Down fine tune | Wheel zoom | Right-drag pan"
NO_SELECTION = "No camera selected"
CAPTURE_MODES = ("Replay timing", "Timed shot")
ATTACH_POINTS = ("Eyes", "Weapon", "Bone Picker...")
CLEARANCES = ("Exact offset", "Automatic clearance")
TABLE_ROWS = (3, 8)        # the Cameras table shows a row per camera, 3 to 8, then scrolls inside (Q12)
# The kit table's 26 px heading and 36 px rows (Library.Treeview): its slot grows by whole rows.
TABLE_HEADING = 26
TABLE_ROW = 36
LANE_HEIGHT = 240          # design px: the least a curve graph gets; the bento's spare makes them taller
BENTO_GAP = 20             # the bento's gutters, between the columns and between cards
# Attach Camera grows at most this much (design px) to meet a taller left column; beyond it
# (stacked graphs) the right column keeps its own height.
STRETCH_LIMIT = 40
# Idle passes the curves slot waits for the workspace's height after its rows changed.
SETTLE_PASSES = 4


@dataclass(frozen=True)
class CameraState:
    capture_mode: tk.Variable
    segment_seconds: tk.Variable
    hotkey_label: tk.Variable
    hotkey_enabled: tk.Variable
    project_text: tk.Variable
    path_summary: tk.Variable
    lens_interpolation: tk.Variable
    interpolation: tk.Variable
    selected_text: tk.Variable
    standard_aspect: tk.Variable
    key_vars: Mapping[str, tk.Variable]
    aspect_presets: tuple
    tree_fields: tuple
    tree_labels: tuple


@dataclass(frozen=True)
class CameraActions:
    start_path_here: Callable[..., object]
    capture_here: Callable[..., object]
    replace_camera_here: Callable[..., object]
    show_keybinds: Callable[..., object]
    capture_mode_changed: Callable[..., object]
    toggle_capture_hotkey: Callable[..., object]
    tree: Callable[..., object]
    select_key: Callable[..., object]
    delete_key: Callable[..., object]
    apply_options: Callable[..., object]
    open_coordinates: Callable[..., object]
    update_key: Callable[..., object]
    reset_aspect: Callable[..., object]
    apply_selected: Callable[..., object]
    open_bone_picker: Callable[..., object]
    cancel_bone_picker: Callable[..., object]
    attach_point_picker: Callable[..., object]
    apply_attach: Callable[..., object]             # apply_attach(all_keys: bool)
    change_unified_curve: Callable[..., object]
    select_curve_key: Callable[..., object]
    reset_rotation_curve: Callable[..., object]
    change_camera_tangent: Callable[..., object]
    preview_camera_curve: Callable[..., object]
    curve_context: Callable[..., object]


class _Spare:
    """One column's spare height in a bento row (a :meth:`.kit_page.KitPage.bento` ``grow`` target).

    The page sets ``extra`` (px) when it changes; ``share(extra)`` hands it to what can
    grow. ``key()`` names anything else the share depends on (the page's column mode):
    while it differs from the last share, ``extra`` reads None so the page sets it again.
    """

    def __init__(self, share, key=lambda: None):
        self.last = 0                   # px handed out last
        self._share = share
        self._key = key
        self._shared = key()

    @property
    def extra(self):
        return self.last if self._key() == self._shared else None

    @extra.setter
    def extra(self, value) -> None:
        self.last = value
        self._shared = self._key()
        self._share(value)


class _SteadyBlock(KitBlock):
    """A fixed-height block that grows in the bento and is moved in place, never unmapped.

    The bento lays a growing card out twice a layout (at its least height, then with its
    share of the spare); :class:`.kit_card.KitBlock` re-places with ``place_forget``, and
    unmapping the curve workspace cancels a drag or a typed value in progress. A second
    ``place`` before the placer runs just replaces the first, so a layout that ends where
    it started moves nothing.
    """

    def _layout(self, x0, y, width) -> int:
        self.x0, self.width = x0, width
        height = px(self.page.scale, self.fixed) + self.extra
        place = (x0 - self.bleed, y, width + self.bleed, height)
        if place != self._placed:
            self._placed = place
            self.widget.place(x=place[0], y=place[1], width=place[2], height=place[3])
        return height


class CameraPage:
    def __init__(self, parent, state: CameraState, commands: CameraActions):
        page = self.camera_page = KitPage(parent, columns=CAMERA_COLUMNS, pinned=True, fade=True)
        page.pack(fill="both", expand=True)
        self.page = page
        page.CARD_GAP = BENTO_GAP
        self._traces = []
        self._wanted_rows = TABLE_ROWS[0]   # one per camera of the shot, 3 to 8
        self._lanes_height = None           # the workspace's own height (px) its slot was last sized to
        self._settle_job = None
        main, side = page.columns()
        self._build_capture(main, state, commands)
        self._build_cameras(main, state, commands)
        self._build_curves(main, state, commands)
        self._build_selected(side, state, commands)
        self._build_attach(side, commands)
        # One bento row: the columns start and end together. The left column's spare goes to the
        # Cameras table and the graphs; the right one has nothing that grows (see _share_right).
        self._left_spare = _Spare(self._share_left, key=lambda: page.two_columns)
        self._right_spare = _Spare(self._share_right)
        page.bento([((self.capture_card, self.cameras_card, self.curves_card),
                     (self.selected_card, self.attach_card))],
                   grow={self.cameras_card: self._left_spare, self.attach_card: self._right_spare})
        # Shot Playback (app.timeline_frame) is pinned here while the page shows (N10), with its art.
        self.playback_host = page.host_card("Shot Playback", where="dock", art=art.playback_art)
        page.bind("<Destroy>", self._destroyed, add="+")
        page.watch_focus()

    # -- cards --------------------------------------------------------------------------------------
    def _build_capture(self, main, state, commands):
        card = self.capture_card = main.add_card("Capture")
        row = card.row()
        self.capture_buttons = [row.button("Start path here", commands.start_path_here, kind="primary")]
        row.gap(10)
        self.capture_buttons.append(row.button("Capture camera", commands.capture_here))
        row.gap(10)
        self.capture_buttons.append(row.button("Replace selected", commands.replace_camera_here))
        row.push()
        self.controls_button = row.button("Controls…", commands.show_keybinds, kind="quiet")
        row = card.row()
        row.label("Capture timing")
        row.gap(10)
        self.capture_mode_combo = row.widget(
            ttk.Combobox(row.host, textvariable=state.capture_mode, values=CAPTURE_MODES, state="readonly",
                         style="Kit.TCombobox"), width=140)
        self.capture_mode_combo.bind("<<ComboboxSelected>>", commands.capture_mode_changed)
        row.gap(20)
        row.label("Spacing")
        row.gap(10)
        self.segment_entry = row.widget(
            ttk.Entry(row.host, textvariable=state.segment_seconds, style="Kit.TEntry",
                      state="disabled" if state.capture_mode.get() == "Replay timing" else "normal"), width=34)
        row.gap(5)
        row.label("s")
        row = card.row(height=18)
        self.capture_hotkey_checkbox = row.widget(
            ttk.Checkbutton(row.host, textvariable=state.hotkey_label, variable=state.hotkey_enabled,
                            command=commands.toggle_capture_hotkey, style="Kit.TCheckbutton"))

    def _build_cameras(self, main, state, commands):
        # The table's last row keeps 10 px under its text: the card closes 10 px below it.
        card = self.cameras_card = main.add_card("Cameras", bottom=10)
        card.trailing.label(textvariable=state.project_text, tone="muted")
        card.trailing.gap(10)
        card.trailing.label(textvariable=state.path_summary, tone="dim")
        table, tree = commands.tree(card.host, ("view", *state.tree_fields), ("Camera", *state.tree_labels),
                                    height=TABLE_ROWS[0])
        self.camera_table, self.camera_tree = table, tree
        s = self.page.scale
        # A slot of the heading and three rows; the bento adds whole rows (_share_left).
        self.table_block = card._add(_SteadyBlock(card, table, top=15, bleed_left=TREE_CELL_INSET,
                                                  height=(px(s, TABLE_HEADING) + TABLE_ROWS[0] * px(s, TABLE_ROW)) / s))
        tree.configure(style="Kit.Treeview", displaycolumns=("view", "time", "aspect_ratio", "roll"))
        # Camera | Arrive · seconds | Aspect ratio | Bank ° (the last one takes the rest, and the
        # table's bleed: headings and cells start on the content edge, like Replay's table).
        for column, width, minwidth in (("view", 233, 95), ("time", 120, 88), ("aspect_ratio", 120, 84),
                                        ("roll", 98, 60)):
            tree.column(column, anchor="w", width=px(s, width) + (TREE_CELL_INSET if column == "roll" else 0),
                        minwidth=px(s, minwidth), stretch=column == "roll")
            tree.heading(column, anchor="w")
        tree.heading("time", text="Arrive · seconds")
        tree.heading("aspect_ratio", text="Aspect ratio")
        tree.heading("roll", text="Bank °")
        tree.bind("<<TreeviewSelect>>", commands.select_key)
        tree.bind("<Double-1>", lambda _event: self.lens_entry.focus_set())
        tree.bind("<Delete>", lambda _event: commands.delete_key())
        hide_idle_scrollbar(table, tree)

    def _build_curves(self, main, state, commands):
        card = self.curves_card = main.add_card("Camera Curves", info=CURVES_INFO)
        self.timeline_view = TimelineView()
        workspace = self.camera_curve_workspace = CameraCurveWorkspace(
            card.host, on_change=commands.change_unified_curve, on_select=commands.select_curve_key,
            on_reset=commands.reset_rotation_curve, view=self.timeline_view,
            on_tangent_change=commands.change_camera_tangent, on_preview=commands.preview_camera_curve,
            context=commands.curve_context, fixed_lane_height=LANE_HEIGHT, fill_lanes=True,
            tool_parent=False,   # Kara removed "Update original" and "Frame all" from this card
            modes=(("Interpolation", state.interpolation, ("smooth", "spline", "linear"), commands.apply_options),
                   ("Framing", state.lens_interpolation, ("smooth", "linear", "step"), commands.apply_options)),
            on_legend=lambda text: card.info.configure(text=text))
        # A slot of the workspace's own height (every graph at LANE_HEIGHT); the bento's spare
        # makes it taller and the workspace hands that to its graphs (fill_lanes).
        self.lanes_block = card._add(_SteadyBlock(card, workspace, top=15, height=LANE_HEIGHT))
        self._fit_lanes()
        workspace.on_geometry = self._lanes_changed
        self.position_curve, self.rotation_curve, self.aspect_curve = workspace.lanes
        self.camera_curve_tabs = workspace.tabs
        self.path_curve_mode, self.lens_curve_mode = workspace.mode_combos
        self.update_original_button = workspace.update_original_button
        self.frame_all_button = workspace.frame_all_button

    def _build_selected(self, side, state, commands):
        t = ui_theme.TOKENS
        card = self.selected_card = side.add_card("Selected Camera")
        row = card.row()
        status = self.selected_label = row.label(textvariable=state.selected_text, tone="dim")

        def tone(*_):
            status.configure(tone="dim" if state.selected_text.get() in ("", NO_SELECTION) else "text")
        self._trace(state.selected_text, tone)
        tone()
        row.push()
        more = self.more_button = row.widget(ttk.Menubutton(row.host, text="More", style="Kit.TMenubutton"), width=90)
        menu = self.more_menu = tk.Menu(more, tearoff=False, bg=t["panel"], fg=t["text"],
                                        activebackground=t["selected"], activeforeground=t["text"])
        menu.add_command(label="Coordinates / timing…", command=commands.open_coordinates)
        menu.add_separator()
        menu.add_command(label="Delete selected camera", command=commands.delete_key)
        more.configure(menu=menu)
        self.key_entries = {}
        for field, label in (("aspect_ratio", "Aspect ratio"), ("time", "Arrive · seconds"),
                             ("roll", "Bank · degrees")):
            row = card.row()
            row.label(label, width=100)
            row.gap(10)
            entry = row.widget(ttk.Entry(row.host, textvariable=state.key_vars[field], style="Kit.TEntry"),
                               width="fill")
            entry.bind("<Return>", lambda _event: commands.update_key())
            self.key_entries[field] = entry
        self.lens_entry = self.key_entries["aspect_ratio"]
        row = card.row()
        row.label("Normal", width=100)
        row.gap(10)
        # Editable: a typed ratio applies on Return.
        self.standard_combo = row.widget(
            ttk.Combobox(row.host, textvariable=state.standard_aspect, values=tuple(state.aspect_presets),
                         style="Kit.TCombobox"), width="fill")
        self.standard_combo.bind("<<ComboboxSelected>>", lambda _event: commands.apply_options())
        self.standard_combo.bind("<Return>", lambda _event: commands.apply_options())
        row.gap(10)
        self.reset_aspect_button = row.button("Reset", commands.reset_aspect)
        row = card.row(top=15)
        self.update_key_button = row.button("Update camera", commands.update_key, kind="primary")
        row.gap(10)
        self.preview_button = row.button("Preview", commands.apply_selected)

    def _build_attach(self, side, commands):
        card = self.attach_card = side.add_card("Attach Camera", info=ATTACH_INFO, title_gap=20)
        self.attach_enabled = tk.BooleanVar(card.host, value=False)
        self.attach_player = tk.StringVar(card.host, value="")
        self.attach_point = tk.StringVar(card.host, value="Eyes")
        self.attach_bone = tk.StringVar(card.host, value="head")
        self.attach_vars = {}
        self.attach_smoothing = tk.StringVar(card.host, value="0")
        self.attach_hide = tk.BooleanVar(card.host, value=True)
        self.attach_lock_vertical_aim = tk.BooleanVar(card.host, value=False)
        self.attach_clearance = tk.StringVar(card.host, value="Exact offset")
        self.source_blend = tk.StringVar(card.host, value="0")
        row = card.row(height=18)
        self.attach_checkbox = row.widget(ttk.Checkbutton(row.host, text="Attach to player",
                                                          variable=self.attach_enabled, style="Kit.TCheckbutton"))
        row = card.row()
        # The live roster ("Live roster: 3 player(s).") is the Player label's info bubble
        # (Kara: "should be in info button"), not a line under the list.
        row.label("Player", width=75, info="Load a replay to list players.")
        self.attach_note = row.core.cells[-1].info
        row.gap(10)
        self.attach_player_combo = row.widget(
            ttk.Combobox(row.host, textvariable=self.attach_player, values=(), state="readonly",
                         style="Kit.TCombobox"), width="fill")
        row = card.row()
        row.label("Attach point", width=75)
        row.gap(10)
        self.attach_point_combo = row.widget(
            ttk.Combobox(row.host, textvariable=self.attach_point, values=ATTACH_POINTS, state="readonly",
                         style="Kit.TCombobox"), width="fill")
        # Shown only while the point is a bone.
        self.bone_row = card.section()
        row = self.bone_row.row()
        row.label("Bone", width=75)
        row.gap(10)
        self.attach_bone_combo = row.widget(ttk.Combobox(row.host, textvariable=self.attach_bone, values=(),
                                                         style="Kit.TCombobox"), width="fill")
        row = card.row()
        self.bone_picker_button = row.button("Bone Picker...", commands.open_bone_picker)
        row.gap(10)
        self.cancel_picker_button = row.button("Cancel picker", commands.cancel_bone_picker)
        row = card.row(height=18)
        self.attach_lock_checkbox = row.widget(ttk.Checkbutton(
            row.host, text="Lock vertical aim", variable=self.attach_lock_vertical_aim,
            style="Kit.TCheckbutton"))
        row.gap(5)
        row.info("Bone/weapon follows position and yaw without tilting with aim. "
                 "Detach, change this setting, then Snap again to keep your view. Eyes is unaffected.")
        card.text("Offsets", tone="text", wrap=False)
        self.attach_sliders = {}
        for name, label, low, high in (("x", "Forward", -10, 10), ("y", "Sideways", -10, 10), ("z", "Up", -10, 10),
                                       ("pitch", "Pitch", -180, 180)):
            self.attach_vars[name] = tk.StringVar(card.host, value="0")
            self._slider_row(card, name, label, self.attach_vars[name], low, high)
        self._slider_row(card, "smooth", "Smooth", self.attach_smoothing, 0, 1)
        row = card.row(height=18)
        self.attach_hide_checkbox = row.widget(ttk.Checkbutton(row.host, text="Hide this hero",
                                                               variable=self.attach_hide, style="Kit.TCheckbutton"))
        row = card.row()
        row.label("Clearance", width=75, info=CLEARANCE_INFO)
        row.gap(10)
        self.attach_clearance_combo = row.widget(
            ttk.Combobox(row.host, textvariable=self.attach_clearance, values=CLEARANCES, state="readonly",
                         style="Kit.TCombobox"), width="fill")
        advanced = self.rotation_disclosure = card.disclosure("Rotation & transition")
        for name, label in (("yaw", "Yaw"), ("roll", "Roll")):
            self.attach_vars[name] = tk.StringVar(card.host, value="0")
            self._slider_row(advanced.body, name, label, self.attach_vars[name], -180, 180)
        self._slider_row(advanced.body, "blend", "Blend in", self.source_blend, 0, 10)
        advanced.body.text("Seconds; zero keeps a cut.", tone="dim")
        row = card.row(top=20)
        self.apply_attach_button = row.button("Apply to camera", lambda: commands.apply_attach(False),
                                              kind="primary", width=119)
        row.gap(10)
        self.apply_all_button = row.button("Apply to all", lambda: commands.apply_attach(True), width=93)
        # The roster line moved into the Player info bubble; its 22 px stay at the card's foot
        # so the right column still sets the bento's height (the page keeps one height
        # whichever curve tab shows).
        card.row(height=16, top=6)

        # "Bone Picker..." opens the picker (the app restores the key's point first);
        # a bone point shows the bone row.
        def point_changed(*_):
            point = self.attach_point.get()
            if point == "Bone Picker...":
                commands.attach_point_picker()
                return
            self.bone_row.set_visible(point == "Bone")
        self._trace(self.attach_point, point_changed)
        point_changed()

    def _slider_row(self, container, name, label, variable, low, high):
        """Label (60) · 10 · kit slider · 10 · the same value in a 50 px field (attach offsets)."""
        row = container.row()
        row.label(label, width=60)
        row.gap(10)
        slider = row.widget(KitSlider(row.host, from_=low, to=high, variable=variable, format="compact"),
                            width="fill", height=12)
        row.gap(10)
        entry = row.widget(ttk.Entry(row.host, textvariable=variable, style="Kit.TEntry"), width=50)
        self.attach_sliders[name] = (slider, entry)
        return row

    # -- the bento ----------------------------------------------------------------------------------
    def fit_camera_rows(self) -> int:
        """Follow the shot: the Cameras table wants a row per camera, 3 to 8 (Q12); returns the rows it shows.

        In two columns it shows as many of them as the left column's spare height holds
        and scrolls inside for the rest; in one column it grows with the shot.
        """
        low, high = TABLE_ROWS
        wanted = max(low, min(high, len(self.camera_tree.get_children())))
        if wanted != self._wanted_rows:
            self._wanted_rows = wanted
            self._share_left(self._left_spare.last, measure=False)
            self.cameras_card._changed()
            self.curves_card._changed()
        return int(self.camera_tree.cget("height"))

    def _share_left(self, extra, *, measure=True):
        """The left column's spare height (px): whole table rows the shot can fill first, then the graphs.

        Called by the page's layout (``measure``: the Camera Curves card is laid out again
        here, the page lays out the Cameras card itself). One column has no spare: the
        table shows its wanted rows and the graphs their least height.
        """
        page, s = self.page, self.page.scale
        low = TABLE_ROWS[0]
        row = px(s, TABLE_ROW)
        two = page.two_columns
        rows = low + min(self._wanted_rows - low, max(0, extra) // row) if two else self._wanted_rows
        if int(self.camera_tree.cget("height")) != rows:
            self.camera_tree.configure(height=rows)
        self.table_block.extra = (rows - low) * row
        lanes = max(0, extra - self.table_block.extra) if two else 0
        fitted = self._fit_lanes()
        if lanes != self.lanes_block.extra or fitted:
            self.lanes_block.extra = lanes
            if measure and page.main_width > 1:
                self.curves_card.measure(page.main_width)

    def _share_right(self, extra):
        """Attach Camera meets a slightly taller left column; a much taller one (stacked graphs) leaves it be."""
        self.attach_card.extra = extra if extra <= px(self.page.scale, STRETCH_LIMIT) else 0

    def _fit_lanes(self) -> bool:
        """Size the curves slot to the workspace's own height (every graph at its least); True if it changed."""
        s = self.page.scale
        height = max(px(s, LANE_HEIGHT), self.camera_curve_workspace.winfo_reqheight())
        if height == self._lanes_height:
            return False
        self._lanes_height = height
        self.lanes_block.fixed = height / s
        return True

    def _lanes_changed(self):
        """The workspace laid its rows out again (a tab, Stack graphs, a narrow width): follow its height.

        Its requested height arrives a few idle passes after its rows changed (grid
        propagates it one level per pass), so the slot is checked until it stays put.
        """
        if self._settle_job is not None:
            self.camera_curve_workspace.after_cancel(self._settle_job)
            self._settle_job = None
        self._settle_lanes(SETTLE_PASSES)

    def _settle_lanes(self, passes):
        self._settle_job = None
        workspace = self.camera_curve_workspace
        if not workspace.winfo_exists():
            return
        if self._fit_lanes():
            self.curves_card._changed()
            passes = SETTLE_PASSES
        if passes > 0:
            self._settle_job = workspace.after_idle(self._settle_lanes, passes - 1)

    # -- behaviour --------------------------------------------------------------------------------
    def _trace(self, variable, callback):
        self._traces.append((variable, variable.trace_add("write", callback)))

    def _destroyed(self, event):
        if event.widget is not self.page:
            return
        if self._settle_job is not None:
            try:
                self.camera_curve_workspace.after_cancel(self._settle_job)
            except tk.TclError:
                pass
            self._settle_job = None
        for variable, token in self._traces:
            try:
                variable.trace_remove("write", token)
            except tk.TclError:
                pass
        self._traces = []


__all__ = ["CameraActions", "CameraPage", "CameraState", "CAMERA_COLUMNS", "ATTACH_INFO", "CLEARANCE_INFO",
           "TITLE", "SUBTITLE"]
