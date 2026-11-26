"""Bridge close, busy snapshot and control-refresh boundaries."""
import logging
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dolly import editor_session, editor_wire, media_wire
from dolly import native_bridge as nb


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(seconds, 1e-4)


class Mapping(bytearray):
    closed = False

    def close(self):
        self.closed = True


def make_bridge(clock=None):
    clock = clock or FakeClock()
    mappings = []

    def factory(name, size):
        mappings.append(Mapping(size))
        return mappings[-1]

    bridge = nb.NativeBridge.create(mapping_factory=factory, clock=clock, sleep=clock.sleep,
                                    start_heartbeat=False, editor_pid=100, token="0" * 32)
    return bridge, clock, mappings


def media_status(seq, ack, video_state, reshade_state):
    return media_wire.STATUS.pack(b"DLYMDS01", seq, media_wire.ABI, ack, 0, video_state, 60,
                                  1920, 1080, reshade_state, 1, 10, 0, 0, 0, 0, b"", b"", b"", 0, 0)


class MediaCloseAfterGameExitTests(unittest.TestCase):
    def test_close_after_game_exit_skips_media_waits_and_unmaps(self):
        bridge, clock, mappings = make_bridge()
        dead = {"value": False}
        bridge.bind_game(1234, exited=lambda: dead["value"])
        bridge._ensure_media()
        media = bridge._media_mapping
        data = media_status(2, 2, 2, 1)  # recording, ReShade enabled, start_video acknowledged
        media[media_wire.STATUS_OFFSET:media_wire.STATUS_OFFSET + len(data)] = data
        bridge._media_sequence = 2
        bridge._media_last_command = "start_video"
        bridge._source_audio_recording = True
        self.assertEqual(bridge.media_status()["state"], "recording")
        dead["value"] = True
        logging.disable(logging.CRITICAL)
        try:
            bridge.close()
        finally:
            logging.disable(logging.NOTSET)
        self.assertLess(clock.now, 0.5)
        self.assertIsNone(bridge._media_mapping)
        self.assertTrue(media.closed)

    def test_media_command_ends_its_wait_when_the_game_exits(self):
        bridge, clock, _ = make_bridge()
        dead = {"value": False}
        bridge.bind_game(1234, exited=lambda: dead["value"])
        bridge._ensure_media()
        dead["value"] = True
        with self.assertRaisesRegex(nb.NativeBridgeError, "exited"):
            bridge.disable_reshade()
        self.assertLess(clock.now, 0.5)


class AttachRosterBusyTests(unittest.TestCase):
    def test_busy_roster_raises_instead_of_returning_none(self):
        bridge, _, _ = make_bridge()
        bridge.bind_game(1234)
        struct.pack_into("<I", bridge._mapping, editor_wire.ROSTER_OFFSET + 8, 7)
        with self.assertRaisesRegex(nb.NativeBridgeError, "roster is busy"):
            bridge.editor_roster()

    def test_attach_picker_keeps_its_list_while_the_roster_is_busy(self):
        def busy():
            raise nb.NativeStatusBusy("The native attach roster is busy; retry in a moment.")
        bridge = SimpleNamespace(editor_roster=busy, editor_bones=lambda: None,
                                 editor_status=lambda: {})
        previous = {"available": True, "players": [{"handle": 0x10002, "entity_index": 2}]}
        changes = []
        app = SimpleNamespace(native_editor_active=True, busy=True, attach_roster=previous,
                              _attach_roster_changed=changes.append,
                              _attach_bones_changed=lambda bones: None,
                              status_text=SimpleNamespace(set=lambda text: None))
        with patch.object(editor_session, "_bridge", return_value=bridge), \
                patch.object(editor_session, "configure"):
            editor_session.poll(app)
        self.assertIs(app.attach_roster, previous)
        self.assertEqual(changes, [])


