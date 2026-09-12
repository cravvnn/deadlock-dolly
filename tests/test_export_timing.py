import unittest
from unittest.mock import Mock
from dolly.controller import Controller


class ExportTimingTests(unittest.TestCase):
    def setUp(self):
        self.controller = Controller()
        self.controller._console = Mock(is_connected=True)
        self.controller._alive = Mock(return_value=True)
        self.values = {"host_framerate": 0.0, "r_wait_on_present": 0.0}
        self.writes = []
        self.clamp = False
        self.controller._request = self.request

    def request(self, command):
        output = []
        for part in command.split(";"):
            words = part.split()
            if len(words) == 1:
                output.append(f"{words[0]} = {self.values[words[0]]}")
            else:
                name, value = words[0], float(words[1])
                self.writes.append((name, value))
                if name in self.values:
                    self.values[name] = min(value, 60) if self.clamp and name == "host_framerate" else value
        return "\n".join(output)

    def test_half_speed_uses_half_a_replay_step_per_video_frame(self):
        self.controller.set_export_timing(60, .5)
        self.assertEqual(self.values, {"host_framerate": 120, "r_wait_on_present": 1})
        self.assertEqual(self.controller._export_timing["speed"], .5)
        self.controller.clear_export_timing()
        self.assertEqual(self.values, {"host_framerate": 0, "r_wait_on_present": 0})

    def test_supported_output_rates_and_speeds_have_correct_engine_step(self):
        for fps in (30, 60, 120, 300, 600):
            for speed in (.05, .5, 1, 4):
                with self.subTest(fps=fps, speed=speed):
                    self.controller.set_export_timing(fps, speed)
                    self.assertAlmostEqual(1 / self.values["host_framerate"], speed / fps)
                    self.controller.clear_export_timing()

    def test_original_nondefault_settings_restore(self):
        self.values.update(host_framerate=90, r_wait_on_present=1)
        self.controller.set_export_timing(60, .5)
        self.controller.clear_export_timing()
        self.assertEqual(self.values, {"host_framerate": 90, "r_wait_on_present": 1})

    def test_clamped_engine_step_aborts_and_restores(self):
        self.clamp = True
        with self.assertRaisesRegex(RuntimeError, "did not accept"):
            self.controller.set_export_timing(60, .5)
        self.assertEqual(self.values, {"host_framerate": 0, "r_wait_on_present": 0})
        self.assertIsNone(self.controller._export_timing)

    def test_unreadable_original_prevents_any_timing_write(self):
        self.values["r_wait_on_present"] = "unavailable"
        with self.assertRaises((ValueError, RuntimeError)):
            self.controller.set_export_timing(60)
        self.assertEqual(self.writes, [])
