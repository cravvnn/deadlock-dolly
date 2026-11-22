"""Desktop state checks: what the window offers after a session or take changes.

No game, native DLL or Tk window is used; the real poll and recorder logic run
against simulated widgets, controller and bridge.
"""
from dataclasses import replace
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


class GameExitCleanup(unittest.TestCase):
    def test_a_failed_cleanup_is_resubmitted_by_the_next_poll(self):
        session = object()
        outcomes = [RuntimeError('The previous camera worker is still stopping. Wait a moment and retry.'), True]

        def recover():
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        app = NS(controller=NS(_session=session, recover_dead_session=recover), _last_game_session=session,
                 _game_exit_shown=session, busy=False, submitted=[])

        def submit(label, operation, callback=None):
            app.submitted.append(label)
            try:
                result = operation()
            except RuntimeError:
                return True  # The worker reports the error; no callback runs.
            callback(result)
            return True
        app._submit = submit
        exited = {'game_running': False}
        DollyApp._handle_game_exit(app, exited)
        DollyApp._handle_game_exit(app, exited)
        DollyApp._handle_game_exit(app, exited)
        self.assertEqual(len(app.submitted), 2)
        self.assertIs(app._game_exit_cleanup, session)

    def test_cleanup_retries_are_bounded(self):
        session = object()

        def recover():
            raise RuntimeError('still stopping')
        app = NS(controller=NS(_session=session, recover_dead_session=recover), _last_game_session=session,
                 _game_exit_shown=session, busy=False, submitted=[])

        def submit(label, operation, callback=None):
            app.submitted.append(label)
            try:
                operation()
            except RuntimeError:
                pass
            return True
        app._submit = submit
        for _ in range(gui.GAME_EXIT_CLEANUP_ATTEMPTS + 3):
            DollyApp._handle_game_exit(app, {'game_running': False})
        self.assertEqual(len(app.submitted), gui.GAME_EXIT_CLEANUP_ATTEMPTS)


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


class SettingsLostUpdate(unittest.TestCase):
    def test_launch_completion_keeps_a_discord_toggle_made_while_it_waited(self):
        from dolly.settings import AppSettings, load_settings
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / 'settings.json'
        app = MagicMock()
        app.app_settings = AppSettings()
        app.launch_options.get.return_value = ''
        for variable in (app.game_path, app.demo_path, app.replay_folder):
            variable.get.return_value = 'chosen'
        app.camera_driver.get.return_value = 'Native (experimental)'
        app._mod_launch_options.return_value = {}
        app.controller.status.return_value = {}
        app._guard = lambda title, operation: operation()
        app._commit_settings = lambda **owned: DollyApp._commit_settings(app, **owned)
        waiting, release = threading.Event(), threading.Event()

        def start_editing(*args, **kwargs):
            waiting.set()
            release.wait(5)
            return 'started'
        app.controller.start_editing = start_editing
        submitted = []
        app._submit = lambda label, operation, callback: submitted.append((operation, callback)) or True
        with patch('dolly.settings.settings_path', return_value=path),                 patch('dolly.graphics_profiles_ui.launch_options', return_value={}):
            DollyApp._start_editing_session(app)
            operation, complete = submitted[0]
            result = []
            worker = threading.Thread(target=lambda: result.append(operation()))
            worker.start()
            self.assertTrue(waiting.wait(5))
            app.discord_presence.get.return_value = False
            DollyApp._save_discord_preference(app)
            release.set()
            worker.join(5)
            complete(result[0])
            saved = load_settings()
        app._error.assert_not_called()
        self.assertFalse(app.app_settings.discord_presence)
        self.assertFalse(saved.discord_presence)
        self.assertEqual((app.app_settings.game_path, saved.game_path), ('chosen', 'chosen'))


