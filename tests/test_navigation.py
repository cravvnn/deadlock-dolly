import copy
from dataclasses import FrozenInstanceError
import math
import unittest

from dolly.navigation import CameraMotion, move_camera


class CameraNavigationTests(unittest.TestCase):
    def frame(self, **changes):
        frame = dict(time=8.5, x=100.0, y=200.0, z=300.0,
                     pitch=0.0, yaw=0.0, roll=17.0, aspect_ratio=1.4,
                     cvars={"r_dof": 1.0}, metadata={"labels": ["shot"]})
        frame.update(changes)
        return frame

    def test_forward_and_camera_right_follow_source_axes(self):
        origin = self.frame()
        forward = move_camera(origin, CameraMotion(forward=1), .1)
        right = move_camera(origin, CameraMotion(right=1), .1)
        self.assertEqual((forward["x"], forward["y"], forward["z"]), (124, 200, 300))
        self.assertEqual((right["x"], right["y"], right["z"]), (100, 176, 300))
        yawed = move_camera(self.frame(yaw=90), CameraMotion(forward=1), .1)
        self.assertAlmostEqual(yawed["x"], 100)
        self.assertAlmostEqual(yawed["y"], 224)
        right_yawed = move_camera(self.frame(yaw=90), CameraMotion(right=1), .1)
        self.assertAlmostEqual(right_yawed["x"], 124)
        self.assertAlmostEqual(right_yawed["y"], 200)

    def test_world_up_and_down_change_height_even_when_rolled(self):
        for direction in (1, -1):
            with self.subTest(direction=direction):
                result = move_camera(self.frame(pitch=60, yaw=31, roll=80),
                                     CameraMotion(up=direction), .1)
                self.assertEqual((result["x"], result["y"]), (100, 200))
                self.assertEqual(result["z"], 300 + 24 * direction)
                self.assertEqual(result["roll"], 80)

    def test_forward_follows_pitch_and_backward_reverses(self):
        for direction in (1, -1):
            result = move_camera(self.frame(pitch=30), CameraMotion(forward=direction), .1)
            self.assertAlmostEqual(result["x"], 100 + direction * 24 * math.cos(math.pi / 6))
            self.assertAlmostEqual(result["z"], 300 - direction * 12)

    def test_turn_left_and_look_down_have_positive_angles(self):
        result = move_camera(self.frame(), CameraMotion(yaw=1, pitch=1), .1)
        self.assertEqual((result["yaw"], result["pitch"]), (6, 6))
        result = move_camera(self.frame(), CameraMotion(yaw=-1, pitch=-1), .1)
        self.assertEqual((result["yaw"], result["pitch"]), (-6, -6))

    def test_pitch_stops_at_poles_and_yaw_does_not_wrap(self):
        high = move_camera(self.frame(pitch=88, yaw=359), CameraMotion(pitch=1, yaw=1), .1)
        low = move_camera(self.frame(pitch=-88, yaw=-359), CameraMotion(pitch=-1, yaw=-1), .1)
        self.assertEqual((high["pitch"], high["yaw"]), (89.9, 365))
        self.assertEqual((low["pitch"], low["yaw"]), (-89.9, -365))

    def test_movement_uses_new_rotation(self):
        result = move_camera(self.frame(yaw=84), CameraMotion(forward=1, yaw=1), .1)
        self.assertAlmostEqual(result["x"], 100)
        self.assertAlmostEqual(result["y"], 224)

    def test_diagonal_speed_is_capped_in_actual_world_vector(self):
        for motion in (CameraMotion(forward=1, right=1),
                       CameraMotion(forward=1, right=1, up=1),
                       CameraMotion(forward=-1, up=1)):
            result = move_camera(self.frame(pitch=45), motion, .1)
            length = math.dist((100, 200, 300), (result["x"], result["y"], result["z"]))
            self.assertLessEqual(length, 24.0000000001)
        result = move_camera(self.frame(), CameraMotion(forward=1, right=1), .1)
        self.assertAlmostEqual(result["x"] - 100, 24 / math.sqrt(2))
        self.assertAlmostEqual(200 - result["y"], 24 / math.sqrt(2))

    def test_partial_axis_is_not_normalized_up_to_full_speed(self):
        result = move_camera(self.frame(), CameraMotion(forward=.25), .1)
        self.assertEqual(result["x"], 106)

    def test_boost_multiplies_translation_only(self):
        ordinary = move_camera(self.frame(), CameraMotion(forward=1, yaw=1), .1)
        boosted = move_camera(self.frame(), CameraMotion(forward=1, yaw=1, boost=True), .1)
        self.assertEqual(ordinary["yaw"], boosted["yaw"])
        for axis in ("x", "y", "z"):
            self.assertAlmostEqual(boosted[axis] - self.frame()[axis],
                                   4 * (ordinary[axis] - self.frame()[axis]))

    def test_elapsed_time_caps_stalls_but_small_steps_accumulate(self):
        frame = self.frame()
        motion = CameraMotion(forward=1)
        self.assertEqual(move_camera(frame, motion, 8), move_camera(frame, motion, .1))
        small = frame
        for _ in range(10):
            small = move_camera(small, motion, .01)
        self.assertAlmostEqual(small["x"], 124)
        self.assertEqual(small["time"], 8.5)

    def test_custom_speeds(self):
        result = move_camera(self.frame(), CameraMotion(up=1, yaw=1), .05, 500, 120)
        self.assertEqual((result["z"], result["yaw"]), (325, 6))

    def test_stop_and_zero_delta_preserve_frame_without_aliasing(self):
        for motion, seconds in ((CameraMotion(forward=1, stop=True), .1),
                                (CameraMotion(forward=1), 0)):
            original = self.frame(pitch=120)
            result = move_camera(original, motion, seconds)
            self.assertEqual(result, original)
            self.assertIsNot(result, original)
            result["cvars"]["r_dof"] = 0
            self.assertEqual(original["cvars"]["r_dof"], 1)

    def test_preserves_independent_metadata_and_does_not_mutate_inputs(self):
        original = self.frame()
        saved = copy.deepcopy(original)
        result = move_camera(original, CameraMotion(forward=1), .1)
        for field in ("time", "roll", "aspect_ratio", "cvars", "metadata"):
            self.assertEqual(result[field], saved[field])
        result["metadata"]["labels"].append("second")
        self.assertEqual(original, saved)

    def test_motion_is_frozen_and_validates_axes_and_flags(self):
        motion = CameraMotion()
        with self.assertRaises(FrozenInstanceError):
            motion.up = 1
        for axis in ("forward", "right", "up", "pitch", "yaw"):
            for value in (-1.01, 1.01, math.nan, math.inf, -math.inf, True, "1", None, 10 ** 1000):
                with self.subTest(axis=axis, value=str(value)[:20]):
                    with self.assertRaises(ValueError):
                        CameraMotion(**{axis: value})
        for flag in ("boost", "stop"):
            for value in (0, 1, "false", None):
                with self.assertRaises(ValueError):
                    CameraMotion(**{flag: value})

    def test_invalid_elapsed_speeds_frames_and_motion_fail(self):
        for seconds in (-.01, True, math.nan, math.inf, "0.1"):
            with self.assertRaises(ValueError):
                move_camera(self.frame(), CameraMotion(), seconds)
        for option, invalid in (("move_speed", (0, -1, 10001, True, math.inf, "1")),
                                ("turn_speed", (0, -1, 721, False, math.nan, "1"))):
            for value in invalid:
                with self.assertRaises(ValueError):
                    move_camera(self.frame(), CameraMotion(), .1, **{option: value})
        for name in ("x", "y", "z", "pitch", "yaw"):
            frame = self.frame()
            del frame[name]
            with self.assertRaises(ValueError):
                move_camera(frame, CameraMotion(), .1)
            with self.assertRaises(ValueError):
                move_camera(self.frame(**{name: math.nan}), CameraMotion(), .1)
        with self.assertRaises(ValueError):
            move_camera([], CameraMotion(), .1)
        with self.assertRaises(ValueError):
            move_camera(self.frame(), {}, .1)


if __name__ == "__main__":
    unittest.main()
