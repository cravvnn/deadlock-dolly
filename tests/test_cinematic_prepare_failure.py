"""A failed cinematic preparation reports its own error, not the cleanup's."""
from types import SimpleNamespace as NS
import unittest

from dolly import editor_dispatch
from dolly.path import Keyframe, Project


class CinematicPrepareFailureTests(unittest.TestCase):
    def test_seek_error_survives_a_closed_bridge(self):
        states = []

        def configure(token, revision, state):
            states.append(state)
            if state == 3:
                raise OSError("Native camera session is closed")

        def seek(snapshot, time):
            raise RuntimeError("The replay could not seek.")

        bridge = NS(configure_editor_cinematic=configure)
        project = Project(keyframes=[Keyframe(0, 0, 0, 0, 0, 0, 0), Keyframe(1, 1, 0, 0, 0, 0, 0)])
        caught = []

        def submit(_label, worker, _done=None):
            try:
                worker()
            except RuntimeError as exc:
                caught.append(str(exc))
            return True

        app = NS(project=project, shot_history=NS(revision=4), busy=False, playing=False,
                 preview_attach=False, _snapshot=lambda: project, controller=NS(seek=seek), _submit=submit)
        with self.assertLogs("dolly.editor_session", "WARNING"):
            editor_dispatch.dispatch(app, {"action": "prepare_cinematic", "value": 9, "pose": (4,)},
                                     bridge, publish=lambda: None)
        self.assertEqual(caught, ["The replay could not seek."])
        self.assertEqual(states, [1, 3])


if __name__ == "__main__":
    unittest.main()
