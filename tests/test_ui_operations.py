"""Queue lifecycle contracts; real worker threads, no Tk or game calls."""
import queue
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dolly.gui import DollyApp, EVENT_QUEUE_LIMIT


class OperationLifecycleTests(unittest.TestCase):
    def app(self):
        app = DollyApp.__new__(DollyApp)
        app.jobs = queue.Queue()
        app.events = queue.Queue(maxsize=EVENT_QUEUE_LIMIT)
        app.closed = False
        app.busy = False
        app.busy_text = Mock()
        app.status_text = Mock()
        app._base_capture = None
        app._dropped_logs = 0
        return app

    def test_submit_guards_keep_queue_and_busy_state_unchanged(self):
        for closed, picker, busy, message in (
            (True, True, True, None),
            (False, True, True, 'Finish or cancel Bone Picker before starting another operation.'),
            (False, False, True, 'Please wait for the current operation to finish.'),
        ):
            with self.subTest(closed=closed, picker=picker, busy=busy):
                app = self.app(); app.closed = closed; app.busy = busy
                app._bone_picker_context = object() if picker else None
                self.assertFalse(app._submit('Work', Mock()))
                self.assertTrue(app.jobs.empty())
                self.assertEqual(app.busy, busy)
                app.busy_text.set.assert_not_called()
                if message is None: app.status_text.set.assert_not_called()
                else: app.status_text.set.assert_called_once_with(message)

    def test_busy_stop_cancels_the_running_operation_and_queues_the_stop(self):
        app = self.app(); app.busy = True
        app.controller = Mock()
        app.controller.request_operation_cancel.return_value = True
        app._submit = Mock(return_value=True)
        stop = app.controller.stop
        self.assertTrue(app._session_operation('Stopping', stop))
        app.controller.request_operation_cancel.assert_called_once_with()
        app._submit.assert_called_once_with('Stopping', stop)
        app.status_text.set.assert_called_once_with('Cancelling the current operation…')

    def test_busy_stop_that_cannot_cancel_keeps_the_wait_message(self):
        app = self.app(); app.busy = True
        app.controller = Mock()
        app.controller.request_operation_cancel.return_value = False
        app._submit = Mock()
        self.assertFalse(app._session_operation('Stopping', app.controller.stop))
        app._submit.assert_not_called()
        app.status_text.set.assert_called_once_with('Please wait for the current operation to finish.')

    def test_native_effect_stall_is_reported_once_after_the_window(self):
        app = self.app()
        app.controller = Mock()
        bridge = Mock(); bridge.status.return_value = {"effect_phase": 3, "effect_frames": 10}
        app.controller._native_bridge.return_value = bridge
        status = {"playing": True, "camera_backend": "native"}
        with patch("dolly.gui.time.monotonic", side_effect=[0.0, 1.0, 30.0, 31.0, 60.0]), \
                patch("dolly.gui.LOG") as log:
            app._watch_native_effect_stall(status)
            app._watch_native_effect_stall(status)
            app._watch_native_effect_stall(status)
            log.warning.assert_called_once()
            app._watch_native_effect_stall(status)
            log.warning.assert_called_once()
            bridge.status.return_value = {"effect_phase": 4, "effect_frames": 40}
            app._watch_native_effect_stall(status)
            log.warning.assert_called_once()

    def test_idle_or_console_playback_never_watches_for_a_stall(self):
        app = self.app()
        app.controller = Mock()
        app.controller._native_bridge.return_value = Mock()
        with patch("dolly.gui.LOG") as log:
            app._watch_native_effect_stall({"playing": False, "camera_backend": "native"})
            app._watch_native_effect_stall({"playing": True, "camera_backend": "console"})
            log.warning.assert_not_called()
        self.assertIsNone(app._effect_stall)

    def test_submit_sets_busy_display_before_enqueuing_original_objects(self):
        app = self.app(); order = []
        function, callback = Mock(), Mock()
        original_put = app.jobs.put
        app.busy_text.set.side_effect = lambda value: order.append(('display', app.busy, value))
        app.jobs.put = lambda job: (order.append(('queued', app.busy)), original_put(job))[-1]
        self.assertTrue(app._submit('Work', function, callback))
        self.assertEqual(order, [('display', True, 'Work…'), ('queued', True)])
        label, submitted, completed = app.jobs.get_nowait()
        self.assertEqual(label, 'Work'); self.assertIs(submitted, function); self.assertIs(completed, callback)

    def test_success_runs_in_worker_and_delivers_callback_without_executing_it(self):
        app = self.app(); seen = []; callback = Mock(); result = object()
        caller = threading.get_ident()
        app.jobs.put(('Work', lambda: (seen.append(threading.get_ident()), result)[1], callback))
        app.jobs.put(None)
        worker = threading.Thread(target=app._worker_loop, daemon=True); worker.start(); worker.join(2)
        self.assertFalse(worker.is_alive()); self.assertNotEqual(seen, [caller])
        kind, label, payload = app.events.get_nowait()
        self.assertEqual((kind, label), ('done', 'Work'))
        self.assertIs(payload[0], result); self.assertIs(payload[1], callback)
        callback.assert_not_called()

    def test_full_event_queue_drops_logs_but_preserves_completion(self):
        app = self.app(); app.events = queue.Queue(maxsize=1); app.events.put(('log', '', 'old'))
        app._enqueue_log('new', 2); self.assertEqual(app._dropped_logs, 1)
        started = threading.Event(); result = object()
        app.jobs.put(('Work', lambda: (started.set(), result)[1], None)); app.jobs.put(None)
        worker = threading.Thread(target=app._worker_loop, daemon=True); worker.start()
        self.assertTrue(started.wait(2)); self.assertTrue(worker.is_alive())
        self.assertEqual(app.events.get(timeout=2), ('log', '', 'old'))
        worker.join(2); self.assertFalse(worker.is_alive())
        self.assertIs(app.events.get(timeout=2)[2][0], result)

    def test_export_recovery_runs_when_a_capture_actually_engaged(self):
        app = self.app(); app._base_capture = object(); order = []
        problem = ValueError('fixture')
        # A recorder that is active means the layered run really started.
        app.video_export = SimpleNamespace(status=lambda: {'state': 'recording'})
        app.controller = SimpleNamespace(status=lambda: {'playing': False})
        app._recover_export_failure = lambda: order.append('recovered')
        put = app.events.put
        app.events.put = lambda event: (order.append('published'), put(event))[-1]
        app.jobs.put(('Layer', Mock(side_effect=problem), None)); app.jobs.put(None)
        app._worker_loop()
        self.assertEqual(order, ['recovered', 'published'])
        self.assertEqual(app.events.get_nowait(), ('export_error', 'Layer', problem))

    def test_layered_preflight_failure_does_not_run_export_recovery(self):
        # A layered run that fails before any capture (for example the Players
        # disk-space preflight) has nothing to recover; it must fail fast.
        app = self.app(); app._base_capture = object(); order = []
        problem = RuntimeError('Players capture needs about 40.5 GiB')
        app.video_export = SimpleNamespace(status=lambda: {'state': 'idle'})
        app.controller = SimpleNamespace(status=lambda: {'playing': False})
        app._recover_export_failure = Mock()
        app._clear_layer_pipeline = lambda: order.append('cleared')
        put = app.events.put
        app.events.put = lambda event: (order.append('published'), put(event))[-1]
        app.jobs.put(('Preparing', Mock(side_effect=problem), None)); app.jobs.put(None)
        app._worker_loop()
        app._recover_export_failure.assert_not_called()
        self.assertIn('cleared', order)
        self.assertEqual(order[-1], 'published')
        self.assertEqual(app.events.get_nowait(), ('error', 'Preparing', problem))

    def test_failure_without_layer_does_not_recover_and_worker_keeps_processing(self):
        app = self.app(); app._recover_export_failure = Mock(); error = RuntimeError('fixture')
        app.jobs.put(('Fail', Mock(side_effect=error), None))
        app.jobs.put(('Next', lambda: 7, None)); app.jobs.put(None)
        app._worker_loop()
        self.assertEqual(app.events.get_nowait(), ('error', 'Fail', error))
        self.assertEqual(app.events.get_nowait(), ('done', 'Next', (7, None)))
        app._recover_export_failure.assert_not_called()

    def test_failed_crash_recovery_does_not_kill_operation_worker(self):
        app=self.app();error=RuntimeError('Game exited')
        app._base_capture=object()
        app.video_export=SimpleNamespace(status=Mock(side_effect=RuntimeError('Native session closed')))
        app.jobs.put(('Crash',Mock(side_effect=error),None))
        app.jobs.put(('Next',lambda:7,None));app.jobs.put(None)
        app._worker_loop()
        self.assertEqual(app.events.get_nowait(),('error','Crash',error))
        self.assertEqual(app.events.get_nowait(),('done','Next',(7,None)))

    def test_shutdown_sentinel_preserves_fifo_and_stops_before_later_jobs(self):
        app = self.app(); after = Mock()
        app.jobs.put(('Before', lambda: 1, None)); app.jobs.put(None); app.jobs.put(('After', after, None))
        app._worker_loop()
        self.assertEqual(app.events.get_nowait(), ('done', 'Before', (1, None)))
        after.assert_not_called(); self.assertEqual(app.jobs.qsize(), 1)


