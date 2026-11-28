"""Native DOF/effect refusal reporting and optional cvar value typing."""
import json
import logging
from pathlib import Path
import types
import unittest
from unittest.mock import Mock

from dolly import definitions
from dolly import dof_preflight
from dolly import native_bridge as nb
from dolly.controller import (
    Controller, NATIVE_DOF_UNAVAILABLE, NATIVE_DOF_VERIFY_FILES,
)
from core_helpers import FakeConsole


def bridge_with_status(status):
    bridge = nb.NativeBridge.create(mapping_factory=lambda name, size: bytearray(size),
                                    start_heartbeat=False)
    bridge.status = Mock(return_value=status)
    return bridge


class NativeBridgeFaultRecording(unittest.TestCase):
    def test_wait_keeps_the_exact_fault_snapshot_for_diagnostics(self):
        snapshot = {
            "ack_command": 7, "state": "fault", "message": "Native effect rejected",
            "error": 42, "effect_error": 0, "effect_count": 3,
            "tick": 1234, "frame_count": 17,
        }
        bridge = bridge_with_status(snapshot)
        with self.assertRaisesRegex(nb.NativeBridgeError, "Native effect rejected"):
            bridge._wait(7, {"armed"}, 1.0)
        expected = {
            "state": "fault", "error": 42, "effect_error": 0, "effect_count": 3,
            "message": "Native effect rejected", "command": 7,
            "tick": 1234, "frame_count": 17,
        }
        self.assertEqual(bridge._last_fault, expected)
        report = bridge.diagnostics()
        self.assertEqual(report["last_fault"], expected)
        # The diagnostics export writes this dict directly to JSON.
        json.dumps(report)

    def test_unsupported_build_is_recorded_even_without_acknowledgment(self):
        snapshot = {
            "ack_command": 0, "state": "unsupported", "message": "Unreviewed build",
            "error": 0, "effect_error": 0, "effect_count": 0,
            "tick": 0, "frame_count": 0,
        }
        bridge = bridge_with_status(snapshot)
        with self.assertRaisesRegex(nb.NativeBridgeError, "Unreviewed build"):
            bridge._wait(9, {"armed"}, 1.0)
        self.assertEqual(bridge._last_fault["command"], 9)
        self.assertEqual(bridge._last_fault["state"], "unsupported")

    def test_binding_a_game_session_clears_a_previous_fault(self):
        bridge = bridge_with_status({})
        bridge._last_fault = {"state": "fault", "command": 1}
        bridge.bind_game(1234)
        self.assertIsNone(bridge._last_fault)

    def test_effect_error_is_surfaced_once_in_the_refusal_message(self):
        snapshot = {
            "ack_command": 4, "state": "fault", "message": "Native camera fault",
            "error": 36, "effect_error": 2, "effect_count": 1,
            "tick": 5, "frame_count": 6,
        }
        bridge = bridge_with_status(snapshot)
        with self.assertRaisesRegex(nb.NativeBridgeError,
                                    r"Native camera fault \(native effect error 2\)"):
            bridge._wait(4, {"armed"}, 1.0)
        self.assertEqual(bridge._last_fault["effect_error"], 2)

        repeated = dict(snapshot, message="Effect error 2 already reported")
        bridge = bridge_with_status(repeated)
        with self.assertRaises(nb.NativeBridgeError) as caught:
            bridge._wait(4, {"armed"}, 1.0)
        self.assertEqual(str(caught.exception), "Effect error 2 already reported")

    def test_successful_command_does_not_replace_a_recorded_fault(self):
        snapshot = {
            "ack_command": 7, "state": "fault", "message": "Refused",
            "error": 1, "effect_error": 1, "effect_count": 1,
            "tick": 1, "frame_count": 1,
        }
        bridge = bridge_with_status(snapshot)
        with self.assertRaises(nb.NativeBridgeError):
            bridge._wait(7, {"armed"}, 1.0)
        recorded = dict(bridge._last_fault)
        bridge.status = Mock(return_value=dict(snapshot, ack_command=8, state="armed"))
        self.assertEqual(bridge._wait(8, {"armed"}, 1.0)["state"], "armed")
        self.assertEqual(bridge._last_fault, recorded)


class Session:
    command = ()
    pid = 1

    class process:
        @staticmethod
        def poll():
            return None


