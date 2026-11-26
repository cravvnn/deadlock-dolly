"""Debug view option mapping, undo-before-apply sequencing, toggle tracking, and UI wiring."""
from __future__ import annotations

from dataclasses import fields
import json
from pathlib import Path
import re
import struct
import tempfile
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from core_helpers import FakeConsole
from dolly.controller import Controller
from dolly.debug_view import (
    DEBUG_VIEW_BY_ID,
    DEBUG_VIEW_DEFAULT,
    DEBUG_VIEW_IDS,
    DEBUG_VIEW_LABELS,
    DEBUG_VIEW_OPTIONS,
    DebugViewState,
    debug_view_id,
    debug_view_label,
)
from dolly import editing_session, editor_dispatch, editor_publication, editor_wire, graphics_profiles, gui
from dolly.editor_actions import ACTION_ORDER
from dolly.path import Project
from dolly.settings import AppSettings, load_settings, save_settings

ROOT = Path(__file__).resolve().parents[1]


class DebugViewStateTests(unittest.TestCase):
    def setUp(self):
        self.sent: list[tuple[str, bool]] = []
        self.cvars: dict[str, float | None] = {"sv_cheats": 0.0}
        self.state = DebugViewState()

    def send(self, command: str, *, allow_error: bool = False):
        self.sent.append((command, allow_error))
        if command == "sv_cheats 1":
            self.cvars["sv_cheats"] = 1.0
        elif command == "sv_cheats 0":
            self.cvars["sv_cheats"] = 0.0
        return ""

    def read_cvar(self, name: str):
        return self.cvars.get(name)

    def commands(self) -> list[str]:
        return [cmd for cmd, _allow in self.sent]

    def test_all_options_map_to_expected_commands(self):
        expected_apply = {
            "albedo": ["mat_debug ALBEDO"],
            "normals": ["mat_debug NORMAL_WS"],
            "roughness": ["mat_debug ROUGHNESS"],
            "lighting_complexity": ["mat_debug LIGHTING_COMPLEXITY"],
            "fullbright": ["mat_debug FULLBRIGHT"],
            "wireframe": ["sv_cheats 1", "mat_wireframe 1"],
            "objects": ["sc_visualize_sceneobjects SCENEOBJECT_VIS_OBJECT"],
            "materials": ["sc_visualize_sceneobjects SCENEOBJECT_VIS_MATERIAL"],
            "lod": ["sc_visualize_sceneobjects SCENEOBJECT_VIS_LOD"],
            "visibility_grid": ["vis_debug_show"],
            "particle_overlays": [
                "sv_cheats 1",
                "r_showdebugoverlays 1",
                "r_particle_debug_show_control_points 1",
                "r_particle_debug_show_sort_position 1",
            ],
        }
        for option_id, expected in expected_apply.items():
            with self.subTest(option=option_id):
                state = DebugViewState()
                self.sent.clear()
                self.cvars["sv_cheats"] = 0.0
                label = debug_view_label(option_id)
                self.assertEqual(debug_view_id(label), option_id)
                applied = state.apply(label, send=self.send, read_cvar=self.read_cvar)
                self.assertEqual(applied, option_id)
                self.assertEqual(self.commands(), expected)

    def test_unknown_option_rejected_without_sending(self):
        with self.assertRaises(ValueError):
            self.state.apply("Not a real view", send=self.send, read_cvar=self.read_cvar)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.state.current, DEBUG_VIEW_DEFAULT)

    def test_undo_before_apply_sequencing_prevents_stacked_modes(self):
        # Normal -> Albedo
        self.state.apply("albedo", send=self.send, read_cvar=self.read_cvar)
        self.assertEqual(self.commands(), ["mat_debug ALBEDO"])

        # Albedo -> Normals (must clear mat_debug before applying next)
        self.sent.clear()
        self.state.apply("normals", send=self.send, read_cvar=self.read_cvar)
        self.assertEqual(self.commands(), ["mat_debug NONE", "mat_debug NORMAL_WS"])

        # Normals -> Wireframe (clear mat_debug, then cheats + wireframe)
        self.sent.clear()
        self.state.apply("wireframe", send=self.send, read_cvar=self.read_cvar)
        self.assertEqual(self.commands(), ["mat_debug NONE", "sv_cheats 1", "mat_wireframe 1"])
        self.assertTrue(self.state.cheats_owned)

        # Wireframe -> Objects (clear wireframe BEFORE sv_cheats 0, then apply objects)
        self.sent.clear()
        self.state.apply("objects", send=self.send, read_cvar=self.read_cvar)
        self.assertEqual(
            self.commands(),
            ["mat_wireframe 0", "sv_cheats 0", "sc_visualize_sceneobjects SCENEOBJECT_VIS_OBJECT"],
        )
        self.assertFalse(self.state.cheats_owned)

        # Objects -> Materials
        self.sent.clear()
        self.state.apply("materials", send=self.send, read_cvar=self.read_cvar)
        self.assertEqual(
            self.commands(),
            [
                "sc_visualize_sceneobjects SCENEOBJECT_VIS_NONE",
                "sc_visualize_sceneobjects SCENEOBJECT_VIS_MATERIAL",
            ],
        )

        # Materials -> Visibility grid
        self.sent.clear()
        self.state.apply("visibility_grid", send=self.send, read_cvar=self.read_cvar)
        self.assertEqual(
            self.commands(),
            ["sc_visualize_sceneobjects SCENEOBJECT_VIS_NONE", "vis_debug_show"],
        )
        self.assertTrue(self.state.vis_grid_active)

        # Visibility grid -> Particle overlays (toggle vis off ONCE before enabling cheats + particle overlays)
        self.sent.clear()
        self.state.apply("particle_overlays", send=self.send, read_cvar=self.read_cvar)
        self.assertEqual(
            self.commands(),
            [
                "vis_debug_show",
                "sv_cheats 1",
                "r_showdebugoverlays 1",
                "r_particle_debug_show_control_points 1",
                "r_particle_debug_show_sort_position 1",
            ],
        )
        self.assertFalse(self.state.vis_grid_active)
        self.assertTrue(self.state.cheats_owned)

        # Particle overlays -> Fullbright (clear particle overlays BEFORE sv_cheats 0, then apply fullbright)
        self.sent.clear()
        self.state.apply("fullbright", send=self.send, read_cvar=self.read_cvar)
        self.assertEqual(
            self.commands(),
            [
                "r_particle_debug_show_control_points 0",
                "r_particle_debug_show_sort_position 0",
                "r_showdebugoverlays 0",
                "sv_cheats 0",
                "mat_debug FULLBRIGHT",
            ],
        )
        self.assertFalse(self.state.cheats_owned)

    def test_vis_debug_show_toggle_tracked_and_never_double_toggled(self):
        # Turning on from Normal sends vis_debug_show once.
        self.state.apply("visibility_grid", send=self.send, read_cvar=self.read_cvar)
        self.assertEqual(self.commands(), ["vis_debug_show"])
        self.assertTrue(self.state.vis_grid_active)

        # Re-selecting Visibility grid is a no-op (never toggles it back off).
        self.sent.clear()
        self.state.apply("visibility_grid", send=self.send, read_cvar=self.read_cvar)
        self.assertEqual(self.commands(), [])
        self.assertTrue(self.state.vis_grid_active)

        # Returning to Normal toggles vis_debug_show off exactly once.
        self.sent.clear()
        self.state.apply("normal", send=self.send, read_cvar=self.read_cvar)
        self.assertEqual(self.commands().count("vis_debug_show"), 1)
        # The toggle goes last: it is the one command whose state cannot be read back.
        self.assertEqual(self.commands()[-1], "vis_debug_show")
        self.assertFalse(self.state.vis_grid_active)

        # Selecting Normal again never sends vis_debug_show.
        self.sent.clear()
        self.state.apply("normal", send=self.send, read_cvar=self.read_cvar)
        self.assertNotIn("vis_debug_show", self.commands())
        self.assertFalse(self.state.vis_grid_active)

    def test_sv_cheats_only_turned_off_when_dolly_enabled_it(self):
        # User already had sv_cheats 1 enabled externally.
        self.cvars["sv_cheats"] = 1.0
        self.state.apply("wireframe", send=self.send, read_cvar=self.read_cvar)
        self.assertEqual(self.commands(), ["mat_wireframe 1"])
        self.assertFalse(self.state.cheats_owned)

        self.sent.clear()
        self.state.apply("normal", send=self.send, read_cvar=self.read_cvar)
        self.assertIn("mat_wireframe 0", self.commands())
        self.assertNotIn("sv_cheats 0", self.commands())
        self.assertEqual(self.cvars["sv_cheats"], 1.0)

    def test_failed_wireframe_rolls_back_dolly_owned_cheats(self):
        def failing_send(command: str, *, allow_error: bool = False):
            self.sent.append((command, allow_error))
            if command == "mat_wireframe 1":
                raise RuntimeError("rejected")
            if command == "sv_cheats 1":
                self.cvars["sv_cheats"] = 1.0
            elif command == "sv_cheats 0":
                self.cvars["sv_cheats"] = 0.0
            return ""

        with self.assertRaisesRegex(RuntimeError, "rejected"):
            self.state.apply("wireframe", send=failing_send, read_cvar=self.read_cvar)
        self.assertEqual(self.commands(), ["sv_cheats 1", "mat_wireframe 1", "sv_cheats 0"])
        self.assertFalse(self.state.cheats_owned)
        self.assertEqual(self.state.current, DEBUG_VIEW_DEFAULT)

    def test_reapply_restores_mode_after_replay_reload(self):
        self.state.apply("wireframe", send=self.send, read_cvar=self.read_cvar)
        # Simulate disconnect + playdemo resetting sv_cheats to 0.
        self.cvars["sv_cheats"] = 0.0
        self.sent.clear()
        self.state.reapply(send=self.send, read_cvar=self.read_cvar)
        self.assertEqual(self.commands(), ["sv_cheats 1", "mat_wireframe 1"])
        self.assertTrue(self.state.cheats_owned)

    def test_grid_reapplied_once_after_reload(self):
        # A reload turns the grid off (live-verified); reapply must turn it on exactly once.
        self.state.apply("visibility_grid", send=self.send, read_cvar=self.read_cvar)
        self.state.after_reload()
        self.sent.clear()
        self.state.reapply(send=self.send, read_cvar=self.read_cvar)
        self.state.reapply(send=self.send, read_cvar=self.read_cvar)
        self.assertEqual(self.commands(), ["vis_debug_show"])
        self.assertTrue(self.state.vis_grid_active)

    def test_dropped_view_after_failed_reload_never_inverts_grid(self):
        # Grid on, replay reloaded, reload fails before reapply: the game is Normal.
        self.state.apply("visibility_grid", send=self.send, read_cvar=self.read_cvar)
        self.state.drop_view()
        self.assertEqual(self.state.current, DEBUG_VIEW_DEFAULT)
        self.sent.clear()
        # Choosing another view must not toggle the (already off) grid back on.
        self.state.apply("albedo", send=self.send, read_cvar=self.read_cvar)
        self.assertNotIn("vis_debug_show", self.commands())

    def test_dropped_view_keeps_cheats_ownership(self):
        self.state.apply("wireframe", send=self.send, read_cvar=self.read_cvar)
        self.state.drop_view()
        self.sent.clear()
        self.state.apply("normal", send=self.send, read_cvar=self.read_cvar)
        self.assertIn("sv_cheats 0", self.commands())
        self.assertFalse(self.state.cheats_owned)

    def test_undo_continues_after_a_failed_step(self):
        self.state.apply("particle_overlays", send=self.send, read_cvar=self.read_cvar)
        self.sent.clear()

        def flaky_send(command, *, allow_error=False):
            self.sent.append((command, allow_error))
            if command == "r_showdebugoverlays 0":
                raise RuntimeError("timed out")
            return ""

        with self.assertRaisesRegex(RuntimeError, "timed out"):
            self.state.apply("normal", send=flaky_send, read_cvar=self.read_cvar)
        self.assertIn("sv_cheats 0", self.commands())
        self.assertIn("r_particle_debug_show_control_points 0", self.commands())
        self.assertFalse(self.state.cheats_owned)

    def test_reselecting_a_view_reasserts_it(self):
        self.state.apply("albedo", send=self.send, read_cvar=self.read_cvar)
        self.sent.clear()
        self.state.apply("albedo", send=self.send, read_cvar=self.read_cvar)
        self.assertEqual(self.commands(), ["mat_debug ALBEDO"])


