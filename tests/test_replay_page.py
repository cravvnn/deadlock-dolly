"""Replay page (Figma [design reference]): cards on a stage canvas, every library action still reachable."""
from dataclasses import fields
import os
import sys
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dolly import gui_theme, ui_theme
from dolly.ui import art, chrome, kit_controls
from dolly.ui.action_proxy import ActionProxy, ProgressProxy
from dolly.ui.kit_controls import KitButton
from dolly.ui.library_page import (
    CAPTURE_INFO, LAUNCH_INFO, LIBRARY_INFO, SELECTED_INFO, SUBTITLE, TABLE_MIN_ROWS, TABLE_ROWS, TITLE,
    LibraryActions, LibraryPage, LibraryState,
)
from dolly.ui.scale import hairline, px, ui_scale
from dolly.ui.stage_page import END_GAP, FIT_ROUNDING, InfoIcon, StagePage, end_gap, retain_surface
from dolly.ui.tween import Animator, ease_out


def shell_page_area(s, width, height):
    """The page area the shell gives a ``width`` x ``height`` (design px) window at scale ``s``.

    As ``chrome.Stage`` lays it out (rows by window height, side padding by width, the 18 px
    rail gutter): (width, height, mode) in px.
    """
    row = 0 if px(s, height) >= px(s, chrome.DESIGN_HEIGHT) else 1
    pad = px(s, chrome.PAD[0] if px(s, width) >= px(s, chrome.WIDE_WIDTH) else chrome.PAD[1])
    top = px(s, chrome.PAGE_TOP[row])
    bottom = max(top + 1, px(s, height) - px(s, chrome.PAGE_BOTTOM[row]))
    return px(s, width) - 2 * pad + px(s, 18), bottom - top, ('design', 'compact')[row]


class FakeAnimator:
    """Stands in for the page scroller's frame loop: the test runs frames at the times it picks."""

    frame_ms = 16

    def __init__(self):
        self.now = 0.0
        self.steps = {}

    def clock(self):
        return self.now

    def start(self, key, step):
        self.steps[key] = step

    def stop(self, key):
        self.steps.pop(key, None)

    def frame(self, at):
        self.now = at
        for key, step in list(self.steps.items()):
            if not step(at):
                self.steps.pop(key, None)


def walk(widget):
    for child in widget.winfo_children():
        yield child
        yield from walk(child)


class FakeBackdrop:
    """Records what the page asks of the window's backdrop."""

    def __init__(self):
        self.attached = []
        self.synced = []

    def attach(self, canvas, areas=None):
        self.attached.append((canvas, areas))

    def sync(self, canvas=None, *, redraw=False):
        self.synced.append((canvas, redraw))

    def detach(self, canvas):
        pass


class ReplayPageCase(unittest.TestCase):
    """A standalone Replay page on its own root (helpers only)."""

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.addCleanup(self.root.destroy)
        self.root.withdraw()
        self.calls = []
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)

    def tearDown(self):
        if hasattr(self, 'errors'):
            self.assertEqual(self.errors, [], 'Tk callback raised an exception')

    def build(self, *, shown=None, parent=None):
        """A standalone page with recorded callbacks; ``shown`` = window size to map it at."""
        if shown:
            gui_theme.initialize(self.root)
            self.root.deiconify()
            self.root.geometry(shown)
        def record(name):
            return lambda *args, **kwargs: self.calls.append(name)
        def tree(parent, columns, labels, widths, height):
            # Shaped like DollyApp._tree: a card frame holding the table and its rail.
            frame = ttk.Frame(parent, style='Card.TFrame')
            frame.columnconfigure(0, weight=1)
            widget = ttk.Treeview(frame, columns=columns, show='headings', selectmode='browse', height=height)
            for column, label, width in zip(columns, labels, widths):
                widget.heading(column, text=label)
                widget.column(column, width=width)
            widget.grid(row=0, column=0, sticky='nsew')
            bar = ttk.Scrollbar(frame, orient='vertical', command=widget.yview)
            bar.grid(row=0, column=1, sticky='ns')
            widget.configure(yscrollcommand=bar.set)
            return frame, widget
        variables = {f.name: tk.StringVar(self.root, '') for f in fields(LibraryState)}
        variables['hotkey_enabled'] = tk.BooleanVar(self.root, False)
        variables['hotkey_label'].set('In-game capture · F5')
        self.state = LibraryState(**variables)
        commands = LibraryActions(**{f.name: record(f.name) for f in fields(LibraryActions) if f.name != 'tree'},
                                  tree=tree)
        view = LibraryPage(parent or self.root, self.state, commands)
        if shown:
            self.root.update()
        else:
            self.root.update_idletasks()
        return view

    def button(self, parent, text):
        # Browse and Refresh are kit buttons (PLAN-5 N21): one-button canvases, real widgets.
        return next(w for w in walk(parent) if isinstance(w, (ttk.Button, KitButton)) and w.cget('text') == text)

    def in_view(self, page, widget):
        canvas = page.canvas
        top, bottom = widget.winfo_rooty(), widget.winfo_rooty() + widget.winfo_height()
        return (widget.winfo_ismapped() and top >= canvas.winfo_rooty()
                and bottom <= canvas.winfo_rooty() + canvas.winfo_height())

    def scaled_root(self, scale):
        """Work on a fresh root at ``scale`` from here on.

        Tk keeps one ``tk scaling`` for the whole process on Windows, and a root made
        at another scaling keeps a few metrics from it: set it first, put it back after.
        """
        first = self.root
        scaling = first.tk.call('tk', 'scaling')
        self.addCleanup(first.tk.call, 'tk', 'scaling', scaling)
        first.tk.call('tk', 'scaling', scale * 96 / 72)
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        self.root.tk.call('tk', 'scaling', scale * 96 / 72)
        self.root.withdraw()
        self.root.report_callback_exception = lambda *args: self.errors.append(args)

    def at_window(self, window, scale=None):
        """The page, shown in a frame the size of the shell's page area for a ``window`` (design px)."""
        if scale is not None:
            self.scaled_root(scale)
        gui_theme.initialize(self.root)
        width, height, mode = shell_page_area(ui_scale(self.root), *window)
        area = tk.Frame(self.root, width=width, height=height)
        area.mode = mode               # what the shell's Stage tells its pages (StagePage._compact)
        area.pack_propagate(False)
        area.pack(anchor='nw')
        self.root.wm_maxsize(6000, 6000)
        self.root.deiconify()
        self.root.geometry(f'{width}x{height}')
        view = self.build(parent=area)
        self.root.update()
        return view, area

    def resize(self, area, window):
        """Give the page the shell's page area for another ``window`` (design px)."""
        width, height, area.mode = shell_page_area(ui_scale(self.root), *window)
        area.configure(width=width, height=height)
        self.root.geometry(f'{width}x{height}')
        self.root.update()
        self.root.update()

    def fill(self, tree, count):
        for index in range(count):
            tree.insert('', 'end', iid=str(index), values=(f'{index}.dem', '1.0 MB', '2026-10-12, 15:38'))
        self.root.update()


