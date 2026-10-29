"""Wireframe-style ttk surfaces, preserving the native widgets and bindings."""
import base64
import math
import struct
import tkinter as tk
from tkinter import ttk
import zlib

from . import ui_theme


def initialize(root):
    """Initialize Dolly's current desktop theme in its established order.

    Named display fonts come first so styles can use them; base ttk styles
    precede selection handling, rounded assets and wheel guards.
    Call once per Tk root, before constructing the desktop views.
    """
    from .ui import fonts
    from .ui.scale import px, ui_scale
    scale = ui_scale(root)
    fonts.install(root, scale)
    # Paddings and sizes below are design px; fonts are points, which Tk scales itself.
    dp = lambda value: px(scale, value)  # noqa: E731
    _F = ui_theme.FONT_SIZES
    FAMILY = ui_theme.FONT_FAMILY
    BG = ui_theme.TOKENS["bg"]
    PANEL = ui_theme.TOKENS["panel"]
    FIELD = ui_theme.TOKENS["field"]
    EDGE = ui_theme.TOKENS["edge"]
    TEXT = ui_theme.TOKENS["text"]
    MUTED = ui_theme.TOKENS["muted"]
    DIM = ui_theme.TOKENS["dim"]
    ACCENT = ui_theme.TOKENS["accent"]
    ACCENT_HOVER = ui_theme.TOKENS["accent_hover"]
    ACCENT_INK = ui_theme.TOKENS["accent_ink"]
    BUTTON = ui_theme.TOKENS["button"]
    BUTTON_HOVER = ui_theme.TOKENS["button_hover"]
    DISABLED_BG = ui_theme.TOKENS["disabled_bg"]
    DISABLED_FG = ui_theme.TOKENS["disabled_fg"]
    SELECTED = ui_theme.TOKENS["selected"]
    TRACK = ui_theme.TOKENS["track"]
    WARN = ui_theme.TOKENS["warn"]
    WARN_BG = ui_theme.TOKENS["warn_bg"]
    root.configure(bg=BG)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=TEXT, font=(FAMILY, _F["body"]))
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=PANEL)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Card.TLabel", background=PANEL)
    style.configure("CardMuted.TLabel", background=PANEL, foreground=MUTED)
    style.configure("Title.TLabel", font=(FAMILY, _F["title"], "bold"))
    style.configure("Section.TLabel", font=(FAMILY, _F["section"], "bold"), foreground=MUTED)
    style.configure("CardTitle.TLabel", background=PANEL, foreground=TEXT,
                    font=fonts.NAMES["card_title"])
    # Accent blue is a fill colour; as text on the dark surfaces it is too dim.
    style.configure("Accent.TLabel", foreground=TEXT)
    style.configure("Placeholder.TLabel", background=FIELD, foreground=DIM, font=(FAMILY, _F["body"]))
    style.configure("Pill.TLabel", background=SELECTED, foreground=TEXT,
                    font=(FAMILY, _F["small"], "bold"), padding=dp((9, 3)))
    style.configure("PillConsole.TLabel", background=WARN_BG, foreground=WARN,
                    font=(FAMILY, _F["small"], "bold"), padding=dp((9, 3)))
    style.configure("TButton", background=BUTTON, foreground=TEXT, padding=dp((10, 6)), borderwidth=0)
    style.map("TButton", background=[("active", BUTTON_HOVER), ("disabled", DISABLED_BG)],
              foreground=[("disabled", DISABLED_FG)])
    style.configure("Primary.TButton", background=ACCENT, foreground=ACCENT_INK, font=(FAMILY, _F["body"]))
    style.map("Primary.TButton", background=[("active", ACCENT_HOVER), ("disabled", DISABLED_BG)],
              foreground=[("disabled", DISABLED_FG)])
    style.configure("Quiet.TButton", background=PANEL, padding=dp((8, 5)))
    style.configure("TMenubutton", background=PANEL, foreground=MUTED,
                    arrowcolor=MUTED, padding=dp((5, 2)), borderwidth=0)
    style.configure("TEntry", fieldbackground=FIELD, foreground=TEXT, insertcolor=TEXT,
                    bordercolor=EDGE, lightcolor=EDGE, darkcolor=EDGE, padding=dp(5))
    style.configure("TCombobox", fieldbackground=FIELD, background=BUTTON, foreground=TEXT,
                    arrowcolor=MUTED, borderwidth=0, padding=dp(4))
    style.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", TEXT)])
    style.configure("TCheckbutton", background=BG, foreground=MUTED, padding=0)
    style.map("TCheckbutton", background=[("active", BG)], foreground=[("active", TEXT)])
    style.configure("TNotebook", background=BG, borderwidth=0, bordercolor=BG, lightcolor=BG, darkcolor=BG, tabmargins=dp((0, 0, 0, 6)))
    style.configure("TNotebook.Tab", background=BG, foreground=MUTED, padding=dp((17, 9)), borderwidth=0, bordercolor=BG, lightcolor=BG, darkcolor=BG)
    style.map("TNotebook.Tab", background=[("selected", PANEL)], foreground=[("selected", ACCENT)])
    style.layout("TNotebook.Tab", [("Notebook.padding", {"sticky": "nswe", "children": [("Notebook.label", {"sticky": "nswe"})]})])
    # The sidebar is the visible navigation: the page notebook adds no frame.
    style.configure("Navigation.TNotebook", background=BG, tabmargins=(0, 0, 0, 0), padding=0, borderwidth=0)
    style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=MUTED,
                    rowheight=dp(31), borderwidth=0, lightcolor=PANEL, darkcolor=PANEL)
    style.configure("Treeview.Heading", background=PANEL, foreground=DIM, bordercolor=PANEL, lightcolor=PANEL, darkcolor=PANEL, padding=dp((8, 7)),
                    relief="flat", borderwidth=0, font=(FAMILY, _F["small"]))
    style.map("Treeview.Heading", background=[("active", PANEL)])
    style.map("Treeview", background=[("selected", SELECTED)], foreground=[("selected", TEXT)])
    style.configure("Vertical.TScrollbar", background=EDGE, troughcolor=PANEL, arrowcolor=MUTED,
                    borderwidth=0, arrowsize=dp(12), relief="flat")
    style.configure("Horizontal.TScale", background=BG, troughcolor=TRACK, sliderlength=dp(16),
                    sliderthickness=dp(14), borderwidth=0, lightcolor=ACCENT, darkcolor=ACCENT)
    style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=TRACK, bordercolor=PANEL,
                    lightcolor=ACCENT, darkcolor=ACCENT, thickness=dp(5), borderwidth=0)
    style.configure("TLabelframe", background=BG, bordercolor=EDGE)
    style.configure("TLabelframe.Label", foreground=MUTED)
    root.option_add("*TCombobox*Listbox.background", FIELD)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", SELECTED)
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
    root.bind_class("TCombobox", "<<ComboboxSelected>>",
                         _clear_committed_combobox_selection, add="+")

    apply(root)
    apply_display_fonts(root)
    if not getattr(root, "_dolly_display_fonts_hooked", False):
        fonts.on_change(root, lambda: apply_display_fonts(root))
        root._dolly_display_fonts_hooked = True
    install_wheel_guard(root)


