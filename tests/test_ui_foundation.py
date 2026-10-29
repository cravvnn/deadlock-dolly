"""Foundation of the redesigned shell: scale, boil clock, action proxies, fonts and art."""
import base64
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import tkinter as tk
from tkinter import font as tkfont
import unittest
from unittest.mock import patch
import zlib

from dolly import ui_theme
from dolly.ui import action_proxy, art, boil, fonts, scale
from dolly.ui.action_proxy import ActionProxy, ProgressProxy
from dolly.ui.boil import BoilClock, IdleUpdate


class RootCase(unittest.TestCase):
    """A withdrawn root whose Tk callback errors fail the test."""

    def new_root(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        root.withdraw()
        root.report_callback_exception = lambda *error: self.errors.append(error)

        def close():
            try:
                root.destroy()
            except tk.TclError:
                pass  # The test destroyed it already.
        self.addCleanup(close)
        return root

    def setUp(self):
        self.errors = []
        self.root = self.new_root()

    def tearDown(self):
        self.assertEqual(self.errors, [], "Tk callback failed")

    def wait(self, root, milliseconds):
        root.after(milliseconds, root.quit)
        root.mainloop()


class Member:
    def __init__(self, log, name, alive=True, fail=False):
        self.log, self.name, self.alive, self.fail = log, name, alive, fail

    def advance(self):
        if self.fail:
            raise tk.TclError("destroyed canvas")
        self.log.append(self.name)

    def winfo_exists(self):
        return self.alive


def mapped(clock):
    clock._mapped(SimpleNamespace(widget=clock.root))


def resized(clock, width, height):
    clock._configured(SimpleNamespace(widget=clock.root, width=width, height=height))


class ScaleTests(RootCase):
    def test_design_pixels_hairlines_and_buckets(self):
        self.assertEqual(scale.px(1.0, 36), 36)
        self.assertEqual(scale.px(1.5, 13), 20)
        self.assertEqual(scale.px(1.25, 1), 1)
        self.assertEqual(scale.px(2.0, 0.5), 1)
        self.assertEqual(scale.hairline(1.0), 1)
        self.assertEqual(scale.hairline(1.5), 1)
        self.assertEqual(scale.hairline(2.25), 2)
        for value, expected in ((0.9, "1"), (1.0, "1"), (1.25, "1"), (1.495, "1.5"), (1.5, "1.5"),
                                (1.75, "1.5"), (2.0, "2"), (3.0, "2")):
            with self.subTest(scale=value):
                self.assertEqual(scale.bucket(value), expected)

    def test_padding_tuples_scale_item_by_item(self):
        self.assertEqual(scale.px(1.5, (0, 14)), (0, 21))
        self.assertEqual(scale.px(1.0, [16, 12]), (16, 12))
        self.assertEqual(scale.px(2.0, (10, 5, 10, 5)), (20, 10, 20, 10))
        self.assertEqual(scale.scaler(self.root)((3, 4)), scale.px(scale.ui_scale(self.root), (3, 4)))

    def test_display_scale_is_the_whole_dpi_tk_reports(self):
        for scaling, expected in ((96 / 72, 1.0), (1.3323, 1.0), (1.998, 1.5), (2.0, 1.5),
                                  (1.665, 1.25), (2.6667, 2.0), (1.0, 1.0)):
            with self.subTest(scaling=scaling):
                self.assertEqual(scale.display_scale(SimpleNamespace(call=lambda *_a, value=scaling: value)),
                                 expected)

    def test_dpi_awareness_opt_out_and_first_answer_kept(self):
        with patch.object(scale, "_awareness", None), patch.dict(os.environ, {scale.DPI_AWARE_ENV: "0"}):
            self.assertFalse(scale.enable_dpi_awareness())
            # Later calls keep the first answer and never touch the process again.
            with patch.dict(os.environ, {scale.DPI_AWARE_ENV: "1"}):
                self.assertFalse(scale.enable_dpi_awareness())

    def test_scale_matches_the_application_and_is_cached_per_toplevel(self):
        original = self.root.tk.call("tk", "scaling")
        expected = max(1.0, round(float(original) * 72) / 96)
        # Tk on Windows shares `tk scaling` between every interpreter in the process
        # (and keeps it after a root is destroyed): give it back before the roots close.
        self.addCleanup(self.root.tk.call, "tk", "scaling", original)
        child = tk.Frame(self.root)
        self.assertEqual(scale.ui_scale(child), expected)
        self.root.tk.call("tk", "scaling", 4.0)
        self.assertEqual(scale.ui_scale(self.root), expected, "The first reading is kept for the window")
        other = self.new_root()
        other.tk.call("tk", "scaling", 2.0)
        self.assertAlmostEqual(scale.ui_scale(other), 1.5, places=2)

    def test_dpi_awareness_reports_the_mode_the_process_ends_up_in(self):
        import ctypes
        E_ACCESSDENIED = -2147024891
        for set_result, mode, expected in ((0, 1, True), (E_ACCESSDENIED, 2, True), (E_ACCESSDENIED, 0, False)):
            with self.subTest(set_result=set_result, mode=mode):
                def report(_process, reference, mode=mode):
                    reference._obj.value = mode
                    return 0
                windll = SimpleNamespace(shcore=SimpleNamespace(
                    SetProcessDpiAwareness=lambda level, result=set_result: result,
                    GetProcessDpiAwareness=report))
                with patch.object(scale, "_awareness", None), patch.object(scale.sys, "platform", "win32"), \
                        patch.dict(os.environ, {scale.DPI_AWARE_ENV: "1"}), patch.object(ctypes, "windll", windll):
                    # A manifest or the host may already have chosen unaware: report that, not success.
                    self.assertIs(scale.enable_dpi_awareness(), expected)

    def test_character_widths_keep_their_design_size(self):
        self.assertEqual(scale.chars(self.root, 20), 20)  # 100 %: exactly the given characters
        original = self.root.tk.call("tk", "scaling")
        self.addCleanup(self.root.tk.call, "tk", "scaling", original)
        other = self.new_root()
        other.tk.call("tk", "scaling", 2.0)
        family, points = ui_theme.FONT_FAMILY, ui_theme.FONT_SIZES["body"]
        design = tkfont.Font(other, family=family, size=-round(points * 96 / 72)).measure("0")
        actual = tkfont.Font(other, family=family, size=points).measure("0")
        for count in (3, 6, 11, 12, 20):
            with self.subTest(count=count):
                width = scale.chars(other, count)
                # Within half a character of count design characters at 150 %.
                self.assertLessEqual(abs(width * actual - count * design * 1.5), actual / 2 + .01)
                self.assertGreaterEqual(width, 1)


class BoilClockTests(RootCase):
    def setUp(self):
        super().setUp()
        motion = patch.object(boil, "motion_enabled", return_value=True)
        motion.start()
        self.addCleanup(motion.stop)

    def test_one_clock_per_toplevel_held_until_the_root_maps(self):
        clock = BoilClock.of(self.root)
        self.assertIs(BoilClock.of(tk.Frame(self.root)), clock)
        self.assertIsNot(BoilClock.of(self.new_root()), clock)
        self.assertTrue(clock.held)
        self.assertIn("unmapped", clock.holds)
        clock.register(Member([], "a"))
        self.assertFalse(clock.running, "A withdrawn root never schedules a tick")
        mapped(clock)
        self.assertFalse(clock.held)
        self.assertTrue(clock.running)
        clock._unmapped(SimpleNamespace(widget=clock.root))
        self.assertFalse(clock.running)
        clock._mapped(SimpleNamespace(widget=tk.Frame(self.root)))
        self.assertTrue(clock.held, "Descendant map events are ignored")

    def test_manual_tick_advances_members_in_order_and_drops_dead_ones(self):
        log = []
        clock = BoilClock.of(self.root)
        first, dead, broken, last = (Member(log, "first"), Member(log, "dead", alive=False),
                                     Member(log, "broken", fail=True), Member(log, "last"))
        for member in (first, dead, broken, last, first):
            clock.register(member)
        clock.tick()
        self.assertEqual(log, ["first", "last"])
        self.assertEqual(clock.frame, 1)
        self.assertEqual(clock.members, (first, last))
        clock.unregister(first)
        clock.tick()
        self.assertEqual(log, ["first", "last", "last"])
        self.assertEqual(clock.frame, 2)

    def test_member_reads_the_tick_in_progress(self):
        clock = BoilClock.of(self.root)
        frames = []
        member = Member([], "frame")
        member.advance = lambda: frames.append(clock.frame)
        clock.register(member)
        clock.tick(); clock.tick()
        self.assertEqual(frames, [1, 2])

    def test_scheduled_ticks_hold_and_release(self):
        log = []
        clock = BoilClock.of(self.root)
        clock.register(Member(log, "a"))
        with patch.object(boil, "INTERVAL_MS", 5):
            mapped(clock)
            self.wait(self.root, 80)
            self.assertGreater(clock.frame, 1)
            clock.hold("drag")
            self.assertFalse(clock.running)
            frame = clock.frame
            self.wait(self.root, 40)
            self.assertEqual(clock.frame, frame)
            clock.release("drag")
            self.assertTrue(clock.running)
            clock.release("drag")  # Releasing twice is harmless.

    def test_destroying_one_root_leaves_the_other_ticking(self):
        other = self.new_root()
        clocks = [BoilClock.of(self.root), BoilClock.of(other)]
        logs = [[], []]
        with patch.object(boil, "INTERVAL_MS", 5):
            for clock, log in zip(clocks, logs):
                clock.register(Member(log, "m"))
                mapped(clock)
            self.root.destroy()
            self.assertFalse(clocks[0].running)
            self.wait(other, 80)
        self.assertGreater(len(logs[1]), 1)
        self.assertEqual(logs[0], [])
        self.assertTrue(clocks[1].running)

    def test_resize_freezes_then_settles(self):
        clock = BoilClock.of(self.root)
        events = []
        remove = clock.add_settle_listener(events.append)
        clock.register(Member([], "a"))
        with patch.object(boil, "INTERVAL_MS", 5), patch.object(boil, "SETTLE_MS", 20):
            mapped(clock)
            size = clock._size
            resized(clock, *size)  # A move: no hold.
            self.assertEqual(events, [])
            resized(clock, size[0] + 40, size[1] + 30)
            resized(clock, size[0] + 80, size[1] + 60)
            self.assertEqual(events, [True], "One freeze for a burst of size changes")
            self.assertIn("settle", clock.holds)
            self.assertFalse(clock.running)
            clock._configured(SimpleNamespace(widget=tk.Frame(self.root), width=1, height=1))
            self.wait(self.root, 120)
            self.assertEqual(events, [True, False])
            self.assertNotIn("settle", clock.holds)
            self.assertTrue(clock.running)
            remove()
            resized(clock, size[0], size[1])
            self.assertEqual(events, [True, False])
            clock.release("settle")

    def test_motion_off_never_schedules(self):
        clock = BoilClock.of(self.root)
        with patch.object(boil, "motion_enabled", return_value=False):
            clock.register(Member([], "a"))
            mapped(clock)
            self.assertFalse(clock.running)
            clock.tick()
            self.assertEqual(clock.frame, 1, "Tests and callers can still step frames by hand")

    def test_motion_setting_reads_the_environment_first(self):
        with patch.dict(os.environ, {"DOLLY_UI_MOTION": "0"}):
            self.assertFalse(_real_motion_enabled())
        with patch.dict(os.environ, {"DOLLY_UI_MOTION": "1"}):
            self.assertIsInstance(_real_motion_enabled(), bool)

    def test_stop_cancels_everything_and_stays_inert(self):
        clock = BoilClock.of(self.root)
        clock.register(Member([], "a"))
        mapped(clock)
        resized(clock, 900, 700)
        clock.stop()
        self.assertFalse(clock.running)
        self.assertIsNone(clock._settle_job)
        clock.register(Member([], "b"))
        self.assertFalse(clock.running)

    def test_shown_root_maps_and_runs_the_real_events(self):
        clock = BoilClock.of(self.root)
        log = []
        clock.register(Member(log, "a"))
        with patch.object(boil, "INTERVAL_MS", 5), patch.object(boil, "SETTLE_MS", 5):
            self.root.deiconify()
            self.root.update()
            self.wait(self.root, 120)
        self.assertNotIn("unmapped", clock.holds)
        self.assertTrue(log)


_real_motion_enabled = boil.motion_enabled


class IdleUpdateTests(RootCase):
    def test_a_burst_collapses_into_one_idle_call(self):
        calls = []
        frame = tk.Frame(self.root)
        update = IdleUpdate(frame, lambda: calls.append(1))
        update.schedule(); update.schedule("trace", "args"); update()
        self.assertTrue(update.pending)
        self.root.update_idletasks()
        self.assertEqual(calls, [1])
        update.schedule()
        update.flush()
        self.assertEqual(calls, [1, 1])
        self.assertFalse(update.pending)

    def test_destroy_cancels_the_pending_call(self):
        calls = []
        frame = tk.Frame(self.root)
        update = IdleUpdate(frame, lambda: calls.append(1))
        update.schedule()
        frame.destroy()
        self.root.update_idletasks()
        update.schedule()
        self.root.update_idletasks()
        self.assertEqual(calls, [])


def _set_widget_state(widget, state):
    """Mirror of ``dolly.gui._set_widget_state`` (the poll's change-only state writer)."""
    try:
        current = str(widget.cget("state"))
    except (AttributeError, tk.TclError):
        widget.configure(state=state)
        return
    if current != state:
        widget.configure(state=state)


class ActionProxyTests(unittest.TestCase):
    def test_button_api_text_label_state_and_invoke(self):
        calls = []
        proxy = ActionProxy("Open replay in Dolly", lambda: calls.append("start") or "started", label="START")
        self.assertEqual(proxy.cget("text"), "Open replay in Dolly")
        self.assertEqual(proxy["label"], "START")
        self.assertEqual(proxy.cget("-state"), "normal")
        self.assertTrue(proxy.enabled())
        self.assertEqual(proxy.invoke(), "started")
        proxy.configure(state="disabled")
        self.assertFalse(proxy.enabled())
        self.assertEqual(proxy.invoke(), "")
        self.assertEqual(calls, ["start"])
        proxy.config(state="readonly")
        self.assertTrue(proxy.enabled(), "readonly counts as enabled, as on ttk")
        plain = ActionProxy("Cancel startup", state="disabled")
        self.assertEqual(plain.cget("label"), "Cancel startup", "The label defaults to the text")
        self.assertEqual(plain.invoke(), "")
        plain.configure(state="normal")
        self.assertEqual(plain.invoke(), "", "No command: nothing to run")
        self.assertEqual(set(plain.configure()), {"text", "label", "state", "command"})

    def test_subscribers_hear_changes_only(self):
        proxy = ActionProxy("Stop / restore", label="STOP/RESTORE")
        heard = []
        unsubscribe = proxy.subscribe(heard.append)
        proxy.configure(state="normal")
        self.assertEqual(heard, [])
        proxy.configure(state="disabled")
        proxy.configure(text="Stop / restore")
        proxy.configure(label="STOP")
        self.assertEqual(heard, [proxy, proxy])
        unsubscribe(); unsubscribe()
        proxy.configure(state="normal")
        self.assertEqual(len(heard), 2)

    def test_poll_state_writer_is_compatible(self):
        proxy = ActionProxy("Open replay in Dolly")
        heard = []
        proxy.subscribe(heard.append)
        writers = [_set_widget_state]
        try:
            from dolly.gui import _set_widget_state as application_writer
        except Exception:  # The shell may be mid-edit; the mirror keeps the contract tested.
            pass
        else:
            writers.append(application_writer)
        for writer in writers:
            heard.clear()
            writer(proxy, "disabled"); writer(proxy, "disabled")
            writer(proxy, "normal"); writer(proxy, "normal")
            self.assertEqual(len(heard), 2)

    def test_unknown_options_and_states_fail_like_tk(self):
        proxy = ActionProxy("Save")
        with self.assertRaises(tk.TclError):
            proxy.cget("width")
        with self.assertRaises(tk.TclError):
            proxy.configure(width=4)
        with self.assertRaises(tk.TclError):
            proxy.configure(state="hidden")
        with self.assertRaises(tk.TclError):
            ActionProxy("Save", state="off")


class ProgressProxyTests(unittest.TestCase):
    def test_start_stop_notify_once_per_change(self):
        progress = ProgressProxy()
        heard = []
        unsubscribe = progress.subscribe(lambda proxy: heard.append(proxy.running))
        self.assertFalse(progress.running)
        progress.start(15); progress.start()
        progress.stop(); progress.stop()
        self.assertEqual(heard, [True, False])
        unsubscribe()
        progress.start()
        self.assertEqual(heard, [True, False])
        self.assertTrue(progress.running)


def truetype_blob():
    """A minimal TrueType table directory with a ``name`` table (not a usable font)."""
    body = b"\0" * 8
    directory = struct.pack(">4sIII", b"name", 0, 12 + 16, len(body))
    return b"\x00\x01\x00\x00" + struct.pack(">HHHH", 1, 16, 0, 0) + directory + body


def display_metrics_blob():
    head, hhea, os2 = bytearray(54), bytearray(36), bytearray(78)
    struct.pack_into(">H", head, 18, 1000)
    struct.pack_into(">h", head, 38, -300)
    struct.pack_into(">h", hhea, 6, -200)
    struct.pack_into(">h", os2, 70, -200)
    struct.pack_into(">H", os2, 76, 90)
    names = [(1, 'VALVE Oracle Medium'), (3, 'VALVEOracle-Medium'),
             (13, 'Copyright VALVE; original licence'), (16, 'VALVE Oracle')]
    strings, records = b'', b''
    for kind, text in names:
        encoded = text.encode('utf-16-be')
        records += struct.pack('>6H', 3, 1, 1033, kind, len(encoded), len(strings))
        strings += encoded
    name = struct.pack('>3H', 0, len(names), 6 + len(records)) + records + strings
    tables = [(b'head', bytes(head)), (b'hhea', bytes(hhea)), (b'OS/2', bytes(os2)),
              (b'name', name), (b'glyf', b'Outlines stay exact'), (b'hmtx', b'Advances stay exact')]
    directory, body = b'', b''
    for tag, data in tables:
        directory += struct.pack('>4sIII', tag, 0, 12 + 16 * len(tables) + len(body), len(data))
        body += data + b'\0' * (-len(data) % 4)
    return b'\x00\x01\x00\x00' + struct.pack('>4H', len(tables), 0, 0, 0) + directory + body


class FontTests(RootCase):
    def test_display_metrics_keep_glyphs_and_licence_but_reserve_full_descenders(self):
        original = display_metrics_blob()
        fixed = fonts._display_metrics(original)
        self.assertEqual(len(fixed), len(original))
        self.assertEqual(fonts._checksum(fixed), 0xB1B0AFBA)
        tables = {tag: (checksum, offset, length) for tag, checksum, offset, length in
                  (struct.unpack_from('>4sIII', fixed, pos) for pos in range(12, 108, 16))}
        for tag in (b'glyf', b'hmtx'):
            _checksum, offset, length = tables[tag]
            self.assertEqual(fixed[offset:offset + length], original[offset:offset + length])
        for tag, delta in ((b'hhea', 6), (b'OS/2', 70)):
            self.assertEqual(struct.unpack_from('>h', fixed, tables[tag][1] + delta)[0], -300)
        self.assertEqual(struct.unpack_from('>H', fixed, tables[b'OS/2'][1] + 76)[0], 300)
        self.assertIn('DOLLY Oracle Medium'.encode('utf-16-be'), fixed)
        self.assertIn('Copyright VALVE; original licence'.encode('utf-16-be'), fixed)
        for tag in (b'head', b'hhea', b'OS/2', b'name'):
            checksum, offset, length = tables[tag]
            table = bytearray(fixed[offset:offset + length])
            if tag == b'head': struct.pack_into('>I', table, 8, 0)
            self.assertEqual(fonts._checksum(bytes(table)), checksum, tag)
        self.assertEqual(fonts._display_metrics(fixed), fixed)
        with self.assertRaises(ValueError): fonts._display_metrics(truetype_blob())

    def test_private_display_cache_rechecks_source_and_repairs_only_its_derivative(self):
        blob = display_metrics_blob()
        spec = dict(family='VALVE Oracle Medium', bytes=len(blob), sha256=hashlib.sha256(blob).hexdigest())
        with tempfile.TemporaryDirectory() as folder:
            original = Path(folder) / 'original.ttf'
            original.write_bytes(blob)
            display = fonts._display_font(original, spec)
            self.assertNotEqual(original, display)
            self.assertEqual(original.read_bytes(), blob)
            expected = display.read_bytes()
            display.write_bytes(b'bad derived cache')
            self.assertEqual(fonts._display_font(original, spec).read_bytes(), expected)
            original.write_bytes(b'bad original cache')
            with self.assertRaises(ValueError): fonts._display_font(original, spec)
            self.assertEqual(display.read_bytes(), expected)

    def test_private_corrected_faces_replace_named_fonts_after_loading(self):
        listed = ('Segoe UI', 'VALVE Oracle Medium')
        with patch.dict(os.environ, {'DOLLY_UI_FONTS': ''}), \
                patch.object(fonts, 'load_bundled', return_value=[]), \
                patch.object(fonts.tkfont, 'families', return_value=listed):
            fonts.install(self.root, 1.0)
            title = fonts.font(self.root, 'page_title')
            self.assertEqual(title.cget('family'), 'VALVE Oracle Medium')
        changes = []
        fonts.on_change(self.root, lambda: changes.append(True))
        with patch.object(fonts.tkfont, 'families', return_value=listed + ('DOLLY Oracle Medium',)), \
                patch.object(fonts, 'load_bundled', return_value=[]):
            fonts.install(self.root, 1.0)
        self.assertIs(fonts.font(self.root, 'page_title'), title)
        self.assertEqual(title.cget('family'), 'DOLLY Oracle Medium')
        self.assertEqual(fonts.family_available(self.root, 'page_title'), 'VALVE Oracle Medium')
        self.assertEqual(changes, [True])

    def test_forced_fallback_uses_system_faces_per_root(self):
        with patch.dict(os.environ, {"DOLLY_UI_FONTS": "fallback"}):
            fonts.install(self.root, 1.0)
            other = self.new_root()
            fonts.install(other, 1.5)
            for root in (self.root, other):
                names = set(root.tk.splitlist(root.tk.call("font", "names")))
                self.assertLessEqual(set(fonts.NAMES.values()), names)
                for key in ui_theme.DISPLAY_FONTS:
                    with self.subTest(key=key):
                        family = fonts.family_available(root, key)
                        self.assertNotIn(family, ui_theme.TRUE_DESCENT_EM)
                        self.assertGreaterEqual(fonts.descent_shortfall(root, key), 0)
                        self.assertEqual(fonts.descent_shortfall(root, key), 0)
            self.assertEqual(int(fonts.font(other, "wordmark").cget("size")), -32)
            self.assertEqual(int(fonts.font(self.root, "body").cget("size")), -12)
            self.assertIsNot(fonts.font(self.root, "body"), fonts.font(other, "body"))
            self.assertEqual(fonts.request_private_fonts(self.root, "C:/Games/Deadlock"), "fallback")
            self.root.destroy()
            self.assertEqual(other.tk.call("font", "configure", "DollyBody", "-size"), -18)

    def test_installed_faces_win_when_tk_lists_them(self):
        listed = ("Segoe UI", "Segoe UI Semibold", "VALVE Oracle Medium", "VALVE Oracle SemiBold",
                  "VALVE Pulp Bold", "Segoe MDL2 Assets")
        with patch.dict(os.environ, {"DOLLY_UI_FONTS": ""}), \
             patch.dict(fonts._REGISTERED, {}, clear=True), \
             patch.object(fonts.tkfont, "families", return_value=listed):
            fonts.install(self.root, 1.0)
            self.assertEqual(fonts.family_available(self.root, "card_title"), "VALVE Oracle SemiBold")
            self.assertEqual(fonts.family_available(self.root, "icon"), "Segoe MDL2 Assets")
            self.assertGreaterEqual(fonts.descent_shortfall(self.root, "card_title"), 0)
            self.assertEqual(fonts.status(self.root)["status"], "system")
        with patch.object(fonts.tkfont, "families", return_value=("Segoe UI",)):
            fonts.install(self.root, 1.0)
            self.assertEqual(fonts.family_available(self.root, "icon"), "",
                             "No icon font: callers fall back to a text glyph")
            self.assertEqual(fonts.family_available(self.root, "wordmark"), "Segoe UI")
            self.assertEqual(fonts.font(self.root, "wordmark").cget("weight"), "bold")

    def test_font_installs_on_demand_and_listeners_hear_reconfiguration(self):
        self.assertEqual(str(fonts.font(self.root, "sticker")), "DollySticker")
        heard = []
        remove = fonts.on_change(self.root, lambda: heard.append(1))
        fonts.install(self.root, fonts._ensure(self.root).scale)
        self.assertEqual(heard, [], "Nothing changed")
        fonts.install(self.root, 2.0)
        self.assertEqual(heard, [1])
        remove()
        fonts.install(self.root, 1.0)
        self.assertEqual(heard, [1])

    def test_empty_game_path_touches_no_files(self):
        with tempfile.TemporaryDirectory() as local, patch.dict(os.environ, {"LOCALAPPDATA": local, "DOLLY_UI_FONTS": ""}), \
                patch.object(fonts, "_load_private", side_effect=AssertionError("disk")) as load:
            self.assertIn(fonts.request_private_fonts(self.root, ""), ("fallback", "system", "partial", "ready"))
            self.assertIn(fonts.request_private_fonts(self.root, None), ("fallback", "system", "partial", "ready"))
            load.assert_not_called()
            self.assertEqual(os.listdir(local), [])

    def fake_install(self, folder, files):
        root = Path(folder) / "Deadlock"
        for name in ("game/bin/win64/deadlock.exe", "game/citadel/gameinfo.gi", "game/citadel/bin/win64/server.dll"):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")
        fonts_dir = root / "game/citadel/panorama/fonts"
        fonts_dir.mkdir(parents=True)
        for name, data in files.items():
            (fonts_dir / name).write_bytes(data)
        return root

    def test_a_changed_game_font_is_rejected_without_caching_or_registering(self):
        spec = fonts.FONT_SOURCES[0]
        with tempfile.TemporaryDirectory() as folder:
            install = self.fake_install(folder, {spec["name"]: b"\x00\x01\x00\x00" + b"x" * (spec["bytes"] - 4)})
            local = Path(folder) / "Local"
            # (The shipped Inter files are not this test's: a first install would register them.)
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local), "DOLLY_UI_FONTS": ""}), \
                    patch.object(fonts, "_register_private") as register, \
                    patch.object(fonts, "load_bundled", return_value=[]), \
                    patch.object(fonts.tkfont, "families", return_value=("Segoe UI",)):
                result = fonts._load_private(str(install / "game/bin/win64/deadlock.exe"))
                face = result["fonts"][0]
                self.assertEqual(face["status"], "unavailable")
                self.assertIn("SHA-256", face["error"])
                self.assertEqual([state["status"] for state in result["fonts"][1:]], ["unavailable", "unavailable"])
                self.assertEqual(fonts.request_private_fonts(self.root, str(install)), "fallback")
                register.assert_not_called()
            self.assertFalse(list(local.rglob("*.ttf")))
            self.assertEqual(sorted(path.name for path in (install / "game/citadel/panorama/fonts").iterdir()),
                             [spec["name"]], "The game install is never written")

    def test_verified_faces_are_cached_under_localappdata_and_reused(self):
        blob = truetype_blob()
        spec = {"family": "Test Display", "name": "test-display.ttf", "bytes": len(blob),
                "sha256": hashlib.sha256(blob).hexdigest()}
        registered = []
        with tempfile.TemporaryDirectory() as folder:
            install = self.fake_install(folder, {spec["name"]: blob})
            local = Path(folder) / "Local"
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local)}), \
                    patch.object(fonts, "FONT_SOURCES", (spec,)), \
                    patch.object(fonts, "_register_private",
                                 side_effect=lambda path, digest: registered.append(Path(path)) or "registered"):
                first = fonts._load_private(str(install))
                second = fonts._load_private(str(Path(folder) / "elsewhere"))
            cached = local / "DeadlockDolly" / "ui-fonts-v1" / (spec["sha256"] + ".ttf")
            self.assertEqual(first["fonts"][0]["source"], "installed")
            self.assertEqual(second["fonts"][0]["source"], "cache", "Pinned cache objects survive a lost install")
            self.assertEqual([path.resolve() for path in registered], [cached.resolve()] * 2)
            self.assertEqual(cached.read_bytes(), blob)
            self.assertEqual(first["cache_dir"], str(cached.parent.resolve()))
            self.assertEqual(sorted(path.name for path in local.rglob("*") if path.is_file()), [cached.name])

    def test_font_file_checks_refuse_bad_tables(self):
        blob = truetype_blob()
        spec = {"bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}
        fonts._verify_font(blob, spec)
        for bad in (b"OTTO" + blob[4:], blob[:4] + struct.pack(">H", 0) + blob[6:],
                    blob[:12] + struct.pack(">4sIII", b"head", 0, 28, 8) + blob[28:]):
            with self.subTest(header=bad[:16]):
                with self.assertRaises(ValueError):
                    fonts._verify_font(bad, {"bytes": len(bad), "sha256": hashlib.sha256(bad).hexdigest()})
        with self.assertRaises(ValueError):
            fonts._verify_font(blob + b"\0", spec)

    def test_status_is_json_safe(self):
        fonts.install(self.root, 1.0)
        json.dumps(fonts.status(self.root))
        self.assertIn(fonts.status(self.root)["status"], ("fallback", "system", "ready", "partial"))

    def test_shipped_inter_faces_register_privately_once(self):
        blob = truetype_blob()
        registered = []
        self.assertEqual(fonts.bundled_dir().parts[-3:], ("assets", "fonts", "inter"))
        with tempfile.TemporaryDirectory() as folder:
            self.addCleanup(fonts._BUNDLED.pop, str(Path(folder)), None)
            files = {"Inter-Regular.ttf": blob, "Inter-Medium.ttf": blob, "Inter-SemiBold.ttf": b"OTTO" + blob[4:]}
            for name, data in files.items():   # Inter-Bold.ttf is missing
                (Path(folder) / name).write_bytes(data)
            with patch.object(fonts, "_register_private",
                              side_effect=lambda path, digest: registered.append((Path(path).name, digest)) or "registered"):
                first = fonts.load_bundled(folder)
                again = fonts.load_bundled(folder)
        self.assertEqual([face["name"] for face in first], [spec["name"] for spec in fonts.BUNDLED_FACES])
        self.assertEqual([face["status"] for face in first], ["registered", "registered", "unavailable", "missing"])
        self.assertIn("TrueType", first[2]["error"])
        self.assertEqual(again, first, "Registered once per process")
        self.assertEqual(registered, [("Inter-Regular.ttf", hashlib.sha256(blob).hexdigest()),
                                      ("Inter-Medium.ttf", hashlib.sha256(blob).hexdigest())])

    def test_ui_text_faces_follow_inter_and_fall_back_to_segoe_ui(self):
        # UPDATE 23: Inter for text, Inter Medium for values (TkTextFont: entries and dropdowns),
        # Inter SemiBold for titles when the game's VALVE faces are missing; Segoe UI without Inter.
        listed = ("Segoe UI", "Segoe UI Semibold", "Inter", "Inter Medium", "Inter SemiBold", "Segoe MDL2 Assets")

        def standard():
            return {name: tkfont.nametofont(name, root=self.root).cget("family") for name in fonts.STANDARD_FONTS}
        before = standard()
        with patch.object(ui_theme, "FONT_FAMILY", ui_theme.FONT_FAMILY), \
                patch.object(ui_theme, "FONT_VALUE", ui_theme.FONT_VALUE), \
                patch.object(fonts, "load_bundled", return_value=[]):
            with patch.dict(os.environ, {"DOLLY_UI_FONTS": ""}), \
                    patch.object(fonts.tkfont, "families", return_value=listed):
                fonts.install(self.root, 1.0)
                self.assertEqual((ui_theme.FONT_FAMILY, ui_theme.FONT_VALUE), ("Inter", "Inter Medium"))
                for key in ("body", "small"):
                    self.assertEqual(fonts.family_available(self.root, key), "Inter")
                for key in ("wordmark", "page_title", "card_title"):
                    self.assertEqual(fonts.family_available(self.root, key), "Inter SemiBold")
                self.assertEqual(fonts.family_available(self.root, "sticker"), "Inter")
                self.assertEqual(fonts.font(self.root, "sticker").cget("weight"), "bold")
                faces = standard()
                self.assertEqual(faces.pop("TkTextFont"), "Inter Medium", "Field values are Inter Medium")
                self.assertEqual(set(faces.values()), {"Inter"})
                self.assertEqual(fonts.status(self.root)["ui"], {"family": "Inter", "value": "Inter Medium"})
            with patch.dict(os.environ, {"DOLLY_UI_FONTS": "fallback"}), \
                    patch.object(fonts.tkfont, "families", return_value=listed):
                fonts.install(self.root, 1.0)
                self.assertEqual((ui_theme.FONT_FAMILY, ui_theme.FONT_VALUE), ("Segoe UI", "Segoe UI"))
                self.assertEqual(fonts.family_available(self.root, "body"), "Segoe UI")
                self.assertEqual(fonts.family_available(self.root, "card_title"), "Segoe UI Semibold")
                self.assertEqual(standard(), before, "Tk's standard fonts get their own faces back")
            with patch.dict(os.environ, {"DOLLY_UI_FONTS": ""}), \
                    patch.object(fonts.tkfont, "families", return_value=("Segoe UI", "Inter")):
                fonts.install(self.root, 1.0)   # no Inter Medium file: values in Inter Regular
                self.assertEqual((ui_theme.FONT_FAMILY, ui_theme.FONT_VALUE), ("Inter", "Inter"))
            with patch.dict(os.environ, {"DOLLY_UI_FONTS": ""}), \
                    patch.object(fonts.tkfont, "families", return_value=("Segoe UI", "Segoe UI Semibold")):
                fonts.install(self.root, 1.0)   # the Inter files missing: the Segoe UI fallback
                self.assertEqual((ui_theme.FONT_FAMILY, ui_theme.FONT_VALUE), ("Segoe UI", "Segoe UI"))
                self.assertEqual(fonts.family_available(self.root, "body"), "Segoe UI")
                self.assertEqual(standard(), before)

    def test_hug_widths_count_the_shipped_inter_like_figma(self):
        # Figma's auto-width text box is Inter's own advance widths rounded up: Kara's Shot Playback
        # buttons 78 / 61 / 104 hug "Play shot" 52, "Pause" 35 and "Stop / restore" 78 (GDI: 77).
        if not (fonts.bundled_dir() / "Inter-Regular.ttf").is_file():
            self.skipTest("the Inter files are not in this checkout")
        listed = ("Segoe UI", "Segoe UI Semibold", "Inter", "Inter Medium", "Inter SemiBold")
        with patch.object(ui_theme, "FONT_FAMILY", ui_theme.FONT_FAMILY), \
                patch.object(ui_theme, "FONT_VALUE", ui_theme.FONT_VALUE), \
                patch.object(fonts, "load_bundled", return_value=[]):
            with patch.dict(os.environ, {"DOLLY_UI_FONTS": ""}), \
                    patch.object(fonts.tkfont, "families", return_value=listed):
                fonts.install(self.root, 1.0)
                other = self.new_root()
                fonts.install(other, 1.5)
                texts = ("Play shot", "Pause", "Stop / restore", "Open output folder", "Playback speed", "")
                self.assertEqual([fonts.hug_width(self.root, "body", text) for text in texts],
                                 [52, 35, 78, 108, 90, 0], "Figma [design reference] and the footer links")
                self.assertEqual(fonts.hug_width(other, "body", "Stop / restore"), scale.px(1.5, 78))
                with patch.object(fonts, "_face_file", return_value=fonts.bundled_dir() / "missing.ttf"):
                    fonts._ensure(self.root).hugs.clear()   # an unreadable file: measured as drawn
                    self.assertEqual(fonts.hug_width(self.root, "body", "Pause"),
                                     fonts.font(self.root, "body").measure("Pause"))
                fonts._ensure(self.root).hugs.clear()
            with patch.dict(os.environ, {"DOLLY_UI_FONTS": "fallback"}), \
                    patch.object(fonts.tkfont, "families", return_value=listed):
                fonts.install(self.root, 1.0)   # the Segoe UI fallback hugs its drawn label, as before
                self.assertEqual(fonts.hug_width(self.root, "body", "Stop / restore"),
                                 fonts.font(self.root, "body").measure("Stop / restore"))
        with self.assertRaises(ValueError):
            fonts._read_advances(truetype_blob())   # a table directory without cmap or hmtx
        self.assertIsNone(fonts._advances(None))


def rgb_png(width, height, pixels, filters=None):
    rows = b"".join(bytes((0 if filters is None else filters[row],)) + pixels[row * width * 3:(row + 1) * width * 3]
                    for row in range(height))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def write_art(folder):
    """A tiny but complete assets/ui set matching the manifest schema."""
    folder = Path(folder)
    files = {}

    def add(name, data, size):
        (folder / name).write_bytes(data)
        files[name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "px": list(size)}
    level = bytes(range(18))
    for index in range(2):
        add(f"backdrop_l{index}.png", rgb_png(3, 2, bytes((value + index) % 256 for value in level)), (3, 2))
    for key, size in (("1", 2), ("2", 4)):
        add(f"sidebar_art@{key}x.png", art.png_rgba(size, size, b"\x10\x20\x30\x40" * size * size), (size, size))
        add(f"nav_icons@{key}x.png", art.png_rgba(size * 2, size, b"\xff\xff\xff\xff" * size * size * 2),
            (size * 2, size))
    manifest = {"version": 1, "generator": "tests",
                "levels": {"files": ["backdrop_l0.png", "backdrop_l1.png"], "size": [3, 2], "scale": 1,
                           "opacity": [1.0, 0.5], "cycle": [0, 1]},
                "shapes": {"poses": [], "shard_fill": "backdrop_shard", "rim_fill": "backdrop_rim",
                           "rim_stipple": "gray12"},
                "sidebar_art": {"rect": [0, 0, 2, 2], "files": {"1": "sidebar_art@1x.png", "2": "sidebar_art@2x.png"}},
                "nav_icons": {"order": ["replay"], "cell": 2, "rows": ["muted", "text"],
                              "files": {"1": "nav_icons@1x.png", "2": "nav_icons@2x.png"}},
                "files": files}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


class ArtTests(RootCase):
    def test_assets_directory_is_the_packaged_ui_folder(self):
        self.assertEqual(art.assets_dir().parts[-2:], ("assets", "ui"))

    def setUp(self):
        super().setUp()
        warning = patch.object(art.LOG, "warning")
        self.warning = warning.start()
        self.addCleanup(warning.stop)

    def test_load_verifies_the_manifest_and_every_file(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(art.load(directory=folder))
            self.assertEqual(self.warning.call_count, 1)
            self.assertIsNone(art.load(directory=folder))
            self.assertEqual(self.warning.call_count, 1, "The same problem is reported once")
            write_art(folder)
            assets = art.load(self.root, directory=folder)
            self.assertIsNotNone(assets)
            self.assertIs(art.load(self.root, directory=folder), assets, "One verification per window")
            self.assertEqual(assets.level_rgb(1)[:2], (3, 2))
            self.assertEqual(assets.photo(self.root, "sidebar_art@1x.png").width(), 2)
            self.assertIs(assets.photo(self.root, "sidebar_art@1x.png"), assets.photo(self.root, "sidebar_art@1x.png"))
            self.assertEqual(assets.bucket_photo(self.root, "sidebar_art", 1.0).width(), 2)
            self.assertEqual(assets.bucket_photo(self.root, "sidebar_art", 1.5).width(), 2, "No 1.5x file: 1x")
            self.assertEqual(assets.bucket_photo(self.root, "nav_icons", 2.0).width(), 8)
            with self.assertRaises(KeyError):
                assets.path("other.png")
            # Any altered byte disables the whole set.
            path = Path(folder) / "nav_icons@2x.png"
            data = bytearray(path.read_bytes()); data[-13] ^= 1
            path.write_bytes(bytes(data))
            self.assertIsNone(art.load(directory=folder))
            self.assertIn("SHA-256", self.warning.call_args[0][1])

    def test_load_rejects_unknown_versions_bad_json_and_unlisted_files(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = write_art(folder)
            path = Path(folder) / "manifest.json"
            for broken in ({**manifest, "version": 2},
                           {**manifest, "levels": {**manifest["levels"], "files": ["missing.png"]}},
                           {**manifest, "files": {**manifest["files"], "../escape.png": {"sha256": "", "bytes": 0}}},
                           {**manifest, "levels": {**manifest["levels"], "cycle": [0, 5]}}):
                with self.subTest(manifest=sorted(broken)):
                    path.write_text(json.dumps(broken), encoding="utf-8")
                    self.assertIsNone(art.load(directory=folder))
            path.write_text("{not json", encoding="utf-8")
            self.assertIsNone(art.load(directory=folder))

    def test_filter0_reader_returns_exact_rows_and_rejects_other_filters(self):
        pixels = bytes(range(3 * 2 * 3))
        with tempfile.TemporaryDirectory() as folder:
            plain, filtered = Path(folder) / "plain.png", Path(folder) / "filtered.png"
            plain.write_bytes(rgb_png(3, 2, pixels))
            filtered.write_bytes(rgb_png(3, 2, pixels, filters=(0, 1)))
            self.assertEqual(art.read_png_rgb_filter0(plain), (3, 2, pixels))
            with self.assertRaises(ValueError):
                art.read_png_rgb_filter0(filtered)
            rgba = Path(folder) / "rgba.png"
            rgba.write_bytes(art.png_rgba(1, 1, b"\0\0\0\0"))
            with self.assertRaises(ValueError):
                art.read_png_rgb_filter0(rgba)
            damaged = bytearray(rgb_png(3, 2, pixels)); damaged[40] ^= 0xff
            plain.write_bytes(bytes(damaged))
            with self.assertRaises(ValueError):
                art.read_png_rgb_filter0(plain)

    def test_stretch_maps_rows_and_columns(self):
        # 3 x 2 source: pixel value = (x, y, x + 3y).
        src = bytes(value for y in range(2) for x in range(3) for value in (x, y, x + 3 * y))
        doubled = art.stretch_rgb(src, 3, 2, 6, 4)
        pixel = lambda data, width, x, y: tuple(data[(y * width + x) * 3:(y * width + x) * 3 + 3])
        self.assertEqual(pixel(doubled, 6, 0, 0), (0, 0, 0))
        self.assertEqual(pixel(doubled, 6, 1, 1), (0, 0, 0))
        self.assertEqual(pixel(doubled, 6, 5, 3), (2, 1, 5))
        self.assertEqual(pixel(doubled, 6, 2, 2), (1, 1, 4))
        mirrored = art.stretch_rgb(src, 3, 2, 3, 3, map_x=[2, 1, 0], map_y=lambda y: y - 1)
        self.assertEqual([pixel(mirrored, 3, x, 0) for x in range(3)], [(2, 0, 2), (1, 0, 1), (0, 0, 0)])
        self.assertEqual(pixel(mirrored, 3, 0, 2), (2, 1, 5), "Indices are clamped")
        with self.assertRaises(ValueError):
            art.stretch_rgb(src, 3, 2, 2, 2, map_x=[0])

    def test_generated_images_match_their_colours(self):
        t = ui_theme.TOKENS
        gradient = art.column_gradient(self.root, 7, 20, "stage_top", t["bg"], 10)
        self.assertEqual((gradient.width(), gradient.height()), (7, 20))
        self.assertEqual(gradient.get(0, 0), ui_theme.rgb(t["stage_top"]))
        self.assertEqual(gradient.get(6, 0), ui_theme.rgb(t["stage_top"]), "The column tiles across")
        self.assertEqual(gradient.get(3, 15), ui_theme.rgb(t["bg"]))
        middle = gradient.get(3, 5)
        for value, top, bottom in zip(middle, ui_theme.rgb(t["stage_top"]), ui_theme.rgb(t["bg"])):
            self.assertLessEqual(min(top, bottom), value)
            self.assertLessEqual(value, max(top, bottom))
        strip = art.h_gradient_strip(self.root, 10, 3, [(0, "rail_hover", .6), (.5, "nav_idle_to", 0),
                                                         (1, "nav_idle_to", 0)])
        self.assertEqual((strip.width(), strip.height()), (10, 3))
        self.assertFalse(strip.transparency_get(0, 0))
        self.assertTrue(strip.transparency_get(9, 2))
        corner = art.corner_rgba(self.root, 6, "panel", "card_edge", "nw")
        self.assertTrue(corner.transparency_get(0, 0))
        self.assertFalse(corner.transparency_get(5, 5))
        self.assertEqual(corner.get(5, 5), ui_theme.rgb(t["panel"]))
        self.assertTrue(art.corner_rgba(self.root, 6, "panel", "card_edge", "se").transparency_get(5, 5))
        self.assertFalse(art.corner_rgba(self.root, 6, "panel", None, "se").transparency_get(0, 0))
        dot = art.dot_rgba(self.root, 9, "status_busy")
        self.assertEqual(dot.get(4, 4), ui_theme.rgb(t["status_busy"]))
        self.assertTrue(dot.transparency_get(0, 0))
        photo = art.photo_rgba(self.root, 2, 1, b"\x01\x02\x03\xff\x00\x00\x00\x00")
        self.assertEqual(photo.get(0, 0), (1, 2, 3))
        self.assertTrue(photo.transparency_get(1, 0))
        decoded = base64.b64encode(art.png_rgba(1, 1, b"\0\0\0\0"))
        self.assertTrue(decoded)

    def test_caches_are_per_root_and_survive_another_root(self):
        other = self.new_root()
        mine = art.corner_rgba(self.root, 6, "panel", "card_edge", "ne")
        theirs = art.corner_rgba(other, 6, "panel", "card_edge", "ne")
        self.assertIsNot(mine, theirs)
        self.assertIs(art.corner_rgba(self.root, 6, "panel", "card_edge", "ne"), mine)
        self.assertIs(art.dot_rgba(other, 5, "status_ready"), art.dot_rgba(other, 5, "status_ready"))
        self.root.destroy()
        again = art.corner_rgba(other, 6, "panel", "card_edge", "ne")
        self.assertIs(again, theirs)
        self.assertIn(str(again), other.tk.splitlist(other.tk.call("image", "names")))
        canvas = tk.Canvas(other)
        canvas.create_image(0, 0, image=again, anchor="nw")
        canvas.create_image(0, 0, image=art.dot_rgba(other, 5, "status_error"), anchor="nw")


if __name__ == "__main__":
    unittest.main()
