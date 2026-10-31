"""The ``Kit.*`` ttk styles of Kara's Redesign component kit (Figma page [design reference]).

Fields, dropdowns, menu buttons, checkboxes, tabs, keycaps and tables stay real ttk
widgets on the five ported pages; this module gives them the kit's look. Buttons do
not live here: every card button is canvas-drawn (:mod:`.kit_controls`).

Rules (PLAN-5 2.3): a native fill paints the large centre and four small corner
images stamp the rounded ends, precomposed opaque over the card surface (``panel``)
so Tk takes its opaque image path, the same technique as ``gui_theme.apply``'s
``rounded()`` but never its ``scale > 2`` branch: no image element is ever tiled
(:data:`ELEMENTS` records each one for the no-tiling test). Every control is 30
design px tall at any scale (the room around the body line absorbs the font's
drift). No style has a ``focus`` state: the kit draws no focus (N4); keyboard use
is complete without it. Disabled faces are the kit's 50 % opacity pre-mixed over
``panel`` (``*_disabled`` tokens).

:func:`install` is idempotent per interpreter, theme and scale (ttk styles belong to
a theme): the shell calls it at the end of ``gui_theme.apply`` and every
:class:`~dolly.ui.kit_page.KitPage` calls it too, so standalone pages get the styles.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .. import ui_theme
from . import art
from .scale import px, ui_scale

# Every image element made by install(): name -> ((width, height), sticky). Only
# corners (sticky one corner), the checkbox indicator (sticky "w"), chevrons and the
# minimum shim (sticky "") exist; none is stretched, so ttk never tiles one.
ELEMENTS: dict[str, tuple[tuple[int, int], str]] = {}

CORNERS = ("nw", "ne", "sw", "se")
STYLES = ("Kit.TEntry", "Kit.Flat.TEntry", "Kit.TCombobox", "Kit.TMenubutton", "Kit.TCheckbutton",
          "Kit.Muted.TCheckbutton", "Kit.Tab.TButton", "Kit.Tab.Selected.TButton", "Kit.Keycap.TButton",
          "Kit.KeycapEmpty.TButton", "Kit.Treeview")


def body_linespace(widget) -> int:
    """Line height of the ttk body face (``(FONT_FAMILY, FONT_SIZES["body"])``) at this display."""
    family, size = ui_theme.FONT_FAMILY, ui_theme.FONT_SIZES["body"]
    return int(widget.tk.call("font", "metrics", (family, size), "-linespace"))


def kit_padding(widget, side, height=30) -> tuple[int, int, int, int]:
    """Padding that makes a body-text control ``height`` design px tall, its text ``side`` px in.

    The same formula as ``gui_theme.apply``'s ``kit_padding``: the room around the
    body line is split top/bottom, so the control keeps its 100 % height in design px.
    """
    s = ui_scale(widget)
    room = max(0, px(s, height) - body_linespace(widget))
    return (px(s, side), room // 2, px(s, side), room - room // 2)


def _inner_edge(scale, fill, outline):
    # gui_theme.apply's inner_edge: clam's second border line, mixed toward the outline above 100 %.
    amount = min(1.0, scale - 1.0)
    a, b = ui_theme.rgb(fill), ui_theme.rgb(outline)
    return "#" + "".join(f"{round(x * (1 - amount) + y * amount):02x}" for x, y in zip(a, b))


class _Builder:
    """Element and style creation for one interpreter, theme and scale."""

    def __init__(self, root):
        self.root = root
        self.style = ttk.Style(root)
        self.scale = ui_scale(root)
        self.images = []
        self.radius = max(1, round(4 * self.scale))   # kit controls: radius 4 (gui_theme's rounding)

    def image_element(self, name, image, *states, width=None, height=None, sticky="", padding=0):
        self.style.element_create(name, "image", image, *states, sticky=sticky, padding=padding,
                                  **({} if width is None else {"width": width}),
                                  **({} if height is None else {"height": height}))
        ELEMENTS[name] = ((image.width() if width is None else width,
                           image.height() if height is None else height), sticky)

    def rounded(self, name, paints, surface):
        """``name``: a native fill and four corner stamps per state ((state, fill, outline), first = rest)."""
        from ..gui_theme import rounded_image
        style, root, s, r = self.style, self.root, self.scale, self.radius
        style.element_create(name, "from", "clam", "background")
        style.element_create(name + ".fill", "from", "clam", "field")
        minimum = tk.PhotoImage(master=root, width=px(s, 16), height=px(s, 16))
        self.images.append(minimum)
        self.image_element(name + ".minimum", minimum, sticky="")
        corners = {key: [] for key in CORNERS}
        for state, fill, outline in paints:
            whole = rounded_image(root, fill, outline, r, line=s, surface=surface)
            width, height = whole.width(), whole.height()
            for key, (x, y) in (("nw", (0, 0)), ("ne", (width - r, 0)), ("sw", (0, height - r)),
                                ("se", (width - r, height - r))):
                corner = tk.PhotoImage(master=root, width=r, height=r)
                root.tk.call(str(corner), "copy", str(whole), "-from", x, y, x + r, y + r)
                self.images.append(corner)
                corners[key].append((state, corner))
        for key, variants in corners.items():
            self.image_element(name + "." + key, variants[0][1], *((st, im) for st, im in variants[1:]),
                               width=r, height=r, sticky=key)

    @staticmethod
    def decorated(base, children):
        """The layout of a rounded element around ``children`` (gui_theme's surface layout)."""
        return [(base, {"sticky": "nsew", "children": [
            (base + ".fill", {"sticky": "nsew"}),
            *((base + "." + key, {"sticky": key}) for key in CORNERS),
            (base + ".minimum", {"sticky": ""}),
            *children]})]

    def surface(self, name, paints):
        """Options and maps of the clam field fill for ``paints`` (no focus entry, defaults set)."""
        (_, fill, outline), states = paints[0], paints[1:]
        s = self.scale
        self.style.configure(name, fieldbackground=fill, bordercolor=outline,
                             lightcolor=_inner_edge(s, fill, outline), darkcolor=_inner_edge(s, fill, outline))
        self.style.map(name, fieldbackground=[(state, f) for state, f, _ in states],
                       bordercolor=[(state, o) for state, _, o in states],
                       lightcolor=[(state, _inner_edge(s, f, o)) for state, f, o in states],
                       darkcolor=[(state, _inner_edge(s, f, o)) for state, f, o in states])


def install(widget) -> None:
    """Create every ``Kit.*`` style for ``widget``'s interpreter and current theme (once)."""
    root = widget._root()
    from . import dropdown_popup
    try:
        dropdown_popup.install(root)
    except tk.TclError:
        pass  # Keep Tk's native dropdown if installation is unavailable.
    style = ttk.Style(root)
    key = (style.theme_use(), ui_scale(root))
    done = getattr(root, "_dolly_kit_styles", None)
    if done is None:
        done = root._dolly_kit_styles = set()
    if key in done:
        return
    done.add(key)
    b = _Builder(root)
    t = ui_theme.TOKENS
    s = b.scale
    panel, field, text, muted, dim = t["panel"], t["field"], t["text"], t["muted"], t["dim"]
    body = (ui_theme.FONT_FAMILY, ui_theme.FONT_SIZES["body"])
    value_face = (ui_theme.FONT_VALUE, ui_theme.FONT_SIZES["body"])   # Figma's Inter Medium: values, cells, keycaps
    control = kit_padding(root, 13)

    # Field [design reference]: fill bg/field, no stroke in any state, value muted 13 px in.
    field_paints = [(None, field, field), ("disabled", t["field_disabled"], t["field_disabled"])]
    b.rounded("Kit.field", field_paints, panel)
    entry = [("Entry.padding", {"sticky": "nsew", "children": [("Entry.textarea", {"sticky": "nsew"})]})]
    style.layout("Kit.TEntry", b.decorated("Kit.field", entry))
    style.configure("Kit.TEntry", padding=control, background=panel, foreground=muted, insertcolor=text,
                    borderwidth=0, selectbackground=t["selected"], selectforeground=text)
    style.map("Kit.TEntry", foreground=[("disabled", t["muted_disabled"])], background=[("disabled", panel)])
    b.surface("Kit.TEntry", field_paints)

    # The search field's entry: the canvas around it draws the plate (SearchField); no corners.
    style.layout("Kit.Flat.TEntry", [("Kit.field.fill", {"sticky": "nsew", "children": entry})])
    left = max(0, px(s, 26) - px(s, 6))   # its canvas starts at the sheet's left edge, a card radius in
    style.configure("Kit.Flat.TEntry", padding=(left, control[1], control[2], control[3]), background=field,
                    foreground=muted, insertcolor=text, borderwidth=0, selectbackground=t["selected"],
                    selectforeground=text)
    style.map("Kit.Flat.TEntry", foreground=[("disabled", t["muted_disabled"])],
              background=[("disabled", t["field_disabled"])])
    b.surface("Kit.Flat.TEntry", field_paints)

    # Dropdown [design reference]: the field + a chevron (8 x 4, stroke 1.4) ending 13 px from the right, 8 px after the value.
    chevron = art.dropdown_chevron_rgba(root, s, muted, surface=field)
    chevron_disabled = art.dropdown_chevron_rgba(root, s, t["muted_disabled"], surface=t["field_disabled"])
    b.images += [chevron, chevron_disabled]
    b.image_element("Kit.Combobox.downarrow", chevron, ("disabled", chevron_disabled),
                    padding=(px(s, 8) - px(s, 1), 0, 0, 0), sticky="")
    right = max(0, px(s, 13) - px(s, 1))   # the chevron image has 1 design px of room around its stroke
    combo_padding = (control[0], control[1], right, control[3])
    combo_paints = [(None, field, field), ("disabled", t["field_disabled"], t["field_disabled"]), ("pressed", field, t["accent"])]
    b.rounded("Kit.combo", combo_paints, panel)
    style.layout("Kit.TCombobox", b.decorated("Kit.combo", [("Combobox.padding", {"sticky": "nsew", "children": [
        ("Kit.Combobox.downarrow", {"side": "right", "sticky": ""}),
        ("Combobox.textarea", {"sticky": "nsew"})]})]))
    style.configure("Kit.TCombobox", padding=combo_padding, background=panel, foreground=muted, insertcolor=text,
                    arrowcolor=muted, borderwidth=0, selectbackground=field, selectforeground=muted)
    style.map("Kit.TCombobox", foreground=[("disabled", t["muted_disabled"])], background=[("disabled", panel)],
              selectbackground=[("disabled", t["field_disabled"])])
    b.surface("Kit.TCombobox", combo_paints)
    # "More", "Graphs": the dropdown's look on a menu button.
    style.layout("Kit.TMenubutton", b.decorated("Kit.field", [("Menubutton.padding", {"sticky": "nsew", "children": [
        ("Kit.Combobox.downarrow", {"side": "right", "sticky": ""}),
        ("Menubutton.label", {"sticky": "nsew"})]})]))
    style.configure("Kit.TMenubutton", padding=combo_padding, background=panel, foreground=muted, font=value_face,
                    borderwidth=0, relief="flat", width=0, anchor="w")
    style.map("Kit.TMenubutton", foreground=[("disabled", t["muted_disabled"])], background=[("disabled", panel)])
    b.surface("Kit.TMenubutton", field_paints)
    root.option_add("*TCombobox*Listbox.background", field)
    root.option_add("*TCombobox*Listbox.foreground", text)
    root.option_add("*TCombobox*Listbox.selectBackground", t["selected"])
    root.option_add("*TCombobox*Listbox.selectForeground", text)

    # Checkbox [design reference]: a 12 px box (radius 4) 5 px before the label; off field + 1 px edge, on accent + tick.
    _checkbox(b, panel)
    for name, colour, disabled in (("Kit.TCheckbutton", text, t["text_disabled"]),
                                   ("Kit.Muted.TCheckbutton", muted, t["muted_disabled"])):
        style.layout(name, [("Checkbutton.padding", {"sticky": "nsew", "children": [
            ("Kit.Checkbutton.indicator", {"side": "left", "sticky": "w"}),
            ("Checkbutton.label", {"sticky": "nsew"})]})])
        style.configure(name, background=panel, foreground=colour, font=body, padding=0, borderwidth=0)
        style.map(name, foreground=[("disabled", disabled)], background=[("disabled", panel)])

    # Tab [design reference]: selected fill button + label text; unselected no fill, label muted (text on hover/press).
    b.rounded("Kit.tab", [(None, t["button"], t["button"])], panel)
    label = [("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]})]
    style.layout("Kit.Tab.Selected.TButton", b.decorated("Kit.tab", label))
    b.surface("Kit.Tab.Selected.TButton", [(None, t["button"], t["button"])])
    style.layout("Kit.Tab.TButton", label)
    for name, colour in (("Kit.Tab.TButton", muted), ("Kit.Tab.Selected.TButton", text)):
        style.configure(name, padding=control, background=panel, foreground=colour, font=body, borderwidth=0,
                        relief="flat", width=0, anchor="center")
        style.map(name, background=[("disabled", panel)],
                  foreground=[("disabled", dim)] + ([("pressed", text), ("active", text)]
                                                    if name == "Kit.Tab.TButton" else []))

    # Keycap [design reference]: fill field, label centred (Empty: dim); ``alternate`` = Listening, a 1 px accent edge.
    keycap_paints = [(None, field, field), ("alternate", field, t["accent"])]
    b.rounded("Kit.keycap", keycap_paints, panel)
    for name, colour in (("Kit.Keycap.TButton", text), ("Kit.KeycapEmpty.TButton", dim)):
        style.layout(name, b.decorated("Kit.keycap", label))
        style.configure(name, padding=(0, control[1], 0, control[3]), background=panel, foreground=colour,
                        font=value_face, borderwidth=0, relief="flat", width=0, anchor="center")
        style.map(name, foreground=[("disabled", dim)], background=[("disabled", panel)])
        b.surface(name, keycap_paints)

    # Tables: an alias of the Replay table (Library.Treeview) when the shell theme made it
    # (its one-row divider image is the shell's; ttk would answer a missing layout with Treeview's).
    aliased = "Dolly.tree.row" in style.element_names()
    if aliased:
        style.layout("Kit.Treeview", style.layout("Library.Treeview"))
        style.layout("Kit.Treeview.Row", style.layout("Library.Treeview.Row"))
    options = ("rowheight", "font", "foreground", "background", "fieldbackground", "borderwidth", "relief",
               "bordercolor", "lightcolor", "darkcolor")
    if aliased:
        for source, target in (("Library.Treeview", "Kit.Treeview"), ("Library.Treeview.Heading", "Kit.Treeview.Heading")):
            values = {name: style.lookup(source, name) for name in options + ("padding",)}
            style.configure(target, **{name: value for name, value in values.items() if value != ""})
        style.map("Kit.Treeview", background=[("selected", panel)], foreground=[("selected", text)])
        style.map("Kit.Treeview.Heading", background=[("active", panel)])
    else:
        style.configure("Kit.Treeview", rowheight=px(s, 36), font=value_face, foreground=muted, background=panel,
                        fieldbackground=panel, borderwidth=0)
        style.map("Kit.Treeview", background=[("selected", panel)], foreground=[("selected", text)])
        style.configure("Kit.Treeview.Heading", background=panel, foreground=dim, font=body, relief="flat",
                        borderwidth=0)
        style.map("Kit.Treeview.Heading", background=[("active", panel)])

    # Labels drawn over fields (placeholders, elided values); the shell theme defines them the same way.
    bare = [("Label.padding", {"sticky": "nsew", "children": [("Label.label", {"sticky": "nsew"})]})]
    for name, colour, face in (("Placeholder.TLabel", dim, body), ("FieldValue.TLabel", muted, value_face)):
        if not style.lookup(name, "background"):
            style.configure(name, background=field, foreground=colour, font=face)
            style.layout(name, bare)
    images = getattr(root, "_dolly_kit_images", None)
    if images is None:
        images = root._dolly_kit_images = []
    images.extend(b.images)


def _checkbox(b, panel):
    from ..gui_theme import rounded_image
    t = ui_theme.TOKENS
    root, s = b.root, b.scale
    size, radius = round(12 * s), round(4 * s)
    tick = ((3 * s, 6 * s), (5 * s, 8 * s), (9 * s, 4 * s))   # M0 2 L2 4 L6 0 at (+3, +4)

    def box(fill, edge, mark=None):
        image = rounded_image(root, fill, edge, radius, size=size, line=s, surface=panel)
        if mark is not None:
            stroke = art.stroke_rgba(root, size, size, (tick,), s, mark)
            image.tk.call(str(image), "copy", str(stroke), "-compositingrule", "overlay")
        b.images.append(image)
        return image
    off = box(t["field"], t["edge"])
    on = box(t["accent"], t["accent"], t["text"])
    off_disabled = box(t["field_disabled"], t["edge_disabled"])
    on_disabled = box(t["accent_disabled"], t["accent_disabled"], t["text_disabled"])
    b.image_element("Kit.Checkbutton.indicator", off, ("disabled selected", on_disabled),
                    ("disabled", off_disabled), ("selected", on), width=size + px(s, 5), height=size, sticky="w")
