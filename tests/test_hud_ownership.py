"""Game HUD ownership: playback, replay-HUD, POV, Follow, Stop and disconnect."""
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import dolly.follow_capabilities
import dolly.follow_target
import dolly.replay_camera
from dolly.controller import Controller, OWN_HEALTH_HUD
from dolly.follow_camera import FollowSettings
from core_helpers import FakeConsole

HUD = ("citadel_hud_visible", "citadel_hide_replay_hud", "hud_free_cursor")


class Session:
    """A live game process without the exact-build health panel control."""
    command = ()
    pid = 1

    class process:
        @staticmethod
        def poll():
            return None


def controller():
    c = Controller(lambda *args, **kwargs: None)
    console = FakeConsole()
    c._console = console
    c._request = lambda command, **kwargs: console.request(command)
    c._session = Session()
    c._require_connection = Mock()
    return c, console


def failing_once(console, prefix):
    state = {"failed": False}

    def request(command, **kwargs):
        if not state["failed"] and command.startswith(prefix):
            state["failed"] = True
            raise RuntimeError("Console timed out")
        return console.request(command)
    return request


class ShotHud(unittest.TestCase):
    def shot(self, *, show_hud, hide_hud=True):
        c, console = controller()
        c._show_replay_hud = show_hud
        c._require_demo = lambda **kwargs: {"tick": 100}
        c._require_native_demo = Mock()
        c.status = lambda: {}
        bridge = Mock()
        bridge.status.side_effect = lambda: {"paused": console.paused, "state": "idle", "frame_count": 1}
        c._native_bridge = lambda: bridge
        console.values["hud_free_cursor"] = 1.0
        console.paused = False
        c._prepare_playback_settings(hide_hud)
        worker = threading.Thread(target=c._stop_event.wait, daemon=True)
        worker.start()
        self.addCleanup(c._stop_event.set)
        c._thread = worker
        return c, console

    def test_pause_and_resume_during_shot_keep_the_users_hud_original(self):
        c, console = self.shot(show_hud=False)
        c.toggle_replay()
        c.toggle_replay()
        self.assertEqual(c._replay_hud_restore, {})
        c._finish_playback()
        c._halt(native_action="release")
        self.assertEqual(console.values["citadel_hud_visible"], 1)
        self.assertEqual(console.values["citadel_hide_replay_hud"], 0)
        self.assertEqual(c._replay_hud_restore, {})
        self.assertEqual(c._playback_restore, {})

    def test_resume_during_hidden_shot_does_not_record_the_hud(self):
        c, console = self.shot(show_hud=True)
        c.toggle_replay()
        c.toggle_replay()
        self.assertEqual(console.values["citadel_hud_visible"], 0)
        self.assertEqual(console.values["citadel_hide_replay_hud"], 1)

    def test_shot_finish_returns_the_replay_hud_before_playback_settings(self):
        c, console = self.shot(show_hud=False, hide_hud=False)
        c.toggle_replay()
        c.toggle_replay()
        self.assertEqual(console.values["citadel_hud_visible"], 0)
        self.assertIsNone(c._finish_playback())
        self.assertEqual({name: console.values[name] for name in HUD},
                         {"citadel_hud_visible": 1, "citadel_hide_replay_hud": 0, "hud_free_cursor": 1})
        self.assertEqual(c._replay_hud_restore, {})