def apply_display_fonts(root):
    """Re-measure the room display faces need below their reported descent.

    The VALVE faces report a shallow descent, so a label would crop "p" and
    "y". Runs at initialization and again whenever :mod:`dolly.ui.fonts`
    reconfigures the named fonts (for example after a late private load).
    """
    from .ui import fonts
    style = ttk.Style(root)
    style.configure("CardTitle.TLabel", font=str(fonts.font(root, "card_title")),
                    padding=(0, 0, 0, fonts.descent_shortfall(root, "card_title")))


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


def _rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def _photo(root, width, height, pixel, *, surface=None):
    """Small RGBA assets with smooth edges, generated without a runtime library."""
    raw = bytearray()
    backdrop = _rgb(surface) if surface is not None else None
    for y in range(height):
        raw.append(0)
        for x in range(width):
            color = pixel(x + .5, y + .5)
            if backdrop is not None:
                alpha = color[3]
                color = (*((value * alpha + parent * (255 - alpha) + 127) // 255
                           for value, parent in zip(color[:3], backdrop)), 255)
            raw.extend(color)
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", width, height, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return tk.PhotoImage(master=root, data=base64.b64encode(png), format="png")


def _stretched(root, image, length, vertical, border):
    """``image`` made ``length`` px long along one axis by repeating its centre.

    The first and last ``border`` px (the ttk element's border) are kept as they
    are and the rows (or columns) between them are tiled, so an image element
    draws the same pixels with far fewer tiles. Returns ``image`` when it is
    already long enough.
    """
    width, height = image.width(), image.height()
    extent = height if vertical else width
    if extent >= length:
        return image
    out = tk.PhotoImage(master=root, width=width if vertical else length, height=length if vertical else height)
    # (source span, target span) along the axis: the head stays, the centre is
    # tiled over the new middle (Tk repeats a source smaller than its target),
    # the tail moves to the new end.
    spans = (((0, border), (0, border)),
             ((border, extent - border), (border, length - border)),
             ((extent - border, extent), (length - border, length)))
    for (a, b), (c, d) in spans:
        if b <= a:
            continue
        source, target = ((0, a, width, b), (0, c, width, d)) if vertical else ((a, 0, b, height), (c, 0, d, height))
        root.tk.call(str(out), "copy", str(image), "-from", *source, "-to", *target)
    return out


def _coverage(distance):
    return max(0.0, min(1.0, .5 - distance))