class ReplayPageTests(ReplayPageCase):
    def test_cards_follow_the_design_titles_and_copy(self):
        view = self.build()
        self.assertEqual(TITLE, 'Replay & Shots')
        self.assertEqual(SUBTITLE, 'Choose a replay, open it in Dolly, then frame your cameras inside Deadlock.')
        self.assertIsInstance(view.library_page, StagePage)
        for card, title, info in ((view.launch_card, 'Launch Setup', LAUNCH_INFO),
                                  (view.selected_card, 'Selected Replay', SELECTED_INFO),
                                  (view.library_card, 'Replay Library', LIBRARY_INFO)):
            self.assertEqual(card.title_label.cget('text'), title)
            self.assertEqual(str(card.title_label.cget('style')), 'Stage.CardTitle.TLabel')
            self.assertIsInstance(card.info, InfoIcon)
            self.assertEqual(card.info.text, info)
        icons = [w for w in walk(view.selected_card.body) if isinstance(w, InfoIcon)]
        self.assertEqual([icon.text for icon in icons], [CAPTURE_INFO])
        self.assertEqual(view.selected_replay_text.get(), 'Choose a replay')
        self.state.demo_path.set('replays/121021448.dem')
        self.assertEqual(view.selected_replay_text.get(), '121021448.dem')
        self.assertEqual(tuple(view.home_camera_driver_combo.cget('values')),
                         ('Native (experimental)', 'Console (legacy)'))
        self.assertEqual(str(view.home_camera_driver_combo.cget('state')), 'readonly')
        self.assertEqual(str(view.library_capture_toggle.cget('textvariable')), str(self.state.hotkey_label))
        tree = view.replay_tree
        self.assertEqual([tree.heading(c, 'text') for c in ('name', 'size', 'modified')], ['Replay', 'Size', 'Modified'])
        self.assertEqual(str(tree.cget('style')), 'Library.Treeview')
        self.assertEqual([bool(int(tree.column(c, 'stretch'))) for c in ('name', 'size', 'modified')],
                         [True, False, False])

    def test_every_library_action_is_reachable_from_a_control(self):
        view = self.build()
        self.assertIsInstance(view.play_replay_button, ActionProxy)
        self.assertEqual(view.play_replay_button.cget('text'), 'Open replay in Dolly')
        self.assertEqual(view.play_replay_button.cget('label'), 'START')
        self.assertEqual(view.cancel_startup_button.cget('label'), 'CANCEL')
        self.assertIsInstance(view.startup_bar, ProgressProxy)
        self.button(view.launch_card.body, 'Browse').invoke()
        self.button(view.library_card.body, 'Browse').invoke()
        self.button(view.library_card.body, 'Refresh').invoke()
        self.state.replay_search.set('1210')
        tree = view.replay_tree
        tree.insert('', 'end', iid='0', values=('121021448.dem', '225.7 MB', '2026-10-12, 15:38'))
        tree.event_generate('<<TreeviewSelect>>')
        for _ in range(2):   # Tk folds two quick presses into <Double-1>
            tree.event_generate('<ButtonPress-1>', x=1, y=1)
            tree.event_generate('<ButtonRelease-1>', x=1, y=1)
        view.library_capture_toggle.invoke()
        view.play_replay_button.invoke()
        # The CANCEL sticker drives this proxy; disabled, it does nothing.
        view.cancel_startup_button.invoke()
        view.cancel_startup_button.configure(state='normal')
        view.cancel_startup_button.invoke()
        self.assertEqual(self.calls, [
            'browse_game', 'browse_demo', 'refresh_replays', 'filter_replays', 'select_replay',
            'use_selected_replay', 'toggle_capture_hotkey', 'start_editing_session', 'cancel_startup'])
        self.assertTrue(self.state.hotkey_enabled.get())

    def test_page_draws_only_the_three_design_cards(self):
        view = self.build()
        page = view.library_page
        self.assertFalse(hasattr(view, 'more'))
        self.assertEqual(page.content_height, view.library_card.bottom)
        texts = {w.cget('text') for w in walk(page) if isinstance(w, (ttk.Button, KitButton))}
        self.assertFalse(texts & {'New shot', 'Save as...', 'Rename shot...', 'Stop / restore', 'Cancel startup'})
        # The card buttons are canvas-drawn kit buttons, as on the other pages (PLAN-5 N21).
        self.assertEqual([w for w in walk(page) if isinstance(w, ttk.Button)], [])
        self.assertEqual([b.cget('text') for b in kit_controls.buttons(page)], ['Browse', 'Repair...', 'Browse', 'Refresh'])

    def test_cards_stack_on_the_design_column(self):
        view = self.build(shown='1000x900')
        page = view.library_page
        s = page.scale
        cards = (view.launch_card, view.selected_card, view.library_card)
        self.assertEqual(page.card_width, px(s, 613))
        self.assertEqual(cards[0].top, 0)
        for upper, lower in zip(cards, cards[1:]):
            self.assertEqual(lower.top - upper.bottom, px(s, 15))
        canvas = page.canvas
        for card in cards:
            # The content sits inside the plate's 20 + 1 px inset, in canvas px.
            x = card.frame.winfo_rootx() - canvas.winfo_rootx() + canvas.canvasx(0)
            y = card.frame.winfo_rooty() - canvas.winfo_rooty() + canvas.canvasy(0)
            self.assertEqual((x, y), (page.inset, card.top + page.inset))
            self.assertEqual(card.frame.winfo_width(), page.card_width - 2 * page.inset)
        self.assertEqual(page.canvas.yview(), (0.0, 1.0))
        self.assertFalse(page.scrollbar.winfo_ismapped())

    def test_cards_keep_the_design_heights_and_fit_the_design_window(self):
        # Figma [design reference]: Launch Setup 145, Selected Replay 138, Replay Library 249 tall; the
        # page fits the 591 px page of the 1200 x 810 design window.
        view = self.build(shown='1000x900')
        page = view.library_page
        s = page.scale
        for card, design in ((view.launch_card, 145), (view.selected_card, 138), (view.library_card, 249)):
            self.assertLessEqual(abs((card.bottom - card.top) - px(s, design)), 2, card.title_label.cget('text'))
        self.assertLessEqual(page.content_height, px(s, 810 - 107 - 112) + 2)
        # Kit controls: 30 px fields and buttons.
        browse, refresh = self.button(view.launch_card.body, 'Browse'), self.button(view.library_card.body, 'Refresh')
        for widget in (view.game_entry, view.home_camera_driver_combo, view.search_entry, browse, refresh):
            self.assertEqual(widget.winfo_height(), px(s, 30), widget)
        # The buttons keep the 70 px of the 8-character buttons they replaced (Tk rounds
        # those by a pixel at some scales) and end both rows on the content edge.
        for button in (browse, self.button(view.library_card.body, 'Browse'), refresh):
            self.assertLessEqual(abs(button.winfo_width() - px(s, 70)), 1, button.cget('text'))
        self.assertEqual(browse.winfo_rootx() + browse.winfo_width(), refresh.winfo_rootx() + refresh.winfo_width())

    def test_table_text_lines_up_with_the_card_content_edge(self):
        from dolly.ui.stage_page import TREE_CELL_INSET
        view = self.build(shown='1000x900')
        table = view.replay_tree.master
        holder = table.master
        # Tk indents cell text by a fixed 4 px; the table starts that far left of the
        # card content (clipped there) and the headings use the same indent.
        self.assertEqual(table.winfo_x(), -TREE_CELL_INSET)
        self.assertEqual(table.winfo_width(), holder.winfo_width() + TREE_CELL_INSET)
        self.assertEqual(holder.winfo_height(), table.winfo_reqheight())
        padding = self.root.tk.splitlist(ttk.Style(self.root).lookup('Library.Treeview.Heading', 'padding'))
        self.assertEqual(int(padding[0]), TREE_CELL_INSET)

    def test_a_long_path_shows_elided_until_the_field_is_edited(self):
        view = self.build(shown='1000x900')
        entry, cover = view.game_entry, view.game_path_cover
        path = 'C:\\Program Files (x86)\\Steam\\steamapps\\common\\Deadlock\\game\\bin\\win64\\deadlock.exe'
        self.state.game_path.set(path)
        self.root.update()
        self.assertEqual(cover.winfo_manager(), 'place')
        shown = cover.cget('text')
        self.assertTrue(shown.endswith('\u2026') and path.startswith(shown[:-1]), shown)
        self.assertLessEqual(cover.winfo_width(), entry.winfo_width())
        entry.event_generate('<FocusIn>')
        self.assertEqual(cover.winfo_manager(), '')
        entry.event_generate('<FocusOut>')
        self.assertEqual(cover.winfo_manager(), 'place')
        self.state.game_path.set('C:\\deadlock.exe')   # fits: the entry draws it itself
        self.root.update()
        self.assertEqual(cover.winfo_manager(), '')
        self.assertEqual(self.state.game_path.get(), 'C:\\deadlock.exe')

    def test_reveal_scrolls_cards_into_view_and_opens_the_disclosure(self):
        view = self.build(shown='720x240')
        page = view.library_page
        self.assertNotEqual(page.canvas.yview(), (0.0, 1.0))
        self.assertTrue(page.scrollbar.winfo_ismapped())
        page.canvas.yview_moveto(1)
        self.root.update()
        page.reveal(view.library_capture_toggle)
        self.root.update()
        self.assertTrue(self.in_view(page, view.library_capture_toggle))
        refresh = self.button(view.library_card.body, 'Refresh')
        page.reveal(refresh)
        self.root.update()
        self.assertTrue(self.in_view(page, refresh))

    def test_reveal_sizes_controls_in_a_withdrawn_window(self):
        # The hidden bundle self-test reveals the capture switch in a withdrawn
        # window, where the shell leaves the page a sliver of a canvas.
        holder = ttk.Frame(self.root, width=1, height=26)
        holder.pack_propagate(False)
        holder.pack(anchor='nw')
        view = self.build(parent=holder)
        page = view.library_page
        page.reveal(view.library_capture_toggle)
        self.assertGreater(view.library_capture_toggle.winfo_width(), 1)
        self.assertGreater(view.library_capture_toggle.winfo_height(), 1)

    def test_tab_reaches_page_controls_and_wraps_after_the_table(self):
        view = self.build(shown='720x240')
        def takes_focus(widget):
            return str(self.root.tk.call(str(widget.cget('takefocus')), str(widget))) == '1'
        self.assertIs(view.replay_tree.tk_focusNext(), view.launch_card.info)
        view.home_camera_driver_combo.configure(state='disabled')
        self.assertFalse(takes_focus(view.home_camera_driver_combo))

    def test_search_placeholder_follows_focus_and_text_but_never_writes_the_filter(self):
        view = self.build()
        entry, placeholder = view.search_entry, view.search_placeholder
        writes = []
        self.state.replay_search.trace_add('write', lambda *_: writes.append(self.state.replay_search.get()))
        self.assertEqual(placeholder.winfo_manager(), 'place')
        self.assertEqual(placeholder.cget('text'), 'Search')
        entry.event_generate('<FocusIn>')
        self.assertEqual(placeholder.winfo_manager(), '')
        entry.event_generate('<FocusOut>')
        self.assertEqual(placeholder.winfo_manager(), 'place')
        self.state.replay_search.set('dem')
        self.assertEqual(placeholder.winfo_manager(), '')
        self.state.replay_search.set('')
        self.assertEqual(placeholder.winfo_manager(), 'place')
        # Clicking the drawn hint or magnifier only focuses the field.
        for drawn in placeholder.master.winfo_children():
            if drawn is not entry:
                drawn.event_generate('<Button-1>')
        self.assertEqual(writes, ['dem', ''])
        self.assertEqual(self.calls, ['filter_replays', 'filter_replays'])

    def test_info_bubble_waits_150_ms_on_hover_and_opens_at_once_on_focus(self):
        view = self.build(shown='1000x900')
        page, icon = view.library_page, view.selected_card.info
        scheduled = []
        icon.after = lambda ms, func: (scheduled.append((ms, func)), 'pending')[1]
        animator = Animator.of(self.root)
        icon.event_generate('<Enter>')
        self.assertEqual([ms for ms, _ in scheduled], [150])   # the colour ramp runs on the toplevel's loop
        self.assertEqual(page.canvas.find_withtag('bubble'), ())
        animator.finish()
        self.assertEqual(str(icon.cget('foreground')), ui_theme.TOKENS['accent'])
        scheduled[0][1]()
        self.root.update()
        bubbles = page.canvas.find_withtag('bubble')
        self.assertEqual(len(bubbles), 1)
        tip = page.canvas.nametowidget(page.canvas.itemcget(bubbles[0], 'window'))
        self.assertGreaterEqual(tip.winfo_rooty(), icon.winfo_rooty() + icon.winfo_height())
        self.assertEqual(tip.winfo_rootx(), icon.winfo_rootx() - px(page.scale, 6))
        icon.event_generate('<Leave>')
        self.assertEqual(page.canvas.find_withtag('bubble'), ())
        animator.finish()
        self.assertEqual(str(icon.cget('foreground')), ui_theme.TOKENS['dim'])
        icon.event_generate('<FocusIn>')
        self.assertEqual(len(page.canvas.find_withtag('bubble')), 1)
        # Keyboard focus opens the bubble at once; the icon draws no focus state (PLAN-5 N4).
        self.assertFalse(animator.active())
        self.assertEqual(str(icon.cget('foreground')), ui_theme.TOKENS['dim'])
        icon.event_generate('<FocusOut>')
        self.assertEqual(page.canvas.find_withtag('bubble'), ())
        self.assertFalse(icon.shown)

    def test_wheel_scrolls_only_while_the_cards_overflow(self):
        # They overflow only on a page too small for even one table row (UPDATE 17.5): 240 px
        # is far below the shell's smallest page (ReplayFitTests has the shell's sizes).
        view = self.build(shown='1000x900')
        page = view.library_page
        label = view.launch_card.title_label
        with patch('dolly.ui.boil.motion_enabled', return_value=False):   # motion off: one notch at once
            self.assertIsNone(page._wheel(SimpleNamespace(widget=label, delta=-120)))
            self.root.geometry('720x240')
            self.root.update()
            self.assertEqual(page._wheel(SimpleNamespace(widget=label, delta=-120)), 'break')
            self.assertEqual(page.canvas.canvasy(0), 60)
            before = page.canvas.yview()
            for owner in (view.replay_tree, view.home_camera_driver_combo):
                self.assertIsNone(page._wheel(SimpleNamespace(widget=owner, delta=-120)))
            self.assertEqual(page.canvas.yview(), before)
            # The rail is drawn on the page canvas: the wheel over it scrolls the page.
            self.assertEqual(page._wheel(SimpleNamespace(widget=page.canvas, delta=-120)), 'break')
            self.assertEqual(page.canvas.canvasy(0), 120)
            page.scroll_page_wheel(SimpleNamespace(delta=240, num=0))
            self.assertEqual(page.canvas.canvasy(0), 0)
            self.assertFalse(page.scroller.active)

    def test_scrolling_the_table_redraws_only_the_table(self):
        # A scroll of the replay list stays inside the table: no page layout, no
        # backdrop work and no scrollbar show/hide while its rows still overflow.
        backdrop = self.root._dolly_backdrop = FakeBackdrop()
        view = self.build(shown='1000x900')
        page, tree = view.library_page, view.replay_tree
        for index in range(12):
            tree.insert('', 'end', iid=str(index), values=(f'{index}.dem', '1.0 MB', '2026-10-12, 15:38'))
        self.root.update()
        bar = next(w for w in tree.master.winfo_children() if isinstance(w, ttk.Scrollbar))
        self.assertTrue(bar.winfo_ismapped())
        layouts, managed = [], []
        page._update.callback = lambda: layouts.append(True)
        for name in ('grid', 'grid_remove'):
            setattr(bar, name, lambda *_args, name=name, **_options: managed.append(name))
        backdrop.synced.clear()
        for _ in range(3):
            tree.yview_scroll(1, 'units')
            self.root.update()
        self.assertGreater(tree.yview()[0], 0)
        self.assertEqual((layouts, backdrop.synced, managed), ([], [], []))
        self.assertTrue(bar.winfo_ismapped())

    def test_backdrop_attaches_and_shows_only_outside_the_cards(self):
        backdrop = self.root._dolly_backdrop = FakeBackdrop()
        view = self.build(shown='1000x900')
        page = view.library_page
        # The scrolling canvas spans the card column and its rail; the backdrop
        # right of it is a canvas that never scrolls (shown everywhere).
        self.assertEqual([canvas for canvas, _ in backdrop.attached], [page.canvas, page.side, page.foot])
        self.assertIsNone(backdrop.attached[1][1])
        self.assertIsNone(backdrop.attached[2][1])
        self.assertFalse(page._foot_packed)      # the cards fit: no gap strip, the page as before (UPDATE 9)
        reserve = page.scrollbar.winfo_reqwidth() + px(page.scale, 8)
        self.assertEqual(page.canvas.winfo_width(), page.card_width + reserve)
        self.assertEqual(page.side.winfo_width(), page.winfo_width() - page.canvas.winfo_width())
        self.assertGreater(page.side.winfo_width(), 0)
        self.assertIn((page.canvas, True), backdrop.synced)
        areas = backdrop.attached[0][1]()
        self.assertTrue(areas)
        cards = (view.launch_card, view.selected_card, view.library_card)
        for x0, y0, x1, y1 in areas:
            for card in cards:
                overlaps = x0 < page.card_width and x1 > 0 and y0 < card.bottom and y1 > card.top
                self.assertFalse(overlaps, (x0, y0, x1, y1, card.top, card.bottom))
        gap = (0, cards[0].bottom, page.card_width, cards[1].top)
        self.assertIn(gap, areas)
        self.assertIn((page.card_width, 0, page.canvas.winfo_width(), page.canvas.winfo_height()), areas)


