"""An open Bone Picker must not outlive the editor that can finish it."""
from copy import deepcopy
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from dolly import bone_picker, editor_publication, editor_session
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


class _AttachBridge:
    def __init__(self):
        self._editor_attach_sequence = 10
        self.configure_editor = Mock()
        self.pickers = []

    def editor_status(self):
        return dict(ready=True, flight_active=True, paused=True)

    def configure_editor_attach(self, offsets, attach=None, picker=False, **_options):
        self._editor_attach_sequence += 2
        self.pickers.append((self._editor_attach_sequence, picker))
        return self._editor_attach_sequence


class PickerRequestTests(unittest.TestCase):
    def test_request_is_the_first_published_picker_sequence(self):
        bridge = _AttachBridge()
        app = _app(dict(game_running=True, connected=True, native_editor_active=True), bridge)
        app._bone_picker_context = None
        app.native_editor_active = True
        app.attach_fields = {"offset": 0}
        app.playing = False
        app.app_settings = NS()
        app.controller._follow_active = False
        app.controller._recorder_active = lambda: False

        def submit(_label, worker, ready=None):
            result = worker()
            if ready:
                ready(result)
        app._submit = submit
        # The opening publication can be skipped (here by a history refresh);
        # the picker then first reaches native on a later poll.
        app._refreshing_shot_history = True
        with patch.object(editor_publication.editor_view, "editor_values", return_value={}),                 patch.object(editor_publication, "_playback_values", return_value=(1, 1)),                 patch.object(editor_publication, "_video_values", return_value=(0,) * 7),                 patch.object(editor_publication, "_pov_duration", return_value=0),                 patch.object(editor_publication, "_attach_state", return_value=None),                 patch.object(editor_publication, "_configure_visualization"),                 patch("dolly.curve_preview.configure"):
            bone_picker.open_picker(app, publish=lambda: editor_publication.configure(app))
            app._refreshing_shot_history = False
            editor_publication.configure(app)
            editor_publication.configure(app)
        self.assertEqual(bridge.pickers, [(12, True)])
        self.assertEqual(app._bone_picker_context["request"], 12)


class EditorCloseTests(unittest.TestCase):
    def test_closing_editor_is_not_re_enabled_by_the_next_poll(self):
        bridge = NS(configure_editor=Mock(), editor_status=Mock(return_value={"enabled": True}))
        app = _app(dict(game_running=True, connected=True, native_editor_active=True), bridge)
        app.native_editor_active = True
        editor_session.close(app)
        editor_session.poll(app)
        editor_publication.configure(app)
        bridge.configure_editor.assert_called_once_with(enabled=False, owner="disabled")
        bridge.editor_status.assert_not_called()
        self.assertFalse(app.native_editor_active)


if __name__ == "__main__":
    unittest.main()
