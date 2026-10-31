"""Environment edition UI; button and existing native keybind share one action."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog

from . import edition
from .environment_job import PanoramaJob


def build(app, page):
    if not edition.is_environment():
        return
    box = page.add_card('Environment panorama')
    app.environment_job = PanoramaJob()
    pictures=Path.home()/'Pictures'
    app.environment_folder = tk.StringVar(value=str(pictures if pictures.is_dir() else Path.home()))
    app.environment_size = tk.StringVar(value='1024')
    app.environment_card = box
    box.text('Pause the replay and position the camera. The capture folder is shared with PNG stills.')
    row = box.row()
    row.widget(ttk.Entry(row.host, textvariable=app.environment_folder, style='Kit.TEntry'), width='fill')
    row.gap(10)
    def browse():
        folder = filedialog.askdirectory(parent=app.root, title='Capture output folder',
                                         initialdir=app.environment_folder.get())
        if folder: app.environment_folder.set(folder)
    row.button('Browse', browse)
    row = box.row()
    row.label('Square size (next launch)')
    row.gap(10)
    app.environment_size_combo = row.widget(ttk.Combobox(
        row.host, textvariable=app.environment_size, values=('1024','2048','4096'),
        state='readonly', style='Kit.TCombobox'), width=100)
    row.gap(10)
    row.button('Apply size', lambda: app._guard('Panorama size', lambda: apply_size(app)))
    row = box.row()
    row.button('Set capture key', lambda: app._show_keybinds('capture_environment'))
    row.push()
    row.button('Cancel', app.environment_job.cancelled.set)
    row.gap(10)
    app.environment_capture_button = row.button('Capture panorama', lambda: start(app), kind='primary')
    box.text('Six overlapping HDR faces become an EXR panorama. Inspect for remaining objects and seams.')


def simplify(app):
    """Present only still capture, viewpoints and fixed scene controls in Kara's kit."""
    for card in app.export_page.cards:
        card.set_visible(card in (app.screenshot_card, app.environment_card))
    # Saved viewpoints have no authored path timing. Retain the internal
    # variables for old files, but remove their editable rows from this UI.
    app.capture_mode.set('Replay timing')
    app.capture_buttons[0].configure(text='New viewpoints')
    for card, widget in ((app.camera_view.capture_card, app.camera_view.capture_mode_combo),
                         (app.camera_view.selected_card, app.camera_view.key_entries['time'])):
        for element in card.elements:
            widgets = getattr(element, 'widgets', None)
            if callable(widgets) and widget in widgets():
                element.set_visible(False)
                break
    app.camera_tree.configure(displaycolumns=('view', 'aspect_ratio', 'roll'))
    for card in (app.camera_view.curves_card, app.camera_view.attach_card,
                 app.look_view.tracks_card, app.look_view.editor_card, app.look_view.particles_card,
                 app.settings_view.updates_card, app.settings_view.reshade_card,
                 app.settings_view.presence_card):
        card.set_visible(False)
    app.look_page.spec = None
    app.look_page._bento = None
    for card in app.look_page.cards:
        card.column = None
    app.look_page.layout()
    # The shared shot transport is retained internally for saved-view compatibility.
    # No page adopts it in this edition, so path playback is never offered.
    app._playback_hosts.clear()
    app.screenshot_depth.set(False)
    app.screenshot_players.set(False)
    app.screenshot_depth_check.configure(state='disabled')
    app.screenshot_depth_check.place_forget()
    # File/Edit remain useful for saving viewpoints and undoing camera changes.
    app.full_editor.set(True)


def apply_size(app):
    from dataclasses import replace
    from .replays import parse_launch_options
    if app.busy or app.playing:return False
    size=int(app.environment_size.get())
    if size not in (1024,2048,4096):raise ValueError('Unsupported panorama size')
    from .screenshot import apply_render_size
    options=apply_render_size(app.launch_options.get(), (size, size))
    parse_launch_options(options)
    settings=replace(app.app_settings,launch_options=options)
    app.launch_options.set(options)
    return app._persist_preferences(settings,'Panorama render size saved. Reopen the replay to apply it.')


def start(app, event=None):
    if not edition.is_environment():
        return False
    if (app.closed or app.busy or app.playing or getattr(app, '_still_run', None) or
            getattr(app, '_layer_queue', ()) or getattr(app, '_bone_picker_context', None)):
        app.status_text.set('Finish the current operation before capturing a panorama.')
        return False
    factory = getattr(app.controller, 'environment_backend', None)
    if not callable(factory):
        app.status_text.set('Panorama capture is not ready: live HDR acquisition and empty-scene validation remain in development.')
        return False
    folder = app.environment_folder.get()
    app.environment_job.cancelled.clear()
    def run():
        # Backend must revalidate current owned replay/view; a queued key event
        # is not authority to capture a stale pose or a different game session.
        backend = factory(event=event,cancelled=app.environment_job.cancelled.is_set)
        try:
            view = backend.view()
            return app.environment_job.run(backend, folder, view['position'], view['tick'],
                                           face_size=view['face_size'],face_fov=100,rendered_reference=True)
        except Exception:
            # view() forces full render scale; if the capture transaction never
            # reached its restore step, release it here so the session is not
            # left at 100% scale.
            try:
                app.controller.clear_export_resolution()
            except Exception:
                pass
            raise
    return app._submit('Capturing panorama', run,
                       lambda output: app.status_text.set('Rendered HDR reference saved: '+str(output)))
