"""An open Bone Picker must not outlive the editor that can finish it."""
from copy import deepcopy
from types import SimpleNamespace as NS
import unittest

from dolly import editor_publication, editor_session
from dolly.path import AttachKey, Keyframe, Project


class _Var:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def _app(status, bridge):
    key = Keyframe(0, 1, 2, 3, 4, 5, 6, 90, source="attach",
                   attach=AttachKey(handle=7, model="models/hero.vmdl"))
    app = NS(project=Project(keyframes=[key]), camera_tree=NS(), status_text=_Var(),
             preview_attach=False, native_editor_active=False, closed=False, busy=False,
             _selection_index=lambda _tree: 0,
             controller=NS(status=lambda: dict(status), _native_bridge=lambda: bridge))
    app._bone_picker_context = dict(index=0, key=deepcopy(key), bridge=bridge,
                                    preview=True, resume=True, request=4)
    return app


class EditorLossTests(unittest.TestCase):
    def test_lost_console_cancels_the_picker_locally(self):
        bridge = NS()
        app = _app(dict(game_running=True, connected=False, native_editor_active=True), bridge)
        editor_publication.configure(app)
        self.assertIsNone(app._bone_picker_context)
        self.assertTrue(app.preview_attach)
        self.assertIn("Bone Picker cancelled", app.status_text.get())

    def test_lost_bridge_cancels_the_picker_locally(self):
        app = _app(dict(game_running=True, connected=True), None)
        app._bone_picker_context["bridge"] = NS()
        editor_session.poll(app)
        self.assertIsNone(app._bone_picker_context)
        self.assertFalse(app.preview_attach)


if __name__ == "__main__":
    unittest.main()
