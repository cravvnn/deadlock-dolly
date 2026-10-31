"""Picker transactions cannot mutate a shot on Cancel or accept stale identity."""
import copy
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from dolly import bone_picker as picker, editor_wire as wire, gui
from dolly.native_effects import model_token
from dolly.path import AttachKey, Keyframe, Project


class BonePickerTests(unittest.TestCase):
    def setUp(self):
        from dolly import editor_session
        self.publish = lambda: editor_session.configure(self.app)
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

    def test_open_publishes_before_reading_new_request_sequence(self):
        self.app._bone_picker_context = None
        self.controller._recorder_active.return_value = False
        self.bridge.editor_status.return_value = dict(ready=True, flight_active=True, paused=True)
        def publish():
            self.assertIsNotNone(self.app._bone_picker_context)
            self.bridge._editor_attach_sequence = 73
        picker.open_picker(self.app, publish=publish)
        _, work, done = self.app._submit.call_args.args
        done(work())
        self.assertEqual(self.app._bone_picker_context["request"], 73)

    def test_cancel_publishes_restored_preview_before_submitting_resume(self):
        self.context["resume"] = True
        self.context["preview"] = True
        order = []
        def publish():
            self.assertIsNone(self.app._bone_picker_context)
            self.assertTrue(self.app.preview_attach)
            order.append("publish")
        self.app._submit.side_effect = lambda *args: order.append("resume")
        picker.close_picker(self.app, publish=publish)
        self.assertEqual(order, ["publish", "resume"])
        self.app._submit.assert_called_once_with("Resuming replay after Bone Picker", self.controller.toggle_replay)

    @patch("dolly.editor_session.configure")
    def test_cancel_preserves_entire_shot_and_preview(self, configure):
        original = copy.deepcopy(self.app.project)
        self.context["preview"] = True
        picker.close_picker(self.app, publish=self.publish)
        self.assertEqual(self.app.project, original)
        self.app._commit_camera.assert_not_called()
        self.assertTrue(self.app.preview_attach)
        self.assertIsNone(self.app._bone_picker_context)

    @patch("dolly.editor_session.configure")
    def test_done_only_changes_named_point_and_enables_preview(self, configure):
        picker.close_picker(self.app, result=self.result, publish=self.publish)
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
                picker.close_picker(self.app, result={**self.result, **change}, publish=self.publish)
            self.app._commit_camera.assert_not_called()
        self.app.project.keyframes[0].yaw += 1
        with self.assertRaises(ValueError):
            picker.close_picker(self.app, result=self.result, publish=self.publish)
        self.app._commit_camera.assert_not_called()

    def test_finish_waits_for_corresponding_worker_publication(self):
        self.bridge.editor_bone_picker.return_value = None
        event = dict(action="finish_bone_picker", value=600, pose=(42,))
        self.assertFalse(picker.dispatch(self.app, event, self.bridge, publish=self.publish))
        self.app._commit_camera.assert_not_called()
        self.bridge.editor_bone_picker.return_value = self.result
        with patch("dolly.editor_session.configure"):
            self.assertTrue(picker.dispatch(self.app, event, self.bridge, publish=self.publish))
        self.app._commit_camera.assert_called_once()

    def test_finish_retries_a_contended_result_read(self):
        from dolly.native_bridge import NativeBridgeError
        self.bridge.editor_bone_picker.side_effect = NativeBridgeError("The Bone Picker is updating; retry in a moment.")
        self.assertFalse(picker.dispatch(self.app, dict(action="finish_bone_picker", value=600, pose=(42,)), self.bridge, publish=self.publish))
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
        picker.start_attached_view(self.app, mutate, publish=self.publish)
        _, work, done = self.app._submit.call_args.args
        done(work())
        self.assertEqual(self.app.project.start_tick, 1234)
        self.assertEqual(self.app.project.tick_rate, 60)
        self.assertEqual(len(self.app.project.keyframes), 1)
        self.controller.capture_at_replay.assert_called_once_with(None, 60)
        mutate.assert_called_once()
        opened.assert_called_once_with(self.app, publish=self.publish)

    @patch("dolly.bone_picker.open_picker")
    def test_player_first_start_rejects_changed_shot(self, opened):
        self.app.project = Project()
        self.app._resolve_replay_tick_rate = Mock(return_value=60)
        picker.start_attached_view(self.app, Mock(), publish=self.publish)
        done = self.app._submit.call_args.args[2]
        self.app.project = Project(name="Replacement")
        with self.assertRaisesRegex(ValueError, "changed"):
            done((self.key, 1234))
        opened.assert_not_called()
        self.assertEqual(self.app.project.keyframes, [])

    def test_first_bone_camera_transfers_follow_before_capture(self):
        self.app.project = Project()
        self.app._resolve_replay_tick_rate = Mock(return_value=32)
        self.controller._follow_active = True
        self.controller.status.return_value = {"tick": 1234}
        picker.start_attached_view(self.app, Mock(), publish=self.publish)
        work = self.app._submit.call_args.args[1]
        work()
        names = [call[0] for call in self.controller.method_calls]
        self.assertLess(names.index("enter_native_flight"), names.index("capture_at_replay"))
        self.controller.enter_native_flight.assert_called_once_with(owner="panel")

    def test_existing_bone_picker_transfers_follow_in_worker(self):
        self.app._bone_picker_context = None
        self.controller._follow_active = True
        self.controller._recorder_active.return_value = False
        self.bridge.editor_status.return_value = dict(ready=True, flight_active=False, paused=True)
        picker.open_picker(self.app, publish=self.publish)
        self.controller.enter_native_flight.assert_not_called()
        self.app._submit.call_args.args[1]()
        self.controller.enter_native_flight.assert_called_once_with(owner="panel")

    def test_failed_follow_handoff_does_not_capture_or_open_picker(self):
        self.app.project = Project()
        self.app._resolve_replay_tick_rate = Mock(return_value=32)
        self.controller._follow_active = True
        self.controller.enter_native_flight.side_effect = RuntimeError("Restoration failed")
        picker.start_attached_view(self.app, Mock(), publish=self.publish)
        with self.assertRaisesRegex(RuntimeError, "Restoration failed"):
            self.app._submit.call_args.args[1]()
        self.controller.capture_at_replay.assert_not_called()
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
        rigid = wire.ATTACH_CONFIG.unpack(wire.pack_attach(
            2, offsets, {**attach, "orientation_mode": "rigid"}, picker=True))
        self.assertEqual(rigid[2], 6)
        self.assertEqual(rigid[18], 5)
        locked = wire.ATTACH_CONFIG.unpack(wire.pack_attach(
            2, offsets, {**attach, "lock_vertical_aim": True}, picker=True))
        self.assertEqual((locked[2], locked[18]), (6, 9))
        with self.assertRaisesRegex(ValueError, "true or false"):
            wire.pack_attach(2, offsets, {**attach, "lock_vertical_aim": 1})
        self.assertGreaterEqual(wire.PICKER_OFFSET, CONFETTI_DIAGNOSTICS_OFFSET + CONFETTI_DIAGNOSTICS.size)
        self.assertEqual(wire.PICKER_RESULT.size, 168)
        self.assertLessEqual(wire.PICKER_OFFSET + 168, 2 * 1024 * 1024 + 24576)

    def test_snap_result_sets_rigid_mode_only_for_current_free_camera(self):
        self.bridge._editor_attach_sequence = 20
        self.app._mark_dirty = Mock()
        self.app._refresh_attach_card = Mock()
        self.app._attach_snap_pending = True
        self.app._attach_snap_context = dict(project=self.app.project, index=0,
                                             key=copy.deepcopy(self.app.project.keyframes[0]),
                                             bridge=self.bridge)
        original = copy.deepcopy(self.app.project)
        result = dict(config_sequence=18, handle=11, entity_index=11,
                      model=model_token(self.key.attach.model),
                      point=0, offset=self.key.attach.offset)
        gui.DollyApp._attach_result_changed(self.app, result)
        self.assertEqual(self.app.project, original)
        self.assertTrue(self.app._attach_snap_pending)
        gui.DollyApp._attach_result_changed(
            self.app, {**result, "config_sequence": 20, "entity_index": 12})
        self.assertEqual(self.app.project, original)
        self.assertTrue(self.app._attach_snap_pending)
        gui.DollyApp._attach_result_changed(self.app, {**result, "config_sequence": 20})
        self.assertFalse(self.app._attach_snap_pending)
        self.assertEqual(self.app.project.keyframes[0].attach.orientation_mode, "rigid")
        self.assertEqual(self.app.project.keyframes[0].attach.offset, self.key.attach.offset)
        self.app.preview_attach = True
        gui.DollyApp._attach_result_changed(self.app, {
            **result, "config_sequence": 20, "offset": (7, 8, 9, 10, 11, 12)})
        self.assertEqual(self.app.project.keyframes[0].attach.offset, (7, 8, 9, 10, 11, 12))
        self.assertEqual(self.app.project.keyframes[0].attach.orientation_mode, "rigid")

    def test_snap_result_cannot_modify_a_different_camera_with_the_same_target(self):
        second = copy.deepcopy(self.app.project.keyframes[0])
        second.time = 1
        self.app.project.keyframes.append(second)
        self.app._attach_snap_pending = True
        self.app._attach_snap_context = dict(project=self.app.project, index=0,
                                             key=copy.deepcopy(self.app.project.keyframes[0]),
                                             bridge=self.bridge)
        self.app._selection_index = lambda _: 1
        self.app._mark_dirty = Mock()
        self.bridge._editor_attach_sequence = 20
        original = copy.deepcopy(self.app.project)
        result = dict(config_sequence=20, handle=11, entity_index=11,
                      model=model_token(self.key.attach.model), point=0,
                      offset=(7, 8, 9, 10, 11, 12))
        gui.DollyApp._attach_result_changed(self.app, result)
        self.assertEqual(self.app.project, original)
        self.assertFalse(self.app._attach_snap_pending)
        self.app._mark_dirty.assert_not_called()
        self.app.status_text.set.assert_called_with(
            "Snap cancelled because the selected camera changed.")

    @patch("dolly.editor_session.configure")
    def test_finish_timeout_unblocks_cancel_and_keeps_shot(self, configure):
        self.bridge.editor_bone_picker.return_value = None
        self.context["finish_wait"] = 10
        with patch("dolly.bone_picker.time.monotonic", return_value=16):
            with self.assertRaisesRegex(ValueError, "did not finish"):
                picker.dispatch(self.app, dict(action="finish_bone_picker", value=600, pose=(42,)), self.bridge, publish=self.publish)
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


