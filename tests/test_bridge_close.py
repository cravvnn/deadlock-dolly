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


if __name__ == "__main__":
    unittest.main()
