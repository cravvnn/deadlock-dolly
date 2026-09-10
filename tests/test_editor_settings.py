import dataclasses
import json
from pathlib import Path
import tempfile
import unittest

from dolly.bindings import CaptureBinding, DEFAULT_BINDING
from dolly.editor_actions import (
    ACTION_IDS, ACTION_ORDER, EditorBinding, bindings_from_dict, bindings_to_dict,
    default_action_bindings, validate_action_bindings,
)
from dolly.settings import AppSettings, load_settings, save_settings


class EditorSettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "settings.json"

    def write_legacy(self, binding):
        self.path.write_text(json.dumps({"version": 1, "capture_binding": binding.to_dict()}), encoding="utf-8")

    def test_migration_preserves_existing_capture_and_does_not_write(self):
        binding = CaptureBinding("Mouse5", False, False)
        self.write_legacy(binding)
        original = self.path.read_bytes()
        settings = load_settings(self.path)
        self.assertEqual(settings.capture_binding, binding)
        self.assertEqual(settings.action_bindings["capture"].vk, 6)
        self.assertEqual(settings.action_bindings["panel"].key, "F8")
        self.assertEqual(settings.migration_warnings, ())
        self.assertEqual(self.path.read_bytes(), original)

    def test_migration_keeps_capture_and_reports_new_default_conflict(self):
        binding = CaptureBinding("F8", False, False)
        self.write_legacy(binding)
        settings = load_settings(self.path)
        self.assertEqual(settings.capture_binding, binding)
        self.assertIsNone(settings.action_bindings["panel"])
        self.assertIn("Toggle Dolly panel", settings.migration_warnings[0])
        self.assertIn("F8", settings.migration_warnings[0])
        save_settings(settings, self.path)
        self.assertIsNone(load_settings(self.path).action_bindings["panel"])
        self.assertEqual(list(self.path.parent.glob("*.invalid")), [])

    def test_f7_migration_fails_visibly_without_changing_file(self):
        self.write_legacy(CaptureBinding("F7", False, False))
        original = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "F7 is reserved"):
            load_settings(self.path)
        self.assertEqual(self.path.read_bytes(), original)

    def test_complete_editor_preferences_roundtrip(self):
        bindings = default_action_bindings()
        bindings["capture"] = EditorBinding("Mouse4")
        bindings["seek_forward"] = None
        settings = AppSettings(
            game_path="B:/Steam Library/Deadlock", replay_folder="D:/My demos",
            demo_path="D:/My demos/match one.dem", launch_options="-windowed -w 1920 -h 1080",
            movement_speed=600, mouse_sensitivity=0.25, action_bindings=bindings,
        )
        save_settings(settings, self.path)
        self.assertEqual(load_settings(self.path), settings)
        raw = json.loads(self.path.read_text())
        self.assertEqual(raw["version"], 2)
        self.assertNotIn("migration_warnings", raw)
        self.assertNotIn("enabled", raw)
        self.assertEqual(raw["capture_binding"]["key"], "Mouse4")

    def test_helpers_keep_both_binding_views_synchronized(self):
        settings = AppSettings(game_path="B:/Deadlock", movement_speed=700)
        settings = settings.with_capture_binding(CaptureBinding("Mouse5", False, False))
        self.assertEqual(settings.action_bindings["capture"].key, "Mouse5")
        bindings = dict(settings.action_bindings)
        bindings["capture"] = EditorBinding("Mouse4")
        settings = settings.with_action_bindings(bindings)
        self.assertEqual(settings.capture_binding.key, "Mouse4")
        settings = dataclasses.replace(settings, mouse_sensitivity=0.3)
        self.assertEqual(settings.game_path, "B:/Deadlock")
        self.assertEqual(settings.movement_speed, 700)
        self.assertEqual(settings.action_bindings["capture"].key, "Mouse4")

    def test_unbound_capture_is_not_reenabled_by_roundtrip(self):
        bindings = default_action_bindings()
        bindings["capture"] = None
        settings = AppSettings().with_action_bindings(bindings)
        save_settings(settings, self.path)
        self.assertIsNone(load_settings(self.path).action_bindings["capture"])

    def test_invalid_numbers_and_launch_options_fail_before_save(self):
        for name, value in (("movement_speed", 0), ("movement_speed", True),
                            ("movement_speed", float("nan")), ("mouse_sensitivity", float("inf")),
                            ("mouse_sensitivity", 0), ("launch_options", "+quit"),
                            ("game_path", "C:/bad\x00path")):
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                AppSettings(**{name: value})

    def test_json_rejects_unknown_actions_and_mismatched_capture_alias(self):
        save_settings(AppSettings(), self.path)
        raw = json.loads(self.path.read_text())
        raw["action_bindings"]["bogus"] = None
        self.path.write_text(json.dumps(raw))
        with self.assertRaisesRegex(ValueError, "Unknown editor action"):
            load_settings(self.path)
        raw["action_bindings"].pop("bogus")
        raw["capture_binding"] = CaptureBinding("Mouse5", False, False).to_dict()
        self.path.write_text(json.dumps(raw))
        with self.assertRaisesRegex(ValueError, "disagree"):
            load_settings(self.path)


class EditorBindingTests(unittest.TestCase):
    def test_protocol_action_ids_and_modifier_bits_are_stable(self):
        self.assertEqual(len(ACTION_ORDER), 26)
        self.assertEqual(ACTION_IDS["capture"], 0)
        self.assertEqual(ACTION_IDS["panel"], 9)
        self.assertEqual(ACTION_IDS["flight"], 11)
        self.assertEqual(ACTION_IDS["roll_right"], 25)
        self.assertEqual(EditorBinding("K", True, True).modifiers, 3)
        self.assertEqual(EditorBinding("K", False, False, True).modifiers, 4)
        self.assertEqual(EditorBinding("Ctrl").vk, 0x11)
        self.assertEqual(EditorBinding("Comma").vk, 0xBC)

    def test_defaults_are_distinct_and_roundtrip(self):
        defaults = default_action_bindings()
        self.assertEqual(bindings_from_dict(bindings_to_dict(defaults)), defaults)
        self.assertEqual(defaults["capture"].label, DEFAULT_BINDING.label)
        self.assertNotIn("F7", [binding.key for binding in defaults.values() if binding])

    def test_duplicates_are_rejected_with_both_action_names(self):
        bindings = default_action_bindings()
        bindings["capture"] = EditorBinding("W")
        with self.assertRaisesRegex(ValueError, "Capture camera and Move forward"):
            validate_action_bindings(bindings)

    def test_clearing_conflict_allows_reassignment(self):
        bindings = default_action_bindings()
        bindings["panel"] = None
        bindings["capture"] = EditorBinding("F8")
        self.assertEqual(validate_action_bindings(bindings)["capture"].key, "F8")

    def test_f7_and_modifier_only_actions_are_rejected(self):
        for ctrl in (False, True):
            with self.assertRaisesRegex(ValueError, "F7 is reserved"):
                EditorBinding("F7", ctrl=ctrl)
        bindings = default_action_bindings()
        bindings["capture"] = EditorBinding("Ctrl")
        with self.assertRaisesRegex(ValueError, "not a modifier alone"):
            validate_action_bindings(bindings)

    def test_binding_maps_are_detached_and_strict(self):
        bindings = default_action_bindings()
        settings = AppSettings(action_bindings=bindings)
        bindings["capture"] = None
        self.assertIsNotNone(settings.action_bindings["capture"])
        for raw in ([], {"capture": {"key": "K"}}, {"bad": None}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                bindings_from_dict(raw)


if __name__ == "__main__":
    unittest.main()