class DebugViewControllerTests(unittest.TestCase):
    def setUp(self):
        self.c = Controller(lambda *args, **kwargs: None)
        self.console = FakeConsole()
        self.console.values["sv_cheats"] = 0
        self.sent: list[str] = []
        self.c._console = self.console
        self.c._session = Mock()
        self.c._session.process = Mock()
        self.c._session.process.poll.return_value = None
        self.c._require_quiet_session = Mock()

        def fake_request(command, **kwargs):
            self.sent.append(command)
            return self.console.request(command)

        self.c._request = fake_request

    def test_controller_set_debug_view_and_stop_preserves_selection(self):
        self.assertEqual(self.c.status()["debug_view"], "normal")
        self.c.set_debug_view("Normals")
        self.assertEqual(self.c.status()["debug_view"], "normals")
        self.assertIn("mat_debug NORMAL_WS", self.sent)

        # Stopping playback/export must NOT reset the user's chosen debug view.
        self.sent.clear()
        with patch.object(self.c, "_halt", return_value=None), \
             patch.object(self.c, "_native_bridge", return_value=None), \
             patch.object(self.c, "_restore_playback_settings", return_value=None), \
             patch.object(self.c, "_restore_game_ui_settings", return_value=None), \
             patch.object(self.c, "end_matte_layer"), \
             patch.object(self.c, "reset_layer_modes"):
            self.c.stop()
        self.assertEqual(self.c.status()["debug_view"], "normals")
        self.assertNotIn("mat_debug NONE", self.sent)

    def test_controller_disconnect_resets_live_game_and_local_state(self):
        self.c.set_debug_view("visibility_grid")
        self.assertTrue(self.c._debug_view.vis_grid_active)
        self.sent.clear()
        with patch("dolly.controller.console_connected", return_value=True), \
             patch.object(self.c, "stop"), \
             patch.object(self.c, "clear_export_resolution"), \
             patch.object(self.c, "_restore_dof_shader_settings", return_value=""):
            self.c.disconnect()
        self.assertEqual(self.sent.count("vis_debug_show"), 1)
        self.assertEqual(self.c._debug_view.current, "normal")
        self.assertFalse(self.c._debug_view.vis_grid_active)
        self.assertEqual(self.c.status()["debug_view"], "normal")

    def test_controller_reapply_failure_continues_in_normal(self):
        self.c.set_debug_view("wireframe")
        self.c._debug_view.after_reload()
        real = self.c._request

        def failing(command, **kwargs):
            if command == "mat_wireframe 1":
                raise RuntimeError("rejected")
            return real(command, **kwargs)

        self.c._request = failing
        self.c._reapply_debug_view()  # must not raise: a viewing aid never fails a take
        self.assertEqual(self.c.status()["debug_view"], "normal")
        self.assertFalse(self.c._debug_view.cheats_owned)

    def test_console_lost_keeps_view_tracked_until_reconnect_undo(self):
        self.c.set_debug_view("visibility_grid")
        with patch("dolly.controller.console_connected", return_value=False),              patch.object(self.c, "stop"),              patch.object(self.c, "clear_export_resolution"),              patch.object(self.c, "_restore_dof_shader_settings", return_value=""):
            self.c.disconnect()
        # The game still shows the grid, so Dolly keeps tracking it ...
        self.assertTrue(self.c._debug_view.vis_grid_active)
        # ... and the next Connect to the same process turns it off once.
        self.sent.clear()
        self.c._undo_debug_view_after_reconnect()
        self.assertEqual(self.sent.count("vis_debug_show"), 1)
        self.assertEqual(self.c._debug_view.current, "normal")
        self.assertFalse(self.c._debug_view.vis_grid_active)

    def test_controller_recover_dead_session_resets_state_without_sending(self):
        self.c.set_debug_view("wireframe")
        self.assertEqual(self.c._debug_view.current, "wireframe")
        self.assertTrue(self.c._debug_view.cheats_owned)
        self.sent.clear()
        self.c._session.process.poll.return_value = 1
        self.c._session.close = Mock()
        self.assertTrue(self.c.recover_dead_session())
        self.assertEqual(self.sent, [])
        self.assertEqual(self.c._debug_view.current, "normal")
        self.assertFalse(self.c._debug_view.cheats_owned)
        self.assertFalse(self.c._debug_view.vis_grid_active)
        self.assertEqual(self.c.status()["debug_view"], "normal")


