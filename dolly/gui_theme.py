"""Wireframe-style ttk surfaces, preserving the native widgets and bindings."""
import base64
import math
import struct
import tkinter as tk
from tkinter import ttk
import zlib


def _rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def _photo(root, width, height, pixel):
    """Small RGBA assets with smooth edges, generated without a runtime library."""
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw.extend(pixel(x + .5, y + .5))
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", width, height, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return tk.PhotoImage(master=root, data=base64.b64encode(png), format="png")


def _coverage(distance):
    return max(0.0, min(1.0, .5 - distance))


def _segment_distance(x, y, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = max(0.0, min(1.0, ((x - a[0]) * dx + (y - a[1]) * dy) / (dx * dx + dy * dy)))
    return math.hypot(x - a[0] - t * dx, y - a[1] - t * dy)


def rounded_image(root, fill, outline, radius=6, *, size=64, line=1, checked=None):
    fill, outline = _rgb(fill), _rgb(outline)
    tick = _rgb(checked) if checked else None
    def pixel(x, y):
        qx, qy = abs(x - size / 2) - (size / 2 - radius), abs(y - size / 2) - (size / 2 - radius)
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
    return _photo(root, size, size, pixel)


def apply(root, bg, panel, text, muted, accent):
    style = ttk.Style(root)
    images = []
    scale = max(1, root.winfo_fpixels("1i") / 96)
    edge = "#26343b"
    field = "#10171c"
    control_radius, card_radius = round(5 * scale), round(6 * scale)
    def rounded(name, fill, outline, radius=control_radius, min_width=16, **states):
        normal = rounded_image(root, fill, outline, radius, line=scale)
        images.append(normal)
        alternatives = []
        for state, (color, line) in states.items():
            image = rounded_image(root, color, line, radius, line=scale)
            images.append(image)
            alternatives.append((state, image))
        # A wide center prevents tiny repeating tiles from slowing window paints.
        style.element_create(name, "image", normal, *alternatives, border=radius,
                             padding=0, width=min_width, height=16, sticky="nsew")
    rounded("Dolly.card", panel, edge, card_radius)
    style.layout("Rounded.Card.TFrame", [("Dolly.card", {"sticky": "nsew"})])
    style.configure("Rounded.Card.TFrame", background=bg)
    rounded("Dolly.button", "#25313e", "#25313e", disabled=("#172027", "#29353d"),
            pressed=("#233c3b", accent), active=("#203039", accent), focus=(panel, accent))
    rounded("Dolly.primary", accent, accent, disabled=("#24433f", "#31564f"),
            pressed=("#77beaf", "#77beaf"), active=("#afe8dc", "#afe8dc"), focus=(accent, "#dcfff7"))
    def button_layout(element):
        return [(element, {"sticky": "nsew", "children": [("Button.padding", {
            "sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]})]})]
    style.layout("TButton", button_layout("Dolly.button"))
    style.layout("Primary.TButton", button_layout("Dolly.primary"))
    style.configure("TButton", padding=(10, 5))
    style.configure("Quiet.TButton", padding=(10, 5))
    for name, surface in (("TButton", bg), ("Primary.TButton", bg), ("Quiet.TButton", bg),
                          ("Card.TButton", panel), ("Card.Primary.TButton", panel), ("Card.Quiet.TButton", panel)):
        style.configure(name, background=surface)
        style.map(name, background=[("active", surface), ("disabled", surface)])
    rounded("Dolly.nav", bg, bg, active=(panel, edge), focus=(bg, accent))
    rounded("Dolly.nav.selected", bg, accent)
    style.layout("Nav.TButton", button_layout("Dolly.nav"))
    style.layout("Selected.Nav.TButton", button_layout("Dolly.nav.selected"))
    style.configure("Nav.TButton", anchor="w", padding=(12, 8), background=bg)
    style.configure("Selected.Nav.TButton", foreground=accent)
    for name in ("Nav.TButton", "Selected.Nav.TButton"):
        style.map(name, background=[("active", bg)], foreground=[("active", accent)])
    for label, surface in (("Disclosure.TButton", bg), ("Disclosure.Card.TButton", panel)):
        style.layout(label, [("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "w"})]})])
        style.configure(label, background=surface, foreground=text, padding=(0, 5), borderwidth=0)
        style.map(label, background=[("active", surface)], foreground=[("active", accent)])

    rounded("Dolly.field", field, edge, disabled=("#172027", "#29353d"),
            focus=(field, accent), active=(field, "#56716f"))
    style.layout("TEntry", [("Dolly.field", {"sticky": "nsew", "children": [
        ("Entry.padding", {"sticky": "nsew", "children": [("Entry.textarea", {"sticky": "nsew"})]})]})])
    def arrow_pixel(x, y):
        ink = _coverage(min(_segment_distance(x, y, (3, 5), (7, 9)),
                            _segment_distance(x, y, (7, 9), (11, 5))) - .7)
        return *_rgb(muted), round(255 * ink)
    arrow = _photo(root, 14, 14, arrow_pixel)
    images.append(arrow)
    style.element_create("Dolly.Combobox.downarrow", "image", arrow, width=20, sticky="")
    style.layout("TCombobox", [("Dolly.field", {"sticky": "nsew", "children": [
        ("Combobox.padding", {"sticky": "nsew", "children": [
            ("Dolly.Combobox.downarrow", {"side": "right", "sticky": "ns"}),
            ("Combobox.textarea", {"sticky": "nsew"})]})]})])
    for name in ("TEntry", "TCombobox"):
        style.configure(name, fieldbackground=field, background=bg, foreground=text,
                        borderwidth=0, padding=(10, 5))
        style.map(name, fieldbackground=[("disabled", "#172027"), ("readonly", field)],
                  foreground=[("disabled", "#657780"), ("readonly", text)])
        style.configure("Card." + name, background=panel)

    style.configure("Numeric.TEntry", padding=(4, 1), background=panel)

    size = round(14 * scale)
    indicator = rounded_image(root, field, edge, round(3 * scale), size=size, line=scale)
    checked = rounded_image(root, accent, accent, round(3 * scale), size=size, line=scale, checked=bg)
    hover = rounded_image(root, field, accent, round(3 * scale), size=size, line=scale)
    disabled = rounded_image(root, "#172027", "#29353d", round(3 * scale), size=size, line=scale)
    disabled_checked = rounded_image(root, "#31564f", "#31564f", round(3 * scale), size=size, line=scale, checked=muted)
    images.extend((indicator, checked, hover, disabled, disabled_checked))
    style.element_create("Dolly.Checkbutton.indicator", "image", indicator,
                         ("disabled selected", disabled_checked), ("disabled", disabled),
                         ("selected", checked), ("active", hover), ("focus", hover), sticky="w", width=size + 8)
    style.layout("TCheckbutton", [("Checkbutton.padding", {"sticky": "nsew", "children": [
        ("Dolly.Checkbutton.indicator", {"side": "left", "sticky": "w"}),
        ("Checkbutton.label", {"sticky": "nsew"})]})])
    style.configure("TCheckbutton", foreground=text)
    style.configure("Card.TCheckbutton", background=panel)
    style.map("Card.TCheckbutton", background=[("active", panel)])

    style.layout("FullEditor.TCheckbutton", [("Dolly.nav.selected", {"sticky": "nsew", "children":
        style.layout("TCheckbutton")})])
    style.configure("FullEditor.TCheckbutton", padding=(12, 9), background=bg)

    def match_surface(event):
        widget = event.widget
        name = str(widget.cget("style")) or widget.winfo_class()
        if name not in ("TEntry", "TCombobox", "TButton", "Primary.TButton", "Quiet.TButton", "TCheckbutton"):
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
    for cls in ("TEntry", "TCombobox", "TButton", "TCheckbutton"):
        root.bind_class(cls, "<Map>", match_surface, add="+")

    rounded("Dolly.scroll.thumb", "#3b4b55", "#3b4b55", radius=4, min_width=8,
            active=("#526a73", "#526a73"), pressed=(accent, accent))
    style.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
        ("Dolly.scroll.thumb", {"sticky": "nsew", "expand": "1"})]})])
    style.configure("Vertical.TScrollbar", background=bg, troughcolor=bg, bordercolor=bg,
                    lightcolor=bg, darkcolor=bg, borderwidth=0, arrowsize=10, width=10)
    style.configure("Treeview", borderwidth=0, relief="flat", bordercolor=panel, lightcolor=panel, darkcolor=panel)
    root._dolly_theme_images = images


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
    row = ttk.Frame(parent, style="Card.TFrame")
    row.pack(fill="x", pady=1)
    row.columnconfigure(1, weight=1)
    ttk.Label(row, text=label, style="CardMuted.TLabel", width=7).grid(row=0, column=0, sticky="w")
    rail = tk.Canvas(row, height=22, width=80, bg=panel, highlightthickness=0, takefocus=True)
    rail.grid(row=0, column=1, sticky="ew", padx=(4, 10))
    ttk.Entry(row, textvariable=variable, width=7, justify="right", style="Numeric.TEntry").grid(row=0, column=2)
    def redraw(*_):
        rail.delete("all")
        width = max(12, rail.winfo_width())
        try:
            value = float(variable.get())
        except (ValueError, tk.TclError):
            return
        if not math.isfinite(value):
            return
        x = 4 + (width - 8) * max(0, min(1, (value - minimum) / (maximum - minimum)))
        rail.create_line(4, 11, width - 4, 11, fill="#0e171d", width=6, capstyle="round")
        rail.create_line(4, 11, x, 11, fill="#385d59", width=6, capstyle="round")
        rail.create_line(x, 7, x, 15, fill=accent, width=5, capstyle="round")
        if rail.focus_get() == rail:
            rail.create_rectangle(1, 1, width - 1, 21, outline=muted)
    def set_value(value):
        variable.set(f"{max(minimum, min(maximum, value)):.2f}".rstrip("0").rstrip("."))
    def drag(event):
        rail.focus_set()
        set_value(minimum + (maximum - minimum) * (event.x - 4) / max(1, rail.winfo_width() - 8))
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
