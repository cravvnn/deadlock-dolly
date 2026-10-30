"""Startup ordering uses observed scene/command readiness, never a timer guess."""
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from dolly.controller import (Controller, PreloadUnavailableError, StartupAcknowledgementRequired,
                              StartupTimeout)
from dolly.console import ConsoleTimeout
from tests.test_native_flight_controller import configured_controller


class AutoStartupTests(unittest.TestCase):
    def setUp(self):
        self.controller, self.console, self.bridge = configured_controller()
        self.session = self.controller._session
        self.controller._session = None
        self.controller._console = None
        self.controller._unlocker_pid = None
        self.console.demo_output = "Error - Not currently playing back a demo."
        self.console.complete_replay_on_send = True
        self.console.values['status'] = 'Server: Active\nClient: Connected'
        self.session.restore_gameinfo.side_effect = lambda: self.console.events.append("restore_gameinfo")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Keep an unresolved spelling on every OS: Windows runners may use an
        # 8.3 TEMP alias (RUNNER~1), which Path.resolve expands before launch.
        # A parent segment reproduces that distinction without Windows-only
        # filesystem setup; spaces also exercise the quoted console argument.
        replay_dir = Path(self.temp.name) / "Replay files"
        (replay_dir / "nested").mkdir(parents=True)
        self.demo = replay_dir / "nested" / ".." / "example.dem"
        self.demo.write_bytes(b"fixture")
        self.preload_patch = patch("dolly.controller.PreloadMonitor")
        self.preload = self.preload_patch.start().return_value
        self.addCleanup(self.preload_patch.stop)
        self.preload.identity = {"method": "test_preload"}
        self.preload.sample.return_value = {"coherent": True, "started": True,
                                            "completed": 14, "total": 14, "ready": True}
        camera_patch = patch('dolly.controller.ReplayCameraMonitor')
        self.camera_monitor = camera_patch.start().return_value
        self.addCleanup(camera_patch.stop)
        self.camera_monitor.identity = {'method': 'test_camera_handoff'}
        def camera_sample():
            self.assertGreaterEqual(self.console.tick, 2)
            self.console.tick += 1  # Natural replay progress while startup waits.
            return {'coherent': True, 'ready': True}
        self.camera_monitor.sample_startup.side_effect = camera_sample

    def start(self, **kwargs):
        with patch("dolly.controller.launcher.launch", return_value=self.session), \
             patch("dolly.controller.ConsoleClient", return_value=self.console):
            return self.controller.start_editing("installation", self.demo, **kwargs)

    def test_startup_exit_does_not_blame_a_queued_replay(self):
        self.controller._session = self.session
        self.session.process = SimpleNamespace(poll=lambda: 1)
        self.controller._replay_requested = True
        for context in ('waiting for the game console', 'waiting for hideout readiness'):
            message = self.controller._game_death_message(context)
            self.assertIn('before the selected replay was loaded', message)
            self.assertNotIn('incompatible or incomplete', message)

    def test_startup_failure_is_reported_without_an_automatic_second_launch(self):
        self.session.session_dir = Path(self.temp.name)
        (self.session.session_dir / 'game_stdout.log').write_text(
            'CSteamHTMLSurface::Init - failing, no Steam process', encoding='utf-8')
        with patch.object(self.controller, 'launch', wraps=self.controller.launch) as launch, \
             patch.object(self.controller, '_startup_wait', side_effect=RuntimeError('startup stopped')):
            with self.assertRaisesRegex(RuntimeError, 'startup stopped'):
                self.start()
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(self.controller.status()['startup_stage'], 'failed')
        self.assertFalse(any(command.startswith('playdemo ') for command in self.console.sent))

    def test_hideout_console_failure_stops_without_reconnecting_or_unlocking(self):
        with patch.object(self.controller, '_automatic_hideout_check',
                          side_effect=RuntimeError('The console is disconnected')), \
             patch.object(self.console, 'connect', wraps=self.console.connect) as connect:
            with self.assertRaisesRegex(RuntimeError, 'The console is disconnected'):
                self.start()
        self.assertEqual(connect.call_count, 1)
        self.assertNotIn('cvar_unhide', self.console.requests)
        self.assertFalse(any(command.startswith('playdemo ') for command in self.console.sent))

    def test_status_timeout_as_game_exits_reports_crash_before_replay(self):
        state = {'timeout': False, 'polls': 0}
        original_request = self.console.request

        def poll():
            if state['timeout']:
                state['polls'] += 1
                if state['polls'] >= 2:
                    return 3221226505
            return None

        def request(command, *args, **kwargs):
            if command == 'status':
                state['timeout'] = True
                raise ConsoleTimeout("No complete netcon response to 'status' within 3s.")
            return original_request(command, *args, **kwargs)

        self.session.process = SimpleNamespace(pid=1234, poll=poll)
        self.console.request = request
        with self.assertRaises(RuntimeError) as caught:
            self.start()
        message = str(caught.exception)
        self.assertIn('0xC0000409', message)
        self.assertIn('before the selected replay was loaded', message)
        self.assertIn('Console (legacy)', message)
        self.assertIn('U.GG', message)
        self.assertIn('system tray', message)
        self.assertNotIn('incompatible or incomplete', message)
        self.assertNotIn('cvar_unhide', self.console.requests)
        self.assertFalse(any(command.startswith('playdemo ') for command in self.console.sent))

    def test_console_drop_wait_can_be_cancelled_without_unlocking(self):
        class CancelOnWait:
            cancelled = False
            def is_set(self):
                return self.cancelled
            def wait(self, _seconds):
                self.cancelled = True
                return True

        self.controller._session = self.session
        checks = []

        def failed_check():
            checks.append('status')
            raise ConsoleTimeout("No complete netcon response to 'status' within 3s.")

        with self.assertRaisesRegex(RuntimeError, 'startup cancelled'):
            self.controller._startup_wait(failed_check, 'waiting for hideout readiness', CancelOnWait())
        self.assertEqual(checks, ['status'])
        self.assertNotIn('cvar_unhide', self.console.requests)
        self.assertFalse(any(command.startswith('playdemo ') for command in self.console.sent))

    def test_disconnected_console_as_process_exits_reports_actual_crash(self):
        state = {'disconnected': False, 'polls': 0}

        def poll():
            if state['disconnected']:
                state['polls'] += 1
                if state['polls'] >= 2:
                    return 3221226505
            return None

        def failed_check():
            state['disconnected'] = True
            raise RuntimeError('The console is disconnected. Use Connect after Deadlock finishes loading.')

        self.controller._session = self.session
        self.session.process = SimpleNamespace(pid=1234, poll=poll)
        with self.assertRaisesRegex(RuntimeError, '0xC0000409') as caught:
            self.controller._startup_wait(failed_check, 'waiting for hideout readiness', None)
        self.assertIn('before the selected replay was loaded', str(caught.exception))
        self.assertNotIn('cvar_unhide', self.console.requests)

    def test_console_backend_crash_does_not_suggest_relaunching_with_the_same_backend(self):
        self.controller._session = self.session
        self.session.native = None
        self.session.process = SimpleNamespace(poll=lambda: 3221226505)
        message = self.controller._game_death_message('waiting for hideout readiness')
        self.assertIn('before the selected replay was loaded', message)
        self.assertNotIn('Console (legacy)', message)
        self.assertNotIn('U.GG', message)

    def test_timeout_with_a_live_game_keeps_original_error_after_bounded_wait(self):
        class ClockEvent:
            now = 0.0
            def is_set(self):
                return False
            def wait(self, seconds):
                self.now += seconds
                return False

        clock = ClockEvent()
        self.controller._session = self.session
        calls = []

        def failed_check():
            calls.append('status')
            raise ConsoleTimeout("No complete netcon response to 'status' within 3s.")

        with patch('dolly.controller.time.monotonic', side_effect=lambda: clock.now):
            with self.assertRaisesRegex(ConsoleTimeout, 'No complete netcon response'):
                self.controller._startup_wait(failed_check, 'waiting for hideout readiness', clock)
        self.assertEqual(calls, ['status'])
        self.assertGreaterEqual(clock.now, 3.0)
        self.assertLess(clock.now, 3.1)

    def test_console_drop_never_reports_a_replacement_session_crash(self):
        controller = self.controller
        replacement = SimpleNamespace(process=SimpleNamespace(poll=lambda: 3221226505))

        class ReplaceOnWait:
            def is_set(self):
                return False
            def wait(self, _seconds):
                controller._session = replacement
                return False

        controller._session = self.session

        def failed_check():
            raise ConsoleTimeout("No complete netcon response to 'status' within 3s.")

        with self.assertRaisesRegex(ConsoleTimeout, 'No complete netcon response'):
            controller._startup_wait(failed_check, 'waiting for hideout readiness', ReplaceOnWait())

    def test_panorama_notice_keeps_native_duplicate_clear_without_changing_stages(self):
        self.controller._session = self.session
        self.bridge.configure_shader_loading = MagicMock()
        for stage in ('waiting_preload_intro', 'preloading', 'preload_ready', 'editing_ready', 'failed'):
            self.controller._message(stage, startup_stage=stage)
            self.bridge.configure_shader_loading.assert_called_with(False)
            self.assertEqual(self.controller.status()['startup_stage'], stage)

    def test_one_click_waits_for_hideout_then_unlocks_once_before_replay(self):
        result = self.start()
        events = self.console.events
        command = 'playdemo "' + self.demo.resolve().as_posix() + '"'
        self.assertEqual([event for event in events if event.startswith("playdemo ")], [command])
        self.assertEqual(events.count("cvar_unhide"), 1)
        self.assertLess(events.index("cvar_unhide"), events.index("restore_gameinfo"))
        self.assertLess(events.index("restore_gameinfo"), events.index(command))
        self.assertLess(events.index(command), events.index("native.flight"))
        self.assertLess(events.index("hideconsole"), events.index("native.flight"))
        self.assertEqual(result["startup_stage"], "editing_ready")
        self.assertTrue(result["paused_flight"])
        self.assertTrue(self.console.paused)
        evidence = self.controller._startup_evidence["automatic_readiness"]
        self.assertGreater(evidence["rendered_frame"], evidence["initial_frame"])
        self.assertIn("renderer", self.controller._startup_evidence)

    def require_startup_acknowledgement(self):
        self.preload.sample.return_value = {
            'coherent': True, 'started': False, 'completed': 10, 'total': 10,
            'intro_phase': 0, 'scheduled': False, 'job_complete': False,
            'resource_complete': True, 'job_active': False, 'ready': False}
        with patch('dolly.controller.PRELOAD_INTRO_STALL_SECONDS', 0):
            with self.assertRaises(StartupAcknowledgementRequired):
                self.start()
        self.assertEqual(self.controller.status()['startup_stage'], 'waiting_game_screen')
        self.assertFalse(any(c.startswith('playdemo ') for c in self.console.sent))

    def test_acknowledgement_rechecks_same_session_before_one_replay_dispatch(self):
        self.require_startup_acknowledgement()
        self.preload.sample.return_value = {'coherent': True, 'started': True,
            'completed': 14, 'total': 14, 'intro_phase': 3, 'ready': True}
        with patch('dolly.controller.launcher.launch', side_effect=AssertionError('No relaunch')):
            result = self.controller.resume_startup(self.demo)
        self.assertEqual(result['startup_stage'], 'editing_ready')
        self.assertEqual(self.console.events.count('cvar_unhide'), 1)
        self.assertEqual(len([c for c in self.console.sent if c.startswith('playdemo ')]), 1)
        self.assertTrue(self.controller._startup_evidence['preload']['ready'])
        self.assertEqual(self.preload.close.call_count, 2)

    def test_acknowledgement_that_was_not_dismissed_still_never_loads_replay(self):
        self.require_startup_acknowledgement()
        with patch('dolly.controller.PRELOAD_INTRO_STALL_SECONDS', 0):
            with self.assertRaises(StartupAcknowledgementRequired):
                self.controller.resume_startup(self.demo)
        self.assertEqual(self.controller.status()['startup_stage'], 'waiting_game_screen')
        self.assertFalse(any(c.startswith('playdemo ') for c in self.console.sent))
        self.preload.advance_intro.assert_not_called()

    def test_acknowledgement_resume_cancelled_before_any_new_command(self):
        self.require_startup_acknowledgement()
        sent = list(self.console.sent)
        requests = list(self.console.requests)
        cancel = threading.Event(); cancel.set()
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            self.controller.resume_startup(self.demo, cancel_event=cancel)
        self.assertEqual(self.console.sent, sent)
        self.assertEqual(self.console.requests, requests)

    def test_acknowledgement_resume_rejects_changed_replay_and_ended_session(self):
        for scenario in ('changed replay', 'ended session', 'changed pid', 'replay requested'):
            with self.subTest(scenario=scenario):
                # Each rejection starts from an actual pending startup owner.
                self.controller, self.console, self.bridge = configured_controller()
                self.session = self.controller._session
                self.controller._session = None; self.controller._console = None
                self.controller._unlocker_pid = None
                self.console.demo_output = 'Error - Not currently playing back a demo.'
                self.console.complete_replay_on_send = True
                self.console.values['status'] = 'Server: Active\nClient: Connected'
                self.require_startup_acknowledgement()
                selected = self.demo
                if scenario == 'changed replay':
                    selected = Path(self.temp.name)/'another.dem'; selected.write_bytes(b'other')
                elif scenario == 'ended session': self.session.process.poll = lambda: 0
                elif scenario == 'changed pid': self.controller._startup_evidence['pid'] = self.session.pid + 1
                else: self.controller._replay_requested = True
                sent = list(self.console.sent)
                sampled = self.preload.sample.call_count
                with patch('dolly.controller.PRELOAD_INTRO_STALL_SECONDS', 0):
                    with self.assertRaises(RuntimeError):
                        self.controller.resume_startup(selected)
                self.assertEqual(self.console.sent, sent)
                self.assertEqual(self.preload.sample.call_count, sampled)
                if scenario == 'changed replay':
                    self.assertEqual(self.controller.status()['startup_stage'], 'waiting_game_screen')

    def test_native_renderer_evidence_records_engine_answers(self):
        self.controller._startup_evidence = {}
        self.controller._request = lambda command, **kwargs: (
            "engine_rendersystem_used = -dx11 (from CL)" if command.endswith("used")
            else "engine_rendersystem_init = -dx11")
        self.assertIsNone(self.controller._record_native_renderer())
        self.assertEqual(self.controller._startup_evidence["renderer"],
                         {"used": "engine_rendersystem_used = -dx11 (from CL)",
                          "init": "engine_rendersystem_init = -dx11"})

    def test_native_renderer_evidence_warns_on_an_explicit_other_renderer(self):
        self.controller._startup_evidence = {}
        messages = []
        self.controller._message = lambda message, **kwargs: messages.append(message)
        self.controller._request = lambda command, **kwargs: "engine_rendersystem_used = -vulkan"
        warning = self.controller._record_native_renderer()
        self.assertIn("not rendering with DirectX 11", warning)
        self.assertEqual(len(messages), 1)

    def test_native_renderer_evidence_ignores_unknown_or_failed_answers(self):
        self.controller._startup_evidence = {}
        messages = []
        self.controller._message = lambda message, **kwargs: messages.append(message)
        self.controller._request = lambda command, **kwargs: ""
        self.assertIsNone(self.controller._record_native_renderer())

        def failing(command, **kwargs):
            raise RuntimeError("no console")

        self.controller._request = failing
        self.assertIsNone(self.controller._record_native_renderer())
        self.assertEqual(messages, [])
        self.assertIn("error", self.controller._startup_evidence["renderer"])

    def test_cancellation_before_launch_changes_no_game_state(self):
        event = threading.Event()
        event.set()
        with patch("dolly.controller.launcher.launch") as launch:
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                self.controller.start_editing("installation", self.demo, cancel_event=event)
        launch.assert_not_called()
        self.assertEqual(self.console.sent, [])

    def test_preload_not_started_and_equal_counts_never_dispatch_early(self):
        samples = [dict(self.preload.sample.return_value, started=False, ready=False),
                   dict(self.preload.sample.return_value, ready=False),
                   self.preload.sample.return_value]
        def observe():
            self.assertFalse(any(c.startswith('playdemo ') for c in self.console.sent))
            self.assertIn('restore_gameinfo', self.console.events)
            return samples.pop(0) if len(samples) > 1 else samples[0]
        self.preload.sample.side_effect = observe
        self.start()
        self.assertGreaterEqual(self.preload.sample.call_count, 6)
        self.preload.close.assert_called_once()
        self.assertTrue(self.controller._startup_evidence['preload']['ready'])

    def test_cancel_during_preload_closes_reader_without_loading_demo(self):
        self.console.values['citadel_hud_visible'] = 0
        cancelled = threading.Event()
        def observe():
            cancelled.set()
            return dict(self.preload.sample.return_value, started=False, ready=False)
        self.preload.sample.side_effect = observe
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            self.start(cancel_event=cancelled)
        self.preload.close.assert_called_once()
        self.assertFalse(any(c.startswith('playdemo ') for c in self.console.sent))
        self.assertEqual(self.console.values['citadel_hud_visible'], 0)
        self.assertNotIn('citadel_hud_visible', self.controller._playback_restore)

    def test_unsupported_preload_never_automatically_loads_replay(self):
        with patch('dolly.controller.PreloadMonitor', side_effect=RuntimeError('unsupported preload build')):
            with self.assertRaisesRegex(RuntimeError, 'unsupported preload'):
                self.start()
        self.assertFalse(any(c.startswith('playdemo ') for c in self.console.sent))

    def test_preload_read_failure_closes_reader_and_leaves_replay_unloaded(self):
        self.preload.sample.side_effect = RuntimeError('preload read failed')
        with self.assertRaisesRegex(RuntimeError, 'preload read failed'):
            self.start()
        self.preload.close.assert_called_once()
        self.assertFalse(any(c.startswith('playdemo ') for c in self.console.sent))

    def test_console_backend_also_verifies_preload(self):
        self.start(native=False)
        self.assertGreaterEqual(self.preload.sample.call_count, 4)
        self.assertTrue(self.controller._startup_evidence['preload']['ready'])

    def test_preload_timeout_closes_reader_and_does_not_load_replay(self):
        self.controller._session, self.controller._console = self.session, self.console
        self.console.values['citadel_hud_visible'] = 0
        self.preload.sample.return_value = dict(self.preload.sample.return_value, ready=False)
        with patch('dolly.controller.time.perf_counter', side_effect=[0, 121]):
            with self.assertRaisesRegex(RuntimeError, 'Timed out.*preload'):
                self.controller._wait_dashboard_preload(0, None)
        self.preload.close.assert_called_once()
        self.assertFalse(any(c.startswith('playdemo ') for c in self.console.sent))
        self.assertEqual(self.console.values['citadel_hud_visible'], 0)

    def test_intro_never_started_stops_early_and_records_the_manual_path(self):
        self.controller._session, self.controller._console = self.session, self.console
        self.console.values['citadel_hud_visible'] = 0
        self.preload.sample.return_value = {
            "coherent": True, "started": False, "completed": 4, "total": 4,
            "intro_phase": 0, "scheduled": False, "job_complete": False,
            "resource_complete": True, "job_active": False, "ready": False}
        with patch('dolly.controller.PRELOAD_INTRO_STALL_SECONDS', 0):
            with self.assertRaisesRegex(StartupAcknowledgementRequired, 'hideout intro has not started'):
                self.controller._wait_dashboard_preload(0, None)
        evidence = self.controller._startup_evidence['preload_unavailable']
        self.assertEqual(evidence['reason'], 'intro_never_started')
        self.assertEqual(evidence['last_sample']['intro_phase'], 0)
        trace = self.controller._startup_evidence['preload_trace']
        self.assertFalse(trace['observed_started'])
        self.assertFalse(trace['observed_intro'])
        self.assertEqual(self.controller._state['startup_stage'], 'preload_unavailable')
        self.assertFalse(any(c.startswith('playdemo ') for c in self.console.sent))
        self.assertEqual(self.console.values['citadel_hud_visible'], 0)
        self.preload.close.assert_called_once()

    def test_observed_intro_keeps_waiting_for_the_verified_gate(self):
        self.controller._session, self.controller._console = self.session, self.console
        self.preload.sample.return_value = dict(self.preload.sample.return_value,
                                                started=False, intro_phase=1, ready=False)

        def wait(check, *args, **kwargs):
            for _ in range(3):
                self.assertIsNone(check())
            raise RuntimeError('Timed out waiting for preload')

        with patch.object(self.controller, '_startup_wait', side_effect=wait):
            with patch('dolly.controller.PRELOAD_INTRO_STALL_SECONDS', 0):
                with self.assertRaisesRegex(RuntimeError, 'Timed out waiting for preload'):
                    self.controller._wait_dashboard_preload(0, None)
        self.assertTrue(self.controller._startup_evidence['preload_trace']['observed_intro'])
        self.assertNotIn('preload_unavailable', self.controller._startup_evidence)

    def test_started_preload_never_offers_the_manual_load_early(self):
        self.controller._session, self.controller._console = self.session, self.console
        self.preload.sample.return_value = dict(self.preload.sample.return_value, ready=False)

        def wait(check, *args, **kwargs):
            for _ in range(3):
                self.assertIsNone(check())
            raise RuntimeError('Timed out waiting for preload')

        with patch.object(self.controller, '_startup_wait', side_effect=wait):
            with patch('dolly.controller.PRELOAD_INTRO_STALL_SECONDS', 0):
                with self.assertRaisesRegex(RuntimeError, 'Timed out waiting for preload'):
                    self.controller._wait_dashboard_preload(0, None)
        self.assertNotIn('preload_unavailable', self.controller._startup_evidence)
        self.assertFalse(any(c.startswith('playdemo ') for c in self.console.sent))

    def test_a_live_preload_job_extends_the_stall_window_instead_of_giving_up(self):
        """A slow machine compiling one big shader batch is not a wedged preload."""
        self.controller._session, self.controller._console = self.session, self.console
        self.console.values['citadel_hud_visible'] = 0
        working = {"coherent": True, "started": True, "completed": 192, "total": 192,
                   "intro_phase": None, "scheduled": True, "job_complete": False,
                   "resource_complete": False, "job_active": True, "ready": False}
        done = {"coherent": True, "started": True, "completed": 384, "total": 384,
                "intro_phase": None, "scheduled": True, "job_complete": True,
                "resource_complete": True, "job_active": False, "ready": True}
        self.preload.sample.side_effect = [working, working] + [done] * 8
        windows = []
        messages = []
        original_message = self.controller._message

        def record_message(message, **state):
            messages.append(str(message))
            return original_message(message, **state)

        def wait(check, label, cancel, timeout=None):
            windows.append((label, timeout))
            if 'preload_extensions' not in self.controller._startup_evidence:
                for _ in range(2):
                    self.assertIsNone(check())
                raise StartupTimeout('Timed out after 120s waiting for verified map and '
                                     'shader preload completion.')
            for _ in range(8):
                result = check()
                if result:
                    return result
            raise AssertionError('the extended window never verified readiness')

        with patch.object(self.controller, '_startup_wait', side_effect=wait), \
             patch.object(self.controller, '_message', side_effect=record_message):
            self.controller._wait_dashboard_preload(0, None)
        self.assertEqual([timeout for _, timeout in windows],
                         [120.0, 120.0])
        self.assertEqual([label for label, _ in windows],
                         ['waiting for verified map and shader preload completion'] * 2)
        evidence = self.controller._startup_evidence
        self.assertEqual(evidence['preload_extensions']['count'], 1)
        self.assertEqual(evidence['preload_extensions']['last_sample']['total'], 192)
        self.assertNotIn('preload_unavailable', evidence)
        self.assertTrue(evidence['preload']['ready'])
        self.assertEqual(self.controller._state['startup_stage'], 'preload_ready')
        self.preload.close.assert_called_once()
        self.assertEqual(self.console.values['citadel_hud_visible'], 0)
        # The extension is reported while it happens, never as a silent wait.
        self.assertTrue(any('still preloading its map and shaders' in text for text in messages))
        self.assertTrue(any('192 of 192' in text for text in messages))

    def test_a_stalled_preload_offers_the_unverified_replay_with_the_last_batch(self):
        """Silence ends the wait, and the failure must stay actionable.

        A machine slower than the reviewed preload window has no other way
        forward, so this timeout raises the same error that the desktop already
        turns into an explicit "load without the preload check" choice.
        """
        self.controller._session, self.controller._console = self.session, self.console
        self.console.values['citadel_hud_visible'] = 0
        self.preload.sample.return_value = {
            "coherent": True, "started": True, "completed": 192, "total": 192,
            "intro_phase": None, "scheduled": False, "job_complete": False,
            "resource_complete": False, "job_active": False, "ready": False}

        def wait(check, label, cancel, timeout=None):
            for _ in range(2):
                self.assertIsNone(check())
            raise StartupTimeout('Timed out after 120s waiting for verified map and '
                                 'shader preload completion.')

        with patch.object(self.controller, '_startup_wait', side_effect=wait):
            with self.assertRaises(PreloadUnavailableError) as caught:
                self.controller._wait_dashboard_preload(0, None)
        message = str(caught.exception)
        self.assertIn('Timed out', message)
        self.assertIn('preload', message)
        self.assertIn('192 of 192', message)
        self.assertIn('without preload verification', message)
        evidence = self.controller._startup_evidence
        self.assertEqual(evidence['preload_unavailable']['reason'], 'preload_stalled')
        self.assertEqual(evidence['preload_unavailable']['last_sample']['total'], 192)
        self.assertNotIn('preload_extensions', evidence)
        self.assertEqual(self.controller._state['startup_stage'], 'failed')
        self.assertFalse(any(c.startswith('playdemo ') for c in self.console.sent))
        self.preload.close.assert_called_once()
        self.assertEqual(self.console.values['citadel_hud_visible'], 0)

    def test_an_unreadable_preload_never_extends_its_window(self):
        """No coherent sample means no evidence of work, so nothing is extended."""
        self.controller._session, self.controller._console = self.session, self.console
        self.preload.sample.return_value = {"coherent": False}

        def wait(check, label, cancel, timeout=None):
            for _ in range(2):
                self.assertIsNone(check())
            raise StartupTimeout('Timed out after 120s waiting for verified map and '
                                 'shader preload completion.')

        with patch.object(self.controller, '_startup_wait', side_effect=wait) as timed:
            with self.assertRaises(PreloadUnavailableError) as caught:
                self.controller._wait_dashboard_preload(0, None)
        self.assertEqual(timed.call_count, 1)
        evidence = self.controller._startup_evidence
        self.assertEqual(evidence['preload_unavailable']['reason'], 'preload_stalled')
        self.assertNotIn('preload_extensions', evidence)
        self.assertFalse(any(c.startswith('playdemo ') for c in self.console.sent))

    def test_a_preload_that_keeps_running_still_stops_at_the_absolute_ceiling(self):
        """Endless progress cannot hold startup forever without ever being ready."""
        self.controller._session, self.controller._console = self.session, self.console
        self.console.values['citadel_hud_visible'] = 0
        self.preload.sample.return_value = {
            "coherent": True, "started": True, "completed": 96, "total": 192,
            "intro_phase": None, "scheduled": True, "job_complete": False,
            "resource_complete": False, "job_active": True, "ready": False}

        def wait(check, label, cancel, timeout=None):
            for _ in range(2):
                self.assertIsNone(check())
            raise StartupTimeout('Timed out after 120s waiting for verified map and '
                                 'shader preload completion.')

        with patch.object(self.controller, '_startup_wait', side_effect=wait), \
             patch('dolly.controller.PRELOAD_MAX_SECONDS', 0):
            with self.assertRaises(PreloadUnavailableError) as caught:
                self.controller._wait_dashboard_preload(0, None)
        self.assertIn("kept running", str(caught.exception))
        evidence = self.controller._startup_evidence
        self.assertEqual(evidence['preload_unavailable']['reason'], 'preload_ceiling')
        self.assertNotIn('preload_extensions', evidence)
        self.assertEqual(self.controller._state['startup_stage'], 'failed')
        self.assertFalse(any(c.startswith('playdemo ') for c in self.console.sent))
        self.preload.close.assert_called_once()

    def test_hidden_hud_is_shown_for_preload_and_restored_before_replay(self):
        self.console.values['citadel_hud_visible'] = 0
        good = dict(self.preload.sample.return_value)

        def sample():
            self.assertEqual(self.console.values['citadel_hud_visible'], 1)
            return good

        self.preload.sample.side_effect = sample
        self.start()
        events = self.console.events
        enable = events.index('citadel_hud_visible 1')
        restore = events.index('citadel_hud_visible 0', enable)
        replay = next(i for i, cmd in enumerate(events) if cmd.startswith('playdemo '))
        self.assertLess(enable, restore)
        self.assertLess(restore, replay)
        self.assertTrue(self.controller._startup_evidence['preload_hud']['restored'])

    def test_preload_read_error_restores_hidden_hud_and_closes_monitor(self):
        self.console.values['citadel_hud_visible'] = 0
        self.preload.sample.side_effect = RuntimeError('reader failed')
        with self.assertRaisesRegex(RuntimeError, 'reader failed'):
            self.start()
        self.assertEqual(self.console.values['citadel_hud_visible'], 0)
        self.assertNotIn('citadel_hud_visible', self.controller._playback_restore)
        self.assertFalse(self.console.sent)
        self.preload.close.assert_called_once()

    def test_preload_hud_restore_failure_blocks_replay_and_keeps_recovery_value(self):
        self.console.values['citadel_hud_visible'] = 0
        self.console.fail_commands.add('citadel_hud_visible 0')
        with self.assertRaisesRegex(RuntimeError, 'Simulated console failure'):
            self.start()
        self.assertEqual(self.controller._playback_restore['citadel_hud_visible'], 0)
        self.assertFalse(self.controller._startup_evidence['preload_hud']['restored'])
        self.assertFalse(self.console.sent)
        self.preload.close.assert_called_once()

    def test_completion_must_survive_fresh_hideout_check(self):
        good = self.preload.sample.return_value
        # The sample immediately after hideout revalidation goes incomplete.
        self.preload.sample.side_effect = [good, good, good, dict(good, ready=False),
                                          good, good, good, good]
        self.start()
        self.assertEqual(self.preload.sample.call_count, 8)
        trace = self.controller._startup_evidence['preload_trace']
        self.assertEqual([row['sample']['ready'] for row in trace['changes']], [True, False, True])

    def test_preload_trace_preserves_invalidation_without_authorizing_replay(self):
        self.controller._session, self.controller._console = self.session, self.console
        active = dict(self.preload.sample.return_value, ready=False)
        invalidated = dict(active, started=False, intro_phase=0)
        self.preload.sample.side_effect = [active, invalidated, invalidated]

        def wait(check, *args, **kwargs):
            for _ in range(3):
                self.assertIsNone(check())
            raise RuntimeError('Timed out waiting for preload')

        with patch.object(self.controller, '_startup_wait', side_effect=wait):
            with self.assertRaisesRegex(RuntimeError, 'Timed out'):
                self.controller._wait_dashboard_preload(0, None)
        trace = self.controller._startup_evidence['preload_trace']
        self.assertEqual([row['sample'] for row in trace['changes']], [active, invalidated])
        self.assertTrue(trace['observed_started'])
        self.assertFalse(trace['observed_ready'])
        self.assertEqual(self.console.sent, [])
        self.preload.close.assert_called_once()

    def test_preload_trace_bounds_history_and_keeps_first_observation(self):
        self.controller._session, self.controller._console = self.session, self.console
        samples = [dict(self.preload.sample.return_value, completed=i, total=100, ready=False)
                   for i in range(70)]
        self.preload.sample.side_effect = samples

        def wait(check, *args, **kwargs):
            for _ in samples:
                self.assertIsNone(check())
            raise RuntimeError('Timed out waiting for preload')

        with patch.object(self.controller, '_startup_wait', side_effect=wait):
            with self.assertRaisesRegex(RuntimeError, 'Timed out'):
                self.controller._wait_dashboard_preload(0, None)
        trace = self.controller._startup_evidence['preload_trace']
        self.assertEqual(trace['first']['sample'], samples[0])
        self.assertEqual(len(trace['changes']), 64)
        self.assertEqual(trace['dropped_changes'], 6)
        self.assertEqual(trace['changes'][-1]['sample'], samples[-1])
        samples[0]['completed'] = -1
        self.assertEqual(trace['first']['sample']['completed'], 0)

    def test_crashed_game_during_startup_reports_exit_code_and_replay_identity(self):
        self.controller._session = self.session
        self.session.process = SimpleNamespace(poll=lambda: 3221225477)
        self.controller._replay_requested = True
        self.controller._replay_header = {"name": "9-21Routers2.dem", "build_num": 10725,
                                          "map_name": "dl_midtown"}
        with self.assertRaises(RuntimeError) as caught:
            self.controller._startup_wait(lambda: None, "waiting for the selected replay", None)
        message = str(caught.exception)
        self.assertIn("0xC0000005", message)
        self.assertIn("does not establish", message)
        self.assertIn("9-21Routers2.dem", message)
        self.assertIn("game build 10725", message)

    def test_exit_before_console_does_not_blame_replay_reconstruction(self):
        self.controller._session=self.session
        self.session.process=SimpleNamespace(poll=lambda:1)
        self.controller._replay_requested=False
        with self.assertRaises(RuntimeError) as caught:
            self.controller._startup_wait(lambda:None,"waiting for the game console",None)
        message=str(caught.exception)
        self.assertIn("before the selected replay was loaded",message)
        self.assertNotIn("incompatible",message)

    def test_clean_close_during_startup_keeps_the_short_message(self):
        self.controller._session = self.session
        self.session.process = SimpleNamespace(poll=lambda: 0)
        with self.assertRaisesRegex(RuntimeError, "Deadlock closed while waiting for the selected replay"):
            self.controller._startup_wait(lambda: None, "waiting for the selected replay", None)

    def test_same_build_crash_preserves_identity_without_diagnosing_the_file(self):
        self.controller._session = self.session
        self.session.process = SimpleNamespace(poll=lambda: 3221225477)
        self.controller._replay_requested = True
        self.controller._replay_header = {"name": "cines.dem", "build_num": 10931,
                                          "map_name": "dl_midtown"}
        self.controller._game_build = 10931
        with self.assertRaises(RuntimeError) as caught:
            self.controller._startup_wait(lambda: None, "waiting for the selected replay", None)
        message = str(caught.exception)
        self.assertIn("0xC0000005", message)
        self.assertIn("Installed game build: 10931", message)
        self.assertIn("cines.dem", message)
        self.assertIn("game build 10931", message)
        self.assertIn("Export diagnostics", message)
        self.assertNotIn("experimental repair", message)
        self.assertNotIn("likely incomplete", message)
        self.assertNotIn("cannot reconstruct", message)

    def test_different_build_crash_preserves_both_builds_without_guessing_cause(self):
        self.controller._session = self.session
        self.session.process = SimpleNamespace(poll=lambda: 3221225477)
        self.controller._replay_requested = True
        self.controller._replay_header = {"name": "old.dem", "build_num": 10725,
                                          "map_name": "dl_midtown"}
        self.controller._game_build = 10931
        with self.assertRaises(RuntimeError) as caught:
            self.controller._startup_wait(lambda: None, "waiting for the selected replay", None)
        message = str(caught.exception)
        self.assertIn("recorded on game build 10725", message)
        self.assertIn("Installed game build: 10931", message)
        self.assertNotIn("cannot reconstruct", message)
        self.assertNotIn("The selected replay was recorded on this build", message)

    def test_installed_build_is_captured_from_the_console_banner(self):
        self.controller._console = MagicMock()
        self.controller._console.recent_output.return_value = (
            'Game: "Deadlock"\r\nMap: "dl_hideout"\r\nBuild: 10931 (revision 11112755)\r\n')
        self.controller._game_build = None
        self.controller._startup_evidence = {}
        self.controller._capture_game_build()
        self.assertEqual(self.controller._game_build, 10931)
        self.assertEqual(self.controller._startup_evidence["game_build"], 10931)

    def test_installed_build_is_captured_once(self):
        self.controller._console = MagicMock()
        self.controller._console.recent_output.side_effect = AssertionError("must not re-read")
        self.controller._game_build = 10931
        self.controller._capture_game_build()
        self.assertEqual(self.controller._game_build, 10931)

    def test_native_rendered_tick_zero_does_not_allow_early_pause(self):
        self.console.goto_outputs.extend([0, 0, 1, 2])
        request = self.console.request
        pause_ticks = []

        def observe_pause(command, *args, **kwargs):
            if command == "demo_pause":
                pause_ticks.append(self.console.tick)
            return request(command, *args, **kwargs)

        self.console.request = observe_pause
        result = self.start()
        self.assertEqual(result["startup_stage"], "editing_ready")
        self.assertGreaterEqual(pause_ticks[0], 2)
        self.assertTrue(all(tick >= 2 for tick in pause_ticks))
        self.assertGreater(self.bridge.frames, 0)

    def test_cancel_initial_native_update_never_pauses_or_arms_camera(self):
        event = threading.Event()
        self.console.goto_outputs.extend([0, 0])
        request = self.console.request
        observations = 0

        def cancel_while_loading(command, *args, **kwargs):
            nonlocal observations
            result = request(command, *args, **kwargs)
            if command == "demo_goto":
                observations += 1
                if observations == 2:
                    event.set()
            return result

        self.console.request = cancel_while_loading
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            self.start(cancel_event=event)
        self.assertNotIn("demo_pause", self.console.events)
        self.assertNotIn("native.flight", self.console.events)
        self.assertFalse(any(command.startswith("demo_gototick ")
                             for command in self.console.events))

    def test_intro_camera_waits_before_pausing_and_arming(self):
        answers = iter([{'coherent': True, 'ready': False},
                        {'coherent': False, 'ready': False},
                        {'coherent': True, 'ready': True},
                        {'coherent': True, 'ready': True}])
        def sample():
            self.assertNotIn('demo_pause', self.console.events)
            self.assertNotIn('native.flight', self.console.events)
            return next(answers)
        self.camera_monitor.sample_startup.side_effect = sample
        self.start()
        self.assertEqual(self.camera_monitor.sample_startup.call_count, 4)
        # A successful handoff keeps the reviewed monitor open for the next shot
        # preparation; it is closed on disconnect, not after every wait.
        self.camera_monitor.close.assert_not_called()
        self.controller.disconnect()
        self.camera_monitor.close.assert_called_once()

    def test_replay_camera_monitor_is_reused_across_shot_preparation(self):
        def make(*_args, **_kwargs):
            built = MagicMock()
            built.identity = {'method': 'test_camera_handoff'}
            built.memory = object()  # Look like an open monitor.
            built.sample_startup.return_value = {'coherent': True, 'ready': True}
            return built
        with patch('dolly.controller.ReplayCameraMonitor', side_effect=make) as factory:
            first = self.controller._replay_camera()
            second = self.controller._replay_camera()
            self.assertIs(first, second)
            self.assertEqual(factory.call_count, 1)
            # A new session must never reuse a monitor bound to the old process.
            self.controller._session = object()
            third = self.controller._replay_camera()
            self.assertIsNot(third, first)
            self.assertEqual(factory.call_count, 2)
            first.close.assert_called()

    def test_preshow_accelerates_then_restores_normal_speed_before_pausing(self):
        # The recorded match intro is played faster through the ordinary packet
        # path, but the speed is restored to 1x before the caller pauses so the
        # editor opens at the normal rate. A seek is never used.
        with patch('dolly.controller.REPLAY_PRESHOW_TIMESCALE', 4.0):
            self.start()
        scales = [event for event in self.console.events if event.startswith('demo_timescale ')]
        self.assertEqual(scales[0], 'demo_timescale 4')
        self.assertEqual(scales[-1], 'demo_timescale 1')
        # Speed is raised for the intro, then restored to 1x before the pause so
        # the editor opens at the normal rate.
        self.assertLess(self.console.events.index(scales[0]), self.console.events.index(scales[-1]))
        self.assertLess(self.console.events.index(scales[-1]), self.console.events.index('demo_pause'))
        self.assertNotIn('demo_gototick', self.console.events)

    def test_preshow_acceleration_is_optional_when_timescale_is_rejected(self):
        original = self.console.request

        def request(command, *args, **kwargs):
            if command.startswith('demo_timescale '):
                raise RuntimeError('timescale rejected')
            return original(command, *args, **kwargs)
        self.console.request = request
        with patch('dolly.controller.REPLAY_PRESHOW_TIMESCALE', 4.0):
            self.start()  # Must still succeed without the accelerator.
        self.assertEqual(self.controller.status()['startup_stage'], 'editing_ready')

    def test_camera_ready_does_not_pause_before_stock_hud_takeover_ends(self):
        counts = iter((1, 0, 1, 0, 0))
        def sample():
            self.assertNotIn('demo_pause', self.console.events)
            self.assertNotIn('native.flight', self.console.events)
            self.console.tick += 1
            count = next(counts)
            return {'coherent': True, 'camera_ready': True,
                    'hud_takeover_count': count, 'ready': count == 0}
        self.camera_monitor.sample_startup.side_effect = sample
        self.start()
        self.assertEqual(self.camera_monitor.sample_startup.call_count, 5)
        self.assertEqual(self.controller._startup_evidence['replay_camera']['hud_takeover_count'], 0)
        self.assertTrue(self.console.paused)

    def test_replay_identity_change_during_hud_wait_never_pauses_or_arms(self):
        def sample():
            self.console.demo_name = 'another.dem'
            return {'coherent': True, 'camera_ready': True,
                    'hud_takeover_count': 1, 'ready': False}
        self.camera_monitor.sample_startup.side_effect = sample
        with self.assertRaises(RuntimeError):
            self.start()
        self.assertNotIn('demo_pause', self.console.events)
        self.assertNotIn('native.flight', self.console.events)
        self.camera_monitor.close.assert_called_once()

    def test_cancel_intro_wait_never_pauses_or_arms(self):
        cancelled = threading.Event()
        def sample():
            cancelled.set()
            return {'coherent': True, 'ready': False}
        self.camera_monitor.sample_startup.side_effect = sample
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            self.start(cancel_event=cancelled)
        self.assertNotIn('demo_pause', self.console.events)
        self.assertNotIn('native.flight', self.console.events)
        self.camera_monitor.close.assert_called_once()

    def test_camera_read_failure_closes_monitor_without_pausing(self):
        self.camera_monitor.sample_startup.side_effect = RuntimeError('changed camera build')
        with self.assertRaisesRegex(RuntimeError, 'changed camera build'):
            self.start()
        self.assertNotIn('demo_pause', self.console.events)
        self.camera_monitor.close.assert_called_once()

    def test_one_click_accepts_dotted_recording_without_reported_dem_suffix(self):
        self.demo = self.demo.with_name("practice.session.01.dem")
        self.demo.write_bytes(b"fixture")
        send = self.console.send

        def report_without_suffix(command):
            send(command)
            if command.startswith('playdemo "'):
                self.console.demo_name = self.demo.name[:-4]

        self.console.send = report_without_suffix
        result = self.start()
        self.assertEqual(result["startup_stage"], "editing_ready")
        self.assertTrue(result["paused_flight"])

    def test_unknown_or_partial_unlocker_completion_never_loads_demo(self):
        self.console.unhide_output = "Removed hidden flags from 12 cvars"
        with self.assertRaisesRegex(RuntimeError, "could not confirm"):
            self.start()
        self.assertEqual(self.console.events.count("cvar_unhide"), 1)
        self.assertEqual(self.console.sent, [])
        self.session.restore_gameinfo.assert_not_called()

    def test_cancellation_after_initialization_prevents_replay_load(self):
        event = threading.Event()
        self.session.restore_gameinfo.side_effect = event.set
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            self.start(cancel_event=event)
        self.assertEqual(self.console.events.count("cvar_unhide"), 1)
        self.assertEqual(self.console.sent, [])

    def test_socket_alone_is_not_hideout_readiness(self):
        self.controller._session = self.session
        self.controller._console = self.console
        self.bridge.advance = False
        self.bridge.frames = 0
        self.assertIsNone(self.controller._automatic_hideout_check(0))
        self.assertNotIn("cvar_unhide", self.console.events)

    def test_native_rendered_frames_wait_for_pending_hideout_prerequisites(self):
        self.controller._session = self.session
        self.controller._console = self.console
        request = self.console.request
        for status in ('Server: Inactive\nClient: Disconnected\n@ Current : levelload',
                       'map : dl_hideout\nCL: prerequisite : CWaitForGameServerStartupPrerequisite',
                       ''):
            with self.subTest(status=status):
                self.console.request = lambda cmd, *a, **kw: status if cmd == 'status' else request(cmd, *a, **kw)
                self.assertIsNone(self.controller._automatic_hideout_check(0))
                self.assertEqual(self.console.sent, [])
                self.assertNotIn('cvar_unhide', self.console.events)
        self.console.request = lambda cmd, *a, **kw: 'Client: Connected' if cmd == 'status' else request(cmd, *a, **kw)
        evidence = self.controller._automatic_hideout_check(0)
        self.assertEqual(evidence['settled_status']['method'], 'settled_status')

    def test_named_hideout_does_not_override_loading_status(self):
        self.assertIsNone(Controller._console_hideout_evidence(
            'map : dl_hideout\n@ Current : levelload', {'playing': False}))

    def test_rendered_scene_without_registered_unlocker_is_not_ready(self):
        self.controller._session = self.session
        self.controller._console = self.console
        self.console.supports = lambda name: False
        self.assertIsNone(self.controller._automatic_hideout_check(0))
        self.assertNotIn("cvar_unhide", self.console.events)

    def test_missing_console_hide_keeps_flight_disabled(self):
        self.console.supports = lambda name: name != "hideconsole"
        with self.assertRaisesRegex(RuntimeError, "hideconsole"):
            self.start()
        self.assertNotIn("native.flight", self.console.events)
        self.assertFalse(self.controller.status()["paused_flight"])

    def test_unconfirmed_console_hide_still_proceeds_on_the_dev_flood(self):
        # A -dev data dump can hide the `help hideconsole` confirmation. Unknown
        # availability is not a rejection: the explicit command is still issued
        # and startup reaches the editor (the reported "did not confirm
        # hideconsole" abort must not happen).
        self.console.supports = lambda name: None
        result = self.start()
        self.assertEqual(result["startup_stage"], "editing_ready")
        self.assertIn("hideconsole", self.console.events)
        self.assertLess(self.console.events.index("hideconsole"),
                        self.console.events.index("native.flight"))

    def test_console_command_available_treats_unknown_as_usable(self):
        self.controller._console = self.console
        self.console.supports = lambda name: None
        self.assertIsNone(self.controller._console_command_available("hideconsole"))
        self.console.supports = lambda name: False
        self.assertIs(self.controller._console_command_available("hideconsole"), False)
        self.console.supports = lambda name: True
        self.assertIs(self.controller._console_command_available("hideconsole"), True)
        # A stub without the timeout parameter is still queried safely.
        self.console.supports = lambda name: name == "hideconsole"
        self.assertIs(self.controller._console_command_available("hideconsole"), True)
        self.assertIs(self.controller._console_command_available("absent"), False)

    def test_preloaded_replay_refused_before_initialization(self):
        self.controller._session = self.session
        self.controller._console = self.console
        self.console.demo_output = None
        with self.assertRaisesRegex(RuntimeError, "before unlocker"):
            self.controller._automatic_hideout_check(0)
        self.assertNotIn("cvar_unhide", self.console.events)

    def test_wait_deadline_does_not_convert_missing_evidence_into_success(self):
        self.controller._session = self.session
        with patch("dolly.controller.time.perf_counter", side_effect=[0, 121]):
            with self.assertRaisesRegex(RuntimeError, "Timed out"):
                self.controller._startup_wait(lambda: None, "waiting for scene", None)

    def test_console_readiness_accepts_named_hideout(self):
        evidence = Controller._console_hideout_evidence(
            "map : dl_hideout\nClient:  Connected", {"playing": False})
        self.assertEqual(evidence["method"], "named_hideout_status")
        self.assertEqual(evidence["map"], "dl_hideout")

    def test_console_readiness_accepts_settled_status_without_map_name(self):
        evidence = Controller._console_hideout_evidence(
            "Server:  Inactive\nClient:  Connected", {"playing": False})
        self.assertEqual(evidence["method"], "settled_status")

    def test_console_readiness_rejects_level_load(self):
        self.assertIsNone(Controller._console_hideout_evidence(
            "Client:  Connected\n@ Current  :  levelload", {"playing": False}))
        self.assertIsNone(Controller._console_hideout_evidence(
            "CL:  prerequisite   :  .'CAsyncShaderCompilePrerequisite'", {"playing": False}))
        self.assertIsNone(Controller._console_hideout_evidence("", {"playing": False}))

    def test_extra_launch_options_forwarded_without_embedded_demo_command(self):
        with patch("dolly.controller.launcher.launch", return_value=self.session) as launch, \
             patch("dolly.controller.ConsoleClient", return_value=self.console):
            self.controller.start_editing("installation", self.demo, launch_options="-windowed -w 1280 -h 720")
        self.assertEqual(launch.call_args.kwargs["launch_options"], "-windowed -w 1280 -h 720")
        self.assertNotIn("playdemo", str(launch.call_args))

    def test_selected_graphics_profile_reaches_normal_launcher(self):
        self.session.nvidia_dlss = True
        with patch("dolly.controller.launcher.launch", return_value=self.session) as launch, \
             patch("dolly.controller.ConsoleClient", return_value=self.console) as console:
            self.controller.start_editing("installation", self.demo, graphics_profile="selected-profile-id", nvidia_dlss=True)
        self.assertEqual(launch.call_args.kwargs["graphics_profile"], "selected-profile-id")
        self.assertEqual(self.controller._launch_attempt["graphics_profile"], "selected-profile-id")
        self.assertIs(launch.call_args.kwargs['nvidia_dlss'], True)
        self.assertIs(self.controller._launch_attempt['nvidia_dlss'], True)
        self.assertEqual(console.call_args.kwargs['transcript_path'], self.session.session_dir / 'console_transcript.log')


if __name__ == "__main__":
    unittest.main()