class EditorInputBusyTests(unittest.TestCase):
    def test_brief_status_busy_keeps_polling_for_editor_input(self):
        bridge, clock, _ = make_bridge()
        bridge.bind_game(1234, exited=lambda: False)
        load = bridge._load_sequence

        def odd_status_briefly(offset=nb.CONTROL_BYTES + 8):
            if offset == nb.CONTROL_BYTES + 8:
                return 1 if clock.now < 0.010 else 0
            return load(offset)
        ready = {"enabled": True, "ready": True, "overlay_available": True,
                 "input_available": True, "paused": True}
        with patch.object(bridge, "_load_sequence", odd_status_briefly),                 patch.object(bridge, "editor_status", return_value=ready):
            self.assertEqual(bridge._wait_editor_input(8), ready)
        self.assertLess(clock.now, 1)

    def test_editor_status_mid_publication_is_reported_as_busy(self):
        bridge, _, _ = make_bridge()
        load = bridge._load_sequence

        def odd_editor_status(offset=nb.CONTROL_BYTES + 8):
            return 1 if offset == editor_wire.STATUS_OFFSET + 8 else load(offset)
        with patch.object(bridge, "_load_sequence", odd_editor_status):
            with self.assertRaises(nb.NativeEditorStatusBusy):
                bridge.editor_status()

    def test_editor_input_wait_keeps_polling_through_a_busy_editor_status(self):
        bridge, clock, _ = make_bridge()
        bridge.bind_game(1234, exited=lambda: False)
        ready = {"enabled": True, "ready": True, "overlay_available": True,
                 "input_available": True, "paused": True}

        def editor_status():
            if clock.now < 0.010:
                raise nb.NativeEditorStatusBusy("Native editor status is being updated")
            return ready
        with patch.object(bridge, "status", return_value={"state": "idle"}),                 patch.object(bridge, "editor_status", editor_status):
            self.assertEqual(bridge._wait_editor_input(8), ready)

    def test_editor_input_wait_does_not_hide_other_errors_that_mention_updates(self):
        bridge, _, _ = make_bridge()
        bridge.bind_game(1234, exited=lambda: False)
        failure = nb.NativeBridgeError("Editor ABI being updated by another build")
        with patch.object(bridge, "status", return_value={"state": "idle"}),                 patch.object(bridge, "editor_status", side_effect=failure):
            with self.assertRaises(nb.NativeBridgeError) as caught:
                bridge._wait_editor_input(8)
        self.assertIs(caught.exception, failure)

    def test_editor_session_waits_quietly_only_for_a_busy_editor_status(self):
        for error, shown in ((nb.NativeEditorStatusBusy("Native editor status is being updated"), []),
                             (nb.NativeBridgeError("Editor ABI being updated by another build"),
                              ["Editor ABI being updated by another build"])):
            with self.subTest(error=type(error).__name__):
                texts = []
                bridge = SimpleNamespace(editor_status=lambda: (_ for _ in ()).throw(error))
                app = SimpleNamespace(native_editor_active=True, busy=False, _attach_roster_at=float("inf"),
                                      status_text=SimpleNamespace(set=texts.append))
                logging.disable(logging.CRITICAL)
                try:
                    with patch.object(editor_session, "_bridge", return_value=bridge),                             patch.object(editor_session, "configure"):
                        editor_session.poll(app)
                finally:
                    logging.disable(logging.NOTSET)
                self.assertEqual(texts, shown)


class BlockPublishTests(unittest.TestCase):
    def cases(self):
        from dolly import lite_mode, object_wire, replay_event_wire
        from dolly.follow_camera import FollowSettings
        from dolly.path import Project
        project = Project()
        events = SimpleNamespace(kills=[], players=[(3, "Ana")], status=1, total_ticks=900, message="ready")
        offsets = {name: 8 for name in editor_wire.ATTACH_FIELDS}

        def loading(odd):
            values = list(lite_mode.REPLAY_INFO.unpack(lite_mode.pack_replay_info(odd, project, 1)))
            values[6] |= 4
            return lite_mode.REPLAY_INFO.pack(*values)
        return [
            ("editor", lambda b: b.configure_editor(owner="panel"), editor_wire.CONFIG_OFFSET,
             lambda odd: editor_wire.pack_config(odd, 2, 0, {"owner": "panel"})),
            ("dof", lambda b: b.configure_editor_dof([0.0] * 11, enabled=True), editor_wire.DOF_OFFSET,
             lambda odd: editor_wire.pack_dof(odd, True, [0.0] * 11)),
            ("citadel_dof", lambda b: b.configure_editor_citadel_dof(1.0, 50, available=True, enabled=False),
             editor_wire.CITADEL_DOF_OFFSET, lambda odd: editor_wire.pack_citadel_dof(odd, True, False, 1.0, 50)),
            ("follow", lambda b: b.configure_editor_follow(FollowSettings(), active=True), editor_wire.FOLLOW_OFFSET,
             lambda odd: editor_wire.pack_follow(odd, FollowSettings(), active=True)),
            ("cameras", lambda b: b.configure_editor_cameras(project, 4, can_undo=True),
             editor_wire.CAMERA_LIST_OFFSET, lambda odd: editor_wire.pack_camera_list(odd, project, 4, can_undo=True)),
            ("shader_loading", lambda b: b.configure_shader_loading(True), lite_mode.REPLAY_INFO_OFFSET, loading),
            ("lite_replay", lambda b: b.configure_lite_replay(project, 7, 0, 900), lite_mode.REPLAY_INFO_OFFSET,
             lambda odd: lite_mode.pack_replay_info(odd, project, 7, 0, 900)),
            ("cinematic", lambda b: b.configure_editor_cinematic(5, 6, 2), editor_wire.CINEMATIC_OFFSET,
             lambda odd: editor_wire.CINEMATIC_STATUS.pack(b"DLYCIN01", odd, 1, 5, 6, 2)),
            ("debug_view", lambda b: b.configure_editor_debug_view(2, available=True), editor_wire.DEBUG_VIEW_OFFSET,
             lambda odd: editor_wire.pack_debug_view(odd, 2, available=True)),
            ("replay_events", lambda b: b.configure_replay_events(events), replay_event_wire.OFFSET,
             lambda odd: replay_event_wire.pack(odd, events)),
            ("framing_grid", lambda b: b.configure_framing_grid(enabled=True), editor_wire.FRAMING_GRID_OFFSET,
             lambda odd: editor_wire.pack_framing_grid(odd, True)),
            ("level_view", lambda b: b.configure_level_view(), editor_wire.LEVEL_VIEW_OFFSET,
             lambda odd: editor_wire.pack_level_view(odd)),
            ("object_picker", lambda b: b.configure_object_picker([], active=True), object_wire.OBJECT_CONFIG_OFFSET,
             lambda odd: object_wire.pack_config(odd, [], active=True, distance=object_wire.DISTANCE_DEFAULT)),
            ("attach", lambda b: b.configure_editor_attach(offsets), editor_wire.ATTACH_OFFSET,
             lambda odd: editor_wire.pack_attach(odd, offsets)),
            ("prop_probe", lambda b: b.set_prop_probe("create"), editor_wire.PROP_PROBE_OFFSET,
             lambda odd: editor_wire.pack_prop_probe(odd, editor_wire.PROP_PROBE_CREATE)),
        ]

    def test_each_block_is_published_with_an_even_sequence_and_nothing_else_changes(self):
        for name, publish, offset, build in self.cases():
            with self.subTest(block=name):
                bridge, _, _ = make_bridge()
                before = bytes(bridge._mapping)
                expected = build(3)
                end = offset + len(expected)
                publish(bridge)
                self.assertEqual(struct.unpack_from("<I", bridge._mapping, offset + 8)[0], 2)
                publish(bridge)
                after = bytes(bridge._mapping)
                self.assertEqual(after[offset:end], expected[:8] + struct.pack("<I", 4) + expected[12:])
                self.assertEqual(after[:offset], before[:offset])
                self.assertEqual(after[end:], before[end:])