class Reconnect(unittest.TestCase):
    def test_connect_works_while_game_follow_still_needs_the_console(self):
        # Game Follow restores its rig through the console. With the connection
        # lost that restore cannot run, and Connect used to fail on it before
        # it ever reconnected.
        controller = Controller(lambda *args, **kwargs: None)
        controller._session = NS(pid=42, native=None, session_dir=None, command=[],
                                 process=NS(poll=lambda: None), owns_console_port=lambda: True)
        controller._console = NS(is_connected=False, close=lambda: None)
        restore = Mock(side_effect=RuntimeError('The console is disconnected.'))
        controller._follow_transaction = NS(restore=restore)
        fresh = NS(is_connected=True, connect=lambda **options: None, close=lambda: None,
                   request=lambda command, **options: '', recent_output=lambda **options: '')
        with patch('dolly.controller.ConsoleClient', lambda **options: fresh), \
                self.assertLogs('dolly', level='WARNING'):
            status = controller.connect()
        self.assertTrue(status['connected'])
        restore.assert_called_once()
        # The pending restore is kept for the next Stop / restore.
        self.assertIsNotNone(controller._follow_transaction)

    def test_connect_still_reports_a_failed_restore_on_a_live_console(self):
        controller = Controller(lambda *args, **kwargs: None)
        controller._session = NS(pid=42, native=None, session_dir=None, command=[],
                                 process=NS(poll=lambda: None), owns_console_port=lambda: True)
        controller._console = NS(is_connected=True, close=lambda: None)
        controller._follow_transaction = NS(restore=Mock(side_effect=RuntimeError('readback mismatch')))
        with self.assertRaisesRegex(RuntimeError, 'readback mismatch'):
            controller.connect()


class ConsoleClearedDuringStatus(unittest.TestCase):
    def test_status_reads_the_console_once(self):
        # The operations worker can clear _console between two reads by the poll.
        class Racing(Controller):
            reads = 0

            @property
            def _console(self):
                Racing.reads += 1
                return NS(is_connected=True) if Racing.reads == 1 else None

            @_console.setter
            def _console(self, value):
                pass
        controller = Racing(lambda *args, **kwargs: None)
        controller._session = NS(pid=42, native=None, session_dir=None, command=[],
                                 process=NS(poll=lambda: None))
        controller._alive = lambda: True
        Racing.reads = 0
        self.assertTrue(controller.status()['connected'])


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


class LaunchHideout(unittest.TestCase):
    def app(self, options):
        from dolly.settings import AppSettings
        launched, errors = [], []
        app = NS(app_settings=replace(AppSettings(), nvidia_dlss=True), errors=errors, launched=launched)
        for name, value in (('game_path', 'deadlock.exe'), ('demo_path', 'replay.dem'), ('launch_options', options),
                            ('protocol', 'VConsole'), ('camera_driver', 'Native (experimental)')):
            setattr(app, name, Var())
            getattr(app, name).set(value)
        app.allow_mod_overrides = NS(get=lambda: True)
        app.controller = NS(launch=lambda *args, **kwargs: launched.append((args, kwargs)))
        app._error = lambda title, exc: errors.append(title)
        app._submit = lambda label, operation, callback: operation()
        app._session_result = lambda result: None
        app._mod_launch_options = lambda: DollyApp._mod_launch_options(app)
        return app

    def test_launch_hideout_uses_the_saved_launch_options(self):
        app = self.app(' -windowed -w 1920 -h 1080 ')
        with patch('dolly.graphics_profiles_ui.launch_options', return_value={}):
            DollyApp._launch(app)
        self.assertEqual(app.errors, [])
        (args, kwargs), = app.launched
        self.assertEqual(args, ('deadlock.exe', 'replay.dem'))
        self.assertEqual(kwargs, {'protocol': 'vconsole', 'native': True,
                                  'launch_options': '-windowed -w 1920 -h 1080',
                                  'allow_mod_overrides': True, 'nvidia_dlss': True})
        # Options START would refuse are refused here too, before anything launches.
        app = self.app('+exec autoexec')
        with patch('dolly.graphics_profiles_ui.launch_options', return_value={}):
            DollyApp._launch(app)
        self.assertEqual((app.errors, app.launched), (['Launch options'], []))


