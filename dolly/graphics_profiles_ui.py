"""Settings controls for the local Dolly-only graphics library."""
from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk

from . import graphics_profiles as profiles
from .ui import text_dialog as simpledialog
from .ui import question_dialog as messagebox

# The help text under the controls; on the Settings card it is the title's info bubble.
PROFILES_INFO = ("Profiles apply when Dolly starts a replay. Dolly uses separate replay settings; your normal game settings stay untouched. "
                 "Close the game before saving its graphics. Profiles copy quality settings, excluding display mode, "
                 "resolution, GPU and upscaler settings; use Display launch options for window size.")
# The Settings card's form (Figma [design reference], design px): the label column, and the
# indent of the button rows (label + 10) so they start under the dropdown.
LABEL_WIDTH = 125
BUTTON_INDENT = 135
BUTTON_ROWS = ((("Save current graphics...", "capture"), ("Import video.txt...", "import_file")),
               (("Preview changes...", "preview"), ("Rename...", "rename"), ("Delete...", "delete")))


class GraphicsProfiles:
    """The profile dropdown, its actions and status line.

    ``card=None`` builds today's layout in ``parent`` (a plain card frame). With
    ``card`` (a kit card of the Settings page) it builds the card's rows instead:
    "Next replay session" and a filling dropdown, two indented rows of kit
    buttons and a wrapping status line; the help text is left to the card's info
    icon (``PROFILES_INFO``). ``status_text`` is the status line either way.
    """

    def __init__(self, parent, *, root, on_error, card=None):
        self.root = root
        self.on_error = on_error
        self.library = None
        self.selection = tk.StringVar(value=profiles.CURRENT)
        self.status = tk.StringVar()
        if card is None:
            self._build_frame(parent)
        else:
            self._build_card(card)
        self.combo.bind("<<ComboboxSelected>>", lambda _event: self.run(self.select))
        try:
            self.refresh()
        except (OSError, ValueError, profiles.LaunchError) as exc:
            # Settings is built before the main log widget. Show the error
            # after construction so a damaged library cannot prevent startup.
            self.status.set(str(exc))
            self.root.after_idle(lambda error=exc: self.on_error("Graphics profiles", error))

    def _build_frame(self, parent):
        from .ui.scale import scaler
        from .ui.widgets import actions, field
        dp = scaler(parent)
        self.combo = field(parent, "Next replay session", self.selection, values=(profiles.CURRENT,))
        actions(parent, (("Save current graphics...", lambda: self.run(self.capture)),
                         ("Import video.txt...", lambda: self.run(self.import_file)),
                         ("Preview changes...", lambda: self.run(self.preview))))
        actions(parent, (("Rename...", lambda: self.run(self.rename)),
                         ("Delete...", lambda: self.run(self.delete))), 2)
        ttk.Label(parent, text=PROFILES_INFO, wraplength=dp(700), style="CardMuted.TLabel").pack(fill="x")
        self.status_text = ttk.Label(parent, textvariable=self.status, wraplength=dp(700), style="CardMuted.TLabel")
        self.status_text.pack(fill="x", pady=dp((8, 0)))

    def _build_card(self, card):
        row = card.row()
        row.label("Next replay session", width=LABEL_WIDTH)
        row.gap(10)
        self.combo = row.widget(ttk.Combobox(row.host, textvariable=self.selection, values=(profiles.CURRENT,),
                                             state="readonly", style="Kit.TCombobox"), width="fill")
        for specs in BUTTON_ROWS:
            row = card.row()
            row.gap(BUTTON_INDENT)
            for index, (text, name) in enumerate(specs):
                if index:
                    row.gap(10)
                # Looked up when pressed, like the frame layout's buttons.
                row.button(text, lambda name=name: self.run(getattr(self, name)))
        self.status_text = card.text(textvariable=self.status, top=15)

    def run(self, action):
        try:
            action()
        except (OSError, ValueError, profiles.LaunchError) as exc:
            self.status.set(str(exc))
            self.on_error("Graphics profiles", exc)

    def refresh(self):
        self.library = profiles.load_library()
        self.combo.configure(values=(profiles.CURRENT, *(p["name"] for p in self.library["profiles"])))
        selected = next((p for p in self.library["profiles"] if p["id"] == self.library["selected"]), None)
        self.selection.set(selected["name"] if selected else profiles.CURRENT)
        self.status.set(f"{len(self.library['profiles'])} local profile(s). Selection applies to the next launch.")

    def selected_id(self):
        # Re-read disk so a removed/damaged library cannot silently become a
        # different launch choice. The launcher snapshots its values separately.
        library = profiles.load_library()
        name = self.selection.get()
        if name == profiles.CURRENT:
            return ""
        selected = next((p for p in library["profiles"] if p["name"] == name), None)
        if selected is None:
            raise profiles.LaunchError("Graphics profile changed outside this window. Reopen Settings and choose a profile.")
        return selected["id"]

    def select(self):
        selected = self.selected_id()
        library = profiles.load_library()
        library["selected"] = selected
        profiles.save_library(library)
        self.refresh()

    def chosen(self):
        selected = self.selected_id()
        library = profiles.load_library()
        profile = next((p for p in library["profiles"] if p["id"] == selected), None)
        if profile is None:
            raise ValueError("Select a named graphics profile first.")
        return library, profile

    def capture(self):
        self.add(profiles.capture_source())

    def import_file(self):
        name = filedialog.askopenfilename(parent=self.root, title="Import graphics from video.txt",
                                         filetypes=(("Video settings", "*.txt"),))
        if name:
            self.add(Path(name))

    def add(self, path):
        data = profiles.read_video(path)
        name = simpledialog.askstring("Save graphics profile", "Profile name (for example, Recording):", parent=self.root)
        if name is None:
            return
        profile = profiles.profile_from_bytes(name.strip(), data)
        kept = "\n".join(f"{profiles.QUALITY_FIELDS[k]}: {v}" for k, v in profile["values"].items())
        excluded = profiles.excluded_fields(data, profile)
        text = f"Source: {path}\n\nQuality values to save:\n{kept}\n\nExcluded fields (kept from the destination):\n" + "\n".join(excluded)
        def save():
            library = profiles.load_library()
            library["profiles"].append(profile)
            library["selected"] = profile["id"]
            profiles.save_library(library)
            self.refresh()
        self.show_preview("Save graphics profile", text, save)

    def preview(self):
        _library, profile = self.chosen()
        from .editing_session import preview_video
        _data, audit = preview_video(profile['id'])
        text = f"Separate Dolly session settings\nProfile: {profile['name']}\n\n"
        text += '\n'.join(f"{profiles.QUALITY_FIELDS[key]}: {value}" for key, value in audit['quality_values'].items())
        text += "\n\nNo game files are changed by this preview. Your normal-play settings and startup scripts are not imported. "
        text += "Only these reviewed quality values and display/device fields are written to Dolly's new session folder."
        self.show_preview("Next-launch graphics preview", text)

    def show_preview(self, title, text, save=None):
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        from . import ui_theme
        from .ui import fonts
        from .ui.chrome import frame_size, style_title_bar, work_area
        style_title_bar(dialog)
        from .ui.scale import scaler
        dp = scaler(dialog)
        # Keep the window on the monitor even at 200% scale. The card bodies scroll.
        left, top, right, bottom = work_area(self.root)
        frame_width, frame_height = frame_size(menu=False)
        if save is None:
            # The next-launch preview has its own design; preserve its existing UI
            # and text until that separate state is implemented.
            dialog.geometry(f"{min(dp(740), right - left - frame_width)}x{min(dp(540), bottom - top - frame_height)}")
            body = ttk.Frame(dialog, padding=dp(16))
            body.pack(fill="both", expand=True)
            view = tk.Text(body, wrap="word", width=70, height=22)
            scroll = ttk.Scrollbar(body, orient="vertical", command=view.yview)
            view.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y")
            view.pack(fill="both", expand=True)
            view.insert("1.0", text)
            view.configure(state="disabled")
            buttons = ttk.Frame(dialog, padding=dp(12))
            buttons.pack(fill="x")
            ttk.Button(buttons, text="Close", command=dialog.destroy).pack(side="left")
            return
        dialog.geometry(f"{min(dp(820), right - left - frame_width)}x{min(dp(756), bottom - top - frame_height)}")
        body = ttk.Frame(dialog, padding=dp((24, 22, 24, 20)))
        body.pack(fill="both", expand=True)
        tk.Label(body, text=title, bg=ui_theme.TOKENS["bg"], fg=ui_theme.TOKENS["text"],
                 font=fonts.font(dialog, "page_title"), anchor="w").pack(fill="x", pady=dp((0, 15)))
        buttons = ttk.Frame(body)
        buttons.pack(side="bottom", fill="x", pady=dp((24, 0)))
        if save is not None:
            def commit():
                save()
                dialog.destroy()
            ttk.Button(buttons, text="Save profile for next launch", style="Primary.TButton",
                       command=lambda: self.run(commit)).pack(side="right")
        ttk.Button(buttons, text="Cancel" if save else "Close", command=dialog.destroy).pack(side="right", padx=dp((0, 10)))

        # The save flow already provides these three sections; retain all values
        # and excluded fields, rather than hard-coding the design's sample data.
        first = "\n\nQuality values to save:\n"
        second = "\n\nExcluded fields (kept from the destination):\n"
        if save is not None and text.startswith("Source: ") and first in text and second in text:
            source, rest = text[len("Source: "):].split(first, 1)
            values, excluded = rest.split(second, 1)
            source_card = ttk.Frame(body, style="Rounded.Card.TFrame", padding=dp(20))
            source_card.pack(fill="x", pady=dp((0, 15)))
            ttk.Label(source_card, text="Source", style="CardTitle.TLabel").pack(anchor="w")
            tk.Label(source_card, text=source, bg=ui_theme.TOKENS["panel"], fg=ui_theme.TOKENS["muted"],
                     font=(ui_theme.FONT_MONO, -dp(12)), anchor="w", justify="left",
                     wraplength=dp(725)).pack(fill="x", pady=dp((10, 0)))
            columns = ttk.Frame(body)
            columns.pack(fill="both", expand=True)
            columns.columnconfigure(0, weight=1, uniform="graphics")
            columns.columnconfigure(1, weight=1, uniform="graphics")
            columns.rowconfigure(0, weight=1)
            quality = ttk.Frame(columns, style="Rounded.Card.TFrame", padding=dp(20))
            quality.grid(row=0, column=0, sticky="nsew", padx=dp((0, 8)))
            ttk.Label(quality, text="Quality Values to Save", style="CardTitle.TLabel").pack(anchor="w", pady=dp((0, 10)))
            value_area = ttk.Frame(quality, style="Card.TFrame")
            value_area.pack(fill="both", expand=True)
            table = ttk.Treeview(value_area, columns=("name", "value"), show="", selectmode="none")
            table.column("name", width=dp(240), minwidth=dp(70), stretch=True)
            table.column("value", width=dp(60), minwidth=dp(40), stretch=False, anchor="e")
            table.pack(side="left", fill="both", expand=True)
            rail = ttk.Scrollbar(value_area, orient="vertical", command=table.yview, style="Card.Vertical.TScrollbar")
            rail.pack(side="right", fill="y")
            table.configure(yscrollcommand=rail.set)
            for line in values.splitlines():
                name, separator, value = line.partition(": ")
                table.insert("", "end", values=(name, value if separator else ""))
            excluded_card = ttk.Frame(columns, style="Rounded.Card.TFrame", padding=dp(20))
            excluded_card.grid(row=0, column=1, sticky="nsew", padx=dp((7, 0)))
            ttk.Label(excluded_card, text="Excluded Fields", style="CardTitle.TLabel").pack(anchor="w")
            ttk.Label(excluded_card, text="Kept from the destination.", style="CardMuted.TLabel").pack(anchor="w", pady=dp((6, 10)))
            list_area = ttk.Frame(excluded_card, style="Card.TFrame")
            list_area.pack(fill="x")
            view = tk.Text(list_area, height=7, width=1, wrap="word", bg=ui_theme.TOKENS["field"],
                           fg=ui_theme.TOKENS["muted"], relief="flat", borderwidth=0,
                           padx=dp(12), pady=dp(10), font=(ui_theme.FONT_MONO, -dp(12)))
            scroll = ttk.Scrollbar(list_area, orient="vertical", command=view.yview, style="Card.Vertical.TScrollbar")
            view.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y")
            view.pack(fill="x", expand=True)
            view.insert("1.0", excluded)
            view.configure(state="disabled")
        else:
            view = tk.Text(body, wrap="word", width=70, height=22)
            scroll = ttk.Scrollbar(body, orient="vertical", command=view.yview)
            view.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y")
            view.pack(fill="both", expand=True)
            view.insert("1.0", text)
            view.configure(state="disabled")

    def rename(self):
        library, profile = self.chosen()
        name = simpledialog.askstring("Rename graphics profile", "New name:", initialvalue=profile["name"], parent=self.root)
        if name is not None:
            profile["name"] = name.strip()
            profiles.save_library(library)
            self.refresh()

    def delete(self):
        library, profile = self.chosen()
        if messagebox.askyesno("Delete graphics profile", f"Delete '{profile['name']}' from Dolly's local library?", parent=self.root):
            library["profiles"].remove(profile)
            library["selected"] = ""
            profiles.save_library(library)
            self.refresh()


def launch_options(app):
    panel = getattr(app, "graphics_profiles", None)
    selected = panel.selected_id() if panel is not None else ""
    return {"graphics_profile": selected} if selected else {}
