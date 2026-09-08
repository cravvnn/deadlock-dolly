import math
import statistics
import unittest

from dolly.playback import ReplayClock


class ReplayClockTests(unittest.TestCase):
    def test_first_observation_is_required(self):
        with self.assertRaisesRegex(RuntimeError, "Observe"):
            ReplayClock(64, 1).position(0)

    def test_slow_motion_advances_between_integer_ticks(self):
        clock = ReplayClock(64, 0.1)
        clock.observe(1000, 2)
        self.assertEqual(clock.position(2), 1000)
        self.assertAlmostEqual(clock.position(2 + 1 / 60), 1000 + 6.4 / 60)
        self.assertAlmostEqual(clock.position(2 + 2 / 60), 1000 + 12.8 / 60)
        self.assertAlmostEqual(clock.position(2 + 0.1), 1000.64)

    def test_repeated_samples_do_not_restart_fractional_progress(self):
        clock = ReplayClock(64, 0.1)
        clock.observe(200, 0)
        clock.observe(200, 0.04)
        self.assertAlmostEqual(clock.position(0.04), 200.256)
        clock.observe(200, 0.1)
        self.assertAlmostEqual(clock.position(0.1), 200.64)
        clock.observe(200, 0.2)
        self.assertEqual(clock.position(0.2), 201)
        clock.observe(200, 10)
        self.assertEqual(clock.position(10), 201)

    def test_missing_samples_never_extrapolate_more_than_one_tick(self):
        for speed in (0.1, 1, 10):
            with self.subTest(speed=speed):
                clock = ReplayClock(64, speed)
                clock.observe(1234, 5)
                for now in (6, 10, 1_000_000, 1e308):
                    self.assertEqual(clock.position(now), 1235)

    def test_advancing_sample_preserves_camera_phase(self):
        clock = ReplayClock(64, 0.1)
        clock.observe(20, 1)
        self.assertAlmostEqual(clock.position(1.1), 20.64)
        before = clock.position(1.15)
        clock.observe(21, 1.15)
        self.assertEqual(clock.position(1.15), before)
        self.assertAlmostEqual(before, 20.96)
        increment = clock.position(1.2) - before
        self.assertGreaterEqual(increment, 6.4 * 0.05 * 0.8)
        self.assertLessEqual(increment, 6.4 * 0.05 * 1.2)

    def test_actual_rewind_resets_immediately_even_after_hold(self):
        clock = ReplayClock(64, 0.1)
        clock.observe(900, 0)
        self.assertEqual(clock.position(10), 901)
        clock.observe(300, 10)
        self.assertEqual(clock.position(10), 300)
        self.assertAlmostEqual(clock.position(10.05), 300.32)
        clock.observe(301, 10.1)
        self.assertAlmostEqual(clock.position(10.1), 300.64)

    def test_jittered_monotonic_samples_do_not_move_camera_backwards(self):
        clock = ReplayClock(64, 0.1)
        # Delayed responses, duplicate ticks and a multi-tick jump are all
        # possible with a console transport. The camera remains bounded.
        observations = [(0, 100), (0.02, 100), (0.14, 100), (0.18, 101),
                        (0.19, 101), (0.32, 102), (0.52, 102), (0.7, 105)]
        positions = []
        for received_at, tick in observations:
            clock.observe(tick, received_at)
            for now in (received_at, received_at + 0.005):
                position = clock.position(now)
                positions.append(position)
                self.assertLessEqual(position, tick + 1)
        self.assertEqual(positions, sorted(positions))

    def test_integer_tick_arrivals_cannot_teleport_camera_forward(self):
        clock = ReplayClock(64, 0.1)
        clock.observe(100, 0)
        for tick, received_at in ((101, 0.145), (102, 0.315), (103, 0.46), (108, 1.2)):
            before = clock.position(received_at)
            clock.observe(tick, received_at)
            self.assertEqual(clock.position(received_at), before)
            self.assertEqual(clock.observed_tick, tick)
            self.assertAlmostEqual(clock.lag_ticks, max(0, tick - before))

    def test_jittered_slow_motion_has_smooth_increments_without_drift(self):
        clock = ReplayClock(64, 0.1)
        # Simulated integer transitions around 6.4 ticks/second. This is a
        # repeatable transport-jitter scenario, not a measured game trace.
        intervals = (0.145, 0.168, 0.154, 0.158, 0.160, 0.152, 0.159, 0.154)
        arrivals = [0.0]
        for index in range(128):
            arrivals.append(arrivals[-1] + intervals[index % len(intervals)])
        positions, legacy_positions = [], []
        observed = 0
        legacy_tick, legacy_sample_time, legacy_position = None, 0.0, 0.0
        for index in range(1200):
            now = index / 60
            while observed + 1 < len(arrivals) and arrivals[observed + 1] <= now:
                observed += 1
            tick = 100 + observed
            clock.observe(tick, now)
            position = clock.position(now)
            positions.append(position)
            self.assertLessEqual(position, tick + 1)
            self.assertLess(clock.lag_ticks, 1)
            # Previous clock behavior used as a visible-shake reference:
            # restart fractional time and snap to each acknowledged integer.
            if tick != legacy_tick:
                legacy_position = max(legacy_position, tick)
                legacy_tick, legacy_sample_time = tick, now
            legacy_position = max(legacy_position, tick + min(1, (now - legacy_sample_time) * 6.4))
            legacy_positions.append(legacy_position)
        # Ignore the initial observer settling period; normal movement should
        # have no repeated tick-edge holds or jumps, and stay near nominal rate.
        increments = [b - a for a, b in zip(positions[60:], positions[61:])]
        legacy_increments = [b - a for a, b in zip(legacy_positions[60:], legacy_positions[61:])]
        nominal = 6.4 / 60
        self.assertGreater(min(increments), nominal * 0.9)
        self.assertLess(max(increments), nominal * 1.1)
        self.assertLess(statistics.pstdev(increments), statistics.pstdev(legacy_increments) * 0.2)
        self.assertLess(abs(positions[-1] - (100 + (1199 / 60) * 6.4)), 1)

    def test_delayed_coalesced_response_recovers_without_a_snap(self):
        clock = ReplayClock(64, 0.1)
        clock.observe(100, 0)
        self.assertEqual(clock.position(0.5), 101)
        clock.observe(103, 0.5)
        self.assertEqual(clock.position(0.5), 101)
        previous = 101
        for index in range(1, 121):
            now = 0.5 + index / 60
            position = clock.position(now)
            self.assertGreaterEqual(position, previous)
            self.assertLessEqual(position - previous, 6.4 / 60 * 1.2 + 1e-12)
            self.assertLessEqual(position, 104)
            previous = position
        self.assertEqual(previous, 104)

    def test_stalled_and_resumed_observations_remain_bounded(self):
        clock = ReplayClock(64, 0.1)
        clock.observe(100, 0)
        for now in (1, 10, 1000):
            clock.observe(100, now)
            self.assertEqual(clock.position(now), 101)
        clock.observe(101, 1000)
        self.assertEqual(clock.position(1000), 101)
        position = clock.position(1000.01)
        self.assertGreater(position, 101)
        self.assertLessEqual(position, 101 + 0.01 * 6.4 * 1.2)
        self.assertTrue(math.isfinite(clock.phase_error_ticks))

    def test_phase_correction_is_time_based_across_query_rates(self):
        clocks = [ReplayClock(64, 0.1), ReplayClock(64, 0.1)]
        for clock in clocks:
            clock.observe(100, 0)
            clock.observe(101, 0.15)
        for index in range(1, 11):
            clocks[0].position(0.15 + index * 0.01)
        self.assertAlmostEqual(clocks[0].position(0.25), clocks[1].position(0.25), places=10)

    def test_large_timestamps_keep_diagnostics_finite(self):
        clock = ReplayClock(64, 0.1)
        clock.observe(100, 0)
        self.assertEqual(clock.position(1e308), 101)
        self.assertTrue(math.isfinite(clock.phase_error_ticks))
        clock.observe(101, 1e308)
        self.assertEqual(clock.position(1e308), 101)
        self.assertTrue(math.isfinite(clock.phase_error_ticks))

    def test_backward_query_timestamp_does_not_rewind_camera(self):
        clock = ReplayClock(64, 0.1)
        clock.observe(10, 2)
        self.assertEqual(clock.position(1), 10)
        advanced = clock.position(2.1)
        self.assertEqual(clock.position(2.05), advanced)
        clock.observe(9, 2.2)
        self.assertEqual(clock.position(2.2), 9)

    def test_invalid_clock_configuration_is_rejected(self):
        invalid = (-1, 0, float("nan"), float("inf"), float("-inf"), True, "64", None)
        for value in invalid:
            for args in ((value, 1), (64, value)):
                with self.subTest(args=args), self.assertRaises(ValueError):
                    ReplayClock(*args)
        for args in ((1e308, 1e308), (1e-308, 1e-308), (5e-324, 1)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                ReplayClock(*args)

    def test_invalid_observation_does_not_replace_valid_sample(self):
        invalid_ticks = (-1, 0.5, 1.0, True, "12", None, math.inf, math.nan, 10**1000)
        clock = ReplayClock(64, 0.1)
        clock.observe(100, 0)
        for tick in invalid_ticks:
            with self.subTest(tick_type=type(tick).__name__), self.assertRaises(ValueError):
                clock.observe(tick, 1)
            self.assertEqual(clock.position(0), 100)

    def test_invalid_timestamps_are_rejected_for_new_and_repeated_ticks(self):
        clock = ReplayClock(64, 0.1)
        clock.observe(100, 0)
        for value in (-1, math.inf, -math.inf, math.nan, True, "2", None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    clock.observe(101, value)
                with self.assertRaises(ValueError):
                    clock.observe(100, value)
                with self.assertRaises(ValueError):
                    clock.position(value)
        self.assertEqual(clock.position(0), 100)


if __name__ == "__main__":
    unittest.main()
