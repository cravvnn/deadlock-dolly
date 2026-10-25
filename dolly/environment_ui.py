"""Environment edition UI; button and existing native keybind share one action."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog

from . import edition
from .environment_job import PanoramaJob


def build(app, parent):
    if not edition.is_environment():
        return
    from .ui.widgets import card
    box = card(parent, 'Environment panorama')
    app.environment_job = PanoramaJob()
    pictures=Path.home()/'Pictures'
    app.environment_folder = tk.StringVar(value=str(pictures if pictures.is_dir() else Path.home()))
    app.environment_size = tk.StringVar(value='1024')
    ttk.Label(box, text='Capture folder (screenshots and panoramas). Assign Capture panorama in Keybinds. '
                       'Pause the replay and position the camera before capturing.',
              style='CardMuted.TLabel', wraplength=760).pack(anchor='w')
    row = ttk.Frame(box, style='Card.TFrame'); row.pack(fill='x', pady=8)
    ttk.Entry(row, textvariable=app.environment_folder).pack(side='left', fill='x', expand=True)
    def browse():
        folder = filedialog.askdirectory(parent=app.root, title='Capture output folder',
                                         initialdir=app.environment_folder.get())
        if folder: app.environment_folder.set(folder)
    ttk.Button(row, text='Browse', command=browse).pack(side='right', padx=6)
    size_row=ttk.Frame(box,style='Card.TFrame');size_row.pack(fill='x',pady=6)
    ttk.Label(size_row,text='Square render size (next launch)',style='CardMuted.TLabel').pack(side='left')
    ttk.Combobox(size_row,textvariable=app.environment_size,values=('1024','2048','4096'),
                 state='readonly',width=8).pack(side='left',padx=8)
    ttk.Button(size_row,text='Apply size',command=lambda:apply_size(app)).pack(side='left')
    ttk.Button(box, text='Capture panorama', command=lambda: start(app)).pack(side='right')
    ttk.Button(box, text='Cancel capture', command=app.environment_job.cancelled.set).pack(side='right',padx=6)
    ttk.Button(box, text='Set capture key', command=lambda: app._show_keybinds('capture_environment')).pack(side='left')
    ttk.Label(box, text='Experimental HDR reference: animated units and effects are filtered. '
                       'Check the result for remaining attachments, shadows and seams.',
              style='CardMuted.TLabel', wraplength=760).pack(anchor='w', pady=8)


def apply_size(app):
    from dataclasses import replace
    from .replays import parse_launch_options
    if app.busy or app.playing:return False
    size=int(app.environment_size.get())
    if size not in (1024,2048,4096):raise ValueError('Unsupported panorama size')
    options=f'-windowed -noborder -w {size} -h {size}'
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