class DebugViewNotPersistedTests(unittest.TestCase):
    def test_not_saved_in_project_or_settings_or_editing_configs(self):
        project_dict = Project(name="Shot").to_dict()
        self.assertNotIn("debug_view", project_dict)
        self.assertFalse(any("debug_view" in key for key in project_dict))

        setting_names = {f.name for f in fields(AppSettings)}
        self.assertNotIn("debug_view", setting_names)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            save_settings(AppSettings(), path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("debug_view", raw)
            loaded = load_settings(path)
            self.assertFalse(hasattr(loaded, "debug_view"))

        debug_cvars = {
            "mat_debug", "mat_fullbright", "mat_wireframe",
            "sc_visualize_sceneobjects", "vis_debug_show", "sv_cheats",
            "r_showdebugoverlays", "r_particle_debug_show_control_points",
            "r_particle_debug_show_sort_position",
        }
        self.assertTrue(debug_cvars.isdisjoint(editing_session.AUDIO_CONVARS))
        self.assertTrue(debug_cvars.isdisjoint(editing_session.DISPLAY_FIELDS))
        self.assertTrue(debug_cvars.isdisjoint(graphics_profiles.FIELD_LABELS))
        self.assertTrue(debug_cvars.isdisjoint(graphics_profiles.QUALITY_FIELDS))


class DebugViewGuiTests(unittest.TestCase):
    def test_look_page_debug_view_widget_state_and_bento_alignment(self):
        root = tk.Tk()
        root.withdraw()
        app = None
        try:
            with patch.object(gui.DollyApp, "_submit", return_value=False):
                app = gui.DollyApp(root)
            app.notebook.select(app.cvar_tab)
            root.update_idletasks()
            root.update()

            # Starts on Normal and disabled when no replay is running.
            self.assertEqual(app.debug_view.get(), "Normal")
            self.assertEqual(str(app.debug_view_combo.cget("state")), "disabled")
            self.assertEqual(
                tuple(app.debug_view_combo.cget("values")),
                tuple(DEBUG_VIEW_LABELS[key] for key in DEBUG_VIEW_IDS),
            )
            self.assertIsNotNone(app.debug_card.info)

            # Bento alignment: Track Editor bottom aligns with Debug View bottom.
            lv = app.look_view
            self.assertEqual(lv.tracks_card.rect[3], lv.particles_card.rect[3])
            self.assertEqual(lv.editor_card.rect[3], lv.debug_card.rect[3])
            self.assertEqual(lv.debug_card.rect[1] - lv.fixed_card.rect[3], 20)

            # Poll with a live editing_ready session enables the dropdown.
            live_status = {
                "connected": True,
                "game_running": True,
                "unlocker_ready": True,
                "playing": False,
                "startup_stage": "editing_ready",
                "camera_backend": "native",
                "debug_view": "normals",
                "message": "Replay ready.",
            }
            with patch.object(app.controller, "status", return_value=live_status), \
                 patch.object(app, "_refresh_video"), \
                 patch.object(app, "_refresh_discord_presence"), \
                 patch.object(app, "_check_paused_memory"), \
                 patch.object(app, "_refresh_renderer_pressure"), \
                 patch.object(app, "_check_capture_listener"), \
                 patch("dolly.editor_session.poll"):
                app._poll_once()
            self.assertEqual(str(app.debug_view_combo.cget("state")), "readonly")
            self.assertEqual(app.debug_view.get(), "Normals")

            # Parity with in-game drawer and Controller._require_quiet_session:
            # disabled while playing, recording, running layer queues/takes, or RenderLock export.
            orig_video_export = app.video_export
            blockers = [
                ("playing", lambda: live_status.__setitem__("playing", True),
                 lambda: live_status.__setitem__("playing", False)),
                ("recording", lambda: setattr(app, "video_export", SimpleNamespace(status=lambda: {"state": "recording"})),
                 lambda: setattr(app, "video_export", orig_video_export)),
                ("layer_queue", lambda: setattr(app, "_layer_queue", [("world", "color")]),
                 lambda: setattr(app, "_layer_queue", [])),
                ("active_layer_take", lambda: setattr(app, "_active_layer_take", ("world", "color")),
                 lambda: setattr(app, "_active_layer_take", None)),
                ("renderlock_export", lambda: setattr(app, "_renderlock_cancel", object()),
                 lambda: setattr(app, "_renderlock_cancel", None)),
            ]
            for label, enable, restore in blockers:
                with self.subTest(desktop_blocker=label):
                    enable()
                    try:
                        with patch.object(app.controller, "status", return_value=live_status), \
                             patch.object(app, "_refresh_video"), \
                             patch.object(app, "_refresh_discord_presence"), \
                             patch.object(app, "_check_paused_memory"), \
                             patch.object(app, "_refresh_renderer_pressure"), \
                             patch.object(app, "_check_capture_listener"), \
                             patch("dolly.editor_session.poll"):
                            app._poll_once()
                            self.assertEqual(str(app.debug_view_combo.cget("state")), "disabled")
                            app.debug_view.set("Wireframe")
                            with patch.object(app, "_submit") as submit_mock:
                                app._debug_view_changed()
                                submit_mock.assert_not_called()
                            self.assertEqual(app.debug_view.get(), "Normals")
                    finally:
                        restore()
                    with patch.object(app.controller, "status", return_value=live_status), \
                         patch.object(app, "_refresh_video"), \
                         patch.object(app, "_refresh_discord_presence"), \
                         patch.object(app, "_check_paused_memory"), \
                         patch.object(app, "_refresh_renderer_pressure"), \
                         patch.object(app, "_check_capture_listener"), \
                         patch("dolly.editor_session.poll"):
                        app._poll_once()
                    self.assertEqual(str(app.debug_view_combo.cget("state")), "readonly")

            # Poll after session ends disables dropdown and resets to Normal.
            closed_status = {
                "connected": False,
                "game_running": False,
                "unlocker_ready": False,
                "playing": False,
                "startup_stage": "game_closed",
                "camera_backend": "native",
                "debug_view": "normal",
                "message": "Deadlock closed.",
            }
            with patch.object(app.controller, "status", return_value=closed_status), \
                 patch.object(app, "_refresh_video"), \
                 patch.object(app, "_refresh_discord_presence"), \
                 patch.object(app, "_check_paused_memory"), \
                 patch.object(app, "_refresh_renderer_pressure"), \
                 patch.object(app, "_check_capture_listener"), \
                 patch("dolly.editor_session.poll"):
                app._poll_once()
            self.assertEqual(str(app.debug_view_combo.cget("state")), "disabled")
            self.assertEqual(app.debug_view.get(), "Normal")
        finally:
            if app is not None:
                app._destroy()
            elif root.winfo_exists():
                root.destroy()


class DebugViewInGameWireTests(unittest.TestCase):
    def test_action_id_and_native_header_and_drawer_labels_match(self):
        names = ACTION_ORDER + editor_wire.EXTRA_ACTIONS
        self.assertEqual(names.index("set_debug_view"), 147)
        self.assertEqual(names[-1], "set_debug_view")

        header = (ROOT / "native" / "include" / "dolly_editor.hpp").read_text(encoding="utf-8")
        self.assertIn("SetDebugView) == 147", header)
        self.assertIn("kEditorActionLast) == 147", header)
        self.assertIn("kEditorDebugViewOffset = 2 * 1024 * 1024 + 60032;", header)
        self.assertEqual(editor_wire.DEBUG_VIEW_OFFSET, 2 * 1024 * 1024 + 60032)
        self.assertIn(f"kEditorDebugViewCount = {len(DEBUG_VIEW_OPTIONS)};", header)

        drawer = (ROOT / "native" / "src" / "dolly_rail_drawers.cpp").read_text(encoding="utf-8")
        match = re.search(r"debug_views\[kEditorDebugViewCount\]\{(.*?)\};", drawer, re.S)
        self.assertIsNotNone(match, "Look drawer debug_views label table missing")
        native_labels = tuple(re.findall(r'"([^"]+)"', match.group(1)))
        self.assertEqual(native_labels, tuple(opt.label for opt in DEBUG_VIEW_OPTIONS))
        self.assertIn("EditorAction::SetDebugView", drawer)

    def test_pack_debug_view_validation_and_layout(self):
        self.assertEqual(editor_wire.DEBUG_VIEW_CONFIG.size, 24)
        self.assertGreaterEqual(
            editor_wire.DEBUG_VIEW_OFFSET,
            editor_wire.CINEMATIC_OFFSET + editor_wire.CINEMATIC_STATUS.size,
        )
        self.assertLessEqual(
            editor_wire.DEBUG_VIEW_OFFSET + editor_wire.DEBUG_VIEW_CONFIG.size,
            2 * 1024 * 1024 + 61440,
        )
        for index in range(len(DEBUG_VIEW_OPTIONS)):
            for available in (False, True):
                packed = editor_wire.pack_debug_view(4, index, available=available)
                self.assertEqual(
                    editor_wire.DEBUG_VIEW_CONFIG.unpack(packed),
                    (editor_wire.DEBUG_VIEW_MAGIC, 4, editor_wire.DEBUG_VIEW_ABI, index, int(available)),
                )
        for bad_index in (-1, len(DEBUG_VIEW_OPTIONS), True, False, 1.5, "normal"):
            with self.assertRaises(ValueError):
                editor_wire.pack_debug_view(2, bad_index, available=True)
        for bad_available in (0, 1, None, "yes"):
            with self.assertRaises(ValueError):
                editor_wire.pack_debug_view(2, 0, available=bad_available)

    def test_unpack_status_decodes_set_debug_view_event(self):
        data = bytearray(editor_wire.STATUS_BYTES)
        editor_wire.HEADER.pack_into(
            data, 0, editor_wire.STATUS_MAGIC, 2, editor_wire.EDITOR_ABI,
            2, 1 | 2 | 4 | 16 | 32 | 64, 0, 0, 1, 0,
            320.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, b"", 0.0, 100, 0, 1,
        )
        editor_wire.EVENT.pack_into(
            data, editor_wire.HEADER.size,
            1, 147, 6.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 100, 1,
        )
        status = editor_wire.unpack_status(bytes(data), ack_event=0)
        self.assertEqual(len(status["events"]), 1)
        self.assertEqual(status["events"][0]["action"], "set_debug_view")
        self.assertEqual(status["events"][0]["action_id"], 147)
        self.assertEqual(status["events"][0]["value"], 6.0)


class DebugViewInGameDispatchAndPublicationTests(unittest.TestCase):
    def _make_app(self, *, status=None, recording_state="idle", busy=False, playing=False):
        c = Controller(lambda *args, **kwargs: None)
        console = FakeConsole()
        console.values["sv_cheats"] = 0
        c._console = console
        c._session = Mock()
        c._session.process = Mock()
        c._session.process.poll.return_value = None
        c._require_quiet_session = Mock()
        c._request = lambda command, **kwargs: console.request(command)
        c._state["startup_stage"] = "editing_ready"

        live_status = status if status is not None else {
            "connected": True,
            "game_running": True,
            "native_editor_active": True,
            "playing": playing,
            "startup_stage": "editing_ready",
            "tick": 120,
        }

        def get_status():
            merged = dict(live_status)
            merged.setdefault("debug_view", c._debug_view.current)
            return merged

        c.status = get_status
        published = []
        bridge = SimpleNamespace(
            configure_editor=Mock(),
            configure_editor_debug_view=lambda view, *, available=False: published.append((view, available)),
            editor_status=lambda: {"console_open": False},
        )
        c._native_bridge = lambda: bridge

        debug_var = Mock()
        debug_var._value = "Normal"
        debug_var.get = lambda: debug_var._value
        debug_var.set = lambda v: setattr(debug_var, "_value", v)

        app = SimpleNamespace(
            controller=c,
            project=Project(name="Shot"),
            app_settings=AppSettings(),
            busy=busy,
            playing=playing,
            native_editor_active=True,
            closed=False,
            camera_tree=Mock(),
            _selection_index=lambda _tree: None,
            status_text=Mock(),
            debug_view=debug_var,
            video_export=SimpleNamespace(status=lambda: {"state": recording_state}),
            _layer_queue=[],
            _active_layer_take=None,
            _renderlock_cancel=None,
            _mark_dirty=Mock(),
            _sync_debug_view_from_status=lambda st: debug_var.set(debug_view_label(st.get("debug_view", DEBUG_VIEW_DEFAULT))),
        )

        def submit(_label, fn, complete=None):
            result = fn()
            if complete is not None:
                complete(result)
            return True

        app._submit = submit
        return app, bridge, published, console, live_status

    def test_dispatch_applies_via_controller_syncs_desktop_and_publishes_without_dirtying(self):
        app, bridge, published, console, _status = self._make_app()
        publish = lambda: editor_publication.configure(app)

        # Select Wireframe (index 6) from in-game Look drawer.
        idx = DEBUG_VIEW_IDS.index("wireframe")
        self.assertTrue(editor_dispatch.dispatch(app, {"action": "set_debug_view", "value": float(idx)}, bridge, publish=publish))
        self.assertEqual(app.controller._debug_view.current, "wireframe")
        self.assertEqual(console.values["sv_cheats"], 1.0)
        self.assertEqual(app.debug_view.get(), "Wireframe")
        self.assertEqual(published[-1], (idx, True))

        # Switch to Normals (index 2): undo-before-apply turns off cheats and updates both surfaces.
        normals_idx = DEBUG_VIEW_IDS.index("normals")
        self.assertTrue(editor_dispatch.dispatch(app, {"action": "set_debug_view", "value": float(normals_idx)}, bridge, publish=publish))
        self.assertEqual(app.controller._debug_view.current, "normals")
        self.assertEqual(console.values["sv_cheats"], 0.0)
        self.assertEqual(app.debug_view.get(), "Normals")
        self.assertEqual(published[-1], (normals_idx, True))

        # Strictly session-only: never dirties the shot or writes to Project / AppSettings.
        app._mark_dirty.assert_not_called()
        self.assertNotIn("debug_view", app.project.to_dict())
        self.assertFalse(hasattr(app.app_settings, "debug_view"))

    def test_dispatch_rejects_invalid_indices_and_disabled_states(self):
        app, bridge, _published, _console, live_status = self._make_app()
        publish = lambda: editor_publication.configure(app)
        app.controller.set_debug_view = Mock()

        for bad in (-1, len(DEBUG_VIEW_IDS), 1.5, True, False, float("nan"), float("inf"), "albedo"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    editor_dispatch.dispatch(app, {"action": "set_debug_view", "value": bad}, bridge, publish=publish)

        # Busy leaves the event queued (returns False).
        app.busy = True
        self.assertFalse(editor_dispatch.dispatch(app, {"action": "set_debug_view", "value": 1.0}, bridge, publish=publish))
        app.busy = False

        # Playing, wrong stage, active recording, layer queue, active layer take, RenderLock export.
        blockers = [
            ("app_playing", lambda: setattr(app, "playing", True), lambda: setattr(app, "playing", False)),
            ("status_playing", lambda: live_status.__setitem__("playing", True), lambda: live_status.__setitem__("playing", False)),
            ("stage_loading", lambda: live_status.__setitem__("startup_stage", "loading_replay"), lambda: live_status.__setitem__("startup_stage", "editing_ready")),
            ("recording", lambda: setattr(app, "video_export", SimpleNamespace(status=lambda: {"state": "recording"})),
             lambda: setattr(app, "video_export", SimpleNamespace(status=lambda: {"state": "idle"}))),
            ("finalizing", lambda: setattr(app, "video_export", SimpleNamespace(status=lambda: {"state": "finalizing"})),
             lambda: setattr(app, "video_export", SimpleNamespace(status=lambda: {"state": "idle"}))),
            ("layer_queue", lambda: setattr(app, "_layer_queue", [("world", "color")]), lambda: setattr(app, "_layer_queue", [])),
            ("active_layer_take", lambda: setattr(app, "_active_layer_take", ("world", "color")), lambda: setattr(app, "_active_layer_take", None)),
            ("renderlock_export", lambda: setattr(app, "_renderlock_cancel", object()), lambda: setattr(app, "_renderlock_cancel", None)),
        ]
        for label, enable, restore in blockers:
            with self.subTest(blocker=label):
                enable()
                try:
                    with self.assertRaises(ValueError):
                        editor_dispatch.dispatch(app, {"action": "set_debug_view", "value": 1.0}, bridge, publish=publish)
                finally:
                    restore()
        app.controller.set_debug_view.assert_not_called()

    def test_publication_tracks_state_resets_and_all_disable_conditions(self):
        app, _bridge, published, _console, live_status = self._make_app()
        editor_publication.configure(app)
        self.assertEqual(published[-1], (0, True))

        # Unchanged state does not republish.
        count = len(published)
        editor_publication.configure(app)
        self.assertEqual(len(published), count)

        # Desktop-side controller change publishes the new index.
        app.controller.set_debug_view("lod")
        editor_publication.configure(app)
        self.assertEqual(published[-1], (DEBUG_VIEW_IDS.index("lod"), True))

        # Replay reload failure resets DebugViewState to Normal -> overlay publishes 0.
        app.controller._debug_view.drop_view()
        editor_publication.configure(app)
        self.assertEqual(published[-1], (0, True))

        # Every disable condition publishes available=False.
        disable_cases = [
            ("busy", lambda: setattr(app, "busy", True), lambda: setattr(app, "busy", False)),
            ("playing", lambda: live_status.__setitem__("playing", True), lambda: live_status.__setitem__("playing", False)),
            ("not_ready_stage", lambda: live_status.__setitem__("startup_stage", "loading_replay"), lambda: live_status.__setitem__("startup_stage", "editing_ready")),
            ("recording", lambda: setattr(app, "video_export", SimpleNamespace(status=lambda: {"state": "recording"})),
             lambda: setattr(app, "video_export", SimpleNamespace(status=lambda: {"state": "idle"}))),
            ("layer_queue", lambda: setattr(app, "_layer_queue", [("players", "capture")]), lambda: setattr(app, "_layer_queue", [])),
            ("active_layer_take", lambda: setattr(app, "_active_layer_take", ("players", "capture")), lambda: setattr(app, "_active_layer_take", None)),
            ("renderlock", lambda: setattr(app, "_renderlock_cancel", object()), lambda: setattr(app, "_renderlock_cancel", None)),
        ]
        for label, enable, restore in disable_cases:
            with self.subTest(disable=label):
                enable()
                try:
                    editor_publication.configure(app)
                    self.assertEqual(published[-1], (0, False))
                finally:
                    restore()
                editor_publication.configure(app)
                self.assertEqual(published[-1], (0, True))


if __name__ == "__main__":
    unittest.main()