class CaptureShortcutToggle(unittest.TestCase):
    def test_enabling_then_disabling_starts_and_stops_the_listener_without_saving(self):
        from dolly.settings import AppSettings
        settings = AppSettings()
        self.assertIsNotNone(settings.action_bindings.get('capture'))
        helpers = []

        class Helper:
            running = False

            def __init__(self, callback, game_pid, binding):
                self.binding = binding
                helpers.append(self)

            def start(self):
                self.running = True

            def stop(self):
                self.running = False

        app = NS(app_settings=settings, capture_binding=settings.capture_binding, capture_hotkey=None,
                 capture_generation=0, closed=False, busy=False, hotkey_enabled=Var(), hotkey_label=Var(),
                 status_text=Var(), errors=[], events=NS(put=lambda event: None),
                 controller=NS(game_pid=lambda: None), _capture_binding_label=lambda: 'label',
                 _refresh_bindings=lambda: None)
        app._error = lambda title, exc: app.errors.append(str(exc))
        app._submit = lambda label, operation, callback: callback(operation()) or True
        app._change_capture_listener = lambda *args: DollyApp._change_capture_listener(app, *args)
        with patch.object(gui, 'CaptureHotkey', Helper), patch.object(gui, 'save_settings') as save:
            app.hotkey_enabled.set(True)
            DollyApp._toggle_capture_hotkey(app)
            self.assertTrue(app.hotkey_enabled.get())
            self.assertIs(app.capture_hotkey, helpers[0])
            self.assertTrue(helpers[0].running)
            self.assertIn('captures a view', app.status_text.get())
            app.hotkey_enabled.set(False)
            DollyApp._toggle_capture_hotkey(app)
            self.assertFalse(app.hotkey_enabled.get())
            self.assertIsNone(app.capture_hotkey)
            self.assertFalse(helpers[0].running)
            self.assertEqual(app.status_text.get(), 'In-game capture shortcut disabled.')
        save.assert_not_called()
        self.assertIs(app.app_settings, settings)
        self.assertEqual((len(helpers), app.errors, app.capture_generation), (1, [], 2))


class ReplayTickRate(unittest.TestCase):
    def app(self, dialog_open):
        app = NS(closed=False, root=None, _error_dialog_open=dialog_open, _tick_rate_prompted=set(),
                 controller=NS(replay_tick_rate=lambda: 64), status_text=Var())
        app._valid_tick_rate = DollyApp._valid_tick_rate
        app._background_dialog = lambda *args, **options: DollyApp._background_dialog(app, *args, **options)
        return app

    def candidate(self):
        candidate = NS(tick_rate=32.0, keyframes=['key'])
        candidate.retime = lambda rate: setattr(candidate, 'tick_rate', rate)
        return candidate

    def test_an_in_game_capture_does_not_stack_the_retime_question_on_an_open_dialog(self):
        app, candidate = self.app(True), self.candidate()
        with patch.object(gui.messagebox, 'askyesno', side_effect=AssertionError('stacked dialog')):
            with self.assertRaisesRegex(ValueError, 'Close the open dialog'):
                DollyApp._resolve_replay_tick_rate(app, candidate)
        self.assertEqual(app._tick_rate_prompted, set())
        self.assertEqual(candidate.tick_rate, 32.0)
        # Once the other dialog closes, the next capture asks the question.
        app._error_dialog_open = False
        with patch.object(gui.messagebox, 'askyesno', return_value=True) as ask:
            self.assertEqual(DollyApp._resolve_replay_tick_rate(app, candidate), 64.0)
        ask.assert_called_once()
        self.assertEqual(app._tick_rate_prompted, {(64.0, 32.0)})
        self.assertFalse(app._error_dialog_open)


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
    app._stop_video_recording = lambda cancel=False, **options: DollyApp._stop_video_recording(
        app, cancel=cancel, **options)
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

    def test_a_restore_that_keeps_failing_stops_after_three_tries(self):
        # Each failed try ends in an error dialog. With the console connection
        # lost the restore cannot succeed, and retrying on every poll left the
        # user dismissing dialogs with no chance to reconnect.
        export, controller, bridge, state = recorder()
        export.start(VideoOptions(self.root / 'first.mp4'))
        state.update(state='failed', error='encoder stopped')
        controller._export_timing = {'fps': 60}
        app = video_app(export, controller)
        app._submit = lambda label, operation, callback=None: app.submitted.append(label) or True
        lost = {**READY, 'connected': False}
        for _ in range(8):
            DollyApp._refresh_video(app, lost)
        self.assertEqual(len(app.submitted), 3)
        # Reconnecting is a new chance for the restore.
        DollyApp._refresh_video(app, dict(READY))
        self.assertEqual(len(app.submitted), 4)

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

    def test_movie_audio_is_let_go_without_retries_once_the_game_has_exited(self):
        # The engine's movie recording ended with the game; nothing can confirm
        # its stop any more, so the unconfirmed-stop retries must not start.
        export, controller, bridge, state = recorder()
        export.start(VideoOptions(self.root / 'first.mp4'))
        cancel = Mock(side_effect=RuntimeError('Deadlock closed while Dolly was running a console command.'))
        audio = NS(movie=NS(started=True, finished=False), cancel=cancel)
        done = []
        app = video_app(export, controller, clip_audio=audio, _video_operation_done=done.append)
        closed = {**READY, 'game_running': False, 'connected': False}
        controller.status.return_value = closed
        with self.assertLogs('dolly', level='WARNING'):
            for _ in range(3):
                DollyApp._refresh_video(app, closed)
                if done:
                    app._last_video_state = done[-1]['state']
        cancel.assert_called_once()
        self.assertIsNone(app.clip_audio)
        self.assertEqual([status['state'] for status in done], ['failed'])
        self.assertFalse(gui.movie_stop_held(app))

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

    def test_a_finish_that_keeps_failing_stops_after_three_tries(self):
        # Disconnect pressed during a fixed-step take: the take completes but
        # its timing cannot be restored, so every automatic Finish fails.
        export = NS(status=lambda: {'state': 'completed'}, finish_pending=True)
        controller = NS(_pov_active=False, _export_resolution=None, _export_timing={'fps': 60})
        app = video_app(export, controller, finishes=[])
        app._stop_video_recording = lambda cancel=False: app.finishes.append(cancel)
        lost = {**READY, 'connected': False}
        for _ in range(8):
            DollyApp._refresh_video(app, lost)
        self.assertEqual(app.finishes, [False] * 3)
        DollyApp._refresh_video(app, dict(READY))
        self.assertEqual(app.finishes, [False] * 4)


