"""Fixed environment views and bounded diagnostic RGB captures.

Uses an already-owned, initialized controller session. The caller retains the
normal Stop/restore obligation. These readbacks do not certify empty scenery,
exposure, per-pixel camera timing or HDR and cannot form a capture.json backend.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import time

from .environment_capture import CaptureCancelled
from .environment_panorama import capture_plan
from .screenshot import extract_plate


def _check(cancelled):
    if cancelled():
        raise CaptureCancelled('Environment camera operation cancelled')


def _pose(plan, face):
    canonical = capture_plan(plan['position'], plan['tick'], face_size=plan['face_size'],
                             panorama_width=plan['panorama_width'], face_fov=plan['horizontal_fov'])
    if plan != canonical or face not in canonical['faces']:
        raise ValueError('Camera requires an unchanged canonical capture plan')
    return [*plan['position'], face['pitch'], face['yaw'], 0, (1 if plan['horizontal_fov'] == 90 else math.tan(math.radians(plan['horizontal_fov']/2)))]


def verify_view(controller, bridge, plan, pose, *, command=None):
    """Check fresh native identity, pause, tick and (when armed) camera telemetry."""
    controller._require_connection()
    if controller._native_bridge() is not bridge:
        raise RuntimeError('Environment camera session changed')
    current = bridge.status()
    controller._require_native_demo(current, allow_idle=command is None)
    if not current.get('paused') or current.get('tick') != plan['tick'] or current.get('error'):
        raise RuntimeError('Environment camera requires the same paused replay tick')
    if command is not None:
        if current.get('ack_command') != command:
            raise RuntimeError('Environment camera command was replaced')
        actual = current.get('applied_pose', [])
        fov = current.get('applied_fov')
        if (len(actual) != 7 or any(not math.isfinite(a) or abs(a-b) > 1e-4
                                   for a,b in zip(actual, pose))
                or not isinstance(fov, (float, int)) or not math.isfinite(fov)
                or abs(fov-plan['horizontal_fov']) > 1e-4):
            raise RuntimeError('Environment camera position or projection changed')
    return current


def position_face(controller, plan, face, *, cancelled=lambda: False):
    """Apply one face via normal bridge gates; leave restoration to controller.stop."""
    pose = _pose(plan, face)
    _check(cancelled)
    controller._require_connection()
    bridge = controller._native_bridge()
    if bridge is None:
        raise RuntimeError('Environment camera requires the native backend')
    current = verify_view(controller, bridge, plan, pose)
    _check(cancelled)
    ack = bridge.start_flight(current['demo_name'], pose, owner='panel',
                              environment_face=True, cancelled=cancelled)
    # Mark ownership immediately, even if subsequent verification fails.
    controller._native_active = controller._native_manual = True
    _check(cancelled)
    controller._wait_paused_native_view(bridge, ack['frame_count'], expected_tick=plan['tick'])
    _check(cancelled)
    return verify_view(controller, bridge, plan, pose, command=ack['ack_command'])


def record_face_probe(controller, plan, face, directory, ffmpeg, *,
                      cancelled=lambda: False, clock=time.monotonic, sleep=time.sleep):
    """Record exactly twelve capped frames for diagnosis, then extract frame eight.

    No replay resume, fixed-step playback, exposure or scene-class changes.
    Raw output is retained on failure. Polling is bounded to 15s, cancellation
    requests recorder shutdown. Requires a separately authorized recording plan
    when used for live testing. Does not launch a game or certify a final face.
    """
    pose = _pose(plan, face)
    _check(cancelled)
    bridge = controller._native_bridge()
    if bridge is None:
        raise RuntimeError('Environment camera requires the native backend')
    if bridge.video_status().get('state') in ('starting', 'recording', 'finalizing'):
        raise RuntimeError('Finish the current recording before an environment probe')
    ffmpeg = Path(ffmpeg).resolve(strict=True)
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    video, plate = directory/'probe.mkv', directory/'plate.png'
    result = dict(schema='dolly-environment-face-probe-v1', qualified_environment=False,
                  face=face['name'], plan=plan, color_encoding='display_srgb8',
                  frame_limit=12, selected_frame=8, status='failed')
    attempted = completed = media_replaced = False
    try:
        before = position_face(controller, plan, face, cancelled=cancelled)
        command = before['ack_command']
        _check(cancelled)
        result['before'] = before
        attempted = True
        media_ack = bridge.start_video(video, fps=60, encoder=1, codec=10, ffmpeg_path=ffmpeg,
                                       fixed_step=False, shot_only=False, max_frames=12)
        deadline = clock()+15
        while True:
            _check(cancelled)
            result['after'] = verify_view(controller, bridge, plan, pose, command=command)
            status = bridge.video_status()
            result['media'] = status
            if status.get('ack') != media_ack['ack']:
                media_replaced = True
                raise RuntimeError('Environment recorder command was replaced')
            if status.get('state') == 'completed':
                completed = True
                break
            if status.get('state') not in ('starting', 'recording', 'finalizing'):
                raise RuntimeError('Environment recorder failed: ' + str(status.get('error') or status.get('state')))
            if clock() >= deadline:
                raise TimeoutError('Environment face recording exceeded 15 seconds')
            sleep(.02)
        if (status.get('frames_written') != 12 or status.get('frames_dropped') != 0
                or status.get('error_code') or status.get('command_error')
                or status.get('width') != plan['face_size'] or status.get('height') != plan['face_size']):
            raise RuntimeError('Environment recording dimensions, frame count or delivery failed')
        _check(cancelled)
        if not video.is_file() or video.stat().st_size == 0:
            raise RuntimeError('Environment recording file is missing')
        extract_plate(ffmpeg, video, 8, plate, timeout=15)
        _check(cancelled)
        result.update(status='diagnostic_only', video=video.name, plate=plate.name)
        return result
    except Exception as error:
        result['error'] = str(error)
        raise
    finally:
        if attempted and not completed:
            try:
                if media_replaced:
                    raise RuntimeError('Recorder command changed; leave the replacement operation untouched')
                if controller._native_bridge() is not bridge:
                    raise RuntimeError('Session changed; original recorder cleanup unconfirmed')
                bridge.stop_video(cancel=True)
            except Exception as error:
                result['recorder_cleanup_error'] = str(error)
        (directory/'probe.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
