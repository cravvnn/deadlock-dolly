"""Layer modes show exactly their keep-list and fail closed."""
import unittest

from dolly.layer_modes import LayerModesMixin


class _Game(LayerModesMixin):
    def __init__(self, classes):
        self.classes = classes
        self.commands = []
        self._layer_hidden = None

    def _request(self, command):
        if command == "sc_showclasses":
            return "\n".join(self.classes)
        self.commands.append(command)
        return ""


class LayerModeTests(unittest.TestCase):
    def test_every_mode_is_a_keep_list(self):
        for mode, spec in LayerModesMixin.LAYER_MODES.items():
            self.assertTrue(spec["keep"], mode)
            self.assertLessEqual(set(spec), {"keep", "optional_keep"}, mode)

    def test_layer_hides_every_class_it_does_not_keep(self):
        game = _Game(["SkinnedObject", "ParticleSystem", "projectedDecal", "NewClass"])
        result = game.apply_layer_mode("players")
        self.assertEqual(result["hidden"], ["ParticleSystem", "projectedDecal", "NewClass"])
        self.assertEqual(game._layer_hidden, {"ParticleSystem", "projectedDecal", "NewClass"})
        self.assertEqual(game.commands[-3:], ["sc_setclassflags ParticleSystem 8",
                                              "sc_setclassflags projectedDecal 8",
                                              "sc_setclassflags NewClass 8"])

    def test_optional_classes_are_kept_when_present(self):
        world = LayerModesMixin.LAYER_MODES["world"]
        game = _Game(list(world["keep"]) + ["projectedDecal", "SkinnedObject"])
        self.assertEqual(game.apply_layer_mode("world")["hidden"], ["SkinnedObject"])

    def test_missing_required_class_fails_closed(self):
        game = _Game(["ParticleSystem"])
        with self.assertRaisesRegex(RuntimeError, "SkinnedObject"):
            game.apply_layer_mode("players")
        self.assertEqual(game.commands, [])


if __name__ == "__main__":
    unittest.main()
