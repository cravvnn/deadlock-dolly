"""Framing graph tests without requiring a display or a running game."""

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from dolly.curve import AspectCurve, clamp_aspect, hit_key, plot_bounds, sample_curve
from dolly.path import ASPECT_MIN, ASPECT_MAX, Keyframe, Project


def shot(values=(1.7777777778, 1.1, 2.4), times=(0.0, 3.0, 8.0), interpolation="smooth"):
    return Project(keyframes=[Keyframe(time, 100 * index, 20, 30, 0, 0, 0, 75,
                                       aspect_ratio=value)
                              for index, (time, value) in enumerate(zip(times, values))],
                   lens_interpolation=interpolation)


def graph_harness(project, index=1):
    graph = AspectCurve.__new__(AspectCurve)
    graph.project = project
    graph.selected_index = index
    graph.current_time = 0
    graph._enabled = True
    graph._bounds = plot_bounds(560, 145, project)
    graph._points = [graph._bounds.point(key.time, key.aspect_ratio) for key in project.keyframes]
    graph._drag_index = None
    graph._drag_value = None
    graph._drag_bounds = None
    graph._preview = None
    graph.canvas = SimpleNamespace(focus_set=Mock())
    graph.hint = SimpleNamespace(configure=Mock())
    graph.on_select = Mock()
    graph.on_change = Mock()
    graph._redraw = Mock()
    return graph


