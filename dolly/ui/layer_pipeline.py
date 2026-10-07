"""Layered-export scheduling and state, independent of Tk and engine APIs."""
from dataclasses import dataclass, field
import logging

LOG = logging.getLogger('dolly')


@dataclass
class LayerPipeline:
    pending_auto_play: bool = False
    auto_finish_layered: bool = False
    take_playing_seen: bool = False
    pending_combine: object = None
    pending_capture: object = None
    advance_pending: bool = False
    base_capture: object = None
    queue: list = field(default_factory=list)
    active_take: object = None

    def clear(self):
        self.pending_auto_play = False
        self.auto_finish_layered = False
        self.take_playing_seen = False
        self.pending_combine = None
        self.pending_capture = None
        self.advance_pending = False
        self.base_capture = None
        self.queue = []
        self.active_take = None

    def completed(self, status):
        """Record a finished take; return whether color yielded to layer takes."""
        taken = self.active_take
        if taken is not None:
            self.active_take = None
            if taken[1] == 'capture':
                self.pending_capture = taken[0]
            elif taken[1] == 'white':
                self.pending_combine = taken[0]
        self.advance_pending = True
        return (taken is None and self.base_capture is not None and self.queue
                and not status.get('master'))

    def failed(self):
        layered = self.base_capture is not None or self.queue
        self.pending_combine = None
        self.pending_capture = None
        self.advance_pending = False
        self.base_capture = None
        self.queue = []
        self.active_take = None
        return layered

    def advance(self, *, busy, recorder_active, submit, finish_capture, combine,
                start_next, complete, audit, restore, restored=None):
        if not self.advance_pending or busy:
            return
        if self.pending_capture is not None:
            layer, self.pending_capture = self.pending_capture, None
            self.advance_pending = False
            submit('Building players layer', lambda: finish_capture(layer), complete)
            return
        if self.pending_combine is not None:
            layer, self.pending_combine = self.pending_combine, None
            self.advance_pending = False
            submit('Building layer alpha', lambda: combine(layer), complete)
            return
        if self.queue:
            if recorder_active():
                return
            self.advance_pending = False
            submit('Recording layer take', start_next, complete)
            return
        self.advance_pending = False
        if self.base_capture is not None:
            audit(self.base_capture)
            self.base_capture = None
            submit('Restoring scene layers', restore, restored or (lambda _: None))


def recover_export_failure(stop_recording, stop_camera, restore_scene):
    """Run on the worker before publishing its error; retain completed files."""
    for label, restore in (('recording', stop_recording),
                           ('camera control', stop_camera),
                           ('scene layers', restore_scene)):
        try:
            restore()
        except Exception:
            LOG.exception('Could not restore %s after export failure', label)
