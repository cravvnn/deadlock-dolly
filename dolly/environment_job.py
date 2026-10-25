"""One user capture request: six verified faces, restoration, then assembly."""
from pathlib import Path
import threading
import uuid

from .environment_capture import capture_environment, CaptureCancelled
from .environment_assemble import assemble_hdr_capture, assemble_capture


class PanoramaJob:
    def __init__(self):
        self._lock = threading.Lock()
        self.cancelled = threading.Event()

    def run(self, backend, parent, position, tick, *, face_size=1024, face_fov=90, ffmpeg=None, rendered_reference=False):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError('A panorama capture is already running')
        try:
            parent = Path(parent).expanduser().resolve(strict=True)
            if not parent.is_dir():
                raise ValueError('Choose an existing capture folder')
            folder = parent/('panorama-'+uuid.uuid4().hex)
            report = capture_environment(backend, folder, position, tick,
                face_size=face_size, panorama_width=face_size*4, face_fov=face_fov,
                cancelled=self.cancelled.is_set, rendered_reference=rendered_reference)
            if self.cancelled.is_set():
                raise CaptureCancelled('Panorama assembly cancelled; captured faces retained')
            hdr = report['faces'][0]['color_encoding'] in ('scene_linear_float','rendered_hdr_float')
            output = folder/('panorama.exr' if hdr else 'panorama.png')
            if hdr:
                assemble_hdr_capture(folder, output, rendered_reference=rendered_reference,
                                     cancelled=self.cancelled.is_set)
            else:
                if ffmpeg is None:
                    raise RuntimeError('PNG assembly needs FFmpeg; captured faces retained')
                assemble_capture(folder, output, ffmpeg)
            return output
        finally:
            self._lock.release()
