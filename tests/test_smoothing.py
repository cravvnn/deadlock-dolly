import math
import statistics
import unittest
from unittest.mock import patch

from dolly.smoothing import PhaseSmoother, SMOOTHING_WINDOWS, smoothing_window


class PhaseSmootherTests(unittest.TestCase):
    def test_presets_and_invalid_modes(self):
        self.assertEqual(SMOOTHING_WINDOWS,
                         {"off": 0.0, "light": 0.08, "balanced": 0.16, "strong": 0.28})
        for mode, window in SMOOTHING_WINDOWS.items():
            self.assertEqual(smoothing_window(mode), window)
        for mode in (None, True, 1, [], "", "invalid", "Balanced"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                smoothing_window(mode)

    def test_zero_window_is_exact_pass_through(self):
        smoother = PhaseSmoother(0, 100, 12)
        for now, value in ((100.001, 12.001), (100.019, 12.2), (105, 500), (105, 501)):
            self.assertEqual(smoother.update(now, value), value)
            self.assertEqual(smoother.lag, 0)
            self.assertEqual(smoother.sample_count, 1)

    def test_constant_camera_stays_exact_including_large_time_gaps(self):
        smoother = PhaseSmoother(.16, 0, 12345.75)
        for now in (0, .01, .08, .16, 1, 1_000_000):
            self.assertEqual(smoother.update(now, 12345.75), 12345.75)

    def test_constant_speed_has_half_window_delay_after_startup(self):
        for window in (.08, .16, .28):
            for speed in (.1, 1, 3):
                with self.subTest(window=window, speed=speed):
                    smoother = PhaseSmoother(window, 0, 7)
                    self.assertEqual(smoother.update(0, 7), 7)
                    for index in range(1, 361):
                        now = index / 120
                        result = smoother.update(now, 7 + now * speed)
                        if now >= window:
                            self.assertAlmostEqual(result, 7 + (now - window / 2) * speed)
                            self.assertAlmostEqual(smoother.lag, speed * window / 2)

    def test_initial_padding_eases_into_motion_without_first_frame_jump(self):
        smoother = PhaseSmoother(.2, 0, 0)
        self.assertAlmostEqual(smoother.update(.01, .01), .00025)
        self.assertAlmostEqual(smoother.update(.05, .05), .00625)
        self.assertAlmostEqual(smoother.update(.1, .1), .025)
        self.assertAlmostEqual(smoother.update(.2, .2), .1)

    def test_irregular_samples_integrate_piecewise_linear_phase(self):
        smoother = PhaseSmoother(2, 0, 0)
        self.assertAlmostEqual(smoother.update(.5, 1), .125)
        self.assertAlmostEqual(smoother.update(1.5, 2), .875)
        self.assertAlmostEqual(smoother.update(3, 2.5), 2.125)

    def test_equivalent_linear_history_is_independent_of_sample_density(self):
        for window in (.08, .16, .28):
            dense = PhaseSmoother(window, 0, 4)
            sparse = PhaseSmoother(window, 0, 4)
            for index in range(1, 501):
                now = index / 1000
                result = dense.update(now, 4 + 3 * now)
                if index in (20, 97, 251, 500):
                    self.assertAlmostEqual(result, sparse.update(now, 4 + 3 * now))

    def test_monotone_jitter_is_attenuated_without_backward_or_future_phase(self):
        raw, filtered = [], []
        smoother = PhaseSmoother(.16, 0, 0)
        for index in range(1, 1441):
            now = index / 240
            value = now + .002 * math.sin(2 * math.pi * now / .04)
            result = smoother.update(now, value)
            self.assertLessEqual(result, value)
            if filtered:
                self.assertGreaterEqual(result, filtered[-1])
            raw.append(value)
            filtered.append(result)
        # Synthetic clock ripple, not a claim about game/render jitter. The
        # positive-rate ripple spans four complete cycles in this window.
        raw_speed = [(b - a) * 240 for a, b in zip(raw[240:], raw[241:])]
        smooth_speed = [(b - a) * 240 for a, b in zip(filtered[240:], filtered[241:])]
        self.assertLess(statistics.pstdev(smooth_speed), statistics.pstdev(raw_speed) * .02)
        self.assertAlmostEqual(statistics.mean(smooth_speed), 1, places=4)

    def test_faster_and_slower_inputs_never_overshoot_or_reverse(self):
        smoother = PhaseSmoother(.28, 0, 100)
        previous = 100
        for now, raw in ((.01, 100.2), (.2, 101), (.21, 101), (.9, 150),
                         (1.1, 150), (1.8, 151), (5, 151)):
            result = smoother.update(now, raw)
            self.assertGreaterEqual(result, previous)
            self.assertLessEqual(result, raw)
            previous = result
        self.assertEqual(previous, 151)

    def test_paused_input_flushes_once_then_remains_exact(self):
        smoother = PhaseSmoother(.16, 0, 0)
        for index in range(1, 121):
            smoother.update(index / 120, index / 120)
        self.assertGreater(smoother.lag, 0)
        self.assertLess(smoother.update(1.08, 1), 1)
        self.assertEqual(smoother.update(1.2, 1), 1)
        self.assertEqual(smoother.update(10, 1), 1)

    def test_endpoint_finishes_in_finite_time_without_last_frame_snap(self):
        for window in (.08, .16, .28):
            with self.subTest(window=window):
                smoother = PhaseSmoother(window, 0, 0)
                outputs = [0]
                for index in range(1, 181):
                    now = index / 120
                    outputs.append(smoother.update(now, min(1.0, now)))
                deltas = [b - a for a, b in zip(outputs, outputs[1:])]
                self.assertEqual(outputs[-1], 1)
                self.assertLessEqual(max(deltas), 1 / 120 + 1e-12)
                ending_deltas = deltas[120:]
                self.assertTrue(all(b <= a + 1e-12 for a, b in zip(ending_deltas, ending_deltas[1:])))
                exact_after = math.ceil((1 + window) * 120) + 1
                self.assertEqual(outputs[exact_after], 1)

    def test_phase_rewind_retires_old_history(self):
        smoother = PhaseSmoother(.28, 0, 100)
        smoother.update(.1, 110)
        self.assertEqual(smoother.update(.2, 4), 4)
        self.assertEqual(smoother.sample_count, 1)
        self.assertEqual(smoother.resets, 1)
        self.assertEqual(smoother.lag, 0)
        self.assertAlmostEqual(smoother.update(.3, 4.1), 4 + .1 * .1 / (2 * .28))

    def test_explicit_reset_can_start_an_independent_clock_origin(self):
        smoother = PhaseSmoother(.16, 100, 200)
        smoother.update(101, 205)
        smoother.reset(0, 3)
        self.assertEqual(smoother.update(0, 3), 3)
        self.assertEqual(smoother.resets, 1)
        self.assertEqual(smoother.sample_count, 1)

    def test_same_timestamp_step_does_not_rewrite_prior_history(self):
        smoother = PhaseSmoother(.2, 0, 0)
        self.assertEqual(smoother.update(0, 1), 0)
        self.assertEqual(smoother.update(0, 2), 0)
        self.assertEqual(smoother.sample_count, 2)
        self.assertAlmostEqual(smoother.update(.1, 2), 1)
        before = smoother.update(.1, 3)
        self.assertAlmostEqual(before, 1)
        self.assertAlmostEqual(smoother.update(.2, 3), 2.5)
        self.assertEqual(smoother.update(.31, 3), 3)

    def test_large_counter_epoch_preserves_short_window_precision(self):
        epoch = 100_000_000
        smoother = PhaseSmoother(.16, epoch, 112_249)
        for index in range(1, 241):
            now = epoch + index / 120
            value = 112_249 + (now - epoch) * 6.4
            result = smoother.update(now, value)
            if index >= 30:
                self.assertAlmostEqual(result, value - .16 * 6.4 / 2, places=7)

    def test_extreme_finite_values_remain_finite_and_bounded(self):
        smoother = PhaseSmoother(.16, 0, 0)
        for now, value in ((.05, 1e307), (.1, 1e308), (1e308, 1e308)):
            result = smoother.update(now, value)
            self.assertTrue(math.isfinite(result))
            self.assertGreaterEqual(result, 0)
            self.assertLessEqual(result, value)

    def test_invalid_numbers_are_rejected(self):
        invalid = (True, None, "0.1", float("nan"), float("inf"), -1, 10 ** 1000)
        for value in invalid:
            with self.subTest(value_type=type(value).__name__):
                for args in ((value, 0, 0), (.16, value, 0), (.16, 0, value)):
                    with self.assertRaises(ValueError):
                        PhaseSmoother(*args)
                smoother = PhaseSmoother(.16, 0, 0)
                with self.assertRaises(ValueError):
                    smoother.update(value, 1)
                with self.assertRaises(ValueError):
                    smoother.update(.1, value)
                self.assertAlmostEqual(smoother.update(.2, .2), .12)

    def test_backwards_timestamp_is_rejected_without_mutating_history(self):
        smoother = PhaseSmoother(.16, 1, 4)
        previous = smoother.update(1.1, 4.1)
        with self.assertRaisesRegex(ValueError, "backwards"):
            smoother.update(1.05, 4.2)
        self.assertEqual(smoother.update(1.1, 4.1), previous)

    def test_long_playback_prunes_history(self):
        smoother = PhaseSmoother(.28, 0, 0)
        for index in range(1, 12_001):
            now = index / 120
            smoother.update(now, now)
            self.assertLessEqual(smoother.sample_count, 36)
        self.assertAlmostEqual(smoother.lag, .14)

    def test_excess_sample_density_stops_safely_and_can_recover(self):
        smoother = PhaseSmoother(1, 0, 0)
        with patch("dolly.smoothing.MAX_HISTORY_SAMPLES", 4):
            for now in (.1, .2, .3):
                smoother.update(now, now)
            with self.assertRaisesRegex(ValueError, "too many samples"):
                smoother.update(.4, .4)
            self.assertEqual(smoother.sample_count, 4)
            self.assertAlmostEqual(smoother.update(2, 2), 1.5)


if __name__ == "__main__":
    unittest.main()
