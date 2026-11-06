"""Scene toggles: verified choices, exact off values, retry, fixed-size wire."""
import unittest
from unittest.mock import Mock, patch
from dolly.controller import Controller, HEALTHBAR_SCALE_CVARS, HEALTHBAR_SWITCH_CVARS
from dolly.editor_wire import CONFIG, pack_config
from core_helpers import FakeConsole


class SceneAppearance(unittest.TestCase):
    def setUp(self):
        self.c = Controller(lambda *args, **kwargs: None)
        self.console = FakeConsole()
        self.c._console = self.console
        self.c._request = lambda command, **kwargs: self.console.request(command)
        self.c._require_quiet_session = Mock()
        self.opacity = {"citadel_camera_fade_viewed_near_opacity": .25,
                        "citadel_camera_fade_other_near_opacity": .625}
        self.console.values.update(self.opacity)

    def test_opacity_exact_restore_failure_retry_and_unreadable_enable(self):
        self.c.near_player_opacity_fix()
        self.assertEqual(self.c._near_opacity_restore, self.opacity)
        self.assertEqual([self.console.values[n] for n in self.opacity], [1, 1])
        self.assertEqual(self.c.status()["scene_appearance_flags"], 4)
        with patch.object(self.c, "_console_cvar", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "remains pending"):
                self.c.near_player_opacity_fix()
        self.assertEqual(self.c._near_opacity_restore, self.opacity)
        self.assertEqual(self.c.status()["scene_appearance_flags"], 4)
        self.c.near_player_opacity_fix()
        self.assertEqual({n: self.console.values[n] for n in self.opacity}, self.opacity)
        self.assertEqual(self.c.status()["scene_appearance_flags"], 0)
        with patch.object(self.c, "_console_cvar", return_value=None):
            before = dict(self.console.values)
            with self.assertRaisesRegex(RuntimeError, "No settings were changed"):
                self.c.near_player_opacity_fix()
            self.assertEqual(self.console.values, before)
        self.assertFalse(self.c._near_opacity_restore)

    def test_glow_and_both_healthbar_backends_publish_confirmed_state(self):
        self.console.values.update({"citadel_boss_glow_disabled": 0,
            "citadel_player_glow_disabled": 0, "citadel_trooper_glow_disabled": 0,
            "r_citadel_glow_health_bars": 1})
        # External game settings must never make a Hide click turn glow on.
        self.console.values["citadel_boss_glow_disabled"] = 1
        self.c.toggle_citadel_glow()
        self.assertEqual(self.c.status()["scene_appearance_flags"], 1)
        self.c.toggle_citadel_glow()
        self.assertEqual(self.c.status()["scene_appearance_flags"], 0)
        for backend in (HEALTHBAR_SCALE_CVARS, HEALTHBAR_SWITCH_CVARS):
            self.console.values.update(dict.fromkeys(backend, 1))
            self.c.toggle_healthbars()
            self.assertEqual(self.c.status()["scene_appearance_flags"], 2)
            self.c.toggle_healthbars()
            self.assertEqual(self.c.status()["scene_appearance_flags"], 0)
            self.assertTrue(all(self.console.values[n] == 1 for n in backend))
            # Already hidden externally: on owns the hide choice, off restores zero.
            self.console.values.update(dict.fromkeys(backend, 0))
            self.c.toggle_healthbars()
            self.assertEqual(self.c.status()["scene_appearance_flags"], 2)
            self.assertTrue(all(self.console.values[n] == 0 for n in backend))
            self.c.toggle_healthbars()
            self.assertEqual(self.c.status()["scene_appearance_flags"], 0)
            self.assertTrue(all(self.console.values[n] == 0 for n in backend))
            for n in backend:
                self.console.values.pop(n)

    def test_wire_uses_only_previously_reserved_last_byte(self):
        baseline = pack_config(2, 0, 0, {})
        self.assertEqual(len(baseline), 448)
        for flags in range(8):
            data = pack_config(2, 0, 0, {"scene_appearance_flags": flags})
            self.assertEqual(data[:-1], baseline[:-1])
            self.assertEqual(data[-1], flags)
        for flags in (-1, 8, True, 1.5):
            with self.assertRaises(ValueError):
                pack_config(2, 0, 0, {"scene_appearance_flags": flags})