class AspectCurveTests(unittest.TestCase):
    def test_curve_samples_match_playback_and_contain_each_authored_key(self):
        project = shot(times=(0.0, 0.17, 8.03))
        points = sample_curve(project, project.duration, 47)
        for key in project.keyframes:
            self.assertIn((key.time, key.aspect_ratio), points)
        for timestamp, value in points:
            self.assertEqual(value, project.evaluate(timestamp)["aspect_ratio"])
        between = [value for timestamp, value in points if 0.17 < timestamp < 8.03]
        self.assertTrue(between)
        self.assertTrue(all(1.1 <= value <= 2.4 for value in between))

    def test_step_curve_draws_jump_at_key_without_a_diagonal_ramp(self):
        project = shot(interpolation="step")
        points = sample_curve(project, project.duration, 20)
        knot = points.index((3.0, 1.1))
        before_time, before_value = points[knot - 1]
        self.assertAlmostEqual(before_time, 3.0, places=12)
        self.assertEqual(before_value, project.keyframes[0].aspect_ratio)

    def test_empty_and_one_key_graphs_show_meaningful_baselines(self):
        empty = Project(standard_aspect=4 / 3)
        bounds = plot_bounds(560, 145, empty)
        self.assertEqual(sample_curve(empty, bounds.duration), [(0.0, 4 / 3), (1.0, 4 / 3)])
        self.assertLess(bounds.low, empty.standard_aspect)
        self.assertGreater(bounds.high, empty.standard_aspect)
        one = shot(values=(0.5,), times=(0.0,))
        self.assertTrue(all(value == 0.5 for _, value in sample_curve(one, 1.0)))

    def test_pointer_inverse_preserves_aspects_and_clamps_outside_canvas(self):
        project = shot(values=(ASPECT_MIN, 1.2, ASPECT_MAX))
        bounds = plot_bounds(560, 145, project)
        for value in (ASPECT_MIN, 0.9, 1.777, 3.99, ASPECT_MAX):
            _, y = bounds.point(4.0, value)
            self.assertAlmostEqual(bounds.aspect_at(y), value)
        self.assertEqual(bounds.aspect_at(-10_000), ASPECT_MAX)
        self.assertEqual(bounds.aspect_at(10_000), ASPECT_MIN)

    def test_short_shot_uses_full_graph_width(self):
        project = shot(times=(0.0, 0.05, 0.2))
        bounds = plot_bounds(560, 145, project)
        self.assertEqual(bounds.duration, 0.2)
        self.assertEqual(bounds.point(0.2, 1.0)[0], bounds.right)

    def test_flat_shot_can_reach_full_framing_range_inside_canvas(self):
        project = shot(values=(16 / 9, 16 / 9, 16 / 9))
        bounds = plot_bounds(560, 145, project)
        self.assertEqual(bounds.aspect_at(bounds.top), ASPECT_MAX)
        self.assertEqual(bounds.aspect_at(bounds.bottom), ASPECT_MIN)
        graph = graph_harness(project)
        x, y = graph._points[1]
        graph._press(SimpleNamespace(x=x, y=y))
        graph._motion(SimpleNamespace(x=x, y=bounds.top))
        graph._release(None)
        graph.on_change.assert_called_once_with(1, 3.0, ASPECT_MAX)

    def test_hit_testing_uses_nearest_key_and_rejects_background_clicks(self):
        points = [(50, 60), (62, 60), (200, 90)]
        self.assertEqual(hit_key(points, 61, 60), 1)
        self.assertEqual(hit_key(points, 204, 95), 2)
        self.assertIsNone(hit_key(points, 170, 20))
        self.assertIsNone(hit_key([], 1, 1))

    def test_drag_previews_locally_then_commits_one_edit_without_retiming(self):
        project = shot()
        before = project.to_dict()
        graph = graph_harness(project)
        x, y = graph._points[1]
        graph._press(SimpleNamespace(x=x, y=y))
        _, target_y = graph._bounds.point(3.0, 1.8)
        graph._motion(SimpleNamespace(x=500, y=target_y))
        graph._motion(SimpleNamespace(x=0, y=target_y))
        self.assertEqual(project.to_dict(), before)
        self.assertEqual(graph._preview.keyframes[1].aspect_ratio, 1.8)
        graph.on_change.assert_not_called()
        graph._release(None)
        graph.on_select.assert_called_once_with(1)
        graph.on_change.assert_called_once_with(1, 3.0, 1.8)
        self.assertEqual(project.to_dict(), before)

    def test_click_and_cancel_never_commit_framing_edits(self):
        graph = graph_harness(shot())
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

    def test_keyboard_edits_are_small_bounded_and_preserve_time(self):
        graph = graph_harness(shot())
        graph._key(SimpleNamespace(keysym="Up", state=0))
        graph.on_change.assert_called_once_with(1, 3.0, 1.11)
        graph.on_change.reset_mock()
        graph._key(SimpleNamespace(keysym="Down", state=1))
        graph.on_change.assert_called_once_with(1, 3.0, 1.0)
        graph.project.keyframes[1] = replace(graph.project.keyframes[1], aspect_ratio=ASPECT_MAX)
        graph.on_change.reset_mock()
        graph._key(SimpleNamespace(keysym="Up", state=1))
        graph.on_change.assert_not_called()

    def test_loading_another_project_discards_pending_drag(self):
        graph = graph_harness(shot())
        x, y = graph._points[1]
        graph._press(SimpleNamespace(x=x, y=y))
        graph._motion(SimpleNamespace(x=x, y=0))
        graph.set_project(shot(values=(1.0, 2.0, 3.0)), selected_index=2)
        graph._release(None)
        graph.on_change.assert_not_called()
        self.assertEqual(graph.selected_index, 2)

    def test_playback_disables_and_cancels_a_pending_graph_edit(self):
        graph = graph_harness(shot())
        x, y = graph._points[1]
        graph._press(SimpleNamespace(x=x, y=y))
        graph._motion(SimpleNamespace(x=x, y=0))
        graph.set_enabled(False)
        graph._release(None)
        graph._press(SimpleNamespace(x=x, y=y))
        graph._key(SimpleNamespace(keysym="Up", state=0))
        graph.on_change.assert_not_called()
        self.assertIsNone(graph._drag_index)

    def test_nonfinite_aspect_is_never_converted_into_a_valid_edit(self):
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.assertRaises(ValueError):
                clamp_aspect(value)


if __name__ == "__main__":
    unittest.main()
