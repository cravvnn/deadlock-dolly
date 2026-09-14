"""Small theme refinements using native ttk widgets and scalable image borders."""
import math
import tkinter as tk
from tkinter import ttk


def rounded_image(root, fill, outline, radius=7):
    size = 64
    photo = tk.PhotoImage(master=root, width=size, height=size)
    for y in range(size):
        for x in range(size):
            dx = max(radius - x, x - (size - radius - 1), 0)
            dy = max(radius - y, y - (size - radius - 1), 0)
            distance = math.hypot(dx, dy)
            if distance <= radius:
                border = x in (0, size-1) or y in (0, size-1) or distance > radius-1
                photo.put(outline if border else fill, (x, y))
    return photo


def apply(root, bg, panel, text, muted, accent):
    style = ttk.Style(root)
    images = []
    def rounded(name, fill, line, **states):
        normal = rounded_image(root, fill, line)
        images.append(normal)
        alternatives = []
        for state, (color, edge) in states.items():
            image = rounded_image(root, color, edge); images.append(image)
            alternatives.append((state, image))
        style.element_create(name, "image", normal, *alternatives, border=7, width=16, height=16, sticky="nsew")
    rounded("Dolly.card", panel, "#2a3942")
    style.layout("Rounded.Card.TFrame", [("Dolly.card", {"sticky": "nsew"})])
    style.configure("Rounded.Card.TFrame", background=panel)
    rounded("Dolly.button", "#1c2831", "#3a4c58", disabled=("#182129", "#28333d"),
            pressed=("#29413f", accent), active=("#283941", "#6f9c99"), focus=("#1c2831", accent))
    rounded("Dolly.primary", accent, accent, disabled=("#24433f", "#31564f"),
            pressed=("#4da99c", accent), active=("#8ee7d9", "#8ee7d9"), focus=(accent, "#dcfff7"))
    layout = lambda element: [(element, {"sticky": "nsew", "children": [
        ("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]})]})]
    style.layout("TButton", layout("Dolly.button"))
    style.layout("Primary.TButton", layout("Dolly.primary"))
    style.configure("TButton", padding=(9, 4), background=panel)
    style.map("TButton", background=[("active", panel), ("disabled", panel)])
    style.configure("Primary.TButton", background=panel)
    style.map("Primary.TButton", background=[("active", panel), ("disabled", panel)])
    style.configure("Quiet.TButton", padding=(7, 4), background=bg)
    style.map("Quiet.TButton", background=[("active", bg), ("disabled", bg)])
    style.configure("Nav.Primary.TButton", background=bg)
    style.map("Nav.Primary.TButton", background=[("active", bg), ("disabled", bg)])
    for label, surface in (("Disclosure.TButton", bg), ("Disclosure.Card.TButton", panel)):
        style.layout(label, [("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "w"})]})])
        style.configure(label, background=surface, foreground=text, padding=(0, 5), borderwidth=0)
        style.map(label, background=[("active", surface)], foreground=[("active", accent)])
    style.configure("Card.TCheckbutton", background=panel)
    style.map("Card.TCheckbutton", background=[("active", panel)])
    for name in ("TEntry", "TCombobox"):
        style.configure(name, fieldbackground="#18242b", background="#263640", bordercolor="#364954",
                        lightcolor="#364954", darkcolor="#364954", borderwidth=1, padding=6)
        style.map(name, fieldbackground=[("readonly", "#18242b"), ("disabled", "#172027")],
                  bordercolor=[("focus", accent)], lightcolor=[("focus", accent)], darkcolor=[("focus", accent)])
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
