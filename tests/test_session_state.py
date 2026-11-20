"""Desktop state checks: what the window offers after a session or take changes.

No game, native DLL or Tk window is used; the real poll and recorder logic run
against simulated widgets, controller and bridge.
"""
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import MagicMock, Mock, patch

from dolly import gui
from dolly.gui import DollyApp
from dolly.video_export import VideoExport, VideoOptions

READY = {'connected': True, 'game_running': True, 'camera_backend': 'native',
         'startup_stage': 'editing_ready', 'playing': False}


def polled_app(status, **attributes):
    """Run one real interface poll against recording widgets."""
    app = MagicMock()
    app.closed = app.busy = app.playing = app.dragging = False
    app.startup_cancel = None
    app.operations.pending_events.return_value = []
    app.capture_buttons = []
    app.controller.status.return_value = status
    for name, value in attributes.items():
        setattr(app, name, value)
    with patch.object(gui, 'editor_session'):
        DollyApp._poll_once(app)
    return app


def button_state(button):
    return button.configure.call_args.kwargs['state']


class StartupControls(unittest.TestCase):
    def session(self, stage):
        return {'connected': True, 'unlocker_ready': True, 'game_running': True, 'playing': False,
                'startup_stage': stage, 'camera_backend': 'native', 'message': ''}

    def test_camera_check_stays_available_after_a_cancelled_replay_preparation(self):
        # Cancelling replay preparation tells the user to check camera support
        # again; that button has to be usable in the stage the cancel leaves.
        for stage in ('cancelled', 'failed', 'loading_replay', 'replay_ready'):
            with self.subTest(stage=stage):
                app = polled_app(self.session(stage))
                self.assertEqual(button_state(app.probe_button), 'normal')
        app = polled_app(self.session('unlocker_ready'))
        self.assertEqual(button_state(app.probe_button), 'disabled')


class Widget:
    def __init__(self):
        self.options = {'state': 'disabled', 'text': ''}

    def cget(self, name):
        return self.options[name]

    def configure(self, **options):
        self.options.update(options)


class Var:
    value = ''

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def recorder():
    """A real VideoExport over a simulated controller and native recorder."""
    state = {'state': 'idle', 'ack': 0}
    bridge, controller = Mock(), Mock()
    controller._session, controller._demo = object(), 'replay.dem'
    controller._export_timing = controller._export_resolution = None
    controller._pov_active = False
    controller.status.return_value = dict(READY)
    controller._native_bridge.return_value = bridge
    bridge.video_status.side_effect = lambda: dict(state)
    bridge.start_video.side_effect = lambda path, **options: state.update(state='recording', ack=state['ack'] + 1)
    return VideoExport(controller), controller, bridge, state


def video_app(export, controller, **attributes):
    """The attributes DollyApp._refresh_video reads, with a worker that runs inline."""
    app = NS(video_export=export, controller=controller, busy=False, playing=False, clip_audio=None,
             _layer_queue=[], _auto_finish_layered=False, _last_video_state='recording',
             video_status_text=Var(), status_text=Var(), errors=[], submitted=[],
             _log=lambda text: None, _refresh_reshade=lambda *args: None,
             _advance_layer_pipeline=lambda: None, _video_operation_done=lambda status: None)
    for name in ('video_start_button', 'video_stop_button', 'video_cancel_button', 'video_path_entry',
                 'video_browse_button', 'video_fps_combo', 'video_bitrate_combo',
                 'video_game_audio_check', 'video_reconstructed_audio_check',
                 'reshade_configure_button', 'reshade_forget_button'):
        setattr(app, name, Widget())
    app._error = lambda title, exc: app.errors.append(str(exc))

    def submit(label, operation, callback=None):
        app.submitted.append(label)
        result = operation()
        if callback:
            callback(result)
        return True
    app._submit = submit
    app._stop_video_recording = lambda cancel=False: DollyApp._stop_video_recording(app, cancel=cancel)
    for name, value in attributes.items():
        setattr(app, name, value)
    return app


class FailedTakeRelease(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='dolly-session-state-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_recording_again_works_after_the_game_exits_mid_take(self):
        export, controller, bridge, state = recorder()
        export.start(VideoOptions(self.root / 'first.mp4'))
        app = video_app(export, controller)
        closed = {**READY, 'game_running': False, 'connected': False}
        controller.status.return_value = closed
        # The poll that first sees the exit is busy clearing the dead session.
        app.busy = True
        DollyApp._refresh_video(app, closed)
        self.assertEqual(len(app.errors), 1)
        self.assertEqual(app.submitted, [])
        app.busy = False
        DollyApp._refresh_video(app, closed)
        DollyApp._refresh_video(app, closed)
        self.assertEqual(app.submitted, ['Restoring recording settings'])
        self.assertFalse(export.finish_pending)
        # The relaunched game has its own recorder.
        fresh = {'state': 'idle', 'ack': 0}
        relaunched = Mock()
        relaunched.video_status.side_effect = lambda: dict(fresh)
        relaunched.start_video.side_effect = lambda path, **options: fresh.update(state='recording', ack=1)
        bridge.video_status.side_effect = RuntimeError('The native recorder is closed.')
        controller._session = object()
        controller._native_bridge.return_value = relaunched
        controller.status.return_value = dict(READY)
        self.assertEqual(export.start(VideoOptions(self.root / 'second.mp4'))['state'], 'recording')

    def test_recording_again_works_after_the_recorder_rejects_a_start(self):
        export, controller, bridge, state = recorder()

        def rejected(path, **options):
            state.update(ack=1, command_error=1, command_message='bad dimensions')
            raise RuntimeError('native begin failed')
        bridge.start_video.side_effect = rejected
        with self.assertRaises(RuntimeError):
            export.start(VideoOptions(self.root / 'first.mp4'))
        app = video_app(export, controller, _last_video_state='idle')
        DollyApp._refresh_video(app, dict(READY))
        self.assertEqual(app.errors, ['bad dimensions'])
        self.assertEqual(app.submitted, ['Restoring recording settings'])
        self.assertFalse(export.finish_pending)
        bridge.start_video.side_effect = lambda path, **options: state.update(
            state='recording', ack=2, command_error=0)
        self.assertEqual(export.start(VideoOptions(self.root / 'second.mp4'))['state'], 'recording')

    def test_a_release_that_fails_is_not_repeated_on_every_poll(self):
        export, controller, bridge, state = recorder()
        export.start(VideoOptions(self.root / 'first.mp4'))
        state.update(state='failed', error='encoder stopped')
        app = video_app(export, controller)
        app._submit = lambda label, operation, callback=None: app.submitted.append(label) or True
        for _ in range(3):
            DollyApp._refresh_video(app, dict(READY))
        self.assertEqual(app.submitted, ['Restoring recording settings'])


if __name__ == '__main__':
    unittest.main()
