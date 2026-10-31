import tkinter as tk
from tkinter import ttk
import unittest
from dolly.ui import dropdown_popup
from dolly.ui.scale import ui_scale, px

class DropdownPopupTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk(); self.root.geometry('700x500+10+10'); self.root.update()
        dropdown_popup.install(self.root)
    def tearDown(self):
        self.root.destroy()
    def make(self, scale=1, values=('A','B','C'), state='readonly'):
        self.root._dolly_ui_scale=scale
        c=ttk.Combobox(self.root,values=values,state=state); c.pack(); self.root.update()
        c.current(0); return c
    def post(self,c):
        self.root.tk.call('ttk::combobox::Post',str(c)); self.root.update()
        p=getattr(self.root,'_dolly_combo_popup',None); self.assertIsNotNone(p); return p
    def test_mouse_keyboard_and_event(self):
        for scale in (1,1.5,2):
            with self.subTest(scale=scale):
                c=self.make(scale); events=[]; c.bind('<<ComboboxSelected>>',lambda e:events.append(e.widget.get()))
                p=self.post(c); self.assertEqual(p.row,px(scale,28)); p._goto(2); p._choose(); self.root.update()
                self.assertEqual(c.get(),'C'); self.assertEqual(events,['C']); self.assertIsNone(self.root._dolly_combo_popup)
                p=self.post(c); p._goto(1); p._cancel(); self.assertEqual(c.get(),'C'); c.destroy()
    def test_scroll_and_postcommand(self):
        c=self.make(values=tuple(str(i) for i in range(30))); called=[]
        c.configure(postcommand=lambda:called.append(True)); p=self.post(c)
        self.assertTrue(p.scroll); self.assertEqual(called,[True]); p._goto(29); self.root.update()
        self.assertGreater(p.list.yview()[0],0); p._choose(); self.root.update(); self.assertEqual(c.get(),'29')
    def test_disabled_does_not_open(self):
        c=self.make(); c.state(['disabled']); self.root.tk.call('ttk::combobox::Post',str(c)); self.root.update()
        self.assertIsNone(getattr(self.root,'_dolly_combo_popup',None))
    def test_widget_styles_and_editable_selection(self):
        for style in ('Kit.TCombobox','Card.TCombobox','Stage.Card.TCombobox','Playback.TCombobox'):
            with self.subTest(style=style):
                c=ttk.Combobox(self.root,values=('one','two'),style=style); c.pack(); self.root.update()
                events=[]; c.bind('<<ComboboxSelected>>',lambda e:events.append(e.widget.get()))
                p=self.post(c); p._goto(1); p._choose(); self.root.update()
                self.assertEqual((c.get(),events),('two',['two'])); c.destroy()
    def test_escaping_or_clicking_outside_keeps_value(self):
        c=self.make(); p=self.post(c); p._goto(2); p._cancel(); self.assertEqual(c.get(),'A')
        p=self.post(c)
        class Event: x_root=-10000; y_root=-10000
        p._outside(Event()); self.root.update(); self.assertEqual(c.get(),'A'); self.assertIsNone(self.root._dolly_combo_popup)
    def test_tab_preserves_value_and_closes(self):
        c=self.make(); ttk.Entry(self.root).pack(); self.root.update(); p=self.post(c); p._goto(1); p._tab(None)
        self.root.update(); self.assertEqual(c.get(),'A'); self.assertIsNone(self.root._dolly_combo_popup)
    def test_previous_grab_is_restored(self):
        modal=tk.Toplevel(self.root); modal.geometry('200x100+30+30'); modal.update(); modal.grab_set()
        c=ttk.Combobox(modal,values=('A','B'),state='readonly'); c.pack(); modal.update()
        p=self.post(c); self.assertEqual(self.root.grab_current(),p.top); p._cancel(); self.root.update()
        self.assertEqual(self.root.grab_current(),modal); modal.grab_release(); modal.destroy()

if __name__ == '__main__': unittest.main()