class ReplayScrollTests(ReplayPageCase):
    """What one frame draws (one sheet window, two plate images; PERF-NOTES) and, on a page too small
    for even one table row (720 x 240, far below the shell's smallest; Replay never scrolls in the
    shell's sizes, UPDATE 17.5 and ReplayFitTests), the scrolling that is left: eased wheel
    (UPDATE 4 (c)), immediate everything else, a canvas-drawn rail."""

    def eased_page(self):
        view = self.build(shown='720x240')
        page = view.library_page
        self.assertNotEqual(page.canvas.yview(), (0.0, 1.0))
        animator = FakeAnimator()
        page.scroller._animator = animator
        page.scroller._clock = animator.clock
        page.scroller.measure = False
        return view, page, animator

    def notch(self, view, page, delta=-120):
        return page._wheel(SimpleNamespace(widget=view.launch_card.title_label, delta=delta))

    def test_the_wheel_eases_by_the_clock_in_one_scroll_per_frame(self):
        view, page, animator = self.eased_page()
        distance = 60 * page.scale
        layouts = []
        page._update.callback = lambda: layouts.append(True)
        with patch('dolly.ui.tween.smooth', return_value=True):
            self.assertEqual(self.notch(view, page), 'break')
            self.assertEqual(page.canvas.canvasy(0), 0)       # the first frame draws, not the wheel event
            self.assertTrue(page.scroller.active)
            self.notch(view, page, delta=-30)                 # a quarter notch more, before any frame
            animator.frame(0.070)
            self.assertEqual(page.canvas.canvasy(0), round(1.25 * distance * ease_out(0.5)))
            animator.frame(0.140)
            self.assertEqual(page.canvas.canvasy(0), round(1.25 * distance))
            self.assertFalse(page.scroller.active)
            self.assertEqual(animator.steps, {})
            self.root.update()
        self.assertEqual(layouts, [])                         # frames never lay the page out
        # The rail follows every frame: its thumb sits where ttk would put it.
        first, last = page.canvas.yview()
        height = page.canvas.winfo_height() - page.scrollbar.minimum
        self.assertEqual(page.scrollbar.thumb()[0], int(height * first))

    def test_the_rail_reveal_and_motion_off_scroll_at_once(self):
        view, page, animator = self.eased_page()
        with patch('dolly.ui.tween.smooth', return_value=True):
            self.notch(view, page)
            animator.frame(0.035)
            self.assertTrue(page.scroller.active)
            page.scrollbar.command('moveto', 0)               # what a rail drag does
            self.assertFalse(page.scroller.active)
            self.assertEqual(page.canvas.canvasy(0), 0)
            animator.frame(0.300)
            self.assertEqual(page.canvas.canvasy(0), 0)       # the rest of the notch was dropped
            self.notch(view, page)
            animator.frame(0.335)
            refresh = self.button(view.library_card.body, 'Refresh')
            page.reveal(refresh)                              # focus reveal: one immediate jump
            self.assertFalse(page.scroller.active)
            self.root.update()
            self.assertTrue(self.in_view(page, refresh))
        page.canvas.yview_moveto(0)
        with patch('dolly.ui.tween.smooth', return_value=False):   # motion off
            self.notch(view, page)
            self.assertEqual(page.canvas.canvasy(0), 60 * page.scale)
            self.assertFalse(page.scroller.active)
            self.assertEqual(animator.steps, {})

    def test_the_rail_is_drawn_on_the_canvas_and_handles_like_ttk(self):
        view = self.build(shown='720x240')
        page, canvas, rail = view.library_page, view.library_page.canvas, view.library_page.scrollbar
        t = ui_theme.TOKENS
        self.assertTrue(rail.winfo_ismapped())
        self.assertFalse(any(isinstance(child, ttk.Scrollbar) for child in canvas.winfo_children()))
        self.assertEqual(len(canvas.find_withtag('page_rail')), 4)
        self.assertEqual(rail.winfo_reqwidth(), round(10 * page.scale))
        height = canvas.winfo_height()
        top, length = rail.thumb()
        first, last = canvas.yview()
        span = height - rail.minimum
        self.assertEqual((top, length), (int(span * first), int(span * last) + rail.minimum - int(span * first)))
        self.assertEqual(canvas.itemcget(rail._trough, 'fill'), t['bg'])
        self.assertEqual(canvas.itemcget(rail._middle, 'fill'), t['edge'])
        # The faces ramp (PLAN-5 2.11); each one, once reached, is exactly the ttk thumb's.
        animator = Animator.of(self.root)

        def face(name):
            animator.finish()
            (cap_top, cap_bottom), colour = rail._faces[name]
            self.assertEqual(canvas.itemcget(rail._middle, 'fill'), colour)
            self.assertEqual((canvas.itemcget(rail._ends[0], 'image'), canvas.itemcget(rail._ends[1], 'image')),
                             (str(cap_top), str(cap_bottom)))
            return colour
        x = rail._place[0] + 2
        canvas.event_generate('<Motion>', x=x, y=top + 5)
        self.assertEqual(face('hover'), t['scroll_hover'])
        canvas.event_generate('<ButtonPress-1>', x=x, y=top + 5, button=1)
        self.assertEqual(face('pressed'), t['accent'])
        canvas.event_generate('<B1-Motion>', x=x, y=top + 25, state=0x100)
        self.assertAlmostEqual(canvas.yview()[0], first + 20 / span, places=2)
        canvas.event_generate('<ButtonRelease-1>', x=x, y=top + 25, button=1, state=0x100)
        self.assertEqual(face('hover'), t['scroll_hover'])
        canvas.yview_moveto(0)
        page._follow_view()
        before = canvas.canvasy(0)
        canvas.event_generate('<ButtonPress-1>', x=x, y=height - 2, button=1)   # the trough below the thumb
        canvas.event_generate('<ButtonRelease-1>', x=x, y=height - 2, button=1, state=0x100)
        self.assertEqual(canvas.canvasy(0) - before, (height * 9) // 10)        # one page, like ttk
        self.root.geometry('1000x900')
        self.root.update()
        self.assertFalse(rail.winfo_ismapped())
        self.assertEqual({canvas.itemcget(i, 'state') for i in canvas.find_withtag('page_rail')}, {'hidden'})

    def test_the_plates_ends_are_two_shared_column_images(self):
        view = self.build(shown='1000x900')
        page, canvas = view.library_page, view.library_page.canvas
        t, s = ui_theme.TOKENS, view.library_page.scale
        images = [i for i in canvas.find_all() if canvas.type(i) == 'image'
                  and not {'bd', 'page_rail'} & set(canvas.gettags(i))]
        self.assertEqual(images, list(page._strips))          # however many cards
        self.assertEqual([i for i in canvas.find_all() if canvas.type(i) == 'rectangle'
                          and 'card_plate' in canvas.gettags(i)], [])
        r, line = px(s, page.CARD_RADIUS), hairline(s)
        left, right = page._strip_images
        self.assertEqual((left.width(), left.height()), (r, view.library_card.bottom))
        self.assertEqual(canvas.coords(page._strips[1]), [float(page.card_width - r), 0.0])
        card = view.selected_card
        middle = (card.top + card.bottom) // 2
        rgb = lambda token: ui_theme.rgb(t[token])  # noqa: E731
        self.assertEqual(left.get(0, middle), rgb('card_edge'))
        self.assertEqual(left.get(line, middle), rgb('panel'))
        self.assertEqual(right.get(r - 1, middle), rgb('card_edge'))
        self.assertEqual(right.get(0, middle), rgb('panel'))
        self.assertTrue(left.transparency_get(0, card.top))         # outside the rounded corner
        self.assertFalse(left.transparency_get(r - 1, card.top + r - 1))
        self.assertTrue(left.transparency_get(0, card.bottom + 2))  # the gap below the card
        self.assertEqual(left.get(r - 1, card.top + r - 1),
                         art.corner_rgba(self.root, r, t['panel'], t['card_edge'], 'nw').get(r - 1, r - 1))
        card.set_visible(False)                                    # the ends follow the cards
        self.root.update()
        shown = (view.launch_card, view.library_card)
        self.assertEqual(page._strip_key[3], tuple((c.top, c.bottom) for c in shown))
        self.assertEqual(page._strip_images[0].height(), view.library_card.bottom)
        self.assertTrue(page._strip_images[0].transparency_get(0, (shown[0].bottom + shown[1].top) // 2))

    def test_the_holder_draws_the_plate_between_its_ends(self):
        view = self.build(shown='1000x900')
        page = view.library_page
        r = px(page.scale, page.CARD_RADIUS)
        for card in (view.launch_card, view.selected_card, view.library_card):
            holder = card.holder
            x = holder.winfo_rootx() - page.canvas.winfo_rootx()
            y = holder.winfo_rooty() - page.canvas.winfo_rooty()
            self.assertEqual((x, y, holder.winfo_width(), holder.winfo_height()),
                             (r, card.top, page.card_width - 2 * r, card.bottom - card.top))
            self.assertEqual(str(holder.cget('background')), ui_theme.TOKENS['panel'])
            edges = [w for w in holder.winfo_children() if w is not card.frame]
            self.assertEqual({str(w.cget('background')) for w in edges}, {ui_theme.TOKENS['card_edge']})
            self.assertEqual(sorted(w.winfo_y() for w in edges), [0, holder.winfo_height() - hairline(page.scale)])

    @unittest.skipUnless(sys.platform == 'win32', 'window regions and layered windows are Windows features')
    def test_every_card_sits_on_one_retained_sheet_shaped_to_the_cards(self):
        import ctypes
        from ctypes import wintypes
        view = self.build(shown='1000x900')
        page, canvas = view.library_page, view.library_page.canvas
        self.root.update()
        cards = (view.launch_card, view.selected_card, view.library_card)
        self.assertEqual([i for i in canvas.find_all() if canvas.type(i) == 'window'], [page._sheet_item])
        for card in cards:
            self.assertIs(card.holder.master, page.sheet)
            self.assertIsNone(card.window)
        hwnd = wintypes.HWND(page.sheet.winfo_id())
        user32, gdi32 = ctypes.WinDLL('user32'), ctypes.WinDLL('gdi32')
        user32.GetWindowLongW.restype = ctypes.c_long
        gdi32.CreateRectRgn.restype = wintypes.HRGN
        gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
        user32.GetWindowRgn.argtypes = (wintypes.HWND, wintypes.HRGN)
        gdi32.PtInRegion.argtypes = (wintypes.HRGN, ctypes.c_int, ctypes.c_int)
        layered = bool(user32.GetWindowLongW(hwnd, -20) & 0x80000)
        self.assertEqual(layered, os.environ.get('DOLLY_UI_LAYERED', '1') != '0')
        self.assertEqual(page.retained, layered)

        def shape():
            region = gdi32.CreateRectRgn(0, 0, 0, 0)
            self.addCleanup(gdi32.DeleteObject, region)
            self.assertNotEqual(user32.GetWindowRgn(hwnd, region), 0)   # ERROR: no region set
            return lambda y: bool(gdi32.PtInRegion(region, 10, int(y)))
        inside = shape()
        for card in cards:
            self.assertTrue(inside(card.top + 1) and inside(card.bottom - 1))
        for upper, lower in zip(cards, cards[1:]):
            self.assertFalse(inside((upper.bottom + lower.top) // 2))   # the gaps show the canvas
        # A hidden card leaves the sheet's shape too.
        view.selected_card.set_visible(False)
        self.root.update()
        inside = shape()
        launch, library = view.launch_card, view.library_card
        self.assertFalse(inside((launch.bottom + library.top) // 2))
        self.assertTrue(inside(library.top + 1))

    def test_without_window_regions_every_card_is_its_own_canvas_window(self):
        with patch('dolly.ui.stage_page.set_window_region', return_value=False):
            view = self.build(shown='720x240')
        page, canvas = view.library_page, view.library_page.canvas
        self.assertIsNone(page.sheet)
        cards = (view.launch_card, view.selected_card, view.library_card)
        self.assertEqual([i for i in canvas.find_all() if canvas.type(i) == 'window'], [c.window for c in cards])
        r = px(page.scale, page.CARD_RADIUS)
        for card in cards:
            self.assertEqual(canvas.coords(card.window), [float(r), float(card.top)])
            self.assertIs(page._plate_of(card.body), card)
        page.reveal(view.library_capture_toggle)
        self.root.update()
        self.assertTrue(self.in_view(page, view.library_capture_toggle))

    @unittest.skipUnless(sys.platform == 'win32', 'layered child windows are a Windows feature')
    def test_retain_surface_layers_a_child_window_unless_turned_off(self):
        import ctypes
        user32 = ctypes.WinDLL('user32')
        user32.GetWindowLongW.restype = ctypes.c_long
        frame = tk.Frame(self.root)
        with patch.dict(os.environ, {'DOLLY_UI_LAYERED': '1'}):
            self.assertTrue(retain_surface(frame))
        self.assertTrue(user32.GetWindowLongW(frame.winfo_id(), -20) & 0x80000)
        plain = tk.Frame(self.root)
        with patch.dict(os.environ, {'DOLLY_UI_LAYERED': '0'}):
            self.assertFalse(retain_surface(plain))
        self.assertFalse(user32.GetWindowLongW(plain.winfo_id(), -20) & 0x80000)



class ReplayGapTests(ReplayPageCase):
    """USER-ADDITIONS UPDATE 9, 12 and 17.5: the cards end Replay's own design gap above the footer's
    links; a shorter window takes table rows, never the gap; only a page too small for one row
    overflows, and then its viewport ends at that gap. Replay never fades (UPDATE 12)."""

    def test_the_gap_is_replays_own_design_gap_in_whole_pixels(self):
        self.assertEqual(END_GAP, (29, 22))
        self.assertEqual(StagePage.END_GAP, END_GAP)
        self.assertEqual([end_gap(s) for s in (1.0, 1.25, 1.5, 2.0)], [29, 36, 43, 58])
        # Compact rows start the link row 7 px below the page area: the same 29 px above the links.
        self.assertEqual([end_gap(s, compact=True) for s in (1.0, 1.5)], [22, 33])

    def test_cards_that_fit_leave_the_page_exactly_as_before(self):
        view = self.build(shown='1000x900')
        page = view.library_page
        s = page.scale
        fits = page.content_height + end_gap(s)
        for height in (900, fits):
            self.root.geometry(f'1000x{height}')
            self.root.update()
            page._layout()
            self.root.update()
            self.assertFalse(page._foot_packed, height)
            self.assertEqual(page.foot.winfo_manager(), '')
            self.assertEqual(page.canvas.winfo_height(), page.winfo_height())
            self.assertEqual(page.canvas.yview(), (0.0, 1.0))
            self.assertFalse(page.scrollbar.winfo_ismapped())
        self.assertIsNone(getattr(page, 'fade', None))      # no scroll-edge fade on Replay (UPDATE 12)

    def test_a_short_window_takes_table_rows_not_the_gap(self):
        view = self.build(shown='1000x900')
        page, tree = view.library_page, view.replay_tree
        s = page.scale
        design = page.content_height
        # Whole-px rounding may run the cards FIT_ROUNDING into the gap; one px more costs a row.
        for height, rows in ((design + end_gap(s) - px(s, FIT_ROUNDING), TABLE_ROWS),
                             (design + end_gap(s) - px(s, FIT_ROUNDING) - 1, TABLE_ROWS - 1)):
            self.root.geometry(f'1000x{height}')
            self.root.update()
            page._layout()
            self.root.update()
            self.assertEqual(int(tree.cget('height')), rows, height)
            self.assertLessEqual(page.content_height, height - end_gap(s) + px(s, FIT_ROUNDING))
            self.assertFalse(page._foot_packed, height)
            self.assertEqual(page.canvas.yview(), (0.0, 1.0))
            self.assertFalse(page.scrollbar.winfo_ismapped())
        self.assertEqual(page.content_height, view.library_card.bottom)
        self.assertEqual(page.content_height, design - px(s, 36))      # exactly the one row it gave up

    def test_only_a_page_too_small_for_one_row_ends_the_gap_above_the_links_and_never_fades(self):
        view = self.build(shown='1000x900')
        page, tree = view.library_page, view.replay_tree
        s = page.scale
        # One px short of fitting the cards with a one-row table (no shell window is this small).
        smallest = page.content_height - (TABLE_ROWS - TABLE_MIN_ROWS) * px(s, 36)
        self.root.geometry(f'1000x{smallest + end_gap(s) - px(s, FIT_ROUNDING) - 1}')
        self.root.update()
        page._layout()
        self.root.update()
        self.assertEqual(int(tree.cget('height')), TABLE_MIN_ROWS)
        self.assertTrue(page._foot_packed)
        self.assertEqual(page.foot.winfo_height(), end_gap(s))
        self.assertEqual(page.canvas.winfo_height(), page.winfo_height() - end_gap(s))
        self.assertTrue(page.scrollbar.winfo_ismapped())
        # At the end the last card ends on the viewport's bottom: the gap to the links is the foot.
        page._scroll_to(page.content_height)
        self.root.update()
        self.assertEqual(page.canvas.canvasy(0) + page.canvas.winfo_height(), page.content_height)
        # Content is cut at both edges now, and still nothing fades; no layered popup is ever made.
        made = AssertionError('Replay made a fade')
        with patch('dolly.ui.fade.available', return_value=True), \
                patch('dolly.ui.fade.ScrollFade.__init__', side_effect=made):
            page._scroll_units(-3)
            page._yview('moveto', 0.5)
            page._layout()
        self.assertIsNone(getattr(page, 'fade', None))
        # Shown again in a tall window: no foot, no rail, the design's rows back.
        self.root.geometry('1000x900')
        self.root.update()
        page._layout()
        self.root.update()
        self.assertFalse(page._foot_packed)
        self.assertEqual(page.canvas.yview(), (0.0, 1.0))
        self.assertFalse(page.scrollbar.winfo_ismapped())
        self.assertEqual(int(tree.cget('height')), TABLE_ROWS)


class ReplayFitTests(ReplayPageCase):
    """USER-ADDITIONS UPDATE 17.5: Replay never scrolls. In every window from the shell's minimum up,
    at every scale, the three cards end Replay's gap above the footer links: the Replay Library table
    shows fewer rows (at least one) and scrolls inside itself, its rail hidden while its rows fit;
    no page rail, no page wheel, no foot strip, no fade."""

    SCALES = (1.0, 1.25, 1.5, 2.0)

    def assert_fits(self, view, area):
        """The page fits as the rule says; returns the room the cards have (px)."""
        page = view.library_page
        s = page.scale
        self.assertEqual(page.winfo_height(), int(area.cget('height')))
        room = page.winfo_height() - end_gap(s, area.mode == 'compact') + px(s, FIT_ROUNDING)
        self.assertLessEqual(page.content_height, room)
        self.assertEqual(page.content_height, view.library_card.bottom)
        self.assertEqual(page.canvas.yview(), (0.0, 1.0))
        self.assertFalse(page.scrollbar.winfo_ismapped())       # no page rail
        self.assertFalse(page._foot_packed)                      # the cards themselves end at the gap
        self.assertEqual(page.canvas.winfo_height(), page.winfo_height())
        self.assertIsNone(page._wheel(SimpleNamespace(widget=view.launch_card.title_label, delta=-120)))
        self.assertEqual(page.canvas.canvasy(0), 0)
        self.assertIsNone(getattr(page, 'fade', None))
        return room

    def test_the_shell_minimum_fits_every_card_at_every_scale(self):
        for scale in self.SCALES:
            with self.subTest(scale=scale):
                view, area = self.at_window((1000, 700), scale)
                self.assertEqual(area.mode, 'compact')
                room = self.assert_fits(view, area)
                tree = view.replay_tree
                rows = int(tree.cget('height'))
                self.assertGreaterEqual(rows, TABLE_MIN_ROWS)
                self.assertLess(rows, TABLE_ROWS)
                # The table took all the room there is: one more row would run into the gap.
                pitch = int(ttk.Style(tree).lookup('Library.Treeview', 'rowheight'))
                self.assertEqual(pitch, px(view.library_page.scale, 36))
                self.assertGreater(view.library_page.content_height + pitch, room)

    def test_the_design_window_keeps_the_designs_three_rows_at_every_scale(self):
        for scale in self.SCALES:
            with self.subTest(scale=scale):
                view, area = self.at_window((1200, 810), scale)
                self.assertEqual(area.mode, 'design')
                self.assert_fits(view, area)
                self.assertEqual(int(view.replay_tree.cget('height')), TABLE_ROWS)

    def test_the_table_gives_its_rows_back_and_keeps_the_chosen_replay_shown(self):
        view, area = self.at_window((1200, 810))
        page, tree = view.library_page, view.replay_tree
        self.fill(tree, TABLE_ROWS)
        tree.selection_set(str(TABLE_ROWS - 1))
        self.root.update()
        bar = next(w for w in tree.master.winfo_children() if isinstance(w, ttk.Scrollbar))
        design = page.content_height
        self.assertEqual(int(tree.cget('height')), TABLE_ROWS)
        self.assertFalse(bar.winfo_ismapped())                   # its rows fit: the table's rail rests
        self.resize(area, (1000, 700))
        self.assert_fits(view, area)
        rows = int(tree.cget('height'))
        self.assertLess(rows, TABLE_ROWS)
        self.assertEqual(page.content_height, design - (TABLE_ROWS - rows) * px(page.scale, 36))
        self.assertTrue(bar.winfo_ismapped())                    # now it scrolls inside itself
        self.assertTrue(tree.bbox(str(TABLE_ROWS - 1)))          # the chosen replay is still shown
        self.resize(area, (1200, 810))
        self.assert_fits(view, area)
        self.assertEqual(int(tree.cget('height')), TABLE_ROWS)
        self.assertEqual(page.content_height, design)
        self.assertFalse(bar.winfo_ismapped())

    def test_the_wheel_scrolls_the_table_and_never_the_page(self):
        view, area = self.at_window((1000, 700))
        page, tree = view.library_page, view.replay_tree
        self.fill(tree, 12)
        tree.event_generate('<MouseWheel>', delta=-120)          # the table's own wheel: one row a notch
        self.root.update()
        self.assertGreater(tree.yview()[0], 0)
        self.assertIsNone(page._wheel(SimpleNamespace(widget=tree, delta=-120)))
        for widget in (page.canvas, page.side, view.library_capture_toggle, view.search_entry,
                       view.launch_card.body):
            self.assertIsNone(page._wheel(SimpleNamespace(widget=widget, delta=-120)), widget)
        page.scroll_page_wheel(SimpleNamespace(delta=-120, num=0))   # even when asked directly
        self.root.update()
        self.assertFalse(page.scroller.active)
        self.assertEqual(page.canvas.canvasy(0), 0)
        # Keyboard focus reveals nothing either: everything is already in view.
        page.reveal(tree)
        page.reveal(self.button(view.library_card.body, 'Refresh'))
        self.assertEqual(page.canvas.canvasy(0), 0)
        self.assert_fits(view, area)


if __name__ == '__main__':
    unittest.main()