def controller():
    c = Controller(lambda *args, **kwargs: None)
    c._console = FakeConsole()
    c._request = lambda command, **kwargs: c._console.request(command)
    c._session = Session()
    c._require_connection = Mock()
    return c


def dof_project():
    project = Mock()
    project.setup_values = {"r_dof_override": 1}
    project.tracks = []
    return project


class NativeDofLogging(unittest.TestCase):
    def test_absent_slang_is_reported_as_the_normal_retail_case(self):
        c = controller()
        c.dof_preflight = lambda *args, **kwargs: {
            "status": dof_preflight.READY,
            "dynamic_compile": {"missing": ["slang.dll"]},
        }
        with self.assertLogs("dolly", level=logging.INFO) as captured:
            self.assertFalse(c.ensure_native_dof_shader_support(dof_project()))
        records = [record for record in captured.records
                   if "Native DOF" in record.getMessage()]
        self.assertTrue(records)
        self.assertTrue(all(record.levelno == logging.INFO for record in records))
        text = " ".join(record.getMessage() for record in records)
        self.assertIn("slang.dll", text)
        self.assertIn("precompiled DOF shader", text)

    def test_missing_precompiled_entries_keep_the_verify_scoped_message(self):
        c = controller()
        c.dof_preflight = lambda *args, **kwargs: {
            "status": dof_preflight.VERIFY_FILES,
            "problems": ["missing dof shader"],
        }
        self.assertFalse(c.ensure_native_dof_shader_support(dof_project()))
        self.assertEqual(c._native_dof_unavailable, NATIVE_DOF_VERIFY_FILES)
        self.assertIn("precompiled", NATIVE_DOF_VERIFY_FILES)
        self.assertIn("Verify integrity", NATIVE_DOF_VERIFY_FILES)
        self.assertNotIn("slang", NATIVE_DOF_VERIFY_FILES)

    def test_unavailable_message_does_not_claim_verify_restores_slang(self):
        text = NATIVE_DOF_UNAVAILABLE.lower()
        self.assertNotIn("slang", text)
        self.assertNotIn("verify integrity", text)
        self.assertNotIn("repair the game files", text)
        self.assertIn("citadel", text)
        self.assertIn("precompiled", text)

    def test_failed_preview_logs_the_compiled_effect_tracks(self):
        c = controller()
        bridge = Mock()
        bridge.status.return_value = {"paused": True, "state": "armed", "tick": 100}
        bridge.editor_status.return_value = {"ready": True, "input_mode": "panel"}
        bridge.prepare.side_effect = RuntimeError("Native preparation failed")
        c._native_bridge = lambda: bridge
        c.status = lambda: {}
        for name in ("_validate_project", "_require_probe", "_require_demo",
                     "_require_native_dof_support", "_require_native_demo", "_snapshot"):
            setattr(c, name, Mock())
        c._shot_time = lambda project, shot_time: shot_time
        c._native_pose = lambda status: dict(x=0, y=0, z=0, pitch=0, yaw=0, roll=0,
                                            aspect_ratio=16 / 9)
        c._demo = Path("x.dem")
        project = types.SimpleNamespace(
            keyframes=[], setup_values={"r_dof_override": 1},
            tracks=[types.SimpleNamespace(name="r_dof_override_far_blurry",
                                          keys=[object()])])
        with self.assertLogs("dolly", level=logging.ERROR) as captured:
            with self.assertRaisesRegex(RuntimeError, "Native preparation failed"):
                c.preview_native_effects(project, 0.0)
        text = " ".join(record.getMessage() for record in captured.records)
        self.assertIn("submitted effect tracks", text)
        self.assertIn("r_dof_override", text)
        self.assertIn("r_dof_override_far_blurry", text)


def document(layout):
    return {
        "format": definitions.FORMAT,
        "native_abi": 4,
        "tier0": {"layouts": {"modern": layout}, "profiles": []},
    }


BASE_LAYOUT = {
    "table": "0x313208", "setter": "0x20fea0", "find": "0x6c6b0",
    "data": "0x6c5f0", "data_slot": 41, "extended": True,
}


