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


if __name__ == "__main__":
    unittest.main()