class EditorBlockReadTests(unittest.TestCase):
    def readers(self):
        from dolly import object_wire
        return [
            ("editor_bone_picker", editor_wire.PICKER_OFFSET, nb.NativeStatusBusy,
             "The Bone Picker is updating; retry in a moment."),
            ("editor_object_status", object_wire.OBJECT_STATUS_OFFSET, nb.NativeBridgeError,
             "The Object Picker is updating; retry in a moment."),
            ("editor_roster", editor_wire.ROSTER_OFFSET, nb.NativeStatusBusy,
             "The native attach roster is busy; retry in a moment."),
            ("editor_prop_probe_status", editor_wire.PROP_PROBE_OFFSET + editor_wire.PROP_PROBE_CONFIG.size,
             nb.NativeBridgeError, "The prop probe is updating; retry in a moment."),
            ("editor_bones", editor_wire.BONES_OFFSET, nb.NativeStatusBusy,
             "The native bone picker is busy; retry in a moment."),
            ("editor_attach_result", editor_wire.ATTACH_RESULT_OFFSET, nb.NativeBridgeError,
             "The native attach result is busy; retry in a moment."),
        ]

    @staticmethod
    def outcome(read):
        try:
            return ("value", read())
        except Exception as exc:  # The comparison covers unpack errors too.
            return (type(exc), str(exc))

    def test_a_block_held_mid_publication_raises_the_readers_busy_error(self):
        for name, offset, busy, message in self.readers():
            with self.subTest(reader=name):
                bridge, _, _ = make_bridge()
                load = bridge._load_sequence

                def odd(position=nb.CONTROL_BYTES + 8):
                    return 1 if position == offset + 8 else load(position)
                with patch.object(bridge, "_load_sequence", odd):
                    with self.assertRaises(nb.NativeBridgeError) as caught:
                        getattr(bridge, name)()
                self.assertIs(type(caught.exception), busy)
                self.assertEqual(str(caught.exception), message)

    def test_a_torn_read_is_retried_until_the_block_settles(self):
        for name, offset, _, _ in self.readers():
            with self.subTest(reader=name):
                bridge, _, _ = make_bridge()
                settled = self.outcome(getattr(bridge, name))
                load = bridge._load_sequence
                torn = [2, 4]

                def moving(position=nb.CONTROL_BYTES + 8):
                    if position == offset + 8 and torn:
                        return torn.pop(0)
                    return load(position)
                with patch.object(bridge, "_load_sequence", moving):
                    self.assertEqual(self.outcome(getattr(bridge, name)), settled)
                self.assertEqual(torn, [])