class DefinitionsSeason2Types(unittest.TestCase):
    def cvar_line(self, document):
        text = definitions.native_document(definitions.parse(document))
        return next(line for line in text.splitlines() if line.startswith("+cvar "))

    def test_omitted_flag_keeps_the_eight_token_document(self):
        line = self.cvar_line(document(dict(BASE_LAYOUT)))
        self.assertEqual(line, "+cvar modern 0x313208 0x20fea0 0x6c6b0 0x6c5f0 41 1")
        self.assertEqual(len(line.split()), 8)
        text = definitions.native_document(definitions.parse(document(dict(BASE_LAYOUT))))
        self.assertIn("\nabi 4\n", text)

    def test_explicit_flags_emit_the_optional_ninth_token(self):
        for flag, token in ((True, "1"), (False, "0")):
            with self.subTest(flag=flag):
                layout = dict(BASE_LAYOUT, season2_types=flag)
                parsed = definitions.parse(document(layout))
                self.assertEqual(parsed.tier0_layouts["modern"]["season2_types"],
                                 int(flag))
                line = self.cvar_line(document(layout))
                self.assertEqual(len(line.split()), 9)
                self.assertEqual(line.split()[-1], token)
                self.assertIn("\nabi 4\n",
                              definitions.native_document(parsed))

    def test_non_boolean_flag_is_rejected(self):
        for value in (1, 0, "true", None):
            with self.subTest(value=value), self.assertRaises(definitions.DefinitionsError):
                definitions.parse(document(dict(BASE_LAYOUT, season2_types=value)))

    def test_checked_in_feed_still_parses_with_abi_4(self):
        root = Path(__file__).resolve().parents[1]
        parsed = definitions.load(root / definitions.DEFINITIONS_PATH)
        text = definitions.native_document(parsed)
        self.assertIn("\nabi 4\n", text)
        self.assertTrue(all(len(line.split()) == 8 for line in text.splitlines()
                            if line.startswith("+cvar ")))

    def test_publish_tool_carries_the_optional_flag(self):
        import sys
        tools = Path(__file__).resolve().parents[1] / "tools"
        sys.path.insert(0, str(tools))
        try:
            import publish_definitions
        finally:
            sys.path.remove(str(tools))
        profile = {"tier0": {
            "vtable_rva": "0x313208",
            "set_value": {"rva": "0x20fea0"},
            "find_cvar": {"rva": "0x6c6b0"},
            "get_data": {"rva": "0x6c5f0", "slot": 41},
        }}
        plain = publish_definitions.derived_tier0_layout(profile)
        self.assertNotIn("season2_types", plain)
        profile["tier0"]["season2_types"] = True
        flagged = publish_definitions.derived_tier0_layout(profile)
        self.assertIs(flagged["season2_types"], True)
        row = publish_definitions._layout_row(flagged)
        self.assertIs(row["season2_types"], True)
        self.assertIs(row["extended"], True)

        document = {"format": definitions.FORMAT, "native_abi": 4,
                    "tier0": {"layouts": {}, "profiles": []}}
        name, created = publish_definitions.ensure_layout(
            document, "tier0", flagged, "deadlock-2026-11-27-6882")
        self.assertTrue(created)
        stored = document["tier0"]["layouts"][name]
        self.assertIs(stored["season2_types"], True)
        # The serialized document keeps abi 4 and validates exactly as shipped.
        definitions.parse(document)
        # A profile without the reviewed flag must not silently reuse the
        # flagged layout: the extra token changes native cvar typing.
        other, created = publish_definitions.ensure_layout(
            document, "tier0", plain, "deadlock-2026-11-27-6880")
        self.assertTrue(created)
        self.assertNotEqual(other, name)
        self.assertNotIn("season2_types", document["tier0"]["layouts"][other])

    def test_publish_tool_rejects_a_non_boolean_profile_flag(self):
        import sys
        tools = Path(__file__).resolve().parents[1] / "tools"
        sys.path.insert(0, str(tools))
        try:
            import publish_definitions
        finally:
            sys.path.remove(str(tools))
        profile = {"tier0": {
            "vtable_rva": "0x313208",
            "set_value": {"rva": "0x20fea0"},
            "find_cvar": {"rva": "0x6c6b0"},
            "get_data": {"rva": "0x6c5f0", "slot": 41},
            "season2_types": 1,
        }}
        with self.assertRaises(publish_definitions.PublishError):
            publish_definitions.derived_tier0_layout(profile)


if __name__ == "__main__":
    unittest.main()
