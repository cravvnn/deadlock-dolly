import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dolly.bindings import CaptureBinding, DEFAULT_BINDING
from dolly.settings import (
    AppSettings, MAX_SETTINGS_BYTES, load_settings, save_settings, settings_path,
)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "nested" / "settings.json"

    def write_raw(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))

    def valid_payload(self):
        return {"version": 1, "capture_binding": DEFAULT_BINDING.to_dict()}

    def test_missing_settings_use_default_without_creating_file(self):
        self.assertEqual(load_settings(self.path), AppSettings())
        self.assertFalse(self.path.parent.exists())

    def test_roundtrip_custom_keyboard_binding_and_editor_preferences_persist(self):
        settings = AppSettings(CaptureBinding(key="F8", ctrl=False, alt=False, shift=True))
        save_settings(settings, self.path)
        self.assertEqual(load_settings(self.path), settings)
        raw = json.loads(self.path.read_text("utf-8"))
        self.assertEqual(set(raw), {"version", "capture_binding", "game_path", "replay_folder",
                                   "demo_path", "launch_options", "movement_speed",
                                   "mouse_sensitivity", "action_bindings", "reshade_binding",
                                   "reshade_runtime_path"})
        self.assertEqual(raw["version"], 3)
        self.assertEqual(raw["capture_binding"], settings.capture_binding.to_dict())
        self.assertNotIn("enabled", raw)

    def test_invalid_file_load_preserves_original_and_creates_no_backup(self):
        self.write_raw("{ broken")
        with self.assertRaises(ValueError):
            load_settings(self.path)
        self.assertEqual(self.path.read_text(), "{ broken")
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_roundtrip_side_mouse_buttons(self):
        for key in ("Mouse4", "Mouse5"):
            with self.subTest(key=key):
                settings = AppSettings(CaptureBinding(key=key, ctrl=False, alt=False, shift=False))
                save_settings(settings, self.path)
                self.assertEqual(load_settings(self.path), settings)

    def test_explicit_save_backs_up_invalid_original(self):
        original = b"{ broken\xff"
        self.write_raw(original)
        save_settings(AppSettings(), self.path)
        backups = list(self.path.parent.glob("settings.json.*.invalid"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(load_settings(self.path), AppSettings())

    def test_explicit_save_keeps_multiple_invalid_backups(self):
        self.write_raw("first invalid")
        save_settings(AppSettings(), self.path)
        self.write_raw("second invalid")
        save_settings(AppSettings(), self.path)
        backups = list(self.path.parent.glob("settings.json.*.invalid"))
        self.assertEqual({item.read_text() for item in backups}, {"first invalid", "second invalid"})

    def test_save_over_valid_preferences_does_not_create_backup(self):
        save_settings(AppSettings(), self.path)
        settings = AppSettings(CaptureBinding(key="J", ctrl=True, alt=False, shift=False))
        save_settings(settings, self.path)
        self.assertEqual(load_settings(self.path), settings)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_invalid_top_level_and_versions_are_rejected(self):
        payload = self.valid_payload()
        for bad in [[], None, {}, {**payload, "enabled": True},
                    {**payload, "version": 2}, {**payload, "version": True},
                    {**payload, "version": "1"}, {**payload, "version": 1.0}]:
            with self.subTest(payload=bad):
                self.write_raw(json.dumps(bad))
                with self.assertRaises(ValueError):
                    load_settings(self.path)

    def test_invalid_binding_schema_is_rejected(self):
        for binding in [None, "K", {}, {**DEFAULT_BINDING.to_dict(), "unknown": 1},
                        {**DEFAULT_BINDING.to_dict(), "ctrl": "true"},
                        {**DEFAULT_BINDING.to_dict(), "key": "Invalid Key"}]:
            with self.subTest(binding=binding):
                self.write_raw(json.dumps({"version": 1, "capture_binding": binding}))
                with self.assertRaises(ValueError):
                    load_settings(self.path)

    def test_duplicate_json_keys_are_rejected(self):
        self.write_raw('{"version":1,"version":1,"capture_binding":{}}')
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            load_settings(self.path)

    def test_excessive_json_nesting_raises_validation_error(self):
        self.write_raw("[" * 2000 + "]" * 2000)
        with self.assertRaises(ValueError):
            load_settings(self.path)

    def test_oversized_file_is_rejected_and_preserved(self):
        original = b" " * (MAX_SETTINGS_BYTES + 1)
        self.write_raw(original)
        with self.assertRaisesRegex(ValueError, "64 KiB"):
            load_settings(self.path)
        self.assertEqual(self.path.read_bytes(), original)

    def test_replace_failure_preserves_old_file_and_cleans_temp(self):
        save_settings(AppSettings(), self.path)
        original = self.path.read_bytes()
        replacement = AppSettings(CaptureBinding(key="F12", ctrl=False, alt=False, shift=False))
        with patch("dolly.settings.os.replace", side_effect=OSError("simulated save failure")):
            with self.assertRaisesRegex(OSError, "simulated"):
                save_settings(replacement, self.path)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_backup_failure_preserves_invalid_file_and_cleans_temp(self):
        self.write_raw("broken original")
        with patch("dolly.settings.shutil.copyfileobj", side_effect=OSError("simulated backup failure")):
            with self.assertRaisesRegex(OSError, "backup failure"):
                save_settings(AppSettings(), self.path)
        self.assertEqual(self.path.read_text(), "broken original")
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_read_errors_propagate(self):
        self.path.mkdir(parents=True)
        with self.assertRaises(OSError):
            load_settings(self.path)

    def test_appdata_location(self):
        with patch.dict(os.environ, {"APPDATA": str(Path(self.directory.name) / "Roaming")}):
            self.assertEqual(settings_path(), Path(self.directory.name) / "Roaming" / "DeadlockDolly" / "settings.json")

    def test_fallback_location_for_missing_or_empty_appdata(self):
        for value in [None, "", "   "]:
            with self.subTest(value=value), patch.dict(os.environ, {}, clear=True):
                if value is not None:
                    os.environ["APPDATA"] = value
                with patch("dolly.settings.Path.home", return_value=Path(self.directory.name)):
                    self.assertEqual(settings_path(), Path(self.directory.name) / ".config" / "DeadlockDolly" / "settings.json")

    def test_default_path_roundtrip(self):
        with patch("dolly.settings.settings_path", return_value=self.path):
            save_settings(AppSettings())
            self.assertEqual(load_settings(), AppSettings())

    def test_invalid_app_settings_object_is_rejected(self):
        with self.assertRaises(ValueError):
            AppSettings(capture_binding={"key": "K"})
        with self.assertRaises(ValueError):
            save_settings({"capture_binding": DEFAULT_BINDING}, self.path)
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
