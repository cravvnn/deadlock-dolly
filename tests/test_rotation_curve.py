"""Rotation-curve model, graph, and shared-timeline tests (no display needed)."""

import math
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from dolly.curve import RotationCurve, TimelineView, plot_bounds, rotation_bounds, sample_channel
from dolly.native_path import compile_project
from dolly.path import (ATTACH_FORMAT_VERSION, ROTATION_FORMAT_VERSION, Keyframe, Project,
                        channel_value)


def camera(time, pitch=0.0, yaw=0.0, roll=0.0, **kwargs):
    return Keyframe(time, 100 * time, 20, 30, pitch, yaw, roll, 75, **kwargs)


def shot():
    return Project(keyframes=[camera(0.0, pitch=0.0, yaw=0.0, roll=0.0),
                              camera(3.0, pitch=40.0, yaw=40.0, roll=40.0),
                              camera(8.0, pitch=-20.0, yaw=-20.0, roll=-20.0)])


def curve_harness(project, index=1, channel="pitch"):
    widget = RotationCurve.__new__(RotationCurve)
    widget.project = project
    widget.selected_index = index
    widget.current_time = 0.0
    widget.channel = channel
    widget._enabled = True
    widget._bounds = rotation_bounds(560, 130, project, channel)
    widget._points = [widget._bounds.point(key.time, channel_value(key, channel))
                      for key in project.keyframes]
    widget._drag_index = None
    widget._drag_value = None
    widget._drag_bounds = None
    widget._preview = None
    widget.canvas = SimpleNamespace(focus_set=Mock())
    widget.hint = SimpleNamespace(configure=Mock())
    widget.channel_box = SimpleNamespace(configure=Mock())
    widget.reset_button = SimpleNamespace(configure=Mock())
    widget.on_select = Mock()
    widget.on_change = Mock()
    widget.on_reset = Mock()
    widget._redraw = Mock()
    return widget


class RotationModelTests(unittest.TestCase):
    def test_channel_value_prefers_an_override_per_channel(self):
        key = camera(0.0, pitch=10.0, yaw=20.0, roll=30.0, curve_pitch=-5.5)
        self.assertEqual(channel_value(key, "pitch"), -5.5)
        self.assertEqual(channel_value(key, "yaw"), 20.0)
        self.assertEqual(channel_value(key, "roll"), 30.0)

    def test_evaluate_and_compile_use_the_effective_angles(self):
        override = Project(keyframes=[camera(0.0, yaw=0.0),
                                      camera(2.0, yaw=40.0, curve_yaw=100.0),
                                      camera(4.0, yaw=-40.0)])
        authored = Project(keyframes=[camera(0.0, yaw=0.0),
                                      camera(2.0, yaw=100.0),
                                      camera(4.0, yaw=-40.0)])
        self.assertAlmostEqual(override.evaluate(2.0)["yaw"], 100.0)
        middle = override.evaluate(1.0)["yaw"]
        self.assertTrue(0.0 < middle < 100.0)
        self.assertEqual(compile_project(override), compile_project(authored))

    def test_round_trip_uses_version_five_and_keeps_overrides(self):
        project = Project(keyframes=[camera(0.0, curve_roll=12.5), camera(2.0)])
        data = project.to_dict()
        self.assertEqual(data["version"], ROTATION_FORMAT_VERSION)
        self.assertEqual(data["keyframes"][0]["curve_roll"], 12.5)
        restored = Project.from_dict(data)
        self.assertEqual(restored.keyframes[0].curve_roll, 12.5)
        self.assertIsNone(restored.keyframes[0].curve_pitch)
        self.assertIsNone(restored.keyframes[1].curve_roll)

    def test_older_versions_reject_curve_members(self):
        data = Project(keyframes=[camera(0.0)]).to_dict()
        data["version"] = ATTACH_FORMAT_VERSION
        data["keyframes"][0]["curve_pitch"] = 1.0
        with self.assertRaisesRegex(ValueError, "camera keyframe"):
            Project.from_dict(data)

    def test_validation_rejects_non_finite_overrides(self):
        for bad in (math.nan, math.inf, -math.inf):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Project(keyframes=[camera(0.0, curve_yaw=bad)]).validate()

    def test_channel_samples_stay_inside_the_requested_window(self):
        samples = sample_channel(shot(), "yaw", 2.0, 4.0)
        self.assertTrue(all(2.0 <= time <= 4.0 for time, _ in samples))
        self.assertIn((3.0, 40.0), samples)