class StopHud(unittest.TestCase):
    def test_halt_stops_the_worker_before_a_failed_replay_hud_restore(self):
        c, console = controller()
        c._require_demo = lambda **kwargs: {"tick": 100}
        console.values.update(citadel_hud_visible=0.0)
        c._replay_hud_restore = {"citadel_hud_visible": 1.0}
        c._replay_hud_session = c._session
        c._request = failing_once(console, "citadel_hud_visible 1")
        worker = threading.Thread(target=c._stop_event.wait, daemon=True)
        worker.start()
        self.addCleanup(c._stop_event.set)
        c._thread = worker
        error = c._halt(native_action="release")
        self.assertFalse(worker.is_alive())
        self.assertIsNone(c._thread)
        self.assertIn("Console timed out", error)
        self.assertEqual(c._replay_hud_restore, {"citadel_hud_visible": 1.0})
        self.assertIsNone(c._halt(native_action="release"))
        self.assertEqual(console.values["citadel_hud_visible"], 1)

    def test_stop_continues_after_a_failed_cvar_restore(self):
        c, console = controller()
        c._require_demo = lambda **kwargs: {"tick": 100}
        c._restore_game_ui_settings = lambda: "Replay UI pending."
        c._restore = {"r_aspectratio": 1.0}
        console.fail_commands.add("r_aspectratio 1")
        c.end_matte_layer = Mock()
        c.reset_layer_modes = Mock()
        c.stop()
        c.end_matte_layer.assert_called_once()
        c.reset_layer_modes.assert_called_once()
        message = c.status()["message"]
        self.assertIn("Replay UI pending.", message)
        self.assertIn("Could not restore all cvars", message)
        self.assertEqual(c._restore, {"r_aspectratio": 1.0})

    def test_disconnect_finishes_when_render_scale_restore_fails(self):
        c, console = controller()
        c.stop = Mock()
        c.clear_export_resolution = Mock(side_effect=RuntimeError("Reconnect to restore the render scale."))
        c.disconnect()
        self.assertTrue(console.closed)
        self.assertIsNone(c._console)
        status = c.status()
        self.assertFalse(status["connected"])
        self.assertIn("Reconnect to restore the render scale.", status["message"])


class PovHud(unittest.TestCase):
    def test_pov_finish_retries_a_failed_ui_restore(self):
        c, console = controller()
        c._pov_active = True
        c._follow_active = False
        c._halt = Mock()
        c._finish_playback = Mock()
        c._native_bridge = lambda: None
        c._restore_spectator_view = Mock()
        c._restore_spectator_hero = Mock()
        console.values.update(citadel_hud_visible=0.0, citadel_hide_replay_hud=1.0, hud_free_cursor=0.0)
        c._game_ui_restore = {"citadel_hud_visible": 1.0, "citadel_hide_replay_hud": 0.0, "hud_free_cursor": 1.0}
        c._request = failing_once(console, "citadel_hud_visible 1")
        restore = Mock(wraps=c._restore_game_ui_settings)
        c._restore_game_ui_settings = restore
        with self.assertRaises(RuntimeError):
            c.finish_pov_recording()
        self.assertTrue(c._pov_active)
        c.finish_pov_recording()
        self.assertEqual(restore.call_count, 2)
        self.assertEqual(c._halt.call_count, 1)
        self.assertFalse(c._pov_active)
        self.assertTrue(c._game_ui_visible)
        self.assertEqual({name: console.values[name] for name in HUD},
                         {"citadel_hud_visible": 1, "citadel_hide_replay_hud": 0, "hud_free_cursor": 1})