class ErrorDialogCoalescingTests(unittest.TestCase):
    def test_reentrant_error_does_not_stack_a_second_dialog(self):
        # Every error path funnels through _error, whose guard must reject a
        # second dialog opened while the first native dialog is still shown (its
        # nested Tk wait keeps the poll running, so reentrancy is real).
        app = DollyApp.__new__(DollyApp)
        app.closed = False
        app._error_dialog_open = False
        app._log = Mock()
        app.status_text = Mock()
        app.root = object()
        opened = []

        def fake_show(_root, title, text):
            opened.append(title)
            # A second failure arrives while the first dialog is still open.
            app._error("Second failure", RuntimeError("second"))
            app._error("Third failure", RuntimeError("third"))

        with patch("dolly.gui.dialogs.show_error", fake_show):
            app._error("First failure", RuntimeError("first"))
        self.assertEqual(opened, ["First failure"])
        self.assertFalse(app._error_dialog_open)

    def test_error_after_the_dialog_closes_shows_again(self):
        app = DollyApp.__new__(DollyApp)
        app.closed = False
        app._error_dialog_open = False
        app._log = Mock()
        app.status_text = Mock()
        app.root = object()
        opened = []
        with patch("dolly.gui.dialogs.show_error",
                   lambda _root, title, text: opened.append(title)):
            app._error("First failure", RuntimeError("first"))
            app._error("Later failure", RuntimeError("later"))
        self.assertEqual(opened, ["First failure", "Later failure"])

    def test_operation_error_routes_through_the_single_dialog_guard(self):
        app = DollyApp.__new__(DollyApp)
        app.closed = False
        app._error_dialog_open = False
        app._log = Mock()
        app.status_text = Mock()
        app.root = object()
        opened = []
        with patch("dolly.gui.dialogs.show_error",
                   lambda _root, title, text: opened.append(title)):
            app._report_operation_error("First failure", RuntimeError("first"))
            app._report_operation_error("Second failure", RuntimeError("second"))
        self.assertEqual(opened, ["First failure", "Second failure"])