class VisualizationTransportTests(unittest.TestCase):
    def channels(self):
        from dolly import visualization_preview_wire as preview_wire
        from dolly import visualization_wire as viewer_wire
        from dolly.path import Keyframe, Project
        project = Project(keyframes=[Keyframe(0, 0, 0, 0, 0, 0, 0), Keyframe(1, 100, 0, 0, 0, 10, 0)])
        identity = dict(source=preview_wire.DESKTOP, token=1, revision=1, session_id=5, key_time=0.0)
        compiled = viewer_wire.build_visualization(project, revision=1, session_id=5)
        return [
            dict(name="viewer", suffix=".viewer", prefix="_viewer", size=viewer_wire.MAPPING_BYTES,
                 publish=lambda b: b.publish_visualization(project, revision=1, session_id=5),
                 invalid=lambda b: b.publish_visualization(project, selected_camera=9),
                 packet=lambda seq: viewer_wire.build_visualization(project, sequence=seq, revision=1,
                                                                    session_id=5),
                 cleared=lambda seq: viewer_wire.build_visualization(None, sequence=seq, enabled=False),
                 closed=lambda seq: bytes(8) + struct.pack("<I", seq)),
            dict(name="preview", suffix=".viewer.preview", prefix="_preview", size=preview_wire.MAPPING_BYTES,
                 publish=lambda b: b.publish_visualization_preview(compiled, **identity),
                 invalid=lambda b: b.publish_visualization_preview(b"broken", **identity),
                 packet=lambda seq: preview_wire.build_preview(compiled, sequence=seq, **identity),
                 cleared=lambda seq: preview_wire.build_preview(sequence=seq),
                 closed=lambda seq: preview_wire.build_preview(sequence=seq)),
        ]

    def make(self, sizes=None):
        clock = FakeClock()
        made = []

        def factory(name, size):
            size = next((wrong for end, wrong in (sizes or {}).items() if name.endswith(end)), size)
            made.append((name, Mapping(size)))
            return made[-1][1]
        bridge = nb.NativeBridge.create(mapping_factory=factory, clock=clock, sleep=clock.sleep,
                                        start_heartbeat=False, editor_pid=100, token="0" * 32)
        return bridge, made

    @staticmethod
    def side(made, suffix):
        return [mapping for name, mapping in made if name.endswith(suffix)]

    def test_publish_clear_wrap_and_close_keep_each_side_mapping_consistent(self):
        for channel in self.channels():
            with self.subTest(channel=channel["name"]):
                bridge, made = self.make()
                prefix = channel["prefix"]
                self.assertTrue(channel["publish"](bridge))
                mappings = self.side(made, channel["suffix"])
                self.assertEqual(len(mappings), 1)
                mapping = mappings[0]
                packet = channel["packet"](2)
                self.assertEqual(bytes(mapping[:len(packet)]), packet)
                self.assertFalse(any(mapping[len(packet):]))
                self.assertTrue(getattr(bridge, prefix + "_enabled"))

                self.assertFalse(channel["invalid"](bridge))
                cleared = channel["cleared"](4)
                self.assertEqual(bytes(mapping[:len(cleared)]), cleared)
                self.assertEqual(getattr(bridge, prefix + "_sequence"), 4)
                self.assertFalse(getattr(bridge, prefix + "_enabled"))
                self.assertTrue(getattr(bridge, prefix + "_error"))

                setattr(bridge, prefix + "_sequence", 0xFFFFFFFE)
                self.assertTrue(channel["publish"](bridge))
                self.assertEqual(getattr(bridge, prefix + "_sequence"), 2)
                self.assertEqual(struct.unpack_from("<I", mapping, 8)[0], 2)
                self.assertEqual(len(self.side(made, channel["suffix"])), 1)

                getattr(bridge, "_close_visualization" + ("" if prefix == "_viewer" else "_preview"))()
                closed = channel["closed"](4)
                self.assertTrue(mapping.closed)
                self.assertIsNone(getattr(bridge, prefix + "_mapping"))
                self.assertEqual(bytes(mapping[:len(closed)]), closed)
                self.assertFalse(any(mapping[len(closed):]))

    def test_a_side_mapping_of_the_wrong_size_is_closed_and_reported(self):
        for channel in self.channels():
            with self.subTest(channel=channel["name"]):
                bridge, made = self.make({channel["suffix"]: channel["size"] - 1})
                self.assertFalse(channel["publish"](bridge))
                mapping, = self.side(made, channel["suffix"])
                self.assertTrue(mapping.closed)
                self.assertIsNone(getattr(bridge, channel["prefix"] + "_mapping"))
                self.assertIn("unexpected size", getattr(bridge, channel["prefix"] + "_error"))


if __name__ == "__main__":
    unittest.main()
