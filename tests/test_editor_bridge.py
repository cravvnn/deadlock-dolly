import struct
import unittest
from unittest.mock import patch

from dolly import editor_wire as w
from dolly import native_bridge as nb
from dolly.editor_actions import default_action_bindings, EditorBinding


class EditorBridgeTests(unittest.TestCase):
    def test_free_arrival_blend_does_not_enable_attachment_or_preview(self):
        offsets = dict(zip(w.ATTACH_FIELDS, (816, 48, 200, 212, 2184, 4536, 64, 72)))
        self.bridge.configure_editor_attach(offsets,
            {"selected": False, "source_blend": 0.5, "attached_keys": 1, "key_count": 3}, preview=True)
        fields = w.ATTACH_CONFIG.unpack_from(self.memory, w.ATTACH_OFFSET)
        self.assertEqual(fields[2:4], (3, 1))
        self.assertEqual(fields[19], 500)
        before = bytes(self.memory)
        for value in (-1, 11, float("nan"), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.bridge.configure_editor_attach(offsets, {"source_blend": value})
            self.assertEqual(bytes(self.memory), before)

    def test_bone_preview_preserves_name_and_hash_and_rejects_invalid_names(self):
        from dolly.native_effects import model_token
        offsets = dict(zip(w.ATTACH_FIELDS, (816, 48, 200, 212, 2184, 4536, 64, 72)))
        attach = {"handle": 5, "entity_id": 3, "model": 0x1234, "point": 2, "bone": "head"}
        self.bridge.configure_editor_attach(offsets, attach, preview=True)
        fields = w.ATTACH_CONFIG.unpack_from(self.memory, w.ATTACH_OFFSET)
        self.assertEqual(fields[17], 2)
        self.assertEqual(fields[-2], model_token("head"))
        self.assertEqual(fields[-1].rstrip(b"\0"), b"head")
        self.assertLessEqual(w.ATTACH_OFFSET + w.ATTACH_CONFIG.size, w.ROSTER_OFFSET)
        before = bytes(self.memory)
        for name in ("", "1head", "head/neck", "x" * 65, "héád"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.bridge.configure_editor_attach(offsets, {**attach, "bone": name})
            self.assertEqual(bytes(self.memory), before)

    def test_bone_catalog_bounds_identity_and_busy_publication(self):
        header = w.BONES_HEADER.pack(b"DLYBONE1", 2, 1, 5, 3, 0x1234, 2, 2)
        raw = header + b"head".ljust(64, b"\0") + b"hand_R".ljust(64, b"\0") + bytes(254 * 64)
        self.memory[w.BONES_OFFSET:w.BONES_OFFSET + len(raw)] = raw
        bones = self.bridge.editor_bones()
        self.assertEqual(bones["names"], ["head", "hand_R"])
        self.assertEqual((bones["handle"], bones["model"]), (5, 0x1234))
        self.assertLessEqual(w.BONES_OFFSET + w.BONES_BYTES, len(self.memory))
        bad = bytearray(raw)
        struct.pack_into("<I", bad, 32, 257)
        with self.assertRaises(ValueError):
            w.unpack_bones(bad)
        struct.pack_into("<I", self.memory, w.BONES_OFFSET + 8, 3)
        with self.assertRaises(nb.NativeBridgeError):
            self.bridge.editor_bones()

    def test_dof_publication_is_separate_atomic_and_validated_before_write(self):
        values = (1, 1, -100, 0, 180, 2000, -100, 0, 180, 2000, .5)
        before = bytes(self.memory[:w.DOF_OFFSET])
        self.bridge.configure_editor_dof(values, enabled=True)
        self.assertEqual(bytes(self.memory[:w.DOF_OFFSET]), before)
        self.assertEqual(w.DOF_CONFIG.unpack_from(self.memory, w.DOF_OFFSET),
                         (b"DLYDOF01", 2, 1, 1, 0, *values))
        before = bytes(self.memory)
        with self.assertRaises(ValueError):
            self.bridge.configure_editor_dof((.5, *values[1:]), enabled=True)
        self.assertEqual(bytes(self.memory), before)
        self.bridge.configure_editor_dof(values, enabled=False)
        self.assertEqual(w.DOF_CONFIG.unpack_from(self.memory, w.DOF_OFFSET)[1:4], (4, 1, 0))

    def test_attach_publication_is_separate_atomic_and_validated_before_write(self):
        offsets = {"scene_node": 816, "owner": 48, "player_origin": 200, "player_angles": 212,
                   "eye_offset": 2184, "eye_angles": 4536, "scene_child": 64, "scene_sibling": 72}
        before = bytes(self.memory[:w.ATTACH_OFFSET])
        self.bridge.configure_editor_attach(offsets)
        self.assertEqual(bytes(self.memory[:w.ATTACH_OFFSET]), before)
        fields = w.ATTACH_CONFIG.unpack_from(self.memory, w.ATTACH_OFFSET)
        self.assertEqual(fields[:5], (b"DLYATTC1", 2, 2, 1, 0))
        self.assertEqual(fields[6:14], (816, 48, 200, 212, 2184, 4536, 64, 72))
        self.assertEqual(fields[14:20], (0, 0, 0, 0, 0, 0))
        self.assertEqual(fields[20:27], (0.0,) * 7)
        self.assertEqual(fields[27:29], (0, 0))
        before = bytes(self.memory)
        with self.assertRaises(ValueError):
            self.bridge.configure_editor_attach({name: 0 for name in w.ATTACH_FIELDS})
        self.assertEqual(bytes(self.memory), before)

    def test_attach_publication_carries_the_selected_key(self):
        offsets = {"scene_node": 816, "owner": 48, "player_origin": 200, "player_angles": 212,
                   "eye_offset": 2184, "eye_angles": 4536, "scene_child": 64, "scene_sibling": 72}
        attach = {"handle": 5, "entity_id": 3, "target_index": 2, "model": 0x1234,
                  "point": 1, "hide_body": False, "offset": (1.0, 2.0, 3.0, 4.0, 5.0, 6.0),
                  "smoothing": 0.25, "attached_keys": 2, "key_count": 5}
        self.bridge.configure_editor_attach(offsets, attach, preview=True)
        fields = w.ATTACH_CONFIG.unpack_from(self.memory, w.ATTACH_OFFSET)
        self.assertEqual(fields[3], 7)
        self.assertEqual(fields[5], 0x1234)
        self.assertEqual(fields[14:20], (5, 3, 2, 1, 0, 0))
        self.assertEqual(fields[20:27], (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 0.25))
        self.assertEqual(fields[27:30], (2, 5, 0))

    def test_attach_roster_round_trips_and_validates(self):
        entries = ((19464268, 76, 0x4F5B17F42C61561D, b"models/heroes_staging/astro/astro.vmdl"),
                   (1736781, 53, 0x1234, b"models/heroes_wip/abrams/abrams.vmdl"))
        data = bytearray(w.ROSTER_BYTES)
        w.ROSTER_HEADER.pack_into(data, 0, b"DLYROS01", 6, 1, len(entries), 1)
        for index, (handle, entity_index, model, path) in enumerate(entries):
            w.ROSTER_ENTRY.pack_into(data, w.ROSTER_HEADER.size + index * w.ROSTER_ENTRY.size,
                                     handle, entity_index, model, path)
        self.memory[w.ROSTER_OFFSET:w.ROSTER_OFFSET + len(data)] = data
        roster = self.bridge.editor_roster()
        self.assertTrue(roster["available"])
        self.assertEqual([player["handle"] for player in roster["players"]], [19464268, 1736781])
        self.assertEqual(roster["players"][0]["model_path"],
                         "models/heroes_staging/astro/astro.vmdl")
        bad = bytearray(data)
        bad[0] = ord("X")
        self.memory[w.ROSTER_OFFSET:w.ROSTER_OFFSET + len(bad)] = bad
        with self.assertRaises(ValueError):
            self.bridge.editor_roster()

    def test_attach_result_round_trips_and_reads_empty(self):
        data = w.ATTACH_RESULT.pack(b"DLYATR01", 4, w.ATTACH_RESULT_ABI, 1, 0, 19464268, 76, 0, 0,
                                    0x4F5B17F42C61561D, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 0.25)
        self.memory[w.ATTACH_RESULT_OFFSET:w.ATTACH_RESULT_OFFSET + len(data)] = data
        result = self.bridge.editor_attach_result()
        self.assertTrue(result["valid"])
        self.assertEqual(result["sequence"], 4)
        self.assertEqual(result["handle"], 19464268)
        self.assertEqual(result["offset"], (1.0, 2.0, 3.0, 4.0, 5.0, 6.0))
        self.assertEqual(result["smoothing"], 0.25)
        empty = bytearray(w.ATTACH_RESULT.size)
        self.memory[w.ATTACH_RESULT_OFFSET:w.ATTACH_RESULT_OFFSET + len(empty)] = empty
        self.assertIsNone(self.bridge.editor_attach_result())

    def test_dof_preview_can_reenter_flight_without_closing_the_panel(self):
        with patch.object(self.bridge, "_wait_editor_input"), \
             patch.object(self.bridge, "_wait", return_value={"state": "armed"}):
            self.bridge.start_flight("example.dem", owner="panel")
        self.assertTrue(self.bridge._manual)
        self.assertEqual(self.bridge._editor_values["owner"], "panel")
        with self.assertRaises(ValueError):
            self.bridge.start_flight("example.dem", owner="game_ui")

    def setUp(self):
        self.memory = bytearray(nb.MAPPING_BYTES)
        self.time = 0
        def sleep(seconds):
            self.time += seconds
        self.bridge = nb.NativeBridge.create(mapping_factory=lambda *_: self.memory,
            clock=lambda: self.time, sleep=sleep, start_heartbeat=False, editor_pid=100,
            token="a" * 32)
        self.bridge.bind_game(200)
        self.addCleanup(self.bridge.close)

    def publish(self, events=(), *, seq=2, flags=127, owner=1, latest=None):
        latest = max((e[0] for e in events), default=0) if latest is None else latest
        data = bytearray(w.STATUS_BYTES)
        w.HEADER.pack_into(data, 0, w.STATUS_MAGIC, seq, w.EDITOR_ABI, owner, flags, 0, 2,
                           latest, 0, 320, 10, 20, 30, 5, 6, 7, 16/9,
                           b"Ready", 1.5, 42, 0, 17)
        for index, (serial, action, value) in enumerate(events):
            w.EVENT.pack_into(data, w.HEADER.size + index*w.EVENT.size,
                serial, action, value, 10, 20, 30, 5, 6, 7, 16/9, 42, 1)
        self.memory[w.STATUS_OFFSET:w.STATUS_OFFSET + len(data)] = data

    def test_config_does_not_overwrite_path_control_or_payload(self):
        self.memory[nb.PAYLOAD_OFFSET:nb.PAYLOAD_OFFSET + 32] = b"x"*32
        before = bytes(self.memory[:nb.CONTROL.size])
        bindings = default_action_bindings()
        bindings["capture"] = EditorBinding("Mouse4")
        self.bridge.configure_editor(enabled=True, owner="flight", bindings=bindings,
                                     camera_count=2, selected_camera=1)
        self.assertEqual(bytes(self.memory[:nb.CONTROL.size]), before)
        self.assertEqual(self.memory[nb.PAYLOAD_OFFSET:nb.PAYLOAD_OFFSET+32], b"x"*32)
        packed = w.CONFIG.unpack_from(self.memory, w.CONFIG_OFFSET)
        self.assertEqual(packed[0], w.CONFIG_MAGIC)
        self.assertEqual(packed[1] % 2, 0)
        self.assertEqual(packed[13:15], (5, 0))

    def test_manual_editor_poll_keeps_camera_history_without_shot_status_requests(self):
        self.publish()
        data = nb.STATUS.pack(
            nb.STATUS_MAGIC, 2, nb.ABI, 2, 200, 0, 0,
            42, 10., 28., 0., 205, 1,
            *range(7), *range(10, 17), 75., 70., 45,
            b"Ready", b"example.dem", 16.8, 8.3, 0, 0, 0, 0.)
        self.memory[nb.CONTROL_BYTES:nb.CONTROL_BYTES + len(data)] = data
        with patch.object(self.bridge, "status", wraps=self.bridge.status) as read:
            self.bridge.editor_status()
            self.time = .5
            self.bridge.editor_status()
            self.assertEqual(read.call_count, 1)
            self.time = 1
            self.bridge.editor_status()
            self.assertEqual(len(self.bridge._view_samples), 2)
            self.assertEqual(self.bridge._view_samples[-1]["tick"], 205)
            self.assertEqual(self.bridge._view_samples[-1]["applied_pose"], list(range(10, 17)))
            # A torn camera snapshot still allows editor controls to recover.
            struct.pack_into("<I", self.memory, nb.CONTROL_BYTES + 8, 3)
            self.time = 2
            self.assertTrue(self.bridge.editor_status()["ready"])
            self.assertEqual(len(self.bridge._view_samples), 2)
            self.time = 2.5
            self.bridge.editor_status()
            self.assertEqual(read.call_count, 3)

    def test_config_refresh_does_not_reopen_console_or_reset_owner(self):
        self.bridge.configure_editor(owner="flight")
        owner_sequence = self.bridge._editor_owner_sequence
        self.bridge.configure_editor(message="Camera captured", camera_count=1)
        self.assertEqual(self.bridge._editor_owner_sequence, owner_sequence)
        self.bridge.configure_editor(owner="panel")
        self.assertEqual(self.bridge._editor_owner_sequence, owner_sequence+1)

    def test_invalid_config_cannot_partially_publish(self):
        self.bridge.configure_editor(enabled=True)
        before = bytes(self.memory[w.CONFIG_OFFSET:w.CONFIG_OFFSET+w.CONFIG.size])
        with self.assertRaises(ValueError):
            self.bridge.configure_editor(speed=float("nan"))
        self.assertEqual(bytes(self.memory[w.CONFIG_OFFSET:w.CONFIG_OFFSET+w.CONFIG.size]), before)

    def test_playback_controls_use_reserved_config_space_and_keep_offsets(self):
        self.assertEqual(w.CONFIG.size, 448)
        self.bridge.configure_editor(playback_speed=.1, playback_rate=120)
        config = self.memory[w.CONFIG_OFFSET:w.CONFIG_OFFSET+w.CONFIG.size]
        self.assertEqual(struct.unpack_from("<dI", config, 416), (.1, 120))
        self.assertEqual(struct.unpack_from("<HH", config, 428), (0x7a, 0))
        self.assertEqual(struct.unpack_from("<HHBBf", config, 432), (60, 20, 0, 0, 1.0))
        self.assertEqual(config[442:], b"\0"*6)
        before = bytes(config)
        for setting in ({"playback_speed": 0}, {"playback_speed": float("nan")},
                        {"playback_rate": 90}, {"playback_rate": 60.0}, {"playback_rate": True}):
            with self.subTest(setting=setting), self.assertRaises(ValueError):
                self.bridge.configure_editor(**setting)
            self.assertEqual(bytes(self.memory[w.CONFIG_OFFSET:w.CONFIG_OFFSET+w.CONFIG.size]), before)

    def test_reshade_config_preserves_existing_offsets_and_rejects_conflicts(self):
        self.bridge.configure_editor(reshade_binding=EditorBinding("Mouse5", ctrl=True))
        config = self.memory[w.CONFIG_OFFSET:w.CONFIG_OFFSET+w.CONFIG.size]
        self.assertEqual(struct.unpack_from("<HH", config, 428), (6, 1))
        before = bytes(config)
        with self.assertRaisesRegex(ValueError, "ReShade menu"):
            self.bridge.configure_editor(reshade_binding=EditorBinding("F8"))
        self.assertEqual(bytes(self.memory[w.CONFIG_OFFSET:w.CONFIG_OFFSET+w.CONFIG.size]), before)
        self.bridge.configure_editor(reshade_binding=None)
        self.assertEqual(self.memory[w.CONFIG_OFFSET+428:w.CONFIG_OFFSET+432], b"\0"*4)

    def test_reshade_owner_and_action_follow_existing_ids(self):
        self.publish([(1, 31, 0)], owner=6)
        state = self.bridge.editor_status()
        self.assertEqual(state["input_mode"], "reshade")
        self.assertTrue(state["reshade_open"])
        self.assertEqual(state["events"][0]["action"], "reshade")
        self.assertFalse(state["console_open"])
        self.assertEqual(w.EDITOR_ABI, 2)

    def test_video_action_ids_follow_reshade(self):
        self.publish([(1, 32, 0), (2, 33, 0)])
        self.assertEqual([e["action"] for e in self.bridge.editor_status()["events"]],
                         ["start_video", "stop_video"])

    def test_video_export_config_round_trips(self):
        self.bridge.configure_editor(video_fps=600, video_bitrate_mbps=40, video_encoder=2,
                                     video_fixed_step=True, video_depth=True,
                                     video_depth_exr=True, video_speed=.1)
        config = self.memory[w.CONFIG_OFFSET:w.CONFIG_OFFSET+w.CONFIG.size]
        self.assertEqual(struct.unpack_from("<HHBB", config, 432), (600, 40, 2, 7))
        self.assertAlmostEqual(struct.unpack_from("<f", config, 438)[0], .1, places=5)

    def test_layer_flags_and_action_ids_preserve_existing_protocol(self):
        for layer, bit in (("world", 8), ("players", 16), ("effects", 32)):
            values = {"video_layer_" + name: name == layer for name in ("world", "players", "effects")}
            self.bridge.configure_editor(**values)
            config = self.memory[w.CONFIG_OFFSET:w.CONFIG_OFFSET+w.CONFIG.size]
            self.assertEqual(struct.unpack_from("<B", config, 437)[0], bit)
            with self.assertRaises(ValueError):
                self.bridge.configure_editor(**{"video_layer_" + layer: "yes"})
        self.publish([(1, 54, 1), (2, 55, 0), (3, 56, 1)])
        self.assertEqual([(e["action"], e["value"]) for e in self.bridge.editor_status()["events"]],
                         [("set_video_layer_world", 1), ("set_video_layer_players", 0),
                          ("set_video_layer_effects", 1)])

    def test_video_export_action_ids_follow_media_actions(self):
        self.publish([(1, 34, 300), (2, 35, 40), (3, 36, 2), (4, 37, 1), (5, 38, .1)])
        events = self.bridge.editor_status()["events"]
        self.assertEqual([(e["action"], e["value"]) for e in events],
                         [("set_video_fps", 300), ("set_video_bitrate", 40),
                          ("set_video_encoder", 2), ("set_video_fixed_step", 1),
                          ("set_video_speed", .1)])

    def test_depth_toggle_action_id_follows_dof_actions(self):
        self.publish([(1, 52, 1)])
        events = self.bridge.editor_status()["events"]
        self.assertEqual([(e["action"], e["value"]) for e in events], [("set_video_depth", 1)])

    def test_depth_exr_action_id_follows_the_depth_toggle(self):
        self.publish([(1, 53, 1)])
        events = self.bridge.editor_status()["events"]
        self.assertEqual([(e["action"], e["value"]) for e in events], [("set_video_depth_exr", 1)])
        with self.assertRaisesRegex(ValueError, "requires the depth master"):
            self.bridge.configure_editor(video_depth=False, video_depth_exr=True)

    def test_old_editor_abi_is_rejected(self):
        self.publish()
        struct.pack_into("<I", self.memory, w.STATUS_OFFSET+12, 1)
        with self.assertRaisesRegex(nb.NativeBridgeError, "protocol"):
            self.bridge.editor_status()

    def test_shot_seek_action_round_trips_without_reassigning_previous_ids(self):
        self.publish([(1, 74, .5), (2, 75, 1.375)])
        self.assertEqual([(event["action"], event["value"]) for event in self.bridge.editor_status()["events"]],
                         [("set_source_blend", .5), ("seek_shot", 1.375)])

    def test_playback_action_ids_preserve_existing_action_order(self):
        self.publish([(1, 28, 1), (2, 29, .25), (3, 30, 120)])
        events = self.bridge.editor_status()["events"]
        self.assertEqual([(event["action"], event["value"]) for event in events],
                         [("select_view", 1), ("set_playback_speed", .25), ("set_playback_rate", 120)])

    def test_capture_keeps_pose_tick_and_event_order_and_ack_is_exactly_once(self):
        self.publish([(2, 26, 0), (1, 0, 0)])
        status = self.bridge.editor_status()
        self.assertTrue(status["flight_active"])
        self.assertTrue(status["overlay_available"])
        self.assertEqual([e["action"] for e in status["events"]], ["capture", "console"])
        event = status["events"][0]
        self.assertEqual(event["pose"][:3], [10, 20, 30])
        self.assertEqual(event["tick"], 42)
        self.bridge.acknowledge_editor_event(1)
        self.assertEqual([e["sequence"] for e in self.bridge.editor_status()["events"]], [2])
        with self.assertRaises(nb.NativeBridgeError):
            self.bridge.acknowledge_editor_event(1)

    def test_bad_queue_gap_duplicate_action_and_owner_are_rejected(self):
        for events, owner in (([(2,0,0)],1), ([(1,0,0),(1,0,0)],1), ([(1,99,0)],1), ([],9)):
            with self.subTest(events=events,owner=owner):
                self.publish(events,owner=owner)
                with self.assertRaises(nb.NativeBridgeError):
                    self.bridge.editor_status()

    def test_odd_status_writer_cannot_dispatch_actions(self):
        self.publish([(1,0,0)],seq=3)
        with self.assertRaisesRegex(nb.NativeBridgeError,"being updated"):
            self.bridge.editor_status()

    def test_manual_can_start_and_hold_without_a_compiled_shot(self):
        self.publish()
        with patch.object(self.bridge, "_wait", return_value={"state":"armed"}):
            pose = [1,2,3,4,5,6,16/9]
            self.bridge.start_flight("replays/test.dem", pose)
            header = nb.CONTROL.unpack_from(self.memory)
            self.assertEqual(header[4],4)
            self.assertEqual(header[9],56)
            self.assertEqual(struct.unpack_from("<7d",self.memory,nb.PAYLOAD_OFFSET),tuple(pose))
            self.bridge.hold()
            self.assertEqual(nb.CONTROL.unpack_from(self.memory)[4],3)
            self.assertFalse(self.bridge._prepared)
            self.assertTrue(self.bridge._manual)
            with self.assertRaisesRegex(nb.NativeBridgeError,"Prepare"):
                self.bridge.play()
            self.bridge.release()
            self.assertFalse(self.bridge._manual)

    def test_manual_invalid_seed_does_not_publish_command(self):
        before = self.bridge._command
        for pose in ([1,2], [1,2,float("nan"),4,5,6,1], [1,2,3,4,5,6,0]):
            with self.assertRaises(ValueError):
                self.bridge.start_flight("test.dem",pose)
        self.assertEqual(self.bridge._command,before)

    def test_flight_waits_for_dx11_and_input_even_when_launcher_has_focus(self):
        ready = {"enabled": True, "ready": True, "paused": True,
                 "input_available": True, "overlay_available": True, "focused": False}
        waiting = dict(ready, input_available=False, overlay_available=False)
        published = []
        original = self.bridge._publish
        def publish(mode, *args):
            published.append((mode, self.time))
            return original(mode, *args)
        with patch.object(self.bridge, "editor_status", side_effect=[waiting, waiting, ready]), \
             patch.object(self.bridge, "_publish", side_effect=publish), \
             patch.object(self.bridge, "_wait", return_value={"state": "armed"}):
            self.bridge.start_flight("test.dem")
        self.assertEqual(published, [(4, .04)])
        self.assertTrue(self.bridge._manual)

    def test_unavailable_dx11_never_publishes_a_flight_command(self):
        self.publish(flags=1 | 4 | 16 | 64)  # Input/view ready, no swapchain yet.
        modes = []
        original = self.bridge._publish
        with patch.object(self.bridge, "_publish", side_effect=lambda mode, *args: (modes.append(mode), original(mode, *args))[1]):
            with self.assertRaisesRegex(nb.NativeBridgeError, "DX11 panel"):
                self.bridge.start_flight("test.dem", timeout=.05)
        self.assertEqual(modes, [0])
        self.assertFalse(self.bridge._manual)
        self.assertEqual(self.bridge._editor_values["owner"], "panel")

    def test_playback_flight_arms_without_a_paused_replay(self):
        playing = {"enabled": True, "ready": True, "paused": False,
                   "input_available": True, "overlay_available": True, "focused": False}
        with patch.object(self.bridge, "editor_status", side_effect=[playing]), \
             patch.object(self.bridge, "_wait", return_value={"state": "armed"}):
            result = self.bridge.start_flight("test.dem", playback=True)
        self.assertEqual(result, {"state": "armed"})
        self.assertTrue(self.bridge._manual)
        self.assertFalse(self.bridge._frozen)
        self.assertEqual(nb.CONTROL.unpack_from(self.memory)[4], 4)

    def test_paused_flight_still_requires_a_paused_replay(self):
        playing = {"enabled": True, "ready": True, "paused": False,
                   "input_available": True, "overlay_available": True, "focused": False}
        modes = []
        original = self.bridge._publish
        with patch.object(self.bridge, "editor_status", return_value=playing), \
             patch.object(self.bridge, "_publish",
                          side_effect=lambda mode, *args: (modes.append(mode), original(mode, *args))[1]):
            with self.assertRaisesRegex(nb.NativeBridgeError, "paused replay"):
                self.bridge.start_flight("test.dem", timeout=.05)
        self.assertEqual(modes, [0])
        self.assertFalse(self.bridge._manual)

    def test_flight_playback_mode_must_be_a_boolean_or_none(self):
        with self.assertRaises(ValueError):
            self.bridge.start_flight("test.dem", playback="yes")

    def test_flight_readiness_can_be_cancelled_without_moving_camera(self):
        with patch.object(self.bridge, "_publish", wraps=self.bridge._publish) as publish:
            with self.assertRaisesRegex(nb.NativeBridgeError, "cancelled"):
                self.bridge.start_flight("test.dem", cancelled=lambda: True)
        publish.assert_called_once_with(0)
        self.assertEqual(self.bridge._editor_values["owner"], "panel")

    def test_authored_path_hides_panel_and_failure_restores_controls(self):
        self.bridge.configure_editor(enabled=True, owner="panel")
        self.bridge._prepared = True
        with patch.object(self.bridge, "_wait", return_value={"state": "playing"}):
            self.bridge.play()
        self.assertEqual(self.bridge._editor_values["owner"], "flight")
        with patch.object(self.bridge, "_wait", side_effect=nb.NativeBridgeError("replay seek")):
            with self.assertRaisesRegex(nb.NativeBridgeError, "replay seek"):
                self.bridge.play()
        self.assertEqual(self.bridge._editor_values["owner"], "panel")


if __name__ == "__main__":
    unittest.main()