class DeclinedRecording(unittest.TestCase):
    def test_declining_the_audio_helper_leaves_no_layered_run_behind(self):
        # A queued layer run that never started kept camera editing, the curve
        # editor and screenshots locked with "Finish recording before editing".
        from dolly.path import Keyframe, Project
        app = MagicMock()
        app.busy = False
        app._bone_picker_context = None
        app.project = Project(keyframes=[Keyframe(0, 0, 0, 0, 0, 0, 0), Keyframe(1, 10, 0, 0, 0, 0, 0)])
        app.video_source.get.return_value = 'Camera path'
        app.video_path.get.return_value = 'C:/takes/shot.mp4'
        app.video_fps.get.return_value = '60'
        app.video_bitrate.get.return_value = '20 Mbps'
        app.video_export_speed.get.return_value = '1'
        app.ffmpeg_path.get.return_value = ''
        app.players_scratch.get.return_value = ''
        app.player_processing.get.return_value = 'Background'
        app.video_reconstructed_audio.get.return_value = True
        app._layer_queue = app._base_capture = app._pending_auto_play = 'untouched'
        options = NS(layers=('world', 'effects'), fixed_step=True, speed=1.0, fps=60,
                     path=Path('C:/takes/shot.mp4'))
        with patch.object(gui, 'VideoOptions', return_value=NS(validated=lambda: options)), \
                patch.object(gui, 'resolve_container', return_value='mp4'), \
                patch.object(gui, 'focus_window'), patch.object(gui, 'find_viewer', return_value=None), \
                patch.object(gui, 'ask_reconstructed_helper', return_value=False):
            DollyApp._start_video_recording(app)
        app._submit.assert_not_called()
        self.assertEqual((app._layer_queue, app._base_capture, app._pending_auto_play),
                         ('untouched',) * 3)


