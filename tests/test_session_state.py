"""Desktop state checks: what the window offers after a session or take changes.

No game, native DLL or Tk window is used; the real poll and recorder logic run
against simulated widgets, controller and bridge.
"""
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import MagicMock, Mock, patch

from dolly import gui
from dolly.controller import Controller
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

    def test_a_lost_console_connection_is_named_in_the_ready_editor(self):
        # The native editor's normal stage; without the label the window keeps
        # saying the editor is ready while every action is refused.
        for stage in ('editing_ready', 'replay_ready'):
            with self.subTest(stage=stage):
                app = polled_app({**self.session(stage), 'connected': False})
                app.session_text.set.assert_called_once_with('Disconnected — reconnect to continue')
        app = polled_app(self.session('editing_ready'))
        app.session_text.set.assert_called_once_with('Paused editor ready')


class StartupCrash(unittest.TestCase):
    """A game crash during startup is reported as the crash, with its reason."""

    def test_startup_reports_the_crash_when_the_window_cancelled_its_wait(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'engine_console.log').write_bytes(
                b'11/20 01:02:03 FATAL ERROR: Content file pak01.vpk is corrupt.\r\n')
            controller = Controller(lambda *args, **kwargs: None)
            controller._crash_suffix = lambda: ''
            cancel = threading.Event()
            crashed = NS(pid=42, native=None, session_dir=Path(folder), command=[],
                         process=NS(poll=lambda: 1), owns_console_port=lambda: False)

            def launch(*args, **kwargs):
                # Deadlock dies while loading; the window sees the exit first
                # and cancels the wait, as its poll does.
                controller._session = crashed
                cancel.set()
            controller.launch = launch
            with self.assertRaises(RuntimeError) as raised:
                controller.start_editing('deadlock.exe', 'replay.dem', cancel_event=cancel)
        message = str(raised.exception)
        self.assertIn('Deadlock crashed', message)
        self.assertIn('pak01.vpk is corrupt', message)
        self.assertNotIn('cancelled', message)

    def test_a_cancelled_startup_with_the_game_open_stays_a_cancellation(self):
        controller = Controller(lambda *args, **kwargs: None)
        cancel = threading.Event()
        running = NS(pid=42, native=None, session_dir=Path('x'), command=[],
                     process=NS(poll=lambda: None), owns_console_port=lambda: False)

        def launch(*args, **kwargs):
            controller._session = running
            cancel.set()
        controller.launch = launch
        with self.assertRaisesRegex(RuntimeError, 'Editing startup cancelled'):
            controller.start_editing('deadlock.exe', 'replay.dem', cancel_event=cancel)
        self.assertEqual(controller.status()['startup_stage'], 'cancelled')

    def exited(self, stage):
        session, cancel = object(), threading.Event()
        app = NS(controller=NS(_session=session, recover_dead_session=None), _last_game_session=session,
                 startup_cancel=cancel, busy=True, root=MagicMock())
        DollyApp._handle_game_exit(app, {'game_running': False, 'startup_stage': stage})
        self.assertTrue(cancel.is_set())
        polled = polled_app({'game_running': False, 'startup_stage': stage, 'message': ''},
                            startup_cancel=cancel, _closing=False,
                            _startup_crash_cancel=getattr(app, '_startup_crash_cancel', None),
                            _handle_game_exit=lambda status: None)
        polled.operations.pending_events.return_value = [
            ('error', 'Starting replay editor', RuntimeError('Deadlock crashed (exit code 1)'))]
        with patch.object(gui, 'editor_session'):
            DollyApp._poll_once(polled)
        return polled

    def test_a_crash_during_startup_is_shown_as_an_error_not_a_cancel(self):
        polled = self.exited('failed')
        polled._report_operation_error.assert_called_once()
        self.assertIsNone(polled.startup_cancel)

    def test_closing_the_game_during_startup_stays_quiet(self):
        polled = self.exited('game_closed')
        polled._report_operation_error.assert_not_called()
        polled.status_text.set.assert_any_call('Startup cancelled. You can choose a replay and try again.')


