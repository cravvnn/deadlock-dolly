"""Frame waits preserve cancellation and recover from unsupported native timers."""

import ctypes
import threading
import unittest
from unittest.mock import Mock, patch

from dolly.pacing import FrameWait, _WindowsTimer


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class FakeTimer:
    def __init__(self, clock, on_wait=None):
        self.clock = clock
        self.on_wait = on_wait
        self.waits = []
        self.closed = 0

    def wait(self, seconds):
        self.waits.append(seconds)
        self.clock.now += seconds
        if self.on_wait:
            self.on_wait()

    def close(self):
        self.closed += 1


class PacingTests(unittest.TestCase):
    def make_native(self, timer):
        with patch("dolly.pacing.os.name", "nt"), patch(
            "dolly.pacing._WindowsTimer", return_value=timer,
        ):
            waiter = FrameWait()
        self.addCleanup(waiter.close)
        return waiter

    def test_non_windows_uses_event_once_including_zero(self):
        with patch("dolly.pacing.os.name", "posix"):
            waiter = FrameWait()
        event = Mock()
        event.wait.return_value = False
        self.assertFalse(waiter.wait(0, event))
        event.wait.assert_called_once_with(0.0)
        self.assertEqual(waiter.backend, "event_wait")
        self.assertIsNone(waiter.error)

    def test_missing_or_unsupported_native_api_falls_back(self):
        for failure in (AttributeError("No API"), OSError("Unsupported flag")):
            with self.subTest(failure=failure), patch("dolly.pacing.os.name", "nt"), patch(
                "dolly.pacing._WindowsTimer", side_effect=failure,
            ):
                waiter = FrameWait()
                event = Mock()
                event.wait.return_value = True
                self.assertTrue(waiter.wait(0.01, event))
                event.wait.assert_called_once_with(0.01)
                self.assertEqual(waiter.backend, "event_wait")
                self.assertEqual(waiter.error, str(failure))

    def test_native_waits_reach_deadline_without_oversized_chunks(self):
        clock = FakeClock()
        timer = FakeTimer(clock)
        waiter = self.make_native(timer)
        with patch("dolly.pacing.time.perf_counter", clock):
            self.assertFalse(waiter.wait(0.055, threading.Event()))
        self.assertTrue(timer.waits)
        self.assertTrue(all(0 < item <= 0.020 for item in timer.waits))
        self.assertAlmostEqual(sum(timer.waits), 0.055)
        self.assertEqual(waiter.backend, "windows_high_resolution_timer")

    def test_native_cancellation_is_seen_after_at_most_one_chunk(self):
        clock = FakeClock()
        stop = threading.Event()
        timer = FakeTimer(clock, stop.set)
        waiter = self.make_native(timer)
        with patch("dolly.pacing.time.perf_counter", clock):
            self.assertTrue(waiter.wait(10, stop))
        self.assertEqual(timer.waits, [0.020])
        self.assertAlmostEqual(clock.now, 100.020)

    def test_already_cancelled_and_zero_native_waits_do_not_arm_timer(self):
        clock = FakeClock()
        timer = FakeTimer(clock)
        waiter = self.make_native(timer)
        stop = threading.Event()
        with patch("dolly.pacing.time.perf_counter", clock):
            self.assertFalse(waiter.wait(0, stop))
            stop.set()
            self.assertTrue(waiter.wait(1, stop))
        self.assertEqual(timer.waits, [])

    def test_native_failure_waits_only_remaining_time_and_closes_once(self):
        clock = FakeClock()

        def fail():
            raise OSError("Timer failed")

        timer = FakeTimer(clock, fail)
        waiter = self.make_native(timer)
        stop = threading.Event()
        with patch("dolly.pacing.time.perf_counter", clock), patch.object(
            stop, "wait", return_value=False,
        ) as fallback:
            self.assertFalse(waiter.wait(0.050, stop))
            self.assertAlmostEqual(fallback.call_args.args[0], 0.030)
            self.assertEqual(fallback.call_count, 1)
        self.assertEqual(timer.closed, 1)
        self.assertEqual(waiter.backend, "event_wait")
        self.assertIn("Timer failed", waiter.error)
        waiter.close()
        self.assertEqual(timer.closed, 1)

    def test_custom_event_retains_its_clock_even_on_windows(self):
        clock = FakeClock()
        timer = FakeTimer(clock)
        waiter = self.make_native(timer)
        event = Mock()
        event.wait.return_value = False
        self.assertFalse(waiter.wait(0, event))
        event.wait.assert_called_once_with(0.0)
        self.assertEqual(timer.waits, [])
        self.assertEqual(timer.closed, 1)
        self.assertEqual(waiter.backend, "event_wait")

    def test_close_is_idempotent_and_wait_after_close_uses_event(self):
        timer = FakeTimer(FakeClock())
        waiter = self.make_native(timer)
        waiter.close()
        waiter.close()
        self.assertEqual(timer.closed, 1)
        event = Mock()
        event.wait.return_value = True
        self.assertTrue(waiter.wait(0, event))
        event.wait.assert_called_once_with(0.0)

    def test_invalid_durations_cannot_reach_native_api(self):
        timer = FakeTimer(FakeClock())
        waiter = self.make_native(timer)
        for duration in (-1, float("nan"), float("inf"), True, None, "1", 10**1000):
            with self.subTest(duration=str(duration)), self.assertRaises(ValueError):
                waiter.wait(duration, threading.Event())
        self.assertEqual(timer.waits, [])