class TakeAutoFinish(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='dolly-session-state-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

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

    def test_cancelled_players_processing_does_not_report_a_saved_video(self):
        # The cancelled step is not the recorder's state; the next poll used to
        # see the last finished capture as new and log it as a saved video.
        export = NS(status=lambda: {'state': 'completed'}, finish_pending=False,
                    output_path=self.root / 'take' / 'players' / 'players.mov')
        controller = NS(_pov_active=False, _export_resolution=None, _export_timing=None)
        logged = []
        app = video_app(export, controller, _last_video_state='completed', _pending_auto_play=False,
                        _ae_camera_take=None, _usd_camera_take=None, _still_run=None,
                        _base_capture=None, _active_layer_take=None, _pending_capture=None,
                        _pending_combine=None, _bone_take_project=None,
                        layer_pipeline=NS(completed=lambda status: False, failed=lambda: False),
                        _export_camera=lambda *args: None, _next_take_directory=lambda: self.root,
                        video_path=Var(), project=NS(name='shot'), _log=logged.append)
        app._video_operation_done = lambda status: DollyApp._video_operation_done(app, status)
        app._video_operation_done({'state': 'cancelled', 'layer': 'players',
                                   'reason': 'processing_cancelled'})
        DollyApp._refresh_video(app, dict(READY))
        self.assertEqual(logged, [])
        self.assertEqual(app.video_path.get(), '')


class RecordingInProgress(unittest.TestCase):
    """Closing Dolly or disconnecting during a take asks what to do with it."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='dolly-session-state-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def take(self, answer, **attributes):
        export, controller, bridge, state = recorder()
        bridge.stop_video.side_effect = lambda cancel: state.update(
            state='cancelled' if cancel else 'completed')
        export.start(VideoOptions(self.root / 'take.mp4'))
        app = video_app(export, controller, root=object(), _layer_queue=[('world', 'black')],
                        _pov_project=None, _bone_take_project=None,
                        _cancel_renderlock_export=lambda: None, _finish_close=Mock(),
                        _session_operation=Mock(), _when_idle=lambda action: action(), **attributes)
        app._on_close = lambda discard_confirmed=False: DollyApp._on_close(app, discard_confirmed)
        self.ask = Mock(return_value=answer)
        patcher = patch.object(gui.messagebox, 'askchoice', self.ask)
        patcher.start()
        self.addCleanup(patcher.stop)
        return app, controller, bridge, state

    def test_cancel_keeps_recording_and_leaves_dolly_open(self):
        app, controller, bridge, state = self.take(None)
        DollyApp._on_close(app)
        self.ask.assert_called_once_with(
            'Recording in progress', 'A recording is in progress. Finish it before closing?',
            'Finish', 'Discard', parent=app.root)
        self.assertEqual((app.submitted, state['state']), ([], 'recording'))
        self.assertEqual(app._layer_queue, [('world', 'black')])
        app._finish_close.assert_not_called()

    def test_finish_saves_the_take_without_its_later_passes_then_closes(self):
        app, controller, bridge, state = self.take(True)
        DollyApp._on_close(app, True)
        self.assertEqual(app.submitted, ['Finishing video recording'])
        bridge.stop_video.assert_called_once_with(cancel=False)
        self.assertEqual(app._layer_queue, [])
        app._finish_close.assert_called_once()
        self.assertTrue(app._discard_asked)

    def test_discard_ends_the_take_and_its_fixed_step_timing_then_closes(self):
        audio = NS(movie=None, cancel=Mock())
        app, controller, bridge, state = self.take(False, clip_audio=audio)
        controller._export_timing = {'fps': 60}
        controller.clear_export_timing.side_effect = lambda: setattr(controller, '_export_timing', None)
        DollyApp._on_close(app)
        self.assertEqual(app.submitted, ['Discarding recording'])
        bridge.stop_video.assert_called_once_with(cancel=True)
        controller.clear_export_timing.assert_called_once()
        audio.cancel.assert_called_once()
        app._finish_close.assert_called_once()

    def test_a_take_that_cannot_be_finished_leaves_dolly_open(self):
        app, controller, bridge, state = self.take(True)
        bridge.stop_video.side_effect = lambda cancel: state.update(state='failed', error='encoder stopped')

        def submit(label, operation, callback=None):
            # The worker reports a failure as an error and skips the callback.
            try:
                result = operation()
            except RuntimeError as exc:
                app.errors.append(str(exc))
                return True
            callback(result)
            return True
        app._submit = submit
        DollyApp._on_close(app)
        self.assertEqual(app.errors, ['encoder stopped'])
        app._finish_close.assert_not_called()

    def test_a_busy_dolly_still_only_asks_to_wait(self):
        app, controller, bridge, state = self.take(True, busy=True)
        with patch.object(gui.messagebox, 'showinfo') as wait:
            DollyApp._on_close(app)
        wait.assert_called_once()
        self.ask.assert_not_called()
        self.assertEqual(state['state'], 'recording')

    def test_closing_with_no_take_does_not_ask(self):
        app, controller, bridge, state = self.take(True)
        state.update(state='completed')
        DollyApp._on_close(app)
        self.ask.assert_not_called()
        app._finish_close.assert_called_once()

    def test_disconnect_asks_and_disconnects_after_the_take_is_finished(self):
        app, controller, bridge, state = self.take(True)
        DollyApp._disconnect(app)
        self.ask.assert_called_once_with(
            'Recording in progress', 'A recording is in progress. Finish it before disconnecting?',
            'Finish', 'Discard', parent=app.root)
        self.assertEqual(app.submitted, ['Finishing video recording'])
        app._session_operation.assert_called_once_with('Disconnecting', controller.disconnect)

    def test_disconnect_cancelled_keeps_the_connection_and_the_take(self):
        app, controller, bridge, state = self.take(None)
        DollyApp._disconnect(app)
        app._session_operation.assert_not_called()
        self.assertEqual(state['state'], 'recording')


class EarlyFinish(unittest.TestCase):
    """Finish pressed before the shot ends keeps the color take and ends the run."""

    def take(self, playing):
        export = NS(status=lambda: {'state': 'recording'}, stop=lambda cancel=False: {'state': 'completed'})
        controller = NS(status=lambda: {**READY, 'playing': playing},
                        _pov_active=False, _export_resolution=None, _export_timing=None)
        return video_app(export, controller, _pov_project=None, _bone_take_project=None,
                         _layer_queue=[('world', 'black'), ('effects', 'black'), ('effects', 'white')])

    def test_an_early_finish_on_a_camera_path_take_skips_its_remaining_passes(self):
        app = self.take(playing=True)
        DollyApp._stop_video_recording(app)
        self.assertEqual(app.submitted, ['Finishing video recording'])
        self.assertEqual(app._layer_queue, [])

    def test_the_finish_at_the_end_of_the_shot_still_records_the_passes(self):
        app = self.take(playing=False)
        app._auto_finish_layered = app._take_playing_seen = True
        DollyApp._refresh_video(app, {**READY, 'shot_finished': True})
        self.assertEqual(app.submitted, ['Finishing video recording'])
        self.assertEqual(len(app._layer_queue), 3)

    def test_a_run_that_ended_early_still_restores_the_scene(self):
        from dolly.ui.layer_pipeline import LayerPipeline
        pipeline = LayerPipeline(base_capture='base', queue=[])
        self.assertFalse(pipeline.completed({'state': 'completed'}))
        steps, audited = [], []
        pipeline.advance(busy=False, recorder_active=lambda: False,
                         submit=lambda label, function, callback: steps.append(label),
                         finish_capture=None, combine=None, start_next=None, complete=None,
                         audit=audited.append, restore=None)
        self.assertEqual((steps, audited), (['Restoring scene layers'], ['base']))


LOST_FRAMES = ('Export lost frames and was discarded to prevent a shortened or incomplete video. '
               'Use fixed-step export; if already enabled, lower the export FPS or rendering load.')


class LostFrames(unittest.TestCase):
    """A real-time take discarded for lost frames offers a fixed-step retake."""

    def failed_take(self, offer, answer):
        export = NS(status=lambda: {'state': 'failed', 'error': LOST_FRAMES, 'error_code': 0x8007000D},
                    finish_pending=False)
        controller = NS(_pov_active=False, _export_resolution=None, _export_timing=None)
        app = video_app(export, controller, closed=False, _lost_frames_offer=offer, asked=[],
                        video_fixed_step=Var(), _start_video_recording=Mock(),
                        _when_idle=lambda action: action())
        app.video_fixed_step.set(False)

        def ask(show, title, message, *, retry=True):
            app.asked.append((show, title, message, retry))
            return answer
        app._background_dialog = ask
        return app

    def report_twice(self, app):
        # The poll and the finish operation both report the same failed take.
        DollyApp._refresh_video(app, dict(READY))
        DollyApp._report_operation_error(app, 'Finishing video recording', RuntimeError(LOST_FRAMES))

    def test_yes_turns_on_fixed_step_and_records_again_after_one_question(self):
        app = self.failed_take('ready', True)
        self.report_twice(app)
        self.assertEqual(app.asked, [(
            gui.messagebox.askyesno, 'Recording lost frames',
            'This real-time take lost frames and was discarded. '
            'Turn on fixed-step export and record again?', False)])
        self.assertEqual(app.errors, [])
        self.assertTrue(app.video_fixed_step.get())
        app._start_video_recording.assert_called_once_with()

    def test_the_finish_operation_can_be_the_first_to_report_it(self):
        app = self.failed_take('ready', True)
        DollyApp._report_operation_error(app, 'Finishing video recording', RuntimeError(LOST_FRAMES))
        DollyApp._refresh_video(app, dict(READY))
        self.assertEqual((len(app.asked), app.errors), (1, []))
        app._start_video_recording.assert_called_once_with()

    def test_no_leaves_the_setting_and_the_status_text(self):
        app = self.failed_take('ready', False)
        self.report_twice(app)
        self.assertEqual((len(app.asked), app.errors), (1, []))
        self.assertFalse(app.video_fixed_step.get())
        app._start_video_recording.assert_not_called()
        self.assertEqual(app.status_text.get(), LOST_FRAMES[:240])

    def test_a_fixed_step_take_keeps_the_error(self):
        app = self.failed_take(None, True)
        DollyApp._refresh_video(app, dict(READY))
        self.assertEqual((app.asked, app.errors), ([], [LOST_FRAMES]))
        app._start_video_recording.assert_not_called()

    def test_another_failure_of_a_real_time_take_keeps_the_error(self):
        app = self.failed_take('ready', True)
        DollyApp._report_operation_error(app, 'Finishing video recording', RuntimeError('encoder stopped'))
        self.assertEqual((app.asked, app.errors), ([], ['encoder stopped']))

    def test_the_retake_is_skipped_if_a_recording_is_already_running(self):
        app = self.failed_take('ready', True)
        waiting = []
        app._when_idle = waiting.append
        DollyApp._report_operation_error(app, 'Finishing video recording', RuntimeError(LOST_FRAMES))
        app.video_export.status = lambda: {'state': 'recording'}
        waiting[0]()
        app._start_video_recording.assert_not_called()

    def started(self, fixed_step):
        from dolly.path import Keyframe, Project
        app = MagicMock()
        app.busy = False
        app._bone_picker_context = None
        app.project = Project(keyframes=[Keyframe(0, 0, 0, 0, 0, 0, 0), Keyframe(1, 10, 0, 0, 0, 0, 0)])
        app.video_source.get.return_value = 'Camera path'
        app.video_path.get.return_value = 'C:/takes/shot.mp4'
        app.video_fps.get.return_value = '60'
        app.video_bitrate.get.return_value = '20 Mbps'
        app.video_export_speed.get.return_value = '1'
        app.ffmpeg_path.get.return_value = ''
        app.players_scratch.get.return_value = ''
        app.player_processing.get.return_value = 'Background'
        app.video_game_audio.get.return_value = False
        app.video_reconstructed_audio.get.return_value = False
        options = NS(layers=(), fixed_step=fixed_step, speed=1.0, fps=60, path=Path('C:/takes/shot.mp4'))
        with patch.object(gui, 'VideoOptions', return_value=NS(validated=lambda: options)),                 patch.object(gui, 'resolve_container', return_value='mp4'),                 patch.object(gui, 'focus_window'):
            DollyApp._start_video_recording(app)
        app._submit.assert_called_once()
        return app._lost_frames_offer

    def test_only_a_real_time_take_is_marked_for_the_offer(self):
        self.assertEqual(self.started(fixed_step=False), 'ready')
        self.assertIsNone(self.started(fixed_step=True))


if __name__ == '__main__':
    unittest.main()
