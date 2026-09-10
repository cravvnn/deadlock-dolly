import ctypes
import struct
import unittest
from unittest.mock import patch

from dolly import native_bridge as nb
from dolly import visualization_wire as wire
from dolly.path import Keyframe, Project


class Memory(bytearray):
    def __init__(self, size):
        super().__init__(size)
        self.closed = 0
        self.observe = None

    def __setitem__(self, key, value):
        if self.observe:
            self.observe(key, value)
        super().__setitem__(key, value)

    def close(self):
        self.closed += 1


class VisualizationTransportTests(unittest.TestCase):
    def setUp(self):
        self.maps = []
        self.now = 0.0

        def factory(name, size):
            memory = Memory(size)
            self.maps.append((name, memory))
            return memory

        def sleep(seconds):
            self.now += seconds

        self.bridge = nb.NativeBridge.create(
            mapping_factory=factory, token="a" * 32, editor_pid=1001,
            clock=lambda: self.now, sleep=sleep, start_heartbeat=False)
        self.addCleanup(self.bridge.close)
        self.project = Project(keyframes=[Keyframe(0, 0, 0, 0, 0, 0, 0),
                                          Keyframe(2, 100, 30, 20, 45, 90, 5)])

    def packet(self):
        memory = self.maps[1][1]
        header = wire.HEADER.unpack_from(memory)
        size = wire.HEADER_BYTES + header[5] * 8 + header[6]
        return bytes(memory[:size])

    def test_mapping_is_lazy_private_and_does_not_write_camera_control(self):
        main = bytes(self.maps[0][1])
        self.assertTrue(self.bridge.publish_visualization(Project()))
        self.assertTrue(self.bridge.publish_visualization(self.project, enabled=False))
        self.assertEqual(len(self.maps), 1)
        before = self.project.to_dict()
        self.assertTrue(self.bridge.publish_visualization(self.project, selected_camera=1))
        self.assertEqual(self.maps[1][0], self.maps[0][0] + ".viewer")
        self.assertEqual(len(self.maps[1][1]), 2 * 1024 * 1024)
        self.assertEqual(self.packet(), wire.build_visualization(self.project, selected_camera=1))
        self.assertEqual(bytes(self.maps[0][1]), main)
        self.assertEqual(self.project.to_dict(), before)

    def test_publication_uses_atomic_helper_and_odd_sequence_during_payload_write(self):
        self.assertTrue(self.bridge.publish_visualization(self.project))
        viewer = self.maps[1][1]
        atomic_values = []

        def exchange(pointer, value):
            slot = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_int32))
            previous = slot.contents.value
            slot.contents.value = value.value
            atomic_values.append(value.value)
            return previous

        self.bridge._atomic32 = exchange
        observations = []
        viewer.observe = lambda key, value: observations.append(struct.unpack_from("<I", viewer, 8)[0])
        self.project.keyframes[1].x = 42
        self.assertTrue(self.bridge.publish_visualization(self.project))
        self.assertEqual(atomic_values, [3, 4])
        self.assertTrue(observations)
        self.assertTrue(all(value == 3 for value in observations))
        self.assertEqual(self.packet(), wire.build_visualization(self.project, sequence=4))
        viewer.observe = None

    def test_selection_and_disable_publish_revisions_without_new_mapping(self):
        self.assertTrue(self.bridge.publish_visualization(self.project))
        self.assertTrue(self.bridge.publish_visualization(self.project, selected_camera=1))
        self.assertEqual(self.packet(), wire.build_visualization(self.project, sequence=4, selected_camera=1))
        self.assertTrue(self.bridge.publish_visualization(self.project, enabled=False))
        self.assertEqual(self.packet(), wire.build_visualization(None, sequence=6, enabled=False))
        self.assertEqual(len(self.maps), 2)
        self.assertFalse(self.bridge.visualization_diagnostics()["enabled"])

    def test_viewer_revision_wrap_keeps_close_visible_to_existing_readers(self):
        self.assertTrue(self.bridge.publish_visualization(self.project))
        self.bridge._viewer_sequence = 0xFFFFFFFE
        self.assertTrue(self.bridge.publish_visualization(self.project, selected_camera=1))
        self.assertEqual(self.packet(), wire.build_visualization(self.project, sequence=2, selected_camera=1))
        self.bridge._viewer_sequence = 0xFFFFFFFE
        self.bridge.close()
        self.assertEqual(struct.unpack_from("<I", self.maps[1][1], 8)[0], 2)
        self.assertEqual(self.maps[1][1][:8], b"\0" * 8)

    def test_invalid_update_clears_prior_guides_without_failing_camera(self):
        self.assertTrue(self.bridge.publish_visualization(self.project))
        main = bytes(self.maps[0][1])
        self.assertFalse(self.bridge.publish_visualization(self.project, selected_camera=20))
        diag = self.bridge.visualization_diagnostics()
        self.assertIn("outside", diag["error"])
        self.assertFalse(diag["enabled"])
        self.assertEqual(self.packet(), wire.build_visualization(None, sequence=4, enabled=False))
        self.assertEqual(bytes(self.maps[0][1]), main)
        self.assertTrue(self.bridge.publish_visualization(self.project))
        self.assertEqual(self.bridge.visualization_diagnostics()["error"], "")

    def test_mapping_failure_is_optional_and_can_retry(self):
        factory = self.bridge._mapping_factory
        with patch.object(self.bridge, "_mapping_factory", side_effect=OSError("mapping unavailable")):
            self.assertFalse(self.bridge.publish_visualization(self.project))
        self.assertEqual(self.bridge.visualization_diagnostics()["error"], "mapping unavailable")
        self.assertFalse(self.bridge.visualization_diagnostics()["mapping_open"])
        self.assertIs(self.bridge._mapping_factory, factory)
        self.assertTrue(self.bridge.publish_visualization(self.project))

    def test_wrong_mapping_size_is_closed_and_never_touches_main_mapping(self):
        main = bytes(self.maps[0][1])
        invalid = Memory(128)
        with patch.object(self.bridge, "_mapping_factory", return_value=invalid):
            self.assertFalse(self.bridge.publish_visualization(self.project))
        self.assertEqual(invalid.closed, 1)
        self.assertIn("unexpected size", self.bridge.visualization_diagnostics()["error"])
        self.assertEqual(bytes(self.maps[0][1]), main)

    def test_close_zeros_and_releases_viewer_even_when_camera_release_fails(self):
        self.assertTrue(self.bridge.publish_visualization(self.project))
        viewer = self.maps[1][1]
        with patch.object(self.bridge, "release", side_effect=nb.NativeBridgeError("camera gone")):
            self.bridge.close()
        self.assertEqual(viewer.closed, 1)
        self.assertEqual(viewer[:8], b"\0" * 8)
        self.assertEqual(viewer[12:], b"\0" * (wire.MAPPING_BYTES - 12))
        self.assertEqual(struct.unpack_from("<I", viewer, 8)[0], 4)
        self.assertEqual(self.maps[0][1].closed, 1)
        self.assertFalse(self.bridge.visualization_diagnostics()["mapping_open"])
        self.assertFalse(self.bridge.visualization_diagnostics()["enabled"])
        self.bridge.close()
        self.assertEqual(viewer.closed, 1)
        self.assertFalse(self.bridge.publish_visualization(self.project))
        self.assertEqual(len(self.maps), 2)


if __name__ == "__main__":
    unittest.main()
