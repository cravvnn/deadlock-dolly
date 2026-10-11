"""Object Picker editor events mutate only the placed list and validate input."""
import math
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from dolly import editor_dispatch as dispatch
from dolly import editor_session
from dolly import object_picker as picks
from dolly.path import Project


def event(action, **kw):
    base = dict(action=action, value=0, pose=(0, 0, 0, 0, 0, 0, 0), tick=0, paused=True)
    base.update(kw)
    return base


class ObjectDispatchTests(unittest.TestCase):
    def setUp(self):
        # The Object Picker is a WIP integration, disabled by default. These
        # tests exercise the dispatch logic with the feature enabled; the
        # disabled behavior is covered by DisabledObjectPickerTests.
        self._flag = patch("dolly.editor_actions.OBJECT_PICKER_ENABLED", True)
        self._flag.start()
        self.addCleanup(self._flag.stop)
        self.publish = lambda: editor_session.configure(self.app)
        self.app = SimpleNamespace(
            project=Project(), status_text=Mock(), busy=False, playing=False,
            object_picker_open=False, object_picker_selected=-1,
            _native_object_cache=None, _mark_dirty=Mock(),
            app_settings=SimpleNamespace(action_bindings={}),
        )
        self.bridge = Mock()

    def test_open_sets_mode_and_publishes(self):
        with patch("dolly.editor_session.configure"):
            dispatch.dispatch(self.app, event("object_picker_open"), self.bridge, publish=self.publish)
        self.assertTrue(self.app.object_picker_open)
        self.assertEqual(self.app.object_picker_selected, -1)

    def test_cancel_clears_mode(self):
        self.app.object_picker_open = True
        with patch("dolly.editor_session.configure"):
            dispatch.dispatch(self.app, event("object_picker_cancel"), self.bridge, publish=self.publish)
        self.assertFalse(self.app.object_picker_open)

    def test_place_appends_at_pose_and_selects(self):
        self.app.object_picker_open = True
        with patch("dolly.editor_session.configure"):
            dispatch.dispatch(self.app, event("object_place", value=3, pose=(10, 20, 30, 0, 0, 0, 0)),
                              self.bridge, publish=self.publish)
        self.assertEqual(len(self.app.project.objects), 1)
        item = self.app.project.objects[0]
        self.assertEqual(item.library_id, "crate")
        self.assertEqual(item.position, [10.0, 20.0, 30.0])
        self.assertEqual(self.app.object_picker_selected, 0)

    def test_place_rejects_nonfinite_and_bad_index(self):
        self.app.object_picker_open = True
        with self.assertRaises(ValueError):
            dispatch.dispatch(self.app, event("object_place", value=0, pose=(float("nan"), 0, 0, 0, 0, 0, 0)),
                              self.bridge, publish=self.publish)
        with self.assertRaises(ValueError):
            dispatch.dispatch(self.app, event("object_place", value=99, pose=(0, 0, 0, 0, 0, 0, 0)),
                              self.bridge, publish=self.publish)
        self.assertEqual(self.app.project.objects, [])

    def test_select_and_delete(self):
        self.app.project = picks.place(Project(), "crate", [0, 0, 0])
        self.app.project = picks.place(self.app.project, "barrel", [1, 1, 1])
        self.app.object_picker_open = True
        with patch("dolly.editor_session.configure"):
            dispatch.dispatch(self.app, event("object_select", value=1), self.bridge, publish=self.publish)
            self.assertEqual(self.app.object_picker_selected, 1)
            dispatch.dispatch(self.app, event("object_delete", value=0), self.bridge, publish=self.publish)
        self.assertEqual([o.library_id for o in self.app.project.objects], ["barrel"])
        self.assertEqual(self.app.object_picker_selected, 0)

    def test_select_publishes_so_the_ring_refreshes(self):
        self.app.project = picks.place(Project(), "crate", [0, 0, 0])
        self.app.project = picks.place(self.app.project, "barrel", [1, 1, 1])
        self.app.object_picker_open = True
        with patch("dolly.editor_session.configure") as configure:
            dispatch.dispatch(self.app, event("object_select", value=1), self.bridge, publish=self.publish)
        configure.assert_called()
        self.assertEqual(self.app.object_picker_selected, 1)

    def test_delete_rejects_bad_index(self):
        self.app.object_picker_open = True
        with self.assertRaises(ValueError):
            dispatch.dispatch(self.app, event("object_delete", value=-1), self.bridge, publish=self.publish)

    def test_transform_commits_position_angles_scale(self):
        self.app.project = picks.place(Project(), "crate", [0, 0, 0])
        self.app.object_picker_open = True
        with patch("dolly.editor_session.configure"):
            dispatch.dispatch(
                self.app,
                event("object_transform", value=0,
                      pose=(5.0, 6.0, 7.0, 10.0, 20.0, 30.0, 2.5)),
                self.bridge, publish=self.publish)
        item = self.app.project.objects[0]
        self.assertEqual(item.position, [5.0, 6.0, 7.0])
        self.assertEqual(item.angles, [10.0, 20.0, 30.0])
        self.assertEqual(item.scale, 2.5)

    def test_transform_rejects_bad_input(self):
        self.app.project = picks.place(Project(), "crate", [0, 0, 0])
        self.app.object_picker_open = True
        for value, pose in ((-1, (0, 0, 0, 0, 0, 0, 1)),
                            (0, (0, 0, 0, 0, 0, 0, float("nan"))),
                            (0, (0, 0))):
            with self.subTest(value=value), self.assertRaises(ValueError):
                dispatch.dispatch(self.app, event("object_transform", value=value, pose=pose),
                                  self.bridge, publish=self.publish)


class DisabledObjectPickerTests(unittest.TestCase):
    """The WIP Object Picker must be unreachable in a published build."""

    def test_flag_is_off_by_default(self):
        from dolly import editor_actions
        self.assertFalse(editor_actions.OBJECT_PICKER_ENABLED)

    def test_no_default_binding_when_disabled(self):
        from dolly.editor_actions import default_action_bindings
        self.assertIsNone(default_action_bindings().get("object_picker"))

    def test_saved_binding_is_cleared_when_disabled(self):
        from dolly.editor_actions import EditorBinding, validate_action_bindings
        result = validate_action_bindings({"object_picker": EditorBinding("R")})
        self.assertIsNone(result["object_picker"])

    def test_open_is_refused_when_disabled(self):
        app = SimpleNamespace(
            project=Project(), status_text=Mock(), busy=False, playing=False,
            object_picker_open=False, object_picker_selected=-1,
            _native_object_cache=None, _mark_dirty=Mock(),
            app_settings=SimpleNamespace(action_bindings={}),
        )
        with self.assertRaises(ValueError):
            dispatch.dispatch(app, event("object_picker_open"), Mock(), publish=lambda: None)
        self.assertFalse(app.object_picker_open)


if __name__ == "__main__":
    unittest.main()
