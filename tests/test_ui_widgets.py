"""Behavior contracts for UI-1 shared widgets; no application or game required."""
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest

from dolly import gui_layout as widgets
from dolly.ui.widgets import NavigationMarker


class SharedWidgetTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.addCleanup(self.root.destroy)
        self.root.geometry('480x320')
        style = ttk.Style(self.root)
        style.configure('TFrame', background='#101010')
        style.configure('Card.TFrame', background='#202020')
        style.configure('Equivalent.TFrame', background='#202020')
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)

    def tearDown(self):
        if hasattr(self, 'errors'):
            self.assertEqual(self.errors, [], 'Tk callback raised an exception')

    def test_navigation_animation_keeps_selection_immediate_and_retargets_latest_click(self):
        from unittest.mock import patch
        from dolly import ui_theme
        self.root.withdraw()
        sidebar = ttk.Frame(self.root, padding=(6, 12, 6, 0))
        sidebar.pack(side='left', fill='y')
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill='both', expand=True)
        buttons = []
        pages = []
        for title in ('Replay', 'Camera', 'Controls'):
            page = ttk.Frame(notebook)
            notebook.add(page, text=title)
            def activate(p=page, index=len(buttons)):
                notebook.select(p)
                marker.select(buttons[index])
            button = ttk.Button(sidebar, text=title, command=activate)
            button.pack(fill='x', pady=(0, 8))
            buttons.append(button)
            pages.append(page)
        marker = NavigationMarker(sidebar)
        marker.select(buttons[0])
        self.root.update_idletasks()
        self.assertEqual(marker.itemcget(marker._outline, 'state'), 'hidden')
        self.assertIsNone(marker._box, 'An unmapped target must never create a startup dot')
        self.assertFalse(marker.winfo_ismapped())
        self.root.deiconify()
        self.root.update()
        marker.select(buttons[0])
        self.root.update()
        self.assertEqual(marker.type(marker._outline), 'polygon')
        self.assertEqual(marker.itemcget(marker._outline, 'state'), 'normal')
        self.assertEqual(marker._box, marker._destination)
        self.assertIsNone(marker._job)
        original_geometry = [(b.winfo_x(), b.winfo_y(), b.winfo_width(), b.winfo_height()) for b in buttons]
        original_ids = [b.winfo_id() for b in buttons]
        canvas_geometry = (marker.winfo_x(), marker.winfo_y(), marker.winfo_width(), marker.winfo_height())
        first_box = marker._box
        first_outline = marker._outline
        first_path = marker.coords(first_outline)
        # Native hit testing and the actual ttk mouse binding remain in charge.
        # winfo_containing answers with the desktop's current stacking, so put the
        # test window at a known on-screen position and above other applications
        # first. Without that, an unrelated window covering this point reads as
        # "no widget here" and this asserts a stacking failure that never happened.
        button = buttons[1]
        center = (button.winfo_rootx()+button.winfo_width()//2,
                  button.winfo_rooty()+button.winfo_height()//2)
        self.root.geometry('+40+40')
        self.root.lift()
        self.root.attributes('-topmost', True)
        self.root.update()
        try:
            self.assertIs(self.root.winfo_containing(*center), button)
        finally:
            self.root.attributes('-topmost', False)
        clock = [10.0]
        with patch('dolly.ui.widgets.time.monotonic', side_effect=lambda: clock[0]), \
             patch.object(ttk.Style, 'configure', side_effect=AssertionError('Animation changes global styles')), \
             patch.object(ttk.Style, 'map', side_effect=AssertionError('Animation changes global style maps')):
            button.event_generate('<Enter>')
            button.event_generate('<ButtonPress-1>', x=button.winfo_width()//2, y=button.winfo_height()//2)
            button.event_generate('<ButtonRelease-1>', x=button.winfo_width()//2, y=button.winfo_height()//2)
            self.assertEqual(notebook.select(), str(pages[1]))
            self.assertIsNotNone(marker._job)
            clock[0] += marker.DURATION/2
            # Tk runs the actual scheduled frame; a controlled elapsed clock
            # keeps this half-fade sample independent of native paint delays.
            self.root.after(25, self.root.quit); self.root.mainloop()
            strengths = dict(marker._strengths)
            self.assertEqual(set(strengths), {buttons[0], buttons[1]})
            self.assertTrue(all(0 < value < 1 for value in strengths.values()))
            self.assertEqual(marker._boxes[buttons[0]], first_box)
            self.assertEqual(marker.coords(first_outline), first_path,
                             'The outgoing rounded box must not slide behind other native buttons')
            incoming = marker._outlines[buttons[1]]
            incoming_path = marker.coords(incoming)
            for item in (first_outline, incoming):
                color = marker.itemcget(item, 'outline')
                self.assertNotIn(color, (ui_theme.TOKENS['bg'], ui_theme.TOKENS['accent']))
                for actual, bg, accent in zip(self.root.winfo_rgb(color),
                        self.root.winfo_rgb(ui_theme.TOKENS['bg']), self.root.winfo_rgb(ui_theme.TOKENS['accent'])):
                    self.assertLessEqual(min(bg, accent), actual)
                    self.assertLessEqual(actual, max(bg, accent))
            old_job = marker._job
            # A newer click selects immediately and fades from the colors
            # already on screen, rather than restarting either old endpoint.
            buttons[2].invoke()
            self.assertEqual(notebook.select(), str(pages[2]))
            self.assertEqual(marker._strengths, strengths)
            self.assertEqual(marker.coords(first_outline), first_path)
            self.assertEqual(marker.coords(incoming), incoming_path)
            self.assertNotIn(old_job, self.root.tk.splitlist(self.root.tk.call('after', 'info')))
            clock[0] += marker.DURATION*2  # A delayed frame catches up at once.
            self.root.after(25, self.root.quit); self.root.mainloop()
        self.assertIsNone(marker._job)
        self.assertEqual(marker._strengths, {buttons[2]: 1})
        self.assertTrue(all(marker.itemcget(item, 'state') == ('normal' if target is buttons[2] else 'hidden')
                            for target, item in marker._outlines.items()))
        self.assertGreater(marker._box[1], first_box[1])
        x = buttons[2].winfo_rootx()-marker.winfo_rootx()
        y = buttons[2].winfo_rooty()-marker.winfo_rooty()
        self.assertEqual(marker._box, (x-1, y-1, x+buttons[2].winfo_width(),
                                      y+buttons[2].winfo_height()))
        self.assertEqual(canvas_geometry,
            (marker.winfo_x(), marker.winfo_y(), marker.winfo_width(), marker.winfo_height()),
            'Animation must not move or resize a native child window')
        self.assertEqual(original_geometry,
            [(b.winfo_x(), b.winfo_y(), b.winfo_width(), b.winfo_height()) for b in buttons])
        self.assertEqual(original_ids, [b.winfo_id() for b in buttons])
        buttons[2].focus_force(); self.root.update()
        self.assertIs(self.root.focus_get(), buttons[2])
        self.assertFalse(int(marker.cget('takefocus')))

    def test_navigation_animation_cancels_on_resize_and_destruction(self):
        sidebar = ttk.Frame(self.root, padding=(6, 12, 6, 0))
        sidebar.pack(fill='both', expand=True)
        first = ttk.Button(sidebar, text='Replay'); first.pack()
        last = ttk.Button(sidebar, text='Settings'); last.pack(pady=50)
        other_binding = sidebar.bind('<Configure>', lambda _event: None, add='+')
        marker = NavigationMarker(sidebar, scale=1.5)
        self.root.update()
        marker.select(first)
        marker.select(last)
        self.assertIsNotNone(marker._job)
        self.root.withdraw(); self.root.update()
        self.assertIsNone(marker._job)
        self.assertIsNone(marker._box)
        self.assertEqual(marker._strengths, {})
        self.assertTrue(all(marker.itemcget(item, 'state') == 'hidden' for item in marker.find_all()))
        self.root.deiconify(); self.root.update()
        marker.select(last); self.root.update()
        self.assertEqual(marker._strengths, {last: 1})
        # A parent layout change snaps to correct geometry without queuing work.
        sidebar.configure(padding=(0,20))
        self.root.geometry('520x350')
        self.root.update()
        self.assertIsNone(marker._job)
        x = last.winfo_rootx()-marker.winfo_rootx()
        y = last.winfo_rooty()-marker.winfo_rooty()
        self.assertEqual(marker._box, (x-1, y-1, x+last.winfo_width(), y+last.winfo_height()))
        # Unmapping invalidates stale geometry, and the same selection can
        # recover on remap without another page or focus change.
        last.pack_forget(); self.root.update()
        self.assertIsNone(marker._box)
        self.assertIsNone(marker._destination)
        self.assertIsNone(marker._job)
        self.assertEqual(marker.itemcget(marker._outline, 'state'), 'hidden')
        last.pack(pady=50)
        marker.select(last)
        self.root.update()
        self.assertEqual(marker._box, marker._destination)
        self.assertEqual(marker.itemcget(marker._outline, 'state'), 'normal')
        marker.select(first)
        token = marker._job
        self.assertIsNotNone(token)
        first.destroy(); self.root.update()
        self.assertNotIn(token, self.root.tk.splitlist(self.root.tk.call('after', 'info')))
        self.assertIsNone(marker._target)
        self.assertIsNone(marker._box)
        self.assertEqual(marker.itemcget(marker._outline, 'state'), 'hidden')
        marker.select(last)
        extra = ttk.Button(sidebar, text='Export'); extra.pack()
        self.root.update()
        marker.select(extra)
        token = marker._job
        self.assertIsNotNone(token)
        layout_binding = marker._layout_binding
        target_bindings = tuple(marker._target_bindings)
        visibility_bindings = tuple(marker._visibility_bindings)
        marker.destroy()
        self.assertNotIn(token, self.root.tk.splitlist(self.root.tk.call('after', 'info')))
        self.assertNotIn(layout_binding, sidebar.bind('<Configure>'))
        self.assertIn(other_binding, sidebar.bind('<Configure>'))
        for sequence, binding in target_bindings:
            self.assertNotIn(binding, extra.bind(sequence))
        for sequence, binding in visibility_bindings:
            self.assertNotIn(binding, self.root.bind(sequence))
        self.root.geometry('540x360')
        self.root.update()

    def test_surface_style_preserves_card_name_and_background_inheritance(self):
        for style, expected in [('TFrame', 'TFrame'), ('Rounded.Card.TFrame', 'Card.TFrame'),
                                ('Equivalent.TFrame', 'Card.TFrame')]:
            with self.subTest(style=style):
                self.assertEqual(widgets.surface_style(ttk.Frame(self.root, style=style)), expected)

    def test_card_title_padding_and_pack_contract(self):
        card = widgets.card(self.root, 'Capture')
        self.assertEqual(str(card.cget('style')), 'Rounded.Card.TFrame')
        self.assertEqual(tuple(map(int, card.cget('padding'))), (14,))
        self.assertEqual(card.pack_info()['fill'], 'x')
        self.assertEqual(tuple(map(int, card.pack_info()['pady'])), (0, 14))
        label, = card.winfo_children()
        self.assertEqual(label.cget('text'), 'Capture')
        self.assertEqual(str(label.cget('style')), 'CardTitle.TLabel')

    def test_entry_shares_variable_in_both_directions_and_inherits_surface(self):
        for surface, style in [('TFrame', 'TEntry'), ('Card.TFrame', 'Card.TEntry')]:
            with self.subTest(surface=surface):
                parent = ttk.Frame(self.root, style=surface)
                value = tk.StringVar(self.root, 'before')
                entry = widgets.field(parent, 'Name', value)
                self.assertIsInstance(entry, ttk.Entry)
                self.assertEqual(str(entry.cget('style')), style)
                self.assertEqual(str(entry.cget('textvariable')), str(value))
                value.set('external')
                self.assertEqual(entry.get(), 'external')
                entry.delete(0, 'end'); entry.insert(0, 'edited')
                self.assertEqual(value.get(), 'edited')
                self.assertEqual(entry.pack_info()['fill'], 'x')
                self.assertEqual(entry.master.winfo_children()[0].cget('text'), 'Name')

    def test_readonly_combo_selection_updates_shared_variable_and_event(self):
        parent = ttk.Frame(self.root, style='Card.TFrame')
        value = tk.StringVar(self.root, '30')
        combo = widgets.field(parent, 'FPS', value, values=('30', '60'))
        self.assertIsInstance(combo, ttk.Combobox)
        self.assertEqual(str(combo.cget('state')), 'readonly')
        self.assertEqual(str(combo.cget('style')), 'Card.TCombobox')
        self.assertEqual(tuple(combo.cget('values')), ('30', '60'))
        observed = []
        combo.bind('<<ComboboxSelected>>', lambda event: observed.append(value.get()))
        combo.current(1); combo.event_generate('<<ComboboxSelected>>')
        self.assertEqual(value.get(), '60')
        self.assertEqual(observed, ['60'])
        value.set('30'); self.assertEqual(combo.current(), 0)
        self.assertIsInstance(widgets.field(parent, 'Empty', value, values=()), ttk.Combobox)

    def test_actions_keep_independent_callbacks_order_styles_and_wrapping(self):
        parent = ttk.Frame(self.root, style='Card.TFrame')
        called = []
        specs = [(str(i), lambda value=i: called.append(value)) for i in range(5)]
        specs[1] += ('Primary.TButton',)
        buttons = widgets.actions(parent, specs, columns=2)
        for i, button in enumerate(buttons):
            grid = button.grid_info()
            self.assertEqual((grid['row'], grid['column']), (i // 2, i % 2))
            self.assertEqual(grid['sticky'], 'w')
            self.assertEqual(button.cget('text'), str(i))
        self.assertEqual(str(buttons[1].cget('style')), 'Card.Primary.TButton')
        self.assertEqual(str(buttons[0].cget('style')), 'Card.TButton')
        for index in (3, 0, 4, 1, 2): buttons[index].invoke()
        self.assertEqual(called, [3, 0, 4, 1, 2])
        self.assertEqual(widgets.actions(parent, []), [])

    def test_disclosure_toggle_preserves_children_and_values_across_reopen(self):
        parent = ttk.Frame(self.root, style='Card.TFrame')
        disclosure = widgets.disclosure(parent, 'Advanced')
        value = tk.StringVar(self.root, 'preserved')
        entry = widgets.field(disclosure.body, 'Value', value)
        self.assertFalse(disclosure.opened)
        self.assertEqual(disclosure.body.winfo_manager(), '')
        self.assertEqual(str(disclosure.toggle.cget('style')), 'Disclosure.Card.TButton')
        for _ in range(2):
            disclosure.toggle.invoke()
            self.assertTrue(disclosure.opened)
            self.assertEqual(disclosure.toggle.cget('text'), 'v  Advanced')
            self.assertEqual(disclosure.body.winfo_manager(), 'pack')
            disclosure.set_open(False)
            self.assertEqual(disclosure.toggle.cget('text'), '>  Advanced')
            self.assertEqual(disclosure.body.winfo_manager(), '')
            self.assertEqual(entry.get(), 'preserved')

    def make_page(self):
        page = widgets.ScrollPage(self.root)
        page.pack(fill='both', expand=True)
        labels = []
        for index in range(60):
            label = ttk.Label(page.body, text=str(index))
            label.pack(); labels.append(label)
        self.root.update()
        return page, labels

    def test_scroll_page_resizes_content_and_reveals_late_child(self):
        page, labels = self.make_page()
        self.assertEqual(int(float(page.canvas.itemcget(page.window, 'width'))), page.canvas.winfo_width())
        self.assertEqual(tuple(map(float, page.canvas.cget('scrollregion').split())),
                         tuple(map(float, page.canvas.bbox('all'))))
        page.reveal(labels[45]); self.root.update()
        self.assertGreater(page.canvas.yview()[0], 0)
        self.assertGreaterEqual(labels[45].winfo_rooty(), page.canvas.winfo_rooty())
        self.assertLess(labels[45].winfo_rooty(), page.canvas.winfo_rooty()+page.canvas.winfo_height())

    def test_wheel_routes_descendant_events_and_excludes_text_tree_and_other_pages(self):
        page, labels = self.make_page()
        labels[0].event_generate('<MouseWheel>', delta=-120); self.root.update()
        self.assertGreater(page.canvas.yview()[0], 0)
        before = page.canvas.yview()
        for target in (ttk.Treeview(page.body), tk.Text(page.body), ttk.Frame(self.root)):
            self.assertIsNone(page._wheel(SimpleNamespace(widget=target, delta=-120)))
            self.assertEqual(page.canvas.yview(), before)
        self.assertEqual(page._wheel(SimpleNamespace(widget=labels[0], delta=120)), 'break')
        self.assertLess(page.canvas.yview()[0], before[0])
        before = page.canvas.yview()
        page.scroll_page_wheel(SimpleNamespace(delta=0, num=5))
        self.assertGreater(page.canvas.yview()[0], before[0])
        page.scroll_page_wheel(SimpleNamespace(delta=0, num=4))
        self.assertEqual(page.canvas.yview(), before)

    def test_small_precision_wheel_deltas_accumulate(self):
        page, labels = self.make_page()
        before = page.canvas.canvasy(0)
        page.scroll_page_wheel(SimpleNamespace(delta=-1, num=0))
        self.assertEqual(page.canvas.canvasy(0), before)
        page.scroll_page_wheel(SimpleNamespace(delta=-1, num=0))
        self.assertEqual(page.canvas.canvasy(0), before + 1)
        page.scroll_page_wheel(SimpleNamespace(delta=-30, num=0))
        self.assertEqual(page.canvas.canvasy(0), before + 16)
        page.scroll_page_wheel(SimpleNamespace(delta=-120, num=0))
        self.assertEqual(page.canvas.canvasy(0), before + 76)

    def test_precision_wheel_at_either_edge_does_not_delay_reversal(self):
        page, _ = self.make_page()
        for edge, outward in ((0, 1), (1, -1)):
            with self.subTest(edge=edge):
                page.canvas.yview_moveto(edge)
                before = page.canvas.canvasy(0)
                for _ in range(39):
                    page.scroll_page_wheel(SimpleNamespace(delta=outward, num=0))
                self.assertEqual(page.canvas.canvasy(0), before)
                self.assertEqual(page._wheel_pixels, 0)
                for _ in range(2):
                    page.scroll_page_wheel(SimpleNamespace(delta=-outward, num=0))
                self.assertEqual(page.canvas.canvasy(0), before + outward)

    def test_precision_wheel_discards_opposite_fraction_when_direction_changes(self):
        page, _ = self.make_page()
        page.canvas.yview_moveto(.5)
        before = page.canvas.canvasy(0)
        page.scroll_page_wheel(SimpleNamespace(delta=-1, num=0))
        for _ in range(2):
            page.scroll_page_wheel(SimpleNamespace(delta=1, num=0))
        self.assertEqual(page.canvas.canvasy(0), before - 1)

    def test_wheel_landing_at_edge_clears_fraction_and_can_move_back_immediately(self):
        page, _ = self.make_page()
        for edge, outward in ((0, 1), (1, -1)):
            with self.subTest(edge=edge):
                page.canvas.yview_moveto(edge)
                page.canvas.yview_scroll(outward, 'units')
                page.scroll_page_wheel(SimpleNamespace(delta=outward * 5, num=0))
                self.assertEqual(page._wheel_pixels, 0)
                before = page.canvas.canvasy(0)
                page.scroll_page_wheel(SimpleNamespace(delta=-outward * 2, num=0))
                self.assertEqual(page.canvas.canvasy(0), before + outward)

    def test_nonoverflowing_page_does_not_consume_wheel(self):
        page = widgets.ScrollPage(self.root); page.pack(fill='both', expand=True)
        label = ttk.Label(page.body, text='Short'); label.pack(); self.root.update()
        self.assertEqual(page.canvas.yview(), (0.0, 1.0))
        self.assertIsNone(page._wheel(SimpleNamespace(widget=label, delta=-120)))

    def test_rail_hides_without_changing_content_width_at_overflow_boundary(self):
        page=widgets.ScrollPage(self.root);page.pack(fill='both',expand=True)
        label=ttk.Label(page.body,text='Short');label.pack();self.root.update()
        width=page.canvas.winfo_width()
        self.assertFalse(page.scrollbar.winfo_ismapped())
        extra=[ttk.Label(page.body,text=str(index)) for index in range(40)]
        for item in extra:item.pack()
        self.root.update()
        self.assertTrue(page.scrollbar.winfo_ismapped())
        self.assertEqual(page.canvas.winfo_width(),width)
        for item in extra:item.destroy()
        self.root.update()
        self.assertFalse(page.scrollbar.winfo_ismapped())
        self.assertEqual(page.canvas.winfo_width(),width)

    def test_nearest_nested_page_owns_wheel_independent_of_binding_order(self):
        outer=widgets.ScrollPage(self.root);outer.pack(fill='both',expand=True)
        inner=widgets.ScrollPage(outer.body);inner.configure(height=100);inner.pack(fill='x')
        inner.pack_propagate(False)
        labels=[]
        for index in range(30):
            label=ttk.Label(inner.body,text=str(index));label.pack();labels.append(label)
            ttk.Label(outer.body,text='Outer '+str(index)).pack()
        self.root.update()
        before=outer.canvas.yview()
        event=SimpleNamespace(widget=labels[0],delta=-120)
        self.assertIsNone(outer._wheel(event))
        self.assertEqual(inner._wheel(event),'break')
        self.assertGreater(inner.canvas.yview()[0],0)
        self.assertEqual(outer.canvas.yview(),before)

    def test_overflowing_narrow_page_reserves_a_visible_rail_before_canvas(self):
        self.root.geometry('180x320')
        page,_=self.make_page()
        self.assertNotEqual(page.canvas.yview(),(0,1))
        self.assertTrue(page.scrollbar.winfo_ismapped())
        self.assertEqual(page.scroll_gutter.winfo_width(),page.scrollbar.winfo_reqwidth())
        self.assertGreater(page.canvas.winfo_width(),0)
        self.assertLessEqual(page.canvas.winfo_rootx()+page.canvas.winfo_width(),page.scroll_gutter.winfo_rootx())

    def test_wheel_controls_do_not_also_scroll_the_page(self):
        page,_=self.make_page()
        for control in (ttk.Combobox(page.body,values=('One','Two')),page.scrollbar):
            with self.subTest(control=type(control).__name__):
                before=page.canvas.yview()
                self.assertIsNone(page._wheel(SimpleNamespace(widget=control,delta=-120)))
                self.assertEqual(page.canvas.yview(),before)

    def test_destroy_removes_only_its_own_toplevel_wheel_binding(self):
        calls = []
        other = self.root.bind('<MouseWheel>', lambda event: calls.append(event.delta), add='+')
        first = widgets.ScrollPage(self.root)
        second = widgets.ScrollPage(self.root)
        first_id, second_id = first.bind_id, second.bind_id
        first.destroy()
        binding = self.root.bind('<MouseWheel>')
        self.assertNotIn(first_id, binding)
        self.assertIn(second_id, binding)
        self.assertIn(other, binding)
        self.assertEqual(self.root.tk.call('info', 'commands', first_id), '')
        self.root.update()
        self.root.event_generate('<MouseWheel>', delta=-120)
        self.assertEqual(calls, [-120])
        second.destroy()
        self.assertNotIn(second_id, self.root.bind('<MouseWheel>'))
        self.assertIn(other, self.root.bind('<MouseWheel>'))


class SharedWidgetBoundaryTests(unittest.TestCase):
    def test_original_layout_exports_are_the_same_widget_objects(self):
        from dolly.ui import widgets as shared
        for name in ('GAP', 'ScrollPage', 'Disclosure', 'surface_style',
                     'card', 'field', 'actions', 'disclosure'):
            with self.subTest(name=name):
                self.assertIs(getattr(widgets, name), getattr(shared, name))

    def test_importing_shared_widgets_does_not_load_application_or_pages(self):
        import subprocess
        import sys
        result = subprocess.run([sys.executable, '-c',
            'import sys; from dolly.ui import widgets; '
            'allowed = {"dolly", "dolly.ui", "dolly.ui.widgets", "dolly.ui_theme", "dolly.ui.scale"}; '
            'unexpected = {name for name in sys.modules if name == "dolly" or name.startswith("dolly.")} - allowed; '
            'assert not unexpected, sorted(unexpected)'],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
