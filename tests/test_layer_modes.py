"""Layer isolation commands and take scheduling without a live game."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from dolly.controller import Controller
from dolly.video_export import VideoOptions


CLASSES = ("EnvMap", "BarnLight", "DirectionalLight", "panorama_world_panel",
           "LightProbeVolume", "SkinnedObject", "Default", "MeshBuilderObject",
           "ParticleSystem", "InstancedMesh", "AggregateDesc", "Skybox")


class LayerModeTests(unittest.TestCase):
    def setUp(self):
        self.controller = Controller()
        self.commands = []
        self.classes = CLASSES
        self.controller._request = self.request

    def request(self, command):
        self.commands.append(command)
        if command == "sc_showclasses":
            return "\n".join(f"{name}    Hide DebugLevel: 0 1 2 3" for name in self.classes)
        if command in Controller.MATTE_CVARS:
            return f"{command} = 1"
        return ""

    def test_matte_layer_disables_and_restores_post_processing(self):
        self.controller.begin_matte_layer()
        self.assertEqual(self.commands[-1],
                         "r_effects_bloom 0; r_post_bloom 0; r_post_bloom_strength 0")
        self.controller.end_matte_layer()
        self.assertEqual(self.commands[-1],
                         "r_effects_bloom 1; r_post_bloom 1; r_post_bloom_strength 1")

    def test_matte_layer_ignores_missing_cvars(self):
        original = self.request

        def missing(command):
            if command in Controller.MATTE_CVARS:
                raise RuntimeError("unknown command")
            return original(command)

        self.controller._request = missing
        self.controller.begin_matte_layer()
        self.controller.end_matte_layer()

    def hide_commands(self):
        return [command for command in self.commands if command.endswith(" 8")]

    def reset_commands(self):
        return [command for command in self.commands if command.endswith(" 0")]

    def test_world_mode_hides_players_effects_and_world_ui(self):
        applied = self.controller.apply_layer_mode("world")
        self.assertEqual(sorted(applied["hidden"]),
                         sorted(["SkinnedObject", "ParticleSystem", "panorama_world_panel"]))
        self.assertEqual(sorted(self.hide_commands()),
                         sorted(["sc_setclassflags SkinnedObject 8",
                                 "sc_setclassflags ParticleSystem 8",
                                 "sc_setclassflags panorama_world_panel 8"]))
        self.assertEqual(len(self.reset_commands()), len(CLASSES))

    def test_players_mode_keeps_only_skinned_objects(self):
        applied = self.controller.apply_layer_mode("players")
        self.assertEqual(sorted(applied["hidden"]),
                         sorted(name for name in CLASSES if name != "SkinnedObject"))
        self.assertEqual(len(self.hide_commands()), len(applied["hidden"]))
        self.assertNotIn("sc_setclassflags SkinnedObject 8", self.commands)
        # A previous layer's flags are cleared before the new layer is set.
        self.assertEqual(len(self.reset_commands()), len(CLASSES))

    def test_effects_mode_keeps_only_the_particle_system(self):
        applied = self.controller.apply_layer_mode("effects")
        self.assertEqual(sorted(applied["hidden"]),
                         sorted(name for name in CLASSES if name != "ParticleSystem"))
        self.assertEqual(len(self.hide_commands()), len(applied["hidden"]))
        self.assertNotIn("sc_setclassflags ParticleSystem 8", self.commands)
        self.assertEqual(len(self.reset_commands()), len(CLASSES))

    def test_missing_required_class_fails_instead_of_wrong_layer(self):
        self.classes = tuple(name for name in CLASSES if name != "ParticleSystem")
        with self.assertRaisesRegex(RuntimeError, "ParticleSystem"):
            self.controller.apply_layer_mode("effects")

    def test_a_class_that_cannot_be_set_fails_the_mode(self):
        original = self.request

        def failing(command):
            if command == "sc_setclassflags SkinnedObject 8":
                raise RuntimeError("console busy")
            return original(command)

        self.controller._request = failing
        with self.assertRaisesRegex(RuntimeError, "SkinnedObject"):
            self.controller.apply_layer_mode("world")

    def test_unknown_mode_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown layer mode"):
            self.controller.apply_layer_mode("hud")

    def test_reset_restores_every_reported_class(self):
        self.controller.reset_layer_modes()
        self.assertEqual(sorted(self.reset_commands()),
                         sorted("sc_setclassflags " + name + " 0" for name in CLASSES))


class LayerOptionsTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "shot.mp4"
        exe = Path(self.folder.name) / "ffmpeg.exe"
        exe.write_bytes(b"MZ")
        self.exe = exe

    def test_layers_require_fixed_step_and_unique_known_names(self):
        with self.assertRaisesRegex(ValueError, "Fixed-step"):
            VideoOptions(self.path, layers=("world",), ffmpeg_path=self.exe).validated()
        with self.assertRaisesRegex(ValueError, "Unknown layer"):
            VideoOptions(self.path, fixed_step=True, layers=("hud",),
                         ffmpeg_path=self.exe).validated()
        with self.assertRaisesRegex(ValueError, "once"):
            VideoOptions(self.path, fixed_step=True, layers=("world", "world"),
                         ffmpeg_path=self.exe).validated()
        with self.assertRaisesRegex(ValueError, "tuple"):
            VideoOptions(self.path, fixed_step=True, layers=["world"],
                         ffmpeg_path=self.exe).validated()

    def test_layers_need_an_ffmpeg_encoder_for_the_alpha_matte(self):
        with self.assertRaisesRegex(ValueError, "FFmpeg encoder"):
            VideoOptions(self.path, fixed_step=True, layers=("players",),
                         codec="builtin").validated()

    def test_layers_use_the_take_folder_layout(self):
        options = VideoOptions(self.path, fixed_step=True, layers=("world", "players"),
                               ffmpeg_path=self.exe).validated()
        self.assertEqual(options.layers, ("world", "players"))
        self.assertFalse(options.depth)

    def test_existing_take_folder_is_rejected_for_layer_only_takes(self):
        self.path.with_suffix("").mkdir()
        with self.assertRaisesRegex(ValueError, "folder"):
            VideoOptions(self.path, fixed_step=True, layers=("world",),
                         ffmpeg_path=self.exe).validated()


if __name__ == "__main__":
    unittest.main()
