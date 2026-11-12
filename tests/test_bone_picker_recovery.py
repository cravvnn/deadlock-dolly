"""Bone Picker completion needs the existing detach transition before edits."""
from copy import deepcopy
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from dolly import bone_picker, editor_dispatch
from dolly.gui import DollyApp
from dolly.native_effects import model_token
from dolly.path import Project, Keyframe, AttachKey


class BonePickerRecovery(unittest.TestCase):
    def test_finish_detach_then_delete(self):
        bridge = NS()
        target = AttachKey(handle=4097, entity_id=1, model="models/heroes/hornet/hornet.vmdl")
        key = Keyframe(0, 1, 2, 3, 4, 5, 6, 90, source="attach", attach=target)
        project = Project(keyframes=[key, Keyframe(1, 2, 2, 3, 4, 5, 6, 90)])
        enter = Mock()
        app = NS(project=project, preview_attach=False, busy=False, playing=False,
                 controller=NS(_native_bridge=lambda: bridge, enter_native_flight=enter),
                 camera_tree=NS(), status_text=NS(set=Mock()), _selection_index=lambda _: 0,
                 start_tick=NS(set=Mock()), _refresh_tracks=Mock(), _history_edit_group=None)
        app._commit_project = lambda candidate, *args: setattr(app, "project", candidate)
        app._commit_camera = lambda keys, time: DollyApp._commit_camera(app, keys, time)
        app._guard = lambda _, operation: operation()
        app._shot_edit_ready = lambda: DollyApp._shot_edit_ready(app)
        app._submit = lambda _, worker: worker()
        app._bone_picker_context = dict(index=0, key=deepcopy(key), bridge=bridge,
                                        preview=False, resume=False, request=2)
        result = dict(request=2, finishing=True, ready=True, name="clavicle_R",
                      handle=target.handle, entity_id=1, model=model_token(target.model))
        published = Mock()
        bone_picker.close_picker(app, result=result, publish=published)
        self.assertTrue(app.preview_attach)
        self.assertIsNone(app._bone_picker_context)
        self.assertEqual(app.project.keyframes[0].attach.bone, "clavicle_R")
        before = deepcopy(app.project)
        DollyApp._delete_key(app)
        self.assertEqual(app.project, before)
        self.assertFalse(app._shot_edit_ready())
        editor_dispatch.dispatch(app, dict(action="attach_preview", value=0), bridge,
                                 publish=published)
        enter.assert_called_once_with(owner="panel")
        self.assertFalse(app.preview_attach)
        self.assertTrue(app._shot_edit_ready())
        self.assertEqual(app.project, before)
        DollyApp._delete_key(app)
        self.assertEqual(len(app.project.keyframes), 1)
        self.assertEqual(len(project.keyframes), 2)


if __name__ == "__main__":
    unittest.main()
