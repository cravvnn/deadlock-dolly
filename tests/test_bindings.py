"""Capture settings remain explicit, portable and safe to validate before use."""
import dataclasses
import unittest

from dolly.bindings import CaptureBinding, DEFAULT_BINDING, KEY_CHOICES


class BindingTests(unittest.TestCase):
    def test_existing_keyboard_default_is_preserved(self):
        self.assertEqual(DEFAULT_BINDING.label, "Ctrl+Alt+K")
        self.assertEqual(DEFAULT_BINDING.vk, ord("K"))
        self.assertEqual(DEFAULT_BINDING.to_dict(), {
            "key": "K", "ctrl": True, "alt": True, "shift": False,
        })

    def test_mouse_buttons_have_native_side_button_codes(self):
        for key, vk in (("Mouse4", 5), ("Mouse5", 6), ("MiddleMouse", 4)):
            with self.subTest(key=key):
                binding = CaptureBinding(key, ctrl=False, alt=False)
                self.assertEqual(binding.label, key)
                self.assertEqual(binding.vk, vk)
                self.assertEqual(CaptureBinding.from_dict(binding.to_dict()), binding)

    def test_keyboard_and_modifier_labels(self):
        self.assertEqual(CaptureBinding("F24", False, False, True).label, "Shift+F24")
        self.assertEqual(CaptureBinding("7", True, False, True).label, "Ctrl+Shift+7")
        self.assertEqual(CaptureBinding("Enter", False, True).label, "Alt+Enter")
        self.assertEqual(CaptureBinding("F1").vk, 0x70)
        self.assertEqual(CaptureBinding("F24").vk, 0x87)

    def test_every_ui_choice_round_trips(self):
        self.assertEqual(len(KEY_CHOICES), len(set(KEY_CHOICES)))
        self.assertEqual(KEY_CHOICES[:3], ("Mouse4", "Mouse5", "MiddleMouse"))
        for key in KEY_CHOICES:
            binding = CaptureBinding(key, False, False, False)
            self.assertEqual(CaptureBinding.from_dict(binding.to_dict()), binding)
            self.assertIsInstance(binding.vk, int)

    def test_primary_clicks_modifier_keys_and_unsupported_values_are_rejected(self):
        for key in ("Mouse1", "Mouse2", "LeftMouse", "RightMouse", "Ctrl", "Alt",
                    "Shift", "Win", "F25", "k", "", "A;quit", 5, None, []):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    CaptureBinding(key)

    def test_modifiers_require_actual_booleans(self):
        for field in ("ctrl", "alt", "shift"):
            for value in (0, 1, "true", "false", None, []):
                raw = DEFAULT_BINDING.to_dict()
                raw[field] = value
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        CaptureBinding.from_dict(raw)

    def test_json_shape_is_strict(self):
        for raw in (None, [], "Mouse4", {}, {"key": "Mouse4"},
                    {**DEFAULT_BINDING.to_dict(), "vk": 5},
                    {**DEFAULT_BINDING.to_dict(), "command": "quit"}):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    CaptureBinding.from_dict(raw)

    def test_binding_is_immutable_and_serialization_is_detached(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            DEFAULT_BINDING.key = "Mouse4"
        raw = DEFAULT_BINDING.to_dict()
        raw["key"] = "Mouse4"
        self.assertEqual(DEFAULT_BINDING.key, "K")


if __name__ == "__main__":
    unittest.main()