def _segment_distance(x, y, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = max(0.0, min(1.0, ((x - a[0]) * dx + (y - a[1]) * dy) / (dx * dx + dy * dy)))
    return math.hypot(x - a[0] - t * dx, y - a[1] - t * dy)


def rounded_image(root, fill, outline, radius=6, *, size=64, line=1, checked=None, surface=None):
    fill, outline = _rgb(fill), _rgb(outline)
    tick = _rgb(checked) if checked else None
    width, height = (size, size) if isinstance(size, int) else size
    def pixel(x, y):
        qx, qy = abs(x - width / 2) - (width / 2 - radius), abs(y - height / 2) - (height / 2 - radius)
        distance = math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - radius
        alpha = _coverage(distance)
        if not alpha:
            return 0, 0, 0, 0
        inside = _coverage(distance + line)
        color = tuple(round((a * inside + b * (alpha - inside)) / alpha) for a, b in zip(fill, outline))
        if tick:
            a, b, c = (size * .23, size * .5), (size * .43, size * .7), (size * .77, size * .3)
            ink = _coverage(min(_segment_distance(x, y, a, b), _segment_distance(x, y, b, c)) - size * .055)
            color = tuple(round(a * (1 - ink) + b * ink) for a, b in zip(color, tick))
        return *color, round(255 * alpha)
    return _photo(root, width, height, pixel, surface=surface)


def apply(root):
    from .ui.scale import chars, hairline, px, ui_scale
    from .ui.stage_page import TREE_CELL_INSET, StagePage
    style = ttk.Style(root)
    images = []
    scale = ui_scale(root)
    dp = lambda value: px(scale, value)  # noqa: E731
    t = ui_theme.TOKENS
    bg, panel, text, muted, accent = t["bg"], t["panel"], t["text"], t["muted"], t["accent"]
    edge, field = t["edge"], t["field"]
    # Kit radii: controls 4, cards 6 (Figma "Dolly Blue" component kit).
    control_radius, card_radius = round(4 * scale), round(6 * scale)
    surfaces = {}
    minimum_images = {}
    def rounded(name, fill, outline, radius=control_radius, min_width=16, surface=bg, **states):
        min_width, min_height = dp(min_width), dp(16)
        paints = [(None, fill, outline), *((state, *colors) for state, colors in states.items())]
        if scale > 2:
            # Clam's field has two border pixels. Preserve the original precise
            # stroke above 200% until an equivalent native primitive is proven.
            variants = [rounded_image(root, color, edge, radius, line=scale, surface=surface)
                        for _, color, edge in paints]
            images.extend(variants)
            style.element_create(name, "image", variants[0],
                                 *((state, image) for (state, _, _), image in zip(paints[1:], variants[1:])),
                                 border=radius, padding=0, width=min_width, height=min_height, sticky="nsew")
            return
        # A native fill paints the large center once. Image elements only stamp
        # the four small antialiased corners; no nine-slice center is tiled.
        style.element_create(name, "from", "clam", "background")
        style.element_create(name + ".fill", "from", "clam", "field")
        minimum = minimum_images.get((min_width, min_height))
        if minimum is None:
            minimum = tk.PhotoImage(master=root, width=min_width, height=min_height)
            minimum_images[min_width, min_height] = minimum
            images.append(minimum)
        style.element_create(name + ".minimum", "image", minimum,
                             width=min_width, height=min_height, sticky="")
        corners = {key: [] for key in ("nw", "ne", "sw", "se")}
        for state, color, edge in paints:
            original = rounded_image(root, color, edge, radius, line=scale, surface=surface)
            width, height = original.width(), original.height()
            for key, (x, y) in {"nw": (0, 0), "ne": (width-radius, 0),
                                "sw": (0, height-radius), "se": (width-radius, height-radius)}.items():
                corner = tk.PhotoImage(master=root, width=radius, height=radius)
                root.tk.call(str(corner), "copy", str(original), "-from", x, y, x+radius, y+radius)
                images.append(corner)
                corners[key].append((state, corner))
        for key, variants in corners.items():
            style.element_create(name + "." + key, "image", variants[0][1],
                                 *((state, image) for state, image in variants[1:]),
                                 width=radius, height=radius, sticky=key)
        surfaces[name] = paints
    rounded("Dolly.card", panel, edge, card_radius)
    rounded("Dolly.card.notice", panel, edge, card_radius, surface=t["selected"])
    style.layout("Rounded.Card.TFrame", [("Dolly.card", {"sticky": "nsew"})])
    style.configure("Rounded.Card.TFrame", background=bg)
    style.layout("UpdateNotice.Notes.TFrame", [("Dolly.card.notice", {"sticky": "nsew"})])
    style.configure("UpdateNotice.Notes.TFrame", background=t["selected"])
    # Large image elements repaint often. Precompose their antialiased edges
    # onto the exact parent surface so Tk can use its opaque image path.
    # Card variants are separate: page-coloured corners would leave halos.
    # Kit buttons have no stroke; only keyboard focus draws an outline.
    for suffix, surface in (("", bg), (".card", panel), (".notice", t["selected"])):
        rounded("Dolly.button" + suffix, t["button"], t["button"], surface=surface,
                disabled=(t["disabled_bg"], t["disabled_bg"]), pressed=(t["button_active"], t["button_active"]),
                focus=(t["button"], accent), active=(t["button_hover"], t["button_hover"]))
        rounded("Dolly.primary" + suffix, accent, accent, surface=surface,
                disabled=(t["disabled_bg"], t["disabled_bg"]), focus=(accent, text),
                pressed=(t["accent_active"], t["accent_active"]), active=(t["accent_hover"], t["accent_hover"]))
    def button_layout(element):
        return [(element, {"sticky": "nsew", "children": [("Button.padding", {
            "sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]})]})]
    style.layout("TButton", button_layout("Dolly.button"))
    style.layout("Primary.TButton", button_layout("Dolly.primary"))
    style.layout("Card.TButton", button_layout("Dolly.button.card"))
    style.layout("Card.Quiet.TButton", button_layout("Dolly.button.card"))
    style.layout("Card.Primary.TButton", button_layout("Dolly.primary.card"))
    style.layout("UpdateNotice.TButton", button_layout("Dolly.button.notice"))
    style.layout("UpdateNotice.Primary.TButton", button_layout("Dolly.primary.notice"))
    style.configure("TButton", padding=dp((10, 5)))
    style.configure("Quiet.TButton", padding=dp((10, 5)))
    # Curve axes are local selectors: keep inactive channels quiet and give the
    # active channel the same filled, outlined state as the in-game editor.
    rounded("Dolly.curve.axis", panel, panel, surface=panel,
            disabled=(panel, panel), focus=(panel, accent), active=(t["button"], t["edge"]))
    rounded("Dolly.curve.axis.selected", t["selected"], t["selected_edge"], surface=panel,
            disabled=(t["disabled_bg"], t["edge"]), focus=(t["selected"], accent),
            active=(t["selected"], t["selected_edge"]))
    for name, element in (("Curve.Axis.TButton", "Dolly.curve.axis"),
                          ("Curve.Selected.Axis.TButton", "Dolly.curve.axis.selected")):
        style.layout(name, button_layout(element))
        style.configure(name, padding=dp((10, 3)), background=panel,
                        foreground=t["selected_ink"] if "Selected" in name else text)
        style.map(name, background=[("active", panel), ("disabled", panel)],
                  foreground=[("disabled", t["disabled_fg"])])
    for name, surface in (("TButton", bg), ("Primary.TButton", bg), ("Quiet.TButton", bg),
                          ("Card.TButton", panel), ("Card.Primary.TButton", panel), ("Card.Quiet.TButton", panel)):
        style.configure(name, background=surface)
        style.map(name, background=[("active", surface), ("disabled", surface)])
    rounded("Dolly.nav", bg, bg, focus=(bg, accent), active=(panel, edge))
    rounded("Dolly.nav.selected", bg, accent, focus=(panel, accent))
    rounded("Dolly.nav.current", bg, bg, focus=(panel, panel), active=(panel, panel))
    style.layout("Nav.TButton", button_layout("Dolly.nav"))
    style.layout("Selected.Nav.TButton", button_layout("Dolly.nav.selected"))
    # The sidebar draws the selection outline. Focus on that selected button
    # adds a quiet fill, rather than a second outline inside the first.
    style.layout("Current.Nav.TButton", button_layout("Dolly.nav.current"))
    style.configure("Nav.TButton", anchor="w", padding=dp((12, 8)), background=bg)
    style.configure("Selected.Nav.TButton", foreground=accent)
    style.configure("Current.Nav.TButton", foreground=accent)
    for name in ("Nav.TButton", "Selected.Nav.TButton", "Current.Nav.TButton"):
        style.map(name, background=[("active", bg)], foreground=[("active", accent)])
    for label, surface in (("Disclosure.TButton", bg), ("Disclosure.Card.TButton", panel)):
        style.layout(label, [("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "w"})]})])
        style.configure(label, background=surface, foreground=text, padding=dp((0, 5)), borderwidth=0)
        style.map(label, background=[("active", surface)], foreground=[("active", accent)])

    # Kit fields have no stroke at rest; hover shows the quiet edge, focus the accent.
    for suffix, surface in (("", bg), (".card", panel)):
        rounded("Dolly.field" + suffix, field, field, surface=surface,
                disabled=(t["disabled_bg"], t["disabled_bg"]), focus=(field, accent), active=(field, edge))
    def entry_layout(element):
        return [(element, {"sticky": "nsew", "children": [
            ("Entry.padding", {"sticky": "nsew", "children": [("Entry.textarea", {"sticky": "nsew"})]})]})]
    style.layout("TEntry", entry_layout("Dolly.field"))
    style.layout("Card.TEntry", entry_layout("Dolly.field.card"))
    style.layout("Numeric.TEntry", entry_layout("Dolly.field.card"))
    # A 14 px chevron (stroke 1.4) centred in a 20 px element, drawn at the display scale.
    def arrow_pixel(x, y):
        ink = _coverage(min(_segment_distance(x, y, (3 * scale, 5 * scale), (7 * scale, 9 * scale)),
                            _segment_distance(x, y, (7 * scale, 9 * scale), (11 * scale, 5 * scale))) - .7 * scale)
        return *_rgb(muted), round(255 * ink)
    arrow = _photo(root, dp(14), dp(14), arrow_pixel)
    images.append(arrow)
    style.element_create("Dolly.Combobox.downarrow", "image", arrow, width=dp(20), sticky="")
    def combo_layout(element):
        return [(element, {"sticky": "nsew", "children": [
            ("Combobox.padding", {"sticky": "nsew", "children": [
                ("Dolly.Combobox.downarrow", {"side": "right", "sticky": "ns"}),
                ("Combobox.textarea", {"sticky": "nsew"})]})]})]
    def menu_layout(element):
        return [(element, {"sticky": "nsew", "children": [
            ("Menubutton.padding", {"sticky": "nsew", "children": [
                ("Dolly.Combobox.downarrow", {"side": "right", "sticky": "ns"}),
                ("Menubutton.label", {"sticky": "nsew"})]})]})]
    for prefix, suffix, surface in (("", "", bg), ("Card.", ".card", panel)):
        style.layout(prefix + "TCombobox", combo_layout("Dolly.field" + suffix))
        rounded("Dolly.menu" + suffix, t["button"], t["button"], surface=surface,
                disabled=(t["disabled_bg"], t["disabled_bg"]), focus=(t["button"], accent),
                pressed=(t["button_active"], t["button_active"]), active=(t["button_hover"], t["button_hover"]))
        style.layout(prefix + "TMenubutton", menu_layout("Dolly.menu" + suffix))
    # Image corners are transparent. The widget rectangle behind them must
    # stay on its parent surface even when clam marks a dropdown active or
    # readonly; its inherited pale background otherwise creates square boxes.
    for prefix, surface in (("", bg), ("Card.", panel)):
        for control in ("TEntry", "TCombobox", "TMenubutton"):
            style.configure(prefix + control, background=surface)
            style.map(prefix + control, background=[("disabled", surface), ("!disabled", surface)])
    style.configure("TMenubutton", borderwidth=0, relief="flat")
    style.map("TMenubutton", foreground=[("disabled", t["disabled_fg"])])
    # Kit field values are muted; the typing cursor stays bright.
    for name in ("TEntry", "TCombobox"):
        style.configure(name, fieldbackground=field, background=bg, foreground=muted, insertcolor=text,
                        borderwidth=0, padding=dp((10, 5)))
        style.map(name, fieldbackground=[("disabled", t["disabled_bg"]), ("readonly", field)],
                  foreground=[("disabled", t["disabled_fg"]), ("readonly", muted)])
        style.configure("Card." + name, background=panel)

    style.configure("Numeric.TEntry", padding=dp((4, 1)), background=panel)
    style.map("Numeric.TEntry", background=[("disabled", panel), ("!disabled", panel)])

    # Kit checkbox: a 12 px box, radius 4, 5 px before the label.
    size, box_radius = round(12 * scale), round(4 * scale)
    indicator = rounded_image(root, field, edge, box_radius, size=size, line=scale)
    checked = rounded_image(root, accent, accent, box_radius, size=size, line=scale, checked=text)
    hover = rounded_image(root, field, accent, box_radius, size=size, line=scale)
    disabled = rounded_image(root, t["disabled_bg"], t["edge"], box_radius, size=size, line=scale)
    disabled_checked = rounded_image(root, t["accent_active"], t["accent_active"], box_radius, size=size, line=scale,
                                     checked=t["disabled_fg"])
    images.extend((indicator, checked, hover, disabled, disabled_checked))
    focused_checked = rounded_image(root, accent, text, box_radius, size=size, line=scale, checked=text)
    images.append(focused_checked)
    style.element_create("Dolly.Checkbutton.indicator", "image", indicator,
                         ("disabled selected", disabled_checked), ("disabled", disabled),
                         ("focus selected", focused_checked), ("focus", hover),
                         ("selected", checked), ("active", hover), sticky="w", width=size + round(5 * scale))
    style.layout("TCheckbutton", [("Checkbutton.padding", {"sticky": "nsew", "children": [
        ("Dolly.Checkbutton.indicator", {"side": "left", "sticky": "w"}),
        ("Checkbutton.label", {"sticky": "nsew"})]})])
    style.configure("TCheckbutton", foreground=text)
    style.configure("Card.TCheckbutton", background=panel)
    style.map("Card.TCheckbutton", background=[("active", panel)])

    style.layout("FullEditor.TCheckbutton", [("Dolly.nav.selected", {"sticky": "nsew", "children":
        style.layout("TCheckbutton")})])
    style.configure("FullEditor.TCheckbutton", padding=dp((12, 9)), background=bg)

    def match_surface(event):
        widget = event.widget
        # Tk creates combobox popdown scrollbars in Tcl rather than through
        # tkinter; those Map events carry a path, not a Python widget.
        if not isinstance(widget, ttk.Widget):
            return
        name = str(widget.cget("style")) or widget.winfo_class()
        if name in ("TScrollbar", "TScale"):
            name = ("Vertical" if str(widget.cget("orient")) == "vertical" else "Horizontal") + "." + name
        if name not in ("TEntry", "TCombobox", "TMenubutton", "TButton", "Primary.TButton", "Quiet.TButton", "TCheckbutton",
                        "Vertical.TScrollbar", "Horizontal.TScrollbar", "Vertical.TScale", "Horizontal.TScale"):
            return
        parent = widget.master
        while parent is not None:
            if isinstance(parent, ttk.Widget):
                parent_style = str(parent.cget("style")) or parent.winfo_class()
                if "Card." in parent_style or style.lookup(parent_style, "background") == panel:
                    widget.configure(style="Card." + name)
                    return
                if style.lookup(parent_style, "background") == bg:
                    return
            parent = getattr(parent, "master", None)
    for cls in ("TEntry", "TCombobox", "TMenubutton", "TButton", "TCheckbutton", "TScrollbar", "TScale"):
        root.bind_class(cls, "<Map>", match_surface, add="+")

    # Keep the native *thumb/*trough names: ttk uses them to distinguish
    # dragging from page clicks. Matching the trough to its parent avoids an
    # inset square rail, while opaque assets avoid repeated alpha blending.
    thickness, minimum = round(10 * scale), round(16 * scale)
    radius = max(1, round(4 * scale))
    for direction in ("Vertical", "Horizontal"):
        vertical = direction == "Vertical"
        # ttk tiles its image centers, one image draw per tile, and a page's
        # rail redraws on every scroll step. The strips are drawn at 64 px and
        # then stretched along the scrolling axis to the screen's length
        # (their centre repeated, so every drawn pixel stays the same): one
        # tile per rail. A 64 px strip cost ~30 tiles, ~2.5 ms per redraw at
        # 150 %. The cross-axis thickness stays exact; a square asset would
        # widen the layout.
        strip_size = (thickness, 64) if vertical else (64, thickness)
        size = (thickness, max(64, minimum)) if vertical else (max(64, minimum), thickness)
        length = root.winfo_screenheight() if vertical else root.winfo_screenwidth()
        for prefix, surface in (("", bg), ("Card.", panel)):
            rail = _stretched(root, _photo(root, *strip_size, lambda x, y: (0, 0, 0, 0), surface=surface),
                              length, vertical, 0)
            thumbs = [_stretched(root, rounded_image(root, color, color, radius, size=size, line=0, surface=surface),
                                 length, vertical, radius)
                      for color in (edge, t["scroll_hover"], accent, t["disabled_bg"])]
            images.extend((rail, *thumbs))
            trough = "Dolly." + prefix + direction + ".Scrollbar.trough"
            thumb = "Dolly." + prefix + direction + ".Scrollbar.thumb"
            style.element_create(trough, "image", rail, width=1, height=1, sticky="nsew")
            style.element_create(thumb, "image", thumbs[0], ("disabled", thumbs[3]),
                                 ("pressed", thumbs[2]), ("active", thumbs[1]), ("focus", thumbs[2]),
                                 width=thickness if vertical else minimum,
                                 height=minimum if vertical else thickness,
                                 border=(0, radius) if vertical else (radius, 0), sticky="nsew")
            style.layout(prefix + direction + ".TScrollbar", [(trough, {"sticky": "nsew", "children": [
                (thumb, {"sticky": "nsew", "expand": "1"})]})])
            name = prefix + direction + ".TScrollbar"
            style.configure(name, background=surface, troughcolor=surface, bordercolor=surface,
                            lightcolor=surface, darkcolor=surface, borderwidth=0, arrowsize=thickness,
                            width=thickness)
            style.map(name, background=[("disabled", surface), ("!disabled", surface)])

    # A scale remains a native ttk Scale: its own *slider/*trough targets keep
    # click increments, dragging, keyboard navigation and value callbacks.
    # Center a thin rail in the handle's hit area instead of painting a box.
    track_width, handle_width, handle_height = round(4 * scale), round(12 * scale), round(18 * scale)
    for direction in ("Horizontal", "Vertical"):
        horizontal = direction == "Horizontal"
        track_size = (64, track_width) if horizontal else (track_width, 64)
        handle_size = (handle_width, handle_height) if horizontal else (handle_height, handle_width)
        handles = [rounded_image(root, fill, outline, round(5 * scale), size=handle_size, line=scale)
                   for fill, outline in ((accent, accent), (t["accent_hover"], accent),
                                         (accent, t["selected_ink"]), (t["disabled_fg"], t["edge"]))]
        images.extend(handles)
        slider = "Dolly." + direction + ".Scale.slider"
        style.element_create(slider, "image", handles[0], ("disabled", handles[3]),
                             ("focus", handles[2]), ("pressed", handles[1]), ("active", handles[1]), sticky="")
        for prefix, surface in (("", bg), ("Card.", panel)):
            track = rounded_image(root, t["track"], t["track"], max(1, track_width // 2),
                                  size=track_size, line=0, surface=surface)
            images.append(track)
            trough = "Dolly." + prefix + direction + ".Scale.trough"
            style.element_create(trough, "image", track,
                                 border=(track_width // 2, 0) if horizontal else (0, track_width // 2),
                                 width=1 if horizontal else track_width,
                                 height=track_width if horizontal else 1,
                                 sticky="ew" if horizontal else "ns")
            style.layout(prefix + direction + ".TScale", [(trough, {"sticky": "nsew", "children": [
                (slider, {"side": "left" if horizontal else "top", "sticky": ""})]})])
            name = prefix + direction + ".TScale"
            style.configure(name, background=surface, borderwidth=0, relief="flat")
            style.map(name, background=[("disabled", surface), ("!disabled", surface)])
    style.configure("Treeview", borderwidth=0, relief="flat", bordercolor=panel, lightcolor=panel, darkcolor=panel)

    # Replay library table: no row fill, the current replay in primary text,
    # and a quiet divider along the top of every row. The table has no field
    # border, so its first column starts at the widget's edge.
    style.element_create("Dolly.tree.field", "from", "clam", "background")
    style.layout("Library.Treeview", [("Dolly.tree.field", {"sticky": "nsew", "children": [
        ("Treeview.padding", {"sticky": "nsew", "children": [("Treeview.treearea", {"sticky": "nsew"})]})]})])
    # ttk tiles an image element's centre, so the divider image is a whole row
    # as wide as the widest card: one tile per row. (A 4 px tile cost thousands
    # of image draws per row: a wheel step over the replay list took ~1.2 s at
    # 150 %, now ~8 ms.)
    line, row_width, row_height = hairline(scale), px(scale, StagePage.CARD_MAX), px(scale, 36)
    row = tk.PhotoImage(master=root, width=row_width, height=row_height)
    row.put(panel, to=(0, 0, row_width, row_height))
    row.put(t["card_edge"], to=(0, 0, row_width, line))
    images.append(row)
    style.element_create("Dolly.tree.row", "image", row, ("selected", row),
                         border=(0, line, 0, 0), sticky="nsew")
    style.layout("Library.Treeview.Row", [("Dolly.tree.row", {"sticky": "nsew"})])
    # Cells are values: Inter Medium in Figma (ui_theme.FONT_VALUE), headings the text face.
    style.configure("Library.Treeview", rowheight=row_height, font=(ui_theme.FONT_VALUE, ui_theme.FONT_SIZES["body"]),
                    foreground=muted, background=panel, fieldbackground=panel)
    style.map("Library.Treeview", background=[("selected", panel)], foreground=[("selected", text)])
    # Headings: the text on the 16 px line at the table's top, 10 px above the
    # first divider, and indented like Tk 8.6's cells (a fixed 4 px).
    body_line = int(root.tk.call("font", "metrics", (ui_theme.FONT_FAMILY, ui_theme.FONT_SIZES["body"]), "-linespace"))
    style.configure("Library.Treeview.Heading", background=panel, foreground=t["dim"],
                    padding=(TREE_CELL_INSET, 0, 0, max(0, px(scale, 26) - body_line)),
                    relief="flat", borderwidth=0, bordercolor=panel, lightcolor=panel, darkcolor=panel,
                    font=(ui_theme.FONT_FAMILY, ui_theme.FONT_SIZES["body"]))
    style.map("Library.Treeview.Heading", background=[("active", panel)])

    def kit_padding(side, height=30):
        """Padding that makes a body-text control ``height`` design px tall, its text ``side`` px in."""
        room = max(0, px(scale, height) - body_line)
        return (px(scale, side), room // 2, px(scale, side), room - room // 2)

    # Every control keeps its 100 % height in design px at any display scale. A
    # font's line does not grow exactly with the scale (Segoe UI 9 pt is 15 px
    # tall at 96 DPI, 25 px at 144), so a fixed text padding would make each row
    # of a page ~3 px taller at 150 % and push long pages down; the room above
    # and below the text absorbs the difference instead. At 100 % these are the
    # established paddings ((10, 5) for buttons and fields).
    def fit(side, height, line=body_line):
        room = max(0, dp(height) - line)
        top, bottom = room // 2, room - room // 2
        return (dp(side), top) if top == bottom else (dp(side), top, dp(side), bottom)
    for name, side, height in (("TButton", 10, 25), ("Quiet.TButton", 10, 25), ("TEntry", 10, 25),
                               ("TCombobox", 10, 25), ("Numeric.TEntry", 4, 17), ("TMenubutton", 5, 19),
                               ("Curve.Axis.TButton", 10, 21), ("Curve.Selected.Axis.TButton", 10, 21),
                               ("Disclosure.TButton", 0, 25), ("Disclosure.Card.TButton", 0, 25)):
        style.configure(name, padding=fit(side, height))
    # clam makes buttons and menu buttons at least 11 characters wide (66 px at
    # 100 % in Segoe UI); keep that minimum in design px at any scale and in any
    # face (Inter's "0" is 8 px, so 11 of them would be 88).
    zero = int(root.tk.call("font", "measure", (ui_theme.FONT_FAMILY, ui_theme.FONT_SIZES["body"]), "0"))
    minimum = -max(1, int(dp(66) / zero + .5)) if zero > 0 else -chars(root, 11)
    for name in ("TButton", "TMenubutton"):
        style.configure(name, width=minimum)
    small_line =int(root.tk.call("font", "metrics", (ui_theme.FONT_FAMILY, ui_theme.FONT_SIZES["small"]), "-linespace"))
    style.configure("Treeview.Heading", padding=fit(8, 29, small_line))
    # A read-only value in a field-coloured chip (the selected replay file).
    style.layout("Chip.Card.TLabel", [("Dolly.field.card", {"sticky": "nsew", "children": [
        ("Label.padding", {"sticky": "nsew", "children": [("Label.label", {"sticky": "nsew"})]})]})])
    style.configure("Chip.Card.TLabel", padding=kit_padding(13), foreground=muted, background=panel,
                    font=(ui_theme.FONT_VALUE, ui_theme.FONT_SIZES["body"]))
    style.map("Chip.Card.TLabel", background=[("disabled", panel), ("!disabled", panel)])

    def surface_layout(layout):
        result = []
        for element, options in layout:
            options = dict(options)
            children = surface_layout(options.pop("children", []))
            if element in surfaces:
                # Sibling decoration does not borrow the field's 2px client
                # inset. All original content padding/hit areas stay intact.
                decorations = [(element + ".fill", {"sticky": "nsew"}),
                               *((element + "." + key, {"sticky": key}) for key in ("nw", "ne", "sw", "se")),
                               (element + ".minimum", {"sticky": ""})]
                children = decorations + children
            if children:
                options["children"] = children
            result.append((element, options))
        return result
    def inner_edge(fill, outline):
        amount = min(1., scale - 1.)
        return "#" + "".join(f"{round(a*(1-amount)+b*amount):02x}"
                            for a, b in zip(_rgb(fill), _rgb(outline)))
    surface_styles = ("Rounded.Card.TFrame", "UpdateNotice.Notes.TFrame", "TButton", "Primary.TButton",
                      "Card.TButton", "Card.Quiet.TButton", "Card.Primary.TButton", "UpdateNotice.TButton",
                      "UpdateNotice.Primary.TButton", "Curve.Axis.TButton", "Curve.Selected.Axis.TButton",
                      "Nav.TButton", "Selected.Nav.TButton", "Current.Nav.TButton", "TEntry", "Card.TEntry", "Numeric.TEntry",
                      "TCombobox", "Card.TCombobox", "TMenubutton", "Card.TMenubutton", "FullEditor.TCheckbutton",
                      "Chip.Card.TLabel")
    for name in surface_styles:
        layout = style.layout(name)
        paints = surfaces.get(layout[0][0])
        if paints is None:
            continue
        _, fill, outline = paints[0]
        style.configure(name, fieldbackground=fill, bordercolor=outline,
                        lightcolor=inner_edge(fill, outline), darkcolor=inner_edge(fill, outline))
        style.map(name, fieldbackground=[(state, color) for state, color, _ in paints[1:]],
                  bordercolor=[(state, edge) for state, _, edge in paints[1:]],
                  lightcolor=[(state, inner_edge(color, edge)) for state, color, edge in paints[1:]],
                  darkcolor=[(state, inner_edge(color, edge)) for state, color, edge in paints[1:]])
        style.layout(name, surface_layout(layout))
    # The library search field: a card entry with room for the magnifier.
    style.layout("Search.Card.TEntry", style.layout("Card.TEntry"))
    style.configure("Search.Card.TEntry", padding=dp((26, 5, 10, 5)))
    # Stage cards (the Replay page) follow the Figma kit exactly: 30 px fields
    # and buttons with values 13 px in, and titles and row labels without
    # clam's two 1 px label borders, so their text starts on the card's
    # content edge like the fields and table beneath them.
    for name, source in (("Stage.Card.TEntry", "Card.TEntry"), ("Stage.Card.TCombobox", "Card.TCombobox"),
                         ("Stage.Card.TButton", "Card.TButton")):
        style.layout(name, style.layout(source))
    style.configure("Stage.Card.TEntry", padding=kit_padding(13))
    # The 8 px chevron ends 13 px from the right edge: its 20 px element
    # centres a 14 px image whose stroke stops 3 px short of the image's edge.
    left, top, _right, bottom = kit_padding(13)
    style.configure("Stage.Card.TCombobox", padding=(left, top, max(0, dp(13) - dp(6)), bottom))
    # Buttons are 8 characters wide (Browse, Refresh: 70 px, label centred).
    style.configure("Stage.Card.TButton", padding=kit_padding(11))
    bare_label = [("Label.padding", {"sticky": "nsew", "children": [("Label.label", {"sticky": "nsew"})]})]
    # A field's value drawn over the field (elided with "…" while it is not being edited), in
    # the entry's own value face (TkTextFont, set by dolly.ui.fonts).
    style.configure("FieldValue.TLabel", background=field, foreground=muted,
                    font=(ui_theme.FONT_VALUE, ui_theme.FONT_SIZES["body"]))
    for name in ("Stage.CardTitle.TLabel", "Stage.CardMuted.TLabel", "Placeholder.TLabel", "FieldValue.TLabel"):
        style.layout(name, bare_label)
    root._dolly_theme_images = images
    # The five ported pages' Kit.* styles come last: Kit.Treeview aliases the
    # library table made above (PLAN-5 2.3).
    from .ui import kit_styles
    kit_styles.install(root)
    # One look on all six pages: Replay's fields and dropdowns draw the kit's
    # field, which has no focus or hover face (N4). Their paddings, the value
    # colour and the dropdown chevron stay Replay's.
    def kit_field(layout):
        return [(element.replace("Dolly.field.card", "Kit.field"),
                 {**options, **({"children": kit_field(options["children"])} if "children" in options else {})})
                for element, options in layout]
    plain = [("!disabled", field)]
    for name in ("Stage.Card.TEntry", "Stage.Card.TCombobox"):
        style.layout(name, kit_field(style.layout(name)))
        # A state map of its own for every state, so Card.*'s focus and hover
        # entries are never reached through the style's parents.
        style.map(name, fieldbackground=[("disabled", t["field_disabled"]), *plain],
                  bordercolor=[("disabled", t["field_disabled"]), *plain],
                  lightcolor=[("disabled", t["field_disabled"]), *plain],
                  darkcolor=[("disabled", t["field_disabled"]), *plain],
                  foreground=[("disabled", t["muted_disabled"]), ("!disabled", muted)])


def install_wheel_guard(root):
    """Intercept before the ttk class binding can change a closed value."""
    def wheel(event):
        widget = event.widget
        # This handler is attached to value widgets only. Scroll their enclosing
        # page when possible; never synthesize a change to the selected value.
        parent = widget
        while parent is not None:
            if hasattr(parent, "scroll_page_wheel"):
                parent.scroll_page_wheel(event)
                break
            parent = getattr(parent, "master", None)
        return "break"
    # Replacing only the wheel class actions keeps dropdown opening, listbox
    # scrolling, keyboard arrows, and explicit selections unchanged.
    for cls in ("TCombobox", "TSpinbox", "Spinbox", "TScale", "Scale"):
        for event in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            root.bind_class(cls, event, wheel)


def compact_slider(parent, label, variable, minimum, maximum, panel, muted, accent):
    """Compact rail with keyboard/drag adjustment and an exact editable value."""
    from .ui.scale import chars, scaler
    dp = scaler(parent)
    inset, middle, height = dp(4), dp(11), dp(22)
    row = ttk.Frame(parent, style="Card.TFrame")
    row.pack(fill="x", pady=dp(1))
    row.columnconfigure(1, weight=1)
    ttk.Label(row, text=label, style="CardMuted.TLabel", width=chars(row, 7)).grid(row=0, column=0, sticky="w")
    rail = tk.Canvas(row, height=height, width=dp(80), bg=panel, highlightthickness=0, takefocus=True)
    rail.grid(row=0, column=1, sticky="ew", padx=dp((4, 10)))
    ttk.Entry(row, textvariable=variable, width=chars(row, 7), justify="right", style="Numeric.TEntry").grid(row=0, column=2)
    def redraw(*_):
        rail.delete("all")
        width = max(3 * inset, rail.winfo_width())
        try:
            value = float(variable.get())
        except (ValueError, tk.TclError):
            return
        if not math.isfinite(value):
            return
        x = inset + (width - 2 * inset) * max(0, min(1, (value - minimum) / (maximum - minimum)))
        rail.create_line(inset, middle, width - inset, middle, fill=ui_theme.TOKENS["field"], width=dp(6), capstyle="round")
        rail.create_line(inset, middle, x, middle, fill=ui_theme.TOKENS["accent_active"], width=dp(6), capstyle="round")
        rail.create_line(x, middle - dp(4), x, middle + dp(4), fill=accent, width=dp(5), capstyle="round")
        if rail.focus_get() == rail:
            rail.create_rectangle(1, 1, width - 1, height - 1, outline=muted)
    def set_value(value):
        variable.set(f"{max(minimum, min(maximum, value)):.2f}".rstrip("0").rstrip("."))
    def drag(event):
        rail.focus_set()
        set_value(minimum + (maximum - minimum) * (event.x - inset) / max(1, rail.winfo_width() - 2 * inset))
    def key(event):
        try:
            value = float(variable.get())
        except ValueError:
            value = minimum
        step = (maximum - minimum) / (1000 if event.state & 1 else 100)
        if event.keysym in ("Left", "Down"):
            set_value(value - step)
        elif event.keysym in ("Right", "Up"):
            set_value(value + step)
        elif event.keysym == "Home":
            set_value(minimum)
        elif event.keysym == "End":
            set_value(maximum)
        else:
            return
        return "break"
    token = variable.trace_add("write", redraw)
    rail.bind("<Configure>", redraw)
    rail.bind("<FocusIn>", redraw)
    rail.bind("<FocusOut>", redraw)
    rail.bind("<Button-1>", drag)
    rail.bind("<B1-Motion>", drag)
    rail.bind("<KeyPress>", key)
    rail.bind("<Destroy>", lambda event: variable.trace_remove("write", token))
    return row