class SecondLaunch(unittest.TestCase):
    """Play replay pressed while a session is running must leave that session usable."""

    def test_a_refused_launch_leaves_the_running_session_ready(self):
        controller = Controller(lambda *args, **kwargs: None)
        controller._session = NS(pid=42, native=None, session_dir=Path('x'), command=[],
                                 process=NS(poll=lambda: None))
        controller._state['startup_stage'] = 'editing_ready'
        controller._launch_attempt = {'status': 'started', 'demo_path': 'first.dem'}
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        second = Path(folder.name) / 'second.dem'
        second.write_bytes(b'replay')
        for start in (controller.start_editing, controller.launch):
            with self.subTest(start=start.__name__):
                with self.assertRaisesRegex(RuntimeError, 'Close the existing Dolly game session'):
                    start('deadlock.exe', str(second))
                self.assertEqual(controller.status()['startup_stage'], 'editing_ready')
                self.assertEqual(controller._launch_attempt, {'status': 'started', 'demo_path': 'first.dem'})

    def test_play_replay_with_a_running_session_keeps_its_editor_setup(self):
        from dolly.settings import AppSettings
        app = MagicMock()
        app.app_settings = AppSettings()
        app.attach_fields = {'fields': 'ready'}
        app.launch_options.get.return_value = ''
        for variable in (app.game_path, app.demo_path, app.replay_folder):
            variable.get.return_value = 'chosen'
        app.controller.status.return_value = {**READY}
        raised = []

        def guard(title, operation):
            try:
                return operation()
            except Exception as exc:
                raised.append(str(exc))
        app._guard = guard
        with patch('dolly.graphics_profiles_ui.launch_options', return_value={}):
            DollyApp._start_editing_session(app)
        self.assertEqual(raised, ['Close the existing Dolly game session before launching again.'])
        app._submit.assert_not_called()
        app._disable_external_input.assert_not_called()
        self.assertEqual(app.attach_fields, {'fields': 'ready'})


class PlaybackSpeed(unittest.TestCase):
    def test_choosing_the_shown_speed_again_applies_it_again(self):
        # A take, Play or a new game session returns the replay to 1x while
        # the dropdown still shows the earlier choice; picking it again must
        # reach the game.
        speed, sent = Var(), []
        speed.set('0.5')
        controller = NS(status=lambda: {'connected': True},
                        set_playback_speed=lambda value: sent.append(value))
        app = NS(closed=False, busy=False, speed=speed, controller=controller,
                 _submit=lambda label, operation: operation() or True)
        DollyApp._apply_playback_speed(app)
        DollyApp._apply_playback_speed(app)
        self.assertEqual(sent, [0.5, 0.5])


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

    def test_audio_capture_is_cancelled_when_the_game_exits_mid_take(self):
        export, controller, bridge, state = recorder()
        export.start(VideoOptions(self.root / 'first.mp4'))
        audio = NS(movie=None, cancel=Mock())
        app = video_app(export, controller, clip_audio=audio)
        closed = {**READY, 'game_running': False, 'connected': False}
        controller.status.return_value = closed
        # The poll that first sees the exit is busy clearing the dead session;
        # the audio capture still has to be stopped once the worker is free.
        app.busy = True
        DollyApp._refresh_video(app, closed)
        audio.cancel.assert_not_called()
        app.busy = False
        DollyApp._refresh_video(app, closed)
        audio.cancel.assert_called_once()
        self.assertIsNone(app.clip_audio)
        self.assertFalse(export.finish_pending)

    def test_audio_capture_is_cancelled_when_a_failed_shot_start_discards_the_take(self):
        # A POV segment that cannot start discards its recording on the worker;
        # nothing else would stop the take's audio capture afterwards.
        export, controller, bridge, state = recorder()
        bridge.stop_video.side_effect = lambda cancel: state.update(state='cancelled')
        export.start(VideoOptions(self.root / 'first.mp4'))
        audio = NS(movie=None, cancel=Mock())
        app = video_app(export, controller, clip_audio=audio)
        export.stop(cancel=True)
        DollyApp._refresh_video(app, dict(READY))
        audio.cancel.assert_called_once()
        self.assertIsNone(app.clip_audio)
        self.assertEqual(app.errors, [])


class TakeAutoFinish(unittest.TestCase):
    def test_a_take_finishes_even_if_no_poll_saw_its_shot_playing(self):
        # A very short shot, or a window that was not polling while it ran
        # (an open error dialog), ends before any poll reads "playing".
        played = []
        export = NS(status=lambda: {'state': 'recording'}, finish_pending=False, output_path=None)
        controller = NS(play=lambda project, **options: played.append(project),
                        _pov_active=False, _export_resolution=None, _export_timing=None)
        app = video_app(export, controller, _pending_auto_play=True, _take_playing_seen=False,
                        _auto_finish_layered=True, _pov_project=None, _bone_take_project=None,
                        layer_pipeline=NS(completed=lambda status: False, failed=lambda: False),
                        speed=Var(), rate=Var(), smoothing=Var(), frozen=Var(), hide_hud=Var(),
                        _offer_replay_tick_rate=lambda: None, _snapshot=lambda: 'shot',
                        _set_time=lambda time: None, stops=[])
        app.speed.set('1'), app.rate.set('60'), app.smoothing.set('Off')
        app.frozen.set(False), app.hide_hud.set(True)
        app._guard = lambda title, function: function()
        app._play = lambda **options: DollyApp._play(app, **options)
        app._stop_video_recording = lambda cancel=False: app.stops.append(cancel)
        DollyApp._video_operation_done(app, {'state': 'recording'})
        self.assertEqual(played, ['shot'])
        finished = {**READY, 'shot_finished': True,
                    'message': 'Paused camera ready. Use the in-game movement controls.'}
        DollyApp._refresh_video(app, finished)
        self.assertEqual(app.stops, [False])


if __name__ == '__main__':
    unittest.main()
