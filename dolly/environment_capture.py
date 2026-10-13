"""Backend-independent six-face capture transaction with strict readback gates.

No game access in this module. A live backend must implement snapshot, prepare,
capture and restore; its integration still needs measured replay validation.
"""
from __future__ import annotations
import hashlib
import json
import math
import re
from pathlib import Path
import uuid

from .environment_panorama import capture_plan


class CaptureCancelled(RuntimeError):
    pass


def _close(actual, expected, tolerance, label):
    if not isinstance(actual, (list, tuple)) or len(actual) != len(expected):
        raise ValueError('Missing or invalid ' + label)
    if any(type(a) not in (int, float) or not math.isfinite(a) or abs(a-b) > tolerance
           for a, b in zip(actual, expected)):
        raise ValueError('Capture readback mismatch: ' + label)


def validate_face(plan, face, result, request_id, *, rendered_reference=False):
    """Reject a frame whose reported camera, timing or visual state drifted."""
    if result.get('request_id') != request_id:
        raise ValueError('Stale capture response')
    if not isinstance(result.get('replay_sha256'), str) or not re.fullmatch('[0-9a-f]{64}', result['replay_sha256']):
        raise ValueError('Replay file identity missing')
    if not isinstance(result.get('game_build'), str) or not result['game_build'].strip():
        raise ValueError('Game build identity missing')
    _close(result.get('position'), plan['position'], 1e-4, 'optical centre')
    for axis in ('forward', 'right', 'up'):
        _close(result.get(axis), face[axis], 1e-5, axis)
    _close([result.get('horizontal_fov'), result.get('vertical_fov')], [plan['horizontal_fov'],plan['vertical_fov']], 1e-4, 'projection')
    if result.get('width') != plan['face_size'] or result.get('height') != plan['face_size']:
        raise ValueError('Rendered dimensions differ from the capture plan')
    if result.get('tick') != plan['tick']:
        raise ValueError('Replay tick changed during capture')
    if rendered_reference and (result.get('scene_filter') != 'world_classes' or
                               result.get('color_encoding') != 'rendered_hdr_float'):
        raise ValueError('Rendered reference requires the world filter and HDR source')
    if (not rendered_reference and result.get('scene_empty') is not True) or result.get('hud_hidden') is not True:
        raise ValueError('Empty-environment capture not established')
    if not isinstance(result.get('exposure_signature'), str) or not result['exposure_signature']:
        raise ValueError('Exposure state was not measured')
    if result.get('color_encoding') not in (('rendered_hdr_float',) if rendered_reference else ('display_srgb8', 'scene_linear_float')):
        raise ValueError('Unknown capture color encoding')
    value = result.get('scene_time')
    if type(value) not in (float, int) or not math.isfinite(value):
        raise ValueError('Scene time was not measured')


def capture_environment(backend, directory, position, tick, *, face_size=2048,
                        panorama_width=8192, face_fov=90, cancelled=lambda: False, rendered_reference=False):
    """Own one output directory; always restore after any prepared capture.

    Backend.capture(face, request_id, directory) returns readback metadata plus
    a file path. Backend.restore(snapshot) must verify restoration or raise.
    Raw files and the failure report are retained for diagnosis. No retries.
    """
    plan = capture_plan(position, tick, face_size=face_size, panorama_width=panorama_width, face_fov=face_fov)
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    report = dict(schema='dolly-environment-reference-v1' if rendered_reference else 'dolly-environment-capture-v1', plan=plan,
                  status='incomplete', faces=[], restored=False)
    if rendered_reference:
        report['limits']='Rendered HDR reference; world-class filter requested. No complete actor/attachment/shadow exclusion or calibrated illumination certification. Scene clock is paused replay tick.'
    def check():
        if cancelled(): raise CaptureCancelled('Environment capture cancelled')
    snapshot = None
    captured_error = None
    try:
        check()
        snapshot = backend.snapshot()
        if snapshot is None: raise ValueError('Backend did not supply a restoration snapshot')
        if not isinstance(snapshot, dict) or not snapshot.get('replay_sha256') or not snapshot.get('game_build'):
            raise ValueError('Restoration snapshot lacks replay/build identity')
        check()
        backend.prepare(plan)
        check()
        reference = None
        for face in plan['faces']:
            check()
            request = uuid.uuid4().hex
            result = backend.capture(face, request, directory)
            check()
            validate_face(plan, face, result, request, rendered_reference=rendered_reference)
            if any(result[k] != snapshot[k] for k in ('replay_sha256', 'game_build')):
                raise ValueError('Owned replay or game build changed')
            signature = (result['exposure_signature'], result['color_encoding'], result['scene_time'])
            if reference is not None and (signature[:2] != reference[:2] or abs(signature[2]-reference[2]) > 1e-6):
                raise ValueError('Exposure, color encoding or scene time changed between faces')
            reference = signature
            original_path = Path(result['file'])
            path = original_path.resolve()
            if original_path.is_symlink() or path.parent != directory or not path.is_file() or path.stat().st_size == 0:
                raise ValueError('Capture file missing or outside the owned output directory')
            if any(row['file'] == path.name for row in report['faces']):
                raise ValueError('Backend reused a previous face file')
            with path.open('rb') as stream: digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            report['faces'].append(dict(result, face=face['name'], file=path.name, sha256=digest))
        check()
        report['status'] = 'captured'
    except Exception as error:
        captured_error = error
        report['error'] = str(error)
        report['status'] = 'cancelled' if isinstance(error, CaptureCancelled) else 'failed'
    finally:
        if snapshot is not None:
            try:
                backend.restore(snapshot)
                report['restored'] = True
            except Exception as error:
                report['restoration_error'] = str(error)
                report['status'] = 'restoration_failed'
                if captured_error is None: captured_error = error
        (directory/'capture.json').write_text(json.dumps(report, indent=2, allow_nan=False))
    if captured_error is not None: raise captured_error
    return report