class TimelineViewTests(unittest.TestCase):
    def test_default_window_is_the_full_shot(self):
        self.assertEqual(TimelineView().window(8.0), (0.0, 8.0))
        self.assertGreater(TimelineView().window(0.0)[1], 0.0)

    def test_zoom_keeps_the_time_under_the_cursor_fixed(self):
        view = TimelineView()
        view.zoom(0.5, 6.0, 8.0)
        start, end = view.window(8.0)
        self.assertAlmostEqual(start, 3.0)
        self.assertAlmostEqual(end, 7.0)
        self.assertAlmostEqual((6.0 - start) / (end - start), 0.75)

    def test_zoom_stays_inside_the_shot_with_a_span_floor(self):
        view = TimelineView()
        for _ in range(40):
            view.zoom(0.85, 0.5, 10.0)
        start, end = view.window(10.0)
        self.assertGreaterEqual(start, 0.0)
        self.assertGreaterEqual(end - start, 10.0 / 500.0 - 1e-9)
        view.reset()
        self.assertEqual(view.window(10.0), (0.0, 10.0))

    def test_pan_clamps_inside_the_shot(self):
        view = TimelineView()
        view.zoom(0.5, 4.0, 8.0)
        view.pan(100.0, 8.0)
        self.assertEqual(view.window(8.0), (4.0, 8.0))
        view.pan(-100.0, 8.0)
        self.assertEqual(view.window(8.0), (0.0, 4.0))

    def test_plot_bounds_follow_the_shared_view(self):
        project = shot()
        view = TimelineView()
        view.zoom(0.5, 6.0, project.duration)
        bounds = plot_bounds(560, 145, project, view)
        self.assertAlmostEqual(bounds.start, 3.0)
        self.assertAlmostEqual(bounds.end, 7.0)
        x, _ = bounds.point(3.0, 1.0)
        self.assertAlmostEqual(x, bounds.left)

    def test_rotation_bounds_keep_a_usable_minimum_span(self):
        flat = Project(keyframes=[camera(0.0, pitch=10.0), camera(4.0, pitch=10.0)])
        bounds = rotation_bounds(560, 130, flat, "pitch")
        self.assertGreaterEqual(bounds.high - bounds.low, 19.999)
        self.assertLess(bounds.low, 10.0)
        self.assertGreater(bounds.high, 10.0)


class RotationCurveTests(unittest.TestCase):
    def test_drag_previews_locally_then_commits_one_override(self):
        project = shot()
        before = project.to_dict()
        graph = curve_harness(project)
        x, y = graph._points[1]
        graph._press(SimpleNamespace(x=x, y=y))
        _, target_y = graph._bounds.point(3.0, 12.0)
        graph._motion(SimpleNamespace(x=500, y=target_y))
        graph._motion(SimpleNamespace(x=0, y=target_y))
        self.assertEqual(project.to_dict(), before)
        self.assertEqual(graph._preview.keyframes[1].curve_pitch, 12.0)
        graph.on_change.assert_not_called()
        graph._release(None)
        graph.on_select.assert_called_once_with(1)
        graph.on_change.assert_called_once()
        index, channel, value = graph.on_change.call_args.args
        self.assertEqual((index, channel), (1, "pitch"))
        self.assertAlmostEqual(value, 12.0, places=6)
        self.assertEqual(project.to_dict(), before)

    def test_click_and_cancel_never_commit_a_rotation_edit(self):
        graph = curve_harness(shot())
        x, y = graph._points[1]
        graph._press(SimpleNamespace(x=x, y=y))
        graph._release(None)
        graph.on_change.assert_not_called()
        graph._press(SimpleNamespace(x=x, y=y))
        graph._motion(SimpleNamespace(x=x, y=0))
        graph._cancel()
        graph._release(None)
        graph.on_change.assert_not_called()
        self.assertIsNone(graph._preview)

    def test_keyboard_nudges_are_one_degree_or_ten_with_shift(self):
        graph = curve_harness(shot())
        graph._key(SimpleNamespace(keysym="Up", state=0))
        graph.on_change.assert_called_once_with(1, "pitch", 41.0)
        graph.on_change.reset_mock()
        graph._key(SimpleNamespace(keysym="Down", state=1))
        graph.on_change.assert_called_once_with(1, "pitch", 30.0)

    def test_reset_button_routes_through_the_app_callback(self):
        graph = curve_harness(shot())
        graph._reset()
        graph.on_reset.assert_called_once_with("pitch")
        graph.set_enabled(False)
        graph._reset()
        graph.on_reset.assert_called_once_with("pitch")


if __name__ == "__main__":
    unittest.main()