class WindowsTimerTests(unittest.TestCase):
    def make_timer(self, *, creation=123):
        api = Mock()
        api.CreateWaitableTimerExW.return_value = creation
        api.SetWaitableTimer.return_value = True
        api.WaitForSingleObject.return_value = 0
        api.CloseHandle.return_value = True
        with patch("dolly.pacing.ctypes.WinDLL", return_value=api, create=True):
            timer = _WindowsTimer()
        self.addCleanup(timer.close)
        return timer, api

    def test_high_resolution_timer_uses_relative_one_shot_without_apc(self):
        timer, api = self.make_timer()
        api.CreateWaitableTimerExW.assert_called_once_with(None, None, 0x2, 0x100002)
        timer.wait(0.008)
        handle, due, period, apc, context, resume = api.SetWaitableTimer.call_args.args
        self.assertEqual(handle, 123)
        self.assertEqual(ctypes.cast(due, ctypes.POINTER(ctypes.c_longlong)).contents.value, -80_000)
        self.assertEqual((period, apc, context, resume), (0, None, None, False))
        api.WaitForSingleObject.assert_called_once_with(123, 20)

    def test_creation_failure_never_leaks_a_handle(self):
        api = Mock()
        api.CreateWaitableTimerExW.return_value = 0
        with patch("dolly.pacing.ctypes.WinDLL", return_value=api, create=True), self.assertRaises(OSError):
            _WindowsTimer()
        api.CloseHandle.assert_not_called()

    def test_timer_failure_skips_wait_and_timeout_can_return_to_caller(self):
        timer, api = self.make_timer()
        api.SetWaitableTimer.return_value = False
        with self.assertRaises(OSError):
            timer.wait(0.001)
        api.WaitForSingleObject.assert_not_called()
        api.SetWaitableTimer.return_value = True
        api.WaitForSingleObject.return_value = 258
        timer.wait(0.001)
        api.WaitForSingleObject.return_value = 0xFFFFFFFF
        with self.assertRaises(OSError):
            timer.wait(0.001)

    def test_handle_closed_exactly_once(self):
        timer, api = self.make_timer()
        timer.close()
        timer.close()
        api.CloseHandle.assert_called_once_with(123)
        with self.assertRaises(OSError):
            timer.wait(0.001)


if __name__ == "__main__":
    unittest.main()