class DesktopBonePickerCallTests(unittest.TestCase):
    """The desktop entry points must satisfy the shared keyword-only contract."""

    def fake_app(self, keyframes=()):
        return SimpleNamespace(
            project=Project(keyframes=list(keyframes)),
            _attach_players={"Player": {"handle": 11, "entity_index": 11,
                                        "model_path": "models/heroes/astro/astro.vmdl"}},
            attach_player=SimpleNamespace(get=lambda: "Player"),
            _guard=lambda _title, function: function(),
            _publish_editor=Mock(),
        )

    @patch("dolly.bone_picker.start_attached_view")
    def test_first_bone_view_passes_publish(self, started):
        app = self.fake_app()
        gui.DollyApp._open_bone_picker(app)
        started.assert_called_once()
        self.assertIs(started.call_args.args[0], app)
        self.assertIs(started.call_args.kwargs["publish"], app._publish_editor)

    @patch("dolly.bone_picker.open_picker")
    def test_existing_bone_view_passes_publish(self, opened):
        app = self.fake_app([copy.deepcopy(Keyframe(0, 1, 2, 3, 4, 5, 6))])
        gui.DollyApp._open_bone_picker(app)
        opened.assert_called_once_with(app, publish=app._publish_editor)

    @patch("dolly.bone_picker.close_picker")
    def test_cancel_passes_publish(self, closed):
        app = self.fake_app()
        gui.DollyApp._cancel_bone_picker(app)
        closed.assert_called_once_with(app, publish=app._publish_editor)

    @patch("dolly.gui.editor_session.configure")
    def test_desktop_publish_updates_native_editor(self, configure):
        app = SimpleNamespace()
        gui.DollyApp._publish_editor(app)
        configure.assert_called_once_with(app)

    def test_desktop_apply_keeps_a_snapped_cameras_orientation_mode(self):
        key = Keyframe(0, 1, 2, 3, 4, 5, 6, source="attach",
                       attach=AttachKey(handle=11, entity_id=11,
                                        model="models/heroes/astro/astro.vmdl",
                                        point="bone", bone="head", orientation_mode="rigid"))
        app = self.fake_app([key])
        app._selection_index = lambda _: 0
        app.camera_tree = object()
        app.attach_enabled = SimpleNamespace(get=lambda: True)
        app.attach_point = SimpleNamespace(get=lambda: "Bone")
        app.attach_bone = SimpleNamespace(get=lambda: "head")
        app.attach_vars = {name: SimpleNamespace(get=lambda: "1")
                           for name in ("x", "y", "z", "pitch", "yaw", "roll")}
        app.attach_smoothing = SimpleNamespace(get=lambda: "0")
        app.attach_lock_vertical_aim = SimpleNamespace(get=lambda: True)
        app._shot_edit_ready = lambda: True
        app.attach_hide = SimpleNamespace(get=lambda: True)
        app.attach_clearance = SimpleNamespace(get=lambda: "Exact offset")
        app.source_blend = SimpleNamespace(get=lambda: "0")
        app._mark_dirty = Mock()
        app._refresh_keys = Mock()
        gui.DollyApp._apply_attach(app, False)
        self.assertEqual(app.project.keyframes[0].attach.orientation_mode, "rigid")
        self.assertTrue(app.project.keyframes[0].attach.lock_vertical_aim)
        app.attach_lock_vertical_aim = SimpleNamespace(get=lambda: False)
        app._shot_edit_ready = lambda: False
        with self.assertRaisesRegex(ValueError, "Detach"):
            gui.DollyApp._apply_attach(app, False)
        self.assertTrue(app.project.keyframes[0].attach.lock_vertical_aim)


if __name__ == "__main__":
    unittest.main()
