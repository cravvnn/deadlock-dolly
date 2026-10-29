"""The update notice: Kara's banner ([design reference]), its states, the release highlights ([design reference]),
the header reminder ([design reference]) and the fade-in (USER-ADDITIONS UPDATE 30). Real Tk, hidden."""
import os
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from dolly import ui_theme
from dolly.ui import fonts, tween, update_notice
from dolly.ui.kit_controls import CanvasButton
from dolly.ui.tween import Animator, ease_out

T = ui_theme.TOKENS
RELEASE = ("9.0.1", "official-package", "a" * 64, 100)
NOTES = [
    dict(title="Check Native Depth of Field before use.",
         before="Native DOF support was harder to diagnose, and the support check could miss a newly selected "
                "game folder.",
         after="Dolly checks the installed shader and material indexes, explains missing or unreadable files, "
               "and offers a local support report from the selected install."),
    dict(title="Pause shots more reliably.",
         before="Pausing a fixed-step shot could make Dolly mistake an ordinary render sample for a replay jump "
                "and stop the export.",
         after="Dolly checks fresh paused views and the stable replay clock against the position recorded before "
               "its Pause command. Real timeline jumps and changed replays still stop the shot."),
    dict(title="Recover interrupted exports.",
         before="A failed shot could leave Players capture armed, and stopping could discard its last eligible "
                "image.",
         after="Dolly waits for the matching capture to stop, retains an image already prepared for that stop, "
               "and reports the original failure with restoration status. Completed takes and Players scratch "
               "data are kept; uncertain recovery remains pending."),
    dict(title="Keep error dialogs visible.",
         before="An error could open outside the monitor or leave the wrong window enabled behind a Save dialog.",
         after="Dolly keeps error controls on the owner's monitor, restores a minimized owner, and preserves the "
               "correct modal ownership and keyboard focus."),
]
DETAIL = "Installed: 0.6.62-alpha. Download and install only when you click Update now."
CHROME = 1 + 17 + 30 + 16 + 17 + 21       # pane start, header row and gap, pane padding, card end ([design reference])


class FakeUser32:
    """The four user32 calls of a fade; ``layered=False`` refuses the layered style like an old Windows."""

    def __init__(self, layered=True):
        self.layered = layered
        self.styles = {}
        self.alphas = []
        self.redraws = []

    def GetWindowLongW(self, hwnd, _index):
        return self.styles.get(hwnd, 0)

    def SetWindowLongW(self, hwnd, _index, value):
        old = self.styles.get(hwnd, 0)
        self.styles[hwnd] = value if self.layered else value & ~0x80000
        return old

    def SetLayeredWindowAttributes(self, hwnd, _key, alpha, _flags):
        self.alphas.append((hwnd, alpha))
        return 1

    def RedrawWindow(self, hwnd, *_args):
        self.redraws.append(hwnd)
        return 1


class NoticeCase(unittest.TestCase):
    """A hidden root at ``SCALE``, an animator on a clock the test sets, and a 930 px notice host."""

    SCALE = 1.0

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.root.withdraw()
        # One `tk scaling` per process on Windows: pin it for this root and give the old one back.
        scaling = self.root.tk.call("tk", "scaling")
        self.root.tk.call("tk", "scaling", 96 * self.SCALE / 72)
        self.addCleanup(self.close, scaling)
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.addCleanup(lambda: self.assertEqual(self.errors, []))
        from dolly import gui_theme
        gui_theme.initialize(self.root)
        self.now = 0.0
        self.animator = Animator.of(self.root)
        self.animator.clock = lambda: self.now
        self.host = tk.Frame(self.root, background=T["bg"], height=1)
        self.host.columnconfigure(0, weight=1)
        self.host.place(x=20, y=20, width=self.px(930))
        self.commands = {name: Mock(name=name) for name in ("update", "notes", "full_notes", "later", "reopen")}
        self.limit = Mock(return_value=self.px(2000))
        self.view = self.make()

    def close(self, scaling):
        try:
            for timer in self.root.tk.splitlist(self.root.tk.call("after", "info")):
                self.root.tk.call("after", "cancel", timer)
            self.root.tk.call("tk", "scaling", scaling)
            self.root.destroy()
        except tk.TclError:
            pass

    def px(self, value):
        return int(value * self.SCALE + .5)

    def make(self, **options):
        options.setdefault("height_limit", self.limit)
        view = update_notice.UpdateNotice(self.host, **self.commands, **options)
        view.set_notes(NOTES, release=RELEASE)
        return view

    def show_window(self):
        self.root.deiconify()
        self.root.geometry(f"{self.px(970)}x{self.px(760)}")
        self.settle()

    def settle(self):
        self.root.update_idletasks()
        self.root.update()
        self.root.update_idletasks()

    def at(self, seconds):
        """One animator frame at ``seconds`` on its clock."""
        self.now = seconds
        self.animator.tick(seconds)

    def finish(self):
        self.animator.finish()

    def coords(self, canvas, item):
        return tuple(int(round(value)) for value in canvas.coords(item))


