"""Picker transactions cannot mutate a shot on Cancel or accept stale identity."""
import copy
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from dolly import bone_picker as picker, editor_wire as wire
from dolly.native_effects import model_token
from dolly.path import AttachKey, Keyframe, Project


class BonePickerTests(unittest.TestCase):
    def setUp(self):
        self.key = Keyframe(0, 1, 2, 3, 4, 5, 6, source="attach",
                           attach=AttachKey(handle=11, entity_id=11,
                                            model="models/heroes/astro/astro.vmdl",
                                            offset=(1, 2, 3, 4, 5, 6)))
        self.bridge = Mock()
        self.controller = Mock()
        self.controller._native_bridge.return_value = self.bridge
        self.app = SimpleNamespace(project=Project(keyframes=[copy.deepcopy(self.key)]),
            controller=self.controller, _selection_index=lambda _: 0, camera_tree=object(),
            preview_attach=False, status_text=Mock(), _commit_camera=Mock(), _submit=Mock(),
            busy=False, playing=False, attach_fields={"scene_node": 816})
        self.context = dict(index=0, key=copy.deepcopy(self.key), bridge=self.bridge,
                            preview=False, resume=False, request=42)
        self.app._bone_picker_context = self.context
        self.result = dict(request=42, finishing=True, ready=True, selected=600, name="hand_R",
                           handle=11, entity_id=11, model=model_token(self.key.attach.model))

    @patch("dolly.editor_session.configure")
    def test_cancel_preserves_entire_shot_and_preview(self, configure):
        original = copy.deepcopy(self.app.project)
        self.context["preview"] = True
        picker.close_picker(self.app)
        self.assertEqual(self.app.project, original)
        self.app._commit_camera.assert_not_called()
        self.assertTrue(self.app.preview_attach)
        self.assertIsNone(self.app._bone_picker_context)

    @patch("dolly.editor_session.configure")
    def test_done_only_changes_named_point_and_enables_preview(self, configure):
        picker.close_picker(self.app, result=self.result)
        keys, at = self.app._commit_camera.call_args.args
        expected = copy.deepcopy(self.key)
        expected.attach.point, expected.attach.bone = "bone", "hand_R"
        self.assertEqual(keys, [expected])
        self.assertTrue(self.app.preview_attach)
        self.assertIsNone(self.app._bone_picker_context)
        self.assertEqual(self.app.project.keyframes[0], self.key)

    @patch("dolly.editor_session.configure")
    def test_stale_target_model_request_and_changed_key_rejected(self, configure):
        for change in ({"handle": 12}, {"entity_id": 12}, {"model": 456},
                       {"request": 40}, {"ready": False}, {"finishing": False}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                picker.close_picker(self.app, result={**self.result, **change})
            self.app._commit_camera.assert_not_called()
        self.app.project.keyframes[0].yaw += 1
        with self.assertRaises(ValueError):
            picker.close_picker(self.app, result=self.result)
        self.app._commit_camera.assert_not_called()

    def test_finish_waits_for_corresponding_worker_publication(self):
        self.bridge.editor_bone_picker.return_value = None
        event = dict(action="finish_bone_picker", value=600, pose=(42,))
        self.assertFalse(picker.dispatch(self.app, event, self.bridge))
        self.app._commit_camera.assert_not_called()
        self.bridge.editor_bone_picker.return_value = self.result
        with patch("dolly.editor_session.configure"):
            self.assertTrue(picker.dispatch(self.app, event, self.bridge))
        self.app._commit_camera.assert_called_once()

    def test_finish_retries_a_contended_result_read(self):
        from dolly.native_bridge import NativeBridgeError
        self.bridge.editor_bone_picker.side_effect = NativeBridgeError("The Bone Picker is updating; retry in a moment.")
        self.assertFalse(picker.dispatch(self.app, dict(action="finish_bone_picker", value=600, pose=(42,)), self.bridge))
        self.assertIs(self.app._bone_picker_context, self.context)
        self.app._commit_camera.assert_not_called()

    @patch("dolly.bone_picker.open_picker")
    def test_player_first_start_captures_tick_and_opens_picker(self, opened):
        self.app.project = Project()
        self.app._bone_picker_context = None
        self.app._resolve_replay_tick_rate = Mock(return_value=60)
        self.app.start_tick = Mock()
        self.app.tick_rate = Mock()
        self.app._mark_dirty = Mock()
        self.app._refresh_keys = Mock()
        self.app._set_time = Mock()
        self.controller.capture_at_replay.return_value = copy.deepcopy(self.key)
        self.controller.status.return_value = {"tick": 1234}
        mutate = Mock()
        picker.start_attached_view(self.app, mutate)
        _, work, done = self.app._submit.call_args.args
        done(work())
        self.assertEqual(self.app.project.start_tick, 1234)
        self.assertEqual(self.app.project.tick_rate, 60)
        self.assertEqual(len(self.app.project.keyframes), 1)
        self.controller.capture_at_replay.assert_called_once_with(None, 60)
        mutate.assert_called_once()
        opened.assert_called_once_with(self.app)

    @patch("dolly.bone_picker.open_picker")
    def test_player_first_start_rejects_changed_shot(self, opened):
        self.app.project = Project()
        self.app._resolve_replay_tick_rate = Mock(return_value=60)
        picker.start_attached_view(self.app, Mock())
        done = self.app._submit.call_args.args[2]
        self.app.project = Project(name="Replacement")
        with self.assertRaisesRegex(ValueError, "changed"):
            done((self.key, 1234))
        opened.assert_not_called()
        self.assertEqual(self.app.project.keyframes, [])

    def test_timed_bone_export_preserves_identity_offsets_and_compiles(self):
        from dolly.native_effects import compile_shot
        original = copy.deepcopy(self.app.project)
        self.app.project.keyframes[0].attach.point = "bone"
        self.app.project.keyframes[0].attach.bone = "head"
        original = copy.deepcopy(self.app.project)
        result = picker.timed_attachment(original, 5)
        self.assertEqual([key.time for key in result.keyframes], [0, 5])
        self.assertEqual(result.keyframes[0].attach, result.keyframes[1].attach)
        self.assertIsNot(result.keyframes[0].attach, result.keyframes[1].attach)
        self.assertEqual(result.keyframes[0].attach, original.keyframes[0].attach)
        self.assertEqual(len(original.keyframes), 1)
        self.assertTrue(compile_shot(result))
        for duration in (0, 121, float("nan")):
            with self.assertRaises(ValueError):
                picker.timed_attachment(original, duration)

    def test_wire_extension_preserves_old_config_and_diagnostics(self):
        from dolly.native_bridge import CONFETTI_DIAGNOSTICS_OFFSET, CONFETTI_DIAGNOSTICS
        offsets = dict(zip(wire.ATTACH_FIELDS, (816, 48, 200, 212, 2184, 4536, 64, 72)))
        attach = dict(handle=11, entity_id=11, model=123, point=0)
        old = wire.ATTACH_CONFIG.unpack(wire.pack_attach(2, offsets, attach))
        new = wire.ATTACH_CONFIG.unpack(wire.pack_attach(2, offsets, attach, picker=True))
        self.assertEqual(old[2:4], (2, 3))
        self.assertEqual(new[2:4], (4, 11))
        self.assertEqual(old[4:], new[4:])
        spaced = wire.ATTACH_CONFIG.unpack(wire.pack_attach(
            2, offsets, {**attach, "hide_body": False, "clearance_mode": "auto"},
            picker=True))
        self.assertEqual(spaced[2], 5)
        self.assertEqual(spaced[18], 2)
        self.assertGreaterEqual(wire.PICKER_OFFSET, CONFETTI_DIAGNOSTICS_OFFSET + CONFETTI_DIAGNOSTICS.size)
        self.assertEqual(wire.PICKER_RESULT.size, 168)
        self.assertLessEqual(wire.PICKER_OFFSET + 168, 2 * 1024 * 1024 + 24576)

    @patch("dolly.editor_session.configure")
    def test_finish_timeout_unblocks_cancel_and_keeps_shot(self, configure):
        self.bridge.editor_bone_picker.return_value = None
        self.context["finish_wait"] = 10
        with patch("dolly.bone_picker.time.monotonic", return_value=16):
            with self.assertRaisesRegex(ValueError, "did not finish"):
                picker.dispatch(self.app, dict(action="finish_bone_picker", value=600, pose=(42,)), self.bridge)
        self.app._commit_camera.assert_not_called()
        self.assertIsNone(self.app._bone_picker_context)

    def test_result_rejects_torn_nonfinite_and_malformed_data(self):
        raw = wire.PICKER_RESULT.pack(b"DLYPICK1", 2, 1, 15, 42, 11, 11, 123, 600, 700,
                                      b"hand_R", 1, 2, 3, 4, 5, 6, 1.5)
        result = wire.unpack_picker(raw)
        self.assertEqual((result["name"], result["selected"], result["total"]), ("hand_R", 600, 700))
        for offset, fmt, value in ((8, "I", 3), (16, "I", 16), (40, "i", 4096), (112, "d", float("nan"))):
            bad = bytearray(raw)
            struct.pack_into("<" + fmt, bad, offset, value)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                wire.unpack_picker(bad)
        self.assertIsNone(wire.unpack_picker(bytes(168)))

    def test_generated_cloth_bone_round_trips_without_console_syntax(self):
        project = copy.deepcopy(self.app.project)
        project.keyframes[0].attach.point = "bone"
        project.keyframes[0].attach.bone = "$cloth_m0p266"
        project.validate()
        offsets = dict(zip(wire.ATTACH_FIELDS, (816, 48, 200, 212, 2184, 4536, 64, 72)))
        packed = wire.pack_attach(2, offsets, dict(point=2, bone="$cloth_m0p266"))
        self.assertEqual(wire.ATTACH_CONFIG.unpack(packed)[-1].rstrip(b"\0"), b"$cloth_m0p266")
        for name in ("$bad", "$cloth_m0p", "$cloth_m0p2;quit", "$cloth_m0p2\n"):
            project.keyframes[0].attach.bone = name
            with self.subTest(name=name), self.assertRaises(ValueError):
                project.validate()


if __name__ == "__main__":
    unittest.main()