class FollowHud(unittest.TestCase):
    def select(self, reveal_fails=False):
        c, console = controller()
        for name in ("_require_quiet_session", "_require_demo", "_require_native_demo", "_seek_tick", "_halt"):
            setattr(c, name, Mock())
        c._recorder_active = lambda: False
        c._demo = "x.dem"
        bridge = Mock()
        bridge.status.return_value = {"paused": True, "tick": 500}
        player = {"handle": (1 << 15) | 5, "entity_index": 5, "model": 7, "model_path": "heroes/x.vmdl"}
        bridge.editor_roster.return_value = {"players": [dict(player)]}
        c._native_bridge = lambda: bridge

        def toggle_game_ui(enabled=None, **kwargs):
            console.request("citadel_hud_visible 1; citadel_hide_replay_hud 0; hud_free_cursor 1")
            c._game_ui_visible = True
        c.toggle_game_ui = toggle_game_ui
        if reveal_fails:
            request = c._request
            c._request = lambda command, **kwargs: (
                (_ for _ in ()).throw(RuntimeError("Console timed out"))
                if command.startswith("citadel_hud_visible 1") else request(command))

        class Monitor:
            def __init__(self, *args):
                pass

            def sample(self):
                pass

            def sample_selection_context(self):
                pass

            def sample_target(self, require_chase=True):
                raise RuntimeError("The game did not confirm the selected Follow hero camera.")

            def close(self):
                pass
        with patch.object(dolly.follow_target, "FollowTargetMonitor", Monitor), \
                patch.object(dolly.follow_capabilities, "FollowCapabilityMonitor", Monitor), \
                patch.object(dolly.replay_camera.ReplayCameraMonitor, "sample",
                             staticmethod(lambda monitor: {"coherent": True, "ready": True})):
            with self.assertRaisesRegex(RuntimeError, "did not confirm"):
                c.start_selected_game_follow(FollowSettings(distance=200, shoulder=0, height=0), player)
        return c, console

    def test_failed_follow_selection_returns_the_replay_ui(self):
        c, console = self.select()
        self.assertFalse(c._follow_active)
        self.assertTrue(c._game_ui_visible)
        self.assertEqual({name: console.values[name] for name in HUD},
                         {"citadel_hud_visible": 1, "citadel_hide_replay_hud": 0, "hud_free_cursor": 1})

    def test_failed_follow_selection_reports_a_hidden_replay_ui(self):
        c, console = self.select(reveal_fails=True)
        self.assertEqual(console.values["citadel_hud_visible"], 0)
        self.assertFalse(c._game_ui_visible)

    def test_pawn_identity_rule(self):
        valid = dolly.follow_target.valid_pawn_identity
        self.assertTrue(valid((1 << 15) | 5, 5))
        for handle, index in ((0xffffffff, 0x7fff), (0xfffffffe, 0x7ffe), (1 << 15, 0), (0xffff, 0x7fff),
                              ((1 << 15) | 6, 5), (True, 1), ((1 << 15) | 5, 5.0), (None, 5)):
            with self.subTest(handle=handle, index=index):
                self.assertFalse(valid(handle, index))


class SmallHudOwners(unittest.TestCase):
    def test_dof_shader_original_is_journaled_before_the_write(self):
        c, console = controller()
        console.values["mat_disable_dynamic_shader_compile"] = 1.0
        reads = {"count": 0}

        def request(command, **kwargs):
            if command == "mat_disable_dynamic_shader_compile":
                reads["count"] += 1
                if reads["count"] == 2:
                    raise RuntimeError("Console timed out")
            return console.request(command)
        c._request = request
        c.dof_preflight = lambda *args, **kwargs: {"status": "ready", "dynamic_compile": {"missing": ["vfx_dx11.dll"]}}
        project = Mock()
        project.setup_values = {"r_dof_override": 1}
        project.tracks = []
        c.ensure_native_dof_shader_support(project)
        self.assertEqual(c._dof_shader_restore.get("mat_disable_dynamic_shader_compile"), 1.0)
        self.assertIsNone(c._restore_dof_shader_settings())
        self.assertEqual(console.values["mat_disable_dynamic_shader_compile"], 1.0)

    def test_failed_dof_preview_drops_the_paused_camera(self):
        c, console = controller()
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
        c._native_pose = lambda status: dict(x=0, y=0, z=0, pitch=0, yaw=0, roll=0, aspect_ratio=16 / 9)
        c._demo = Path("x.dem")
        c._native_active = c._native_manual = True
        c._set_paused_pose(dict(x=1), 100)
        with self.assertRaisesRegex(RuntimeError, "Native preparation failed"):
            c.preview_native_effects(types.SimpleNamespace(keyframes=[]), 0.0)
        self.assertIsNone(c._paused_pose)
        self.assertFalse(c._state["paused_camera"])

    def test_playback_restore_keeps_a_late_unsafe_reveal_hidden(self):
        c, console = controller()
        console.values.update(citadel_hud_visible=0.0, citadel_hide_replay_hud=1.0)
        c._game_ui_restore = {OWN_HEALTH_HUD: 0}
        c._playback_restore = {"citadel_hud_visible": 1.0, "citadel_hide_replay_hud": 0.0}
        c._verify_health_hud_handoff = Mock(side_effect=[None, RuntimeError("Hero view changed")])
        c._restore_playback_settings()
        self.assertEqual(console.values["citadel_hud_visible"], 0)
        self.assertEqual(console.values["citadel_hide_replay_hud"], 1)
        self.assertEqual(c._game_ui_restore["citadel_hud_visible"], 1)
        self.assertEqual(c._game_ui_restore["citadel_hide_replay_hud"], 0)


if __name__ == "__main__":
    unittest.main()