class BannerTests(NoticeCase):
    def setUp(self):
        super().setUp()
        self.view.show("0.6.63-alpha", DETAIL)
        self.show_window()

    def test_banner_matches_figmas_update_banner(self):
        view, c = self.view, self.view.banner
        self.assertEqual((c.winfo_width(), c.winfo_height()), (930, 70))
        self.assertEqual(self.host.winfo_height(), 70, "the host is exactly the card: it starts at its top")
        # Card: panel fill, 1 px accent edge, radius 6 ([design reference]).
        self.assertEqual(view._card.box, (0, 0, 930, 70))
        self.assertEqual(c.itemcget(view._card.fills[0], "fill"), T["panel"])
        self.assertEqual({c.itemcget(item, "fill") for item in view._card.edges}, {T["accent"]})
        self.assertEqual(view._card.radius, 6)
        self.assertTrue(all(c.itemcget(item, "state") == "normal" for item in view._card.corners))
        # Accent badge 36 px at (17, 17) with the 18 px glyph box centred in it.
        self.assertEqual(view._badge.box, (17, 17, 53, 53))
        self.assertEqual(c.itemcget(view._badge.fills[0], "fill"), T["accent"])
        self.assertEqual(self.coords(c, view._icon), (26, 26))
        self.assertEqual((view._icon_image.width(), view._icon_image.height()), (18, 18))
        # Title and detail in the text column at x 67, the 39 px block centred.
        self.assertEqual(view._title.lines, ["Dolly 0.6.63-alpha is available"])
        self.assertEqual(view._detail.lines, [DETAIL])
        title_font = fonts.font(self.root, "card_title")
        self.assertEqual(view.faces.title.cget("family"), title_font.cget("family"))
        self.assertEqual(int(view.faces.title.cget("size")), -14)
        self.assertEqual(c.itemcget(view._title.items[0], "fill"), T["text"])
        self.assertEqual(c.itemcget(view._detail.items[0], "fill"), T["muted"])
        top = (70 - 39) // 2
        self.assertEqual(self.coords(c, view._title.items[0])[0], 67)
        self.assertEqual(self.coords(c, view._title.items[0])[1],
                         top + update_notice._top(view.faces.title, 18))
        self.assertEqual(self.coords(c, view._detail.items[0])[1],
                         top + 18 + 5 + update_notice._top(view.faces.body, 16))
        # Later (quiet), What's changed (secondary), Update now (primary): right to 909, 10 apart.
        buttons = (view.later_button, view.notes_button, view.update_button)
        self.assertEqual([button.kind for button in buttons], ["quiet", "secondary", "primary"])
        self.assertEqual([button.cget("text") for button in buttons], ["Later", "What's changed", "Update now"])
        boxes = [button.box() for button in buttons]
        self.assertEqual(boxes[-1][2], 909)
        for left, right in zip(boxes, boxes[1:]):
            self.assertEqual(right[0] - left[2], 10)
        self.assertTrue(all((box[1], box[3]) == (20, 50) for box in boxes))
        self.assertEqual(view._focus.controls, list(buttons), "Tab reaches them left to right")

    def test_states_change_only_the_detail_and_update_now(self):
        view, c = self.view, self.view.banner
        view.show("0.6.63-alpha", "Downloading Dolly 0.6.63-alpha: 42%", busy=True)
        self.settle()
        self.assertEqual(view.update_button.cget("text"), "Downloading...")
        self.assertTrue(view.update_button.instate(["disabled"]))
        self.assertEqual(c.itemcget(view.update_button._fills[0], "fill"), T["field"])
        self.assertEqual(c.itemcget(view.update_button.item, "fill"), T["dim"])
        self.assertEqual(view.update_button.box()[2], 909)
        self.assertEqual(view._title.lines, ["Dolly 0.6.63-alpha is available"])
        view.show("0.6.63-alpha", "Could not start updater: [WinError 5] Access is denied. Click Update now to retry.",
                  error=True)
        self.settle()
        self.assertEqual(c.itemcget(view._detail.items[0], "fill"), T["status_error"])
        self.assertEqual(view.update_button.cget("text"), "Update now")
        self.assertFalse(view.update_button.instate(["disabled"]))
        view.show("0.6.63-alpha", "Update deferred: offline")
        self.settle()
        self.assertEqual(c.itemcget(view._detail.items[0], "fill"), T["muted"])
        self.assertEqual(c.winfo_height(), 70)

    def test_a_long_detail_wraps_and_the_banner_grows_around_it(self):
        view, c = self.view, self.view.banner
        view.show("0.6.63-alpha", "Update deferred: " + "the release metadata could not be verified " * 4)
        self.settle()
        lines = len(view._detail.lines)
        self.assertGreater(lines, 1)
        text_h = 18 + 5 + lines * 16
        self.assertEqual(c.winfo_height(), max(70, text_h + 31))
        self.assertEqual(self.host.winfo_height(), c.winfo_height())
        self.assertEqual(view._badge.box[1], (c.winfo_height() - 36) // 2)
        self.assertEqual(view.update_button.box()[1], (c.winfo_height() - 30) // 2)
        width = view.update_button.box()[0]
        for item in view._detail.items[:lines]:
            self.assertLessEqual(c.bbox(item)[2], width)

    def test_buttons_run_their_commands(self):
        view = self.view
        for button, name in ((view.later_button, "later"), (view.notes_button, "notes"),
                             (view.update_button, "update"), (view.full_notes_button, "full_notes")):
            button.invoke()
            self.commands[name].assert_called_once_with()
        self.assertIsInstance(view.full_notes_button, CanvasButton)


class HighDpiBannerTests(BannerTests):
    SCALE = 1.5

    def test_banner_matches_figmas_update_banner(self):
        view, c = self.view, self.view.banner
        self.assertEqual((c.winfo_width(), c.winfo_height()), (1395, 105))
        self.assertEqual(view._badge.box, (26, 26, 80, 80))
        self.assertEqual(view.update_button.box()[2], 1395 - 32)
        self.assertEqual((view._icon_image.width(), view._icon_image.height()), (27, 27))
        self.assertEqual(int(view.faces.title.cget("size")), -21)

    def test_states_change_only_the_detail_and_update_now(self):
        pass

    def test_a_long_detail_wraps_and_the_banner_grows_around_it(self):
        pass


@patch.dict(os.environ, {"DOLLY_UI_LAYERED": "1"})
@patch.object(tween, "smooth", return_value=True)
class FadeTests(NoticeCase):
    def setUp(self):
        super().setUp()
        self.user32 = FakeUser32()
        patcher = patch.object(update_notice, "_layering", return_value=self.user32)
        patcher.start()
        self.addCleanup(patcher.stop)

    def alphas(self, widget):
        hwnd = widget.winfo_id()
        return [alpha for handle, alpha in self.user32.alphas if handle == hwnd]

    def test_the_banner_fades_in_with_real_alpha_on_the_shared_animator(self, _smooth):
        view = self.view
        view.show("9.0.1", DETAIL)
        hwnd = view.frame.winfo_id()
        # Layered and invisible before it maps, the first step drawn at once (one frame along).
        self.assertTrue(self.user32.styles[hwnd] & 0x80000)
        self.assertEqual(self.alphas(view.frame)[:2], [0, round(255 * ease_out(16 / 220))])
        self.assertTrue(view.frame.winfo_manager())
        self.assertTrue(self.animator.active())
        self.at(0.110)
        self.assertEqual(self.alphas(view.frame)[-1], round(255 * ease_out(0.126 / 0.220)))
        self.assertFalse(self.user32.redraws)
        self.at(0.204)                               # 220 ms after the first frame
        self.assertEqual(self.alphas(view.frame)[-1], 255)
        self.assertFalse(self.user32.styles[hwnd] & 0x80000, "a plain child window again")
        self.assertEqual(self.user32.redraws, [hwnd])
        self.assertFalse(self.animator.active())
        alphas = self.alphas(view.frame)
        self.assertEqual(alphas, sorted(alphas))
        # A notice already up only changes its text: no second fade.
        view.show("9.0.1", "Downloading Dolly 9.0.1: 42%", busy=True)
        self.assertEqual(self.alphas(view.frame), alphas)
        self.assertFalse(self.animator.active())

    def test_the_reminder_fades_in_and_hiding_mid_way_ends_the_fade(self, _smooth):
        view = self.view
        view.show("9.0.1", DETAIL)
        self.finish()
        view.collapse("9.0.1")
        hwnd = view.reminder.winfo_id()
        self.assertEqual(view.reminder.cget("text"), "Update available · 9.0.1")
        self.assertTrue(self.user32.styles[hwnd] & 0x80000)
        self.at(0.050)
        self.assertTrue(0 < self.alphas(view.reminder)[-1] < 255)
        view.show("9.0.1", DETAIL)                   # reopened while it fades: the fade ends opaque
        self.assertEqual(self.alphas(view.reminder)[-1], 255)
        self.assertFalse(self.user32.styles[hwnd] & 0x80000)
        self.assertFalse(view.reminder.winfo_manager())
        self.at(0.060)
        view.hide()                                  # the banner's own fade, cut short the same way
        self.assertFalse(self.user32.styles[view.frame.winfo_id()] & 0x80000)
        self.assertEqual(self.alphas(view.frame)[-1], 255)
        self.assertFalse(view.frame.winfo_manager())
        self.assertFalse(self.animator.active())

    def test_motion_off_shows_at_once_without_layering(self, smooth):
        smooth.return_value = False
        self.view.show("9.0.1", DETAIL)
        self.assertEqual(self.user32.alphas, [])
        self.assertEqual(self.user32.styles, {})
        self.assertTrue(self.view.frame.winfo_manager())
        self.assertFalse(self.animator.active())

    def test_a_windows_without_layered_child_windows_shows_at_once(self, _smooth):
        self.user32.layered = False
        self.view.show("9.0.1", DETAIL)
        self.assertEqual(self.user32.alphas, [])
        self.assertFalse(self.user32.styles.get(self.view.frame.winfo_id(), 0) & 0x80000)
        self.assertTrue(self.view.frame.winfo_manager())
        self.assertFalse(self.animator.active())

    def test_destroy_mid_fade_leaves_no_motion(self, _smooth):
        self.view.show("9.0.1", DETAIL)
        self.assertTrue(self.animator.active())
        self.view.frame.destroy()
        self.assertFalse(self.animator.active())
        self.at(0.5)


@patch.object(tween, "smooth", return_value=True)
class HighlightsMotionTests(NoticeCase):
    def setUp(self):
        super().setUp()
        self.view.show("9.0.1", DETAIL)
        self.finish()
        self.full = CHROME + self.view._content_height

    def test_open_is_gradual_bounded_and_stops_at_the_endpoint(self, _smooth):
        view = self.view
        view.toggle_notes()
        self.assertTrue(view.notes_open)
        self.assertEqual(view.notes_button.cget("text"), "Hide changes")
        self.assertTrue(view._clip_shown())
        first = view._notes_height
        self.assertEqual(first, 0, "opening starts without an initial jump")
        self.at(0.045)
        self.assertGreater(view._notes_height, first)
        heights = [first, view._notes_height]
        for moment in (0.090, 0.135, 0.180):
            self.at(moment)
            heights.append(view._notes_height)
        self.assertEqual(view._notes_height, self.full)
        self.assertEqual(heights, sorted(heights))
        self.assertTrue(all(0 <= height <= self.full for height in heights))
        self.assertFalse(self.animator.active())
        self.assertEqual(int(view.notes_clip.cget("height")), self.full)
        self.commands["update"].assert_not_called()
        self.commands["full_notes"].assert_not_called()

    def test_close_animates_before_removing_the_highlights(self, _smooth):
        view = self.view
        view.toggle_notes()
        self.finish()
        view.toggle_notes()
        self.assertFalse(view.notes_open)
        self.assertEqual(view.notes_button.cget("text"), "What's changed")
        self.assertTrue(view._clip_shown())
        self.at(0.045)
        self.assertTrue(0 < view._notes_height < self.full)
        self.finish()
        self.assertEqual(view._notes_height, 0)
        self.assertFalse(view._clip_shown())
        self.assertTrue(all(view.banner.itemcget(item, "state") == "normal" for item in view._card.corners),
                        "the banner's bottom corners round again")

    def test_reversing_twice_starts_each_motion_where_the_height_is(self, _smooth):
        view = self.view
        view.toggle_notes()
        self.at(0.045)
        opening = view._notes_height
        view.toggle_notes()
        self.assertEqual(view._notes_height, opening)
        self.at(0.060)
        closing = view._notes_height
        self.assertTrue(0 < closing < opening)
        view.toggle_notes()
        self.assertEqual(view._notes_height, closing)
        self.finish()
        self.assertTrue(view.notes_open)
        self.assertEqual(view._notes_height, self.full)

    def test_hide_and_later_close_at_once_and_later_frames_change_nothing(self, _smooth):
        for close in (self.view.hide, lambda: self.view.collapse("9.0.1")):
            with self.subTest(close=close):
                self.view.show("9.0.1", DETAIL)
                self.finish()
                self.view.toggle_notes()
                self.at(self.now + 0.030)
                close()
                self.assertFalse(self.view.notes_open)
                self.assertEqual(self.view._notes_height, 0)
                self.assertFalse(self.view._clip_shown())
                # Later may start the separate reminder fade. The notes motion
                # itself must stop immediately in both paths.
                self.assertFalse(self.animator.active(self.view._notes_key))
                self.at(self.now + 0.5)
                self.assertEqual(self.view._notes_height, 0)
                self.assertEqual(self.view.notes_button.cget("text"), "What's changed")

    def test_destroy_drops_the_motion_and_a_later_toggle_touches_nothing(self, _smooth):
        view = self.view
        view.toggle_notes()
        self.at(0.030)
        view.frame.destroy()
        self.assertTrue(view._destroyed_flag)
        self.assertFalse(self.animator.active())
        view.toggle_notes()
        view.fit_height()
        self.at(0.5)
        self.assertFalse(self.animator.active())

    def test_repeated_render_keeps_the_open_highlights_their_cards_and_scroll(self, _smooth):
        view = self.view
        self.limit.return_value = CHROME + 120
        view.toggle_notes()
        self.at(0.030)
        view.notes_view.yview_moveto(.5)
        before = (view._notes_height, self.animator.active(), view.notes_view.find_withtag("note"),
                  view.notes_view.yview())
        view.set_notes([dict(note) for note in NOTES], release=tuple(RELEASE))
        view.show("9.0.1", "Still waiting for Update now.", busy=True)
        self.assertTrue(view.notes_open)
        self.assertEqual((view._notes_height, self.animator.active(), view.notes_view.find_withtag("note"),
                          view.notes_view.yview()), before)
        self.finish()

    def test_a_changed_release_closes_and_redraws_the_cards(self, _smooth):
        view = self.view
        view.toggle_notes()
        self.at(0.030)
        old = view.notes_view.find_withtag("note")
        changed = (*RELEASE[:2], "b" * 64, RELEASE[3])
        view.set_notes([dict(title="New release", before="Old behaviour", after="New behaviour")], release=changed)
        self.assertFalse(view.notes_open)
        self.assertFalse(view._clip_shown())
        self.assertEqual(view._notes_height, 0)
        self.assertFalse(self.animator.active())
        self.assertFalse(set(old) & set(view.notes_view.find_withtag("note")))
        self.assertEqual(view.notes_view.yview()[0], 0.0)
        self.assertEqual([entry[2] for entry in view.note_layout], [("New release",)])

    def test_resize_retargets_from_the_current_height(self, _smooth):
        view = self.view
        self.limit.return_value = CHROME + 80
        view.toggle_notes()
        self.finish()
        self.assertEqual(view._notes_height, CHROME + 80)
        for target in (CHROME + 200, CHROME + 40):
            with self.subTest(target=target):
                start = view._notes_height
                self.limit.return_value = target
                view.fit_height()
                self.assertTrue(self.animator.active())
                self.at(self.now + 0.045)
                self.assertTrue(min(start, target) < view._notes_height < max(start, target))
                self.finish()
                self.assertEqual(view._notes_height, target)
                self.assertTrue(view.notes_open)

    def test_fit_at_the_same_room_restarts_nothing(self, _smooth):
        view = self.view
        view.toggle_notes()
        self.finish()
        writes = view._notes_height
        view.fit_height()
        self.assertFalse(self.animator.active())
        self.assertEqual(view._notes_height, writes)

    def test_closed_or_destroyed_notice_ignores_fit_without_reading_the_room(self, _smooth):
        view = self.view
        self.limit.reset_mock()
        view.fit_height()
        self.limit.assert_not_called()
        view.toggle_notes()
        self.finish()
        view.frame.destroy()
        self.limit.reset_mock()
        view.fit_height()
        self.limit.assert_not_called()


class HighlightsLayoutTests(NoticeCase):
    """Motion off: every height lands at once, even after the root is mapped."""

    def setUp(self):
        motion = patch.object(tween, "smooth", return_value=False)
        motion.start()
        self.addCleanup(motion.stop)
        super().setUp()
        self.view.show("9.0.1", DETAIL)
        self.show_window()
        self.view.toggle_notes()
        self.settle()

    def test_pane_matches_figmas_release_highlights(self):
        view, c = self.view, self.view.notes_clip
        self.assertEqual(view.notes_button.cget("text"), "Hide changes")
        self.assertEqual(view._notes_height, CHROME + view._content_height)
        self.assertEqual(self.host.winfo_height(), 70 + view._notes_height)
        # The card goes on below the banner: square there, rounded at its new end.
        self.assertEqual([view.banner.itemcget(item, "state") for item in view._card.corners],
                         ["normal", "normal", "hidden", "hidden"])
        self.assertEqual([c.itemcget(item, "state") for item in view._body.corners],
                         ["hidden", "hidden", "normal", "normal"])
        self.assertEqual(c.itemcget(view._body.edges[1], "fill"), T["accent"])
        # Pane: 20 px in from the card's sides, a line below the banner; padding 16 + its edge.
        view_x, view_top, view_w, view_h = view._view
        self.assertEqual(view._pane.box, (21, 1, 909, view_top + view_h + 17))
        self.assertEqual(c.itemcget(view._pane.fills[0], "fill"), T["notes_pane"])
        self.assertEqual({c.itemcget(item, "fill") for item in view._pane.edges}, {T["card_edge"]})
        self.assertEqual((view_x, view_top, view_w, view_h), (38, 64, 854, view._content_height))
        # Header: the eyebrow with 6 % letter spacing, Full release notes at the right.
        eyebrow = view.faces.eyebrow
        self.assertEqual(int(eyebrow.cget("size")), -11)
        self.assertEqual("".join(c.itemcget(item, "text") for item in view._eyebrow), "RELEASE HIGHLIGHTS")
        y = 18 + update_notice._top(eyebrow, 30)
        for index, item in enumerate(view._eyebrow):
            expected = 38 + eyebrow.measure("RELEASE HIGHLIGHTS"[:index]) + round(index * .66)
            self.assertEqual(self.coords(c, item), (expected, y))
        self.assertEqual(c.itemcget(view._eyebrow[0], "fill"), T["muted"])
        self.assertEqual(view.full_notes_button.kind, "secondary")
        self.assertEqual(view.full_notes_button.box()[2], 892)
        self.assertEqual(view.full_notes_button.box()[1], 18)
        # The viewport shows the notes in full.
        self.assertEqual(view.notes_view.winfo_width(), 854 + 16)
        self.assertEqual(view.notes_view.winfo_height(), view._content_height)
        self.assertFalse(view.rail.winfo_ismapped())

    def test_notes_stack_in_two_independent_columns(self):
        view, c = self.view, self.view.notes_view
        layout = view.note_layout
        self.assertEqual([entry[0] for entry in layout], [0, 1, 0, 1])
        self.assertEqual([entry[1][0] for entry in layout], [0, 435, 0, 435])
        self.assertTrue(all(entry[1][2] - entry[1][0] == 419 for entry in layout))
        self.assertEqual((layout[0][1][1], layout[1][1][1]), (0, 0), "top aligned")
        self.assertEqual(layout[2][1][1], layout[0][1][3] + 10)
        self.assertEqual(layout[3][1][1], layout[1][1][3] + 10)
        self.assertEqual(view._content_height, max(layout[2][1][3], layout[3][1][3]))
        for column, (x0, y0, x1, y1), title, rows in layout:
            with self.subTest(note=title):
                expected = 17 + 18 * len(title) + sum(12 + max(18, 18 * len(row[4])) for row in rows) + 17
                self.assertEqual(y1 - y0, expected)
                (before, before_box, before_fill, before_ink, _), (after, after_box, after_fill, after_ink, _) = rows
                self.assertEqual((before, before_fill, before_ink), ("Before:", "edge", "muted"))
                self.assertEqual((after, after_fill, after_ink), ("After:", "accent", "text"))
                self.assertEqual(before_box[:2], (x0 + 17, y0 + 17 + 18 * len(title) + 12))
                self.assertEqual(before_box[3] - before_box[1], 18)
                self.assertEqual(after_box[1], before_box[1] + max(18, 18 * len(rows[0][4])) + 12)
        # The cards: panel, 1 px soft edge, radius 6; titles Inter Medium 12 in white.
        cards = [item for item in c.find_withtag("note") if c.type(item) == "rectangle"
                 and c.itemcget(item, "fill") == T["card_edge"]]
        self.assertTrue(cards)
        self.assertEqual(int(view.faces.value.cget("size")), -12)
        self.assertEqual(view.faces.value.cget("family"), ui_theme.FONT_VALUE)
        self.assertEqual(int(view.faces.tag.cget("size")), -10)
        titles = [item for item in c.find_withtag("note") if c.type(item) == "text"
                  and c.itemcget(item, "text") == NOTES[0]["title"]]
        self.assertEqual(len(titles), 1)
        self.assertEqual(c.itemcget(titles[0], "fill"), T["text"])
        self.assertEqual(self.coords(c, titles[0]), (17, 17 + update_notice._top(view.faces.value, 18)))

    def test_many_notes_scroll_inside_their_room_with_the_page_rail(self):
        view = self.view
        view.toggle_notes()
        self.settle()
        self.limit.return_value = CHROME + 150
        view.set_notes(NOTES * 3, release=("9.0.2", "u", "c" * 64, 1))
        view.toggle_notes()
        self.settle()
        self.assertEqual(view._notes_height, CHROME + 150)
        self.assertEqual(view.notes_view.winfo_height(), 150)
        self.assertGreater(view._content_height, 150)
        self.assertTrue(view.rail.winfo_ismapped())
        self.assertEqual(view.notes_view.cget("scrollregion").split()[-1], str(view._content_height))
        top = view.notes_view.canvasy(0)
        event = Mock(delta=-120, num=0)
        self.assertEqual(view._wheel(event), "break")
        self.assertEqual(view.notes_view.canvasy(0), top + 60, "a notch is 60 px, at once with motion off")
        self.assertGreater(view.rail.first, 0.0)

    def test_short_notes_use_their_height_and_keep_the_full_notes_action(self):
        view = self.view
        view.toggle_notes()
        view.set_notes([], release=("9.0.2", "u", "c" * 64, 1))
        view.toggle_notes()
        self.settle()
        texts = [view.notes_view.itemcget(item, "text") for item in view.notes_view.find_withtag("note")]
        self.assertEqual(texts, [update_notice.FALLBACK])
        self.assertEqual(view._content_height, 18)
        self.assertEqual(view._notes_height, CHROME + 18)
        view.full_notes_button.invoke()
        self.commands["full_notes"].assert_called_once_with()
        self.commands["update"].assert_not_called()

    def test_the_room_callback_bounds_the_highlights_in_physical_pixels(self):
        view = self.view
        view.toggle_notes()
        for room, expected in ((64, 64), (CHROME + 5000, CHROME + view._content_height), (-5, 1)):
            with self.subTest(room=room):
                self.limit.reset_mock()
                self.limit.return_value = room
                view.toggle_notes()
                self.settle()
                self.assertEqual(view._notes_height, expected)
                self.limit.assert_called_once_with()
                self.assertLessEqual(self.host.winfo_height(), 70 + max(1, expected))
                view.toggle_notes()
        unbounded = update_notice.UpdateNotice(self.host, **self.commands)
        unbounded.set_notes(NOTES * 3, release=RELEASE)
        unbounded.toggle_notes()
        self.assertEqual(unbounded._notes_height, unbounded.MAX_NOTES_HEIGHT)
        unbounded.frame.destroy()


class ReminderTests(NoticeCase):
    def test_without_a_stage_the_reminder_sits_below_in_the_host(self):
        view = self.view
        view.show("9.0.1", DETAIL)
        view.collapse("9.0.1")
        self.show_window()
        self.assertFalse(view.frame.winfo_manager())
        self.assertEqual(view.reminder.grid_info()["sticky"], "e")
        self.assertEqual(view.reminder.cget("text"), "Update available · 9.0.1")
        self.assertEqual(view.reminder.winfo_height(), 30)
        body = fonts.font(self.root, "body")
        self.assertEqual(view.reminder.winfo_width(), body.measure("Update available · 9.0.1") + 26)
        reminder = view.reminder
        self.assertEqual(reminder.itemcget(reminder.plate.fills[0], "fill"), T["button"])
        self.assertEqual({reminder.itemcget(item, "fill") for item in reminder.plate.edges}, {T["accent"]})
        self.assertEqual(reminder.itemcget(reminder.label, "fill"), T["text"])
        view.reminder.invoke()
        self.commands["reopen"].assert_called_once_with()
        view.show("9.0.1", DETAIL)
        self.assertFalse(view.reminder.winfo_manager())
        view.hide()
        self.assertFalse(view.frame.winfo_manager())

    def test_on_the_stage_the_reminder_goes_to_the_header_row(self):
        class Stage(tk.Canvas):
            header_widget = None

            def set_header_widget(self, widget):
                self.calls.append(widget)
                self.header_widget = widget

        stage = Stage(self.root)
        stage.calls = []
        host = tk.Frame(stage)
        host.columnconfigure(0, weight=1)
        view = update_notice.UpdateNotice(host, **self.commands)
        self.assertIs(view.reminder.master, stage)
        view.collapse("9.0.1")
        self.assertEqual(stage.calls, [view.reminder])
        view.collapse("9.0.2")
        self.assertEqual(stage.calls, [view.reminder], "placed once; the text follows")
        self.assertEqual(view.reminder.cget("text"), "Update available · 9.0.2")
        view.show("9.0.2", DETAIL)
        self.assertEqual(stage.calls, [view.reminder, None])
        view.collapse("9.0.2")
        view.frame.destroy()
        self.assertEqual(stage.calls[-1], None, "the stage forgets a reminder that goes away")

    def test_the_reminder_eases_its_fill_and_answers_the_keyboard(self):
        view = self.view
        view.collapse("9.0.1")
        reminder = view.reminder
        with patch.object(tween, "smooth", return_value=True):
            reminder.event_generate("<Enter>")
            reminder._entered()
            self.at(0.5)
            self.assertEqual(reminder.plate.fill, T["button_hover"])
            reminder._pressed_down()
            self.at(1.0)
            self.assertEqual(reminder.plate.fill, T["button_active"])
            reminder._released(Mock(x=-5, y=-5))
            self.at(2.0)
            self.assertEqual(reminder.plate.fill, T["button"])
        self.commands["reopen"].assert_not_called()
        self.assertEqual(reminder._key(), "break")
        self.commands["reopen"].assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
