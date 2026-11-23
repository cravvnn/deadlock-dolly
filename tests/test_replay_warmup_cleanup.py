"""Effect-settling cleanup must not replace the error that stopped it."""
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from dolly import replay_warmup
from dolly.path import Keyframe, Project


class Cancelled(Exception):
    pass


class _Controller(replay_warmup.ReplayWarmupMixin):
    def __init__(self, cleanup_error):
        self.cleanup_error = cleanup_error
        self.requests = []
        self.armed = False
        self._session = object()
        self._demo = NS(name="replay.dem")
        self._camera_calibration = {}
        self._last_seek_details = {"game_tick_offset": 5, "verified": True, "actual_tick": 1000}
        self._bridge = NS(prepare=lambda *args: None,
                          status=lambda: {"tick": 900, "paused": False, "frame_count": 1})

    def _supports_native_flight(self):
        return True

    def _console_command_available(self, name):
        return True

    def _recorder_active(self):
        return False

    def _native_bridge(self):
        return self._bridge

    def _check_position_cancelled(self):
        if self.armed:
            raise Cancelled("cancelled")

    def _release_native_camera(self):
        pass

    def _seek_tick(self, tick):
        return {"tick": tick}

    def _detach_spectator_view(self):
        pass

    def _require_native_demo(self, status):
        pass

    def _message(self, *args, **kwargs):
        pass

    def _require_connection(self):
        pass

    def _alive(self):
        return True

    def _require_demo(self, **kwargs):
        if self.cleanup_error:
            raise RuntimeError("The console did not answer.")
        return {"tick": 900}

    def _request(self, command, **kwargs):
        self.requests.append(command)
        if "demo_pauseatservertick" in command:
            self.armed = True
        return ""


class WarmupCleanupTests(unittest.TestCase):
    def _run(self, cleanup_error):
        controller = _Controller(cleanup_error)
        project = Project(keyframes=[Keyframe(0, 0, 0, 0, 0, 0, 0), Keyframe(1, 1, 0, 0, 0, 0, 0)])
        project.tick_rate = 64
        with patch.object(replay_warmup, "packet_index", return_value=None):
            with self.assertRaises(Cancelled):
                controller._warm_replay_for_shot(project, 0, {"tick": 1000})
        self.assertFalse(controller._replay_warmup_active)
        return controller.requests

    def test_cancellation_disarms_the_pause_goal(self):
        self.assertEqual(self._run(False)[-1], "demo_resume; demo_pause")

    def test_failed_cleanup_keeps_the_cancellation(self):
        with self.assertLogs("dolly", "WARNING"):
            requests = self._run(True)
        self.assertNotIn("demo_resume; demo_pause", requests)


if __name__ == "__main__":
    unittest.main()
