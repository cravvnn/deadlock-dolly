"""Styled popup for ttk comboboxes; the native field and public widget API are retained."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from . import chrome, fonts
from .scale import ui_scale, px
from .. import ui_theme


def _rounded(canvas, x0, y0, x1, y1, radius, **kwargs):
    r = min(radius, max(0, (x1-x0)/2), max(0, (y1-y0)/2))
    return canvas.create_polygon(x0+r,y0,x1-r,y0,x1,y0,x1,y0+r,x1,y1-r,x1,y1,
                                 x1-r,y1,x0+r,y1,x0,y1,x0,y1-r,x0,y0+r,x0,y0,
                                 smooth=True, splinesteps=12, **kwargs)


class Popup:
    """Transient list with scaled rows and the same selected value as a ttk Combobox."""
    def __init__(self, combo):
        self.combo = combo
        self.root = combo._root()
        self.scale = ui_scale(combo)
        self.dp = lambda n: px(self.scale, n)
        self.values = tuple(combo.cget('values'))
        self.previous_grab = combo.grab_current()
        self.old_focus = combo.focus_get()
        self.index = max(0, combo.current())
        self.row = self.dp(28)
        self.pad = self.dp(4)
        self.height_rows = min(len(self.values), max(1, min(10, int(combo.cget('height')))))
        self.width = max(combo.winfo_width(), self.dp(80))
        self.height = self.height_rows*self.row + 2*self.pad + 2*self.dp(1)
        self.top = tk.Toplevel(combo)
        self.top.withdraw()
        self.top.overrideredirect(True)
        self.top.transient(combo.winfo_toplevel())
        self.top.configure(bg=ui_theme.TOKENS['edge'])
        self.top.bind('<Destroy>', self._destroyed, add='+')
        self.top.protocol('WM_DELETE_WINDOW', self.close)
        self.outer = tk.Canvas(self.top, bg=ui_theme.TOKENS['edge'], bd=0, highlightthickness=0)
        self.outer.pack(fill='both', expand=True)
        _rounded(self.outer, 0, 0, self.width, self.height, self.dp(6),
                 fill=ui_theme.TOKENS['panel'], outline=ui_theme.TOKENS['edge'], width=max(1,self.dp(1)))
        self.scroll = len(self.values)>self.height_rows
        self.scrollw = self.dp(8) if self.scroll else 0
        innerw = max(1,self.width-2*self.pad-self.scrollw-2*self.dp(1))
        self.list = tk.Canvas(self.top, bg=ui_theme.TOKENS['panel'], bd=0, highlightthickness=0,
                              takefocus=1, width=innerw, height=self.height_rows*self.row)
        self.list.place(x=self.pad+self.dp(1), y=self.pad+self.dp(1), width=innerw, height=self.height_rows*self.row)
        self.list.configure(scrollregion=(0,0,innerw,len(self.values)*self.row), yscrollincrement=self.row)
        self.font = fonts.font(combo, 'body')
        self._draw()
        if self.scroll:
            self.bar = ttk.Scrollbar(self.top, orient='vertical', command=self.list.yview, style='Card.Vertical.TScrollbar')
            self.bar.place(x=self.width-self.pad-self.dp(1)-self.scrollw, y=self.pad+self.dp(1),
                           width=self.scrollw, height=self.height_rows*self.row)
            self.list.configure(yscrollcommand=self.bar.set)
        self.list.bind('<Motion>', self._hover)
        self.list.bind('<ButtonRelease-1>', self._click)
        self.top.bind('<ButtonPress-1>', self._outside, add='+')
        self.list.bind('<MouseWheel>', self._wheel)
        self.list.bind('<Button-4>', lambda e: self._scroll(-1))
        self.list.bind('<Button-5>', lambda e: self._scroll(1))
        self.list.bind('<Up>', lambda e: self._move(-1))
        self.list.bind('<Down>', lambda e: self._move(1))
        self.list.bind('<Home>', lambda e: self._goto(0))
        self.list.bind('<End>', lambda e: self._goto(len(self.values)-1))
        self.list.bind('<Prior>', lambda e: self._move(-self.height_rows))
        self.list.bind('<Next>', lambda e: self._move(self.height_rows))
        self.list.bind('<Return>', self._choose)
        self.list.bind('<KP_Enter>', self._choose)
        self.list.bind('<Escape>', self._cancel)
        self.list.bind('<Tab>', self._tab)
        self.list.bind('<Shift-Tab>', self._backtab)
        self.list.bind('<KeyPress>', self._key, add='+')
        self.list.bind('<FocusOut>', self._focus_out, add='+')
        x = combo.winfo_rootx(); y = combo.winfo_rooty()+combo.winfo_height()+self.dp(4)
        left, top, right, bottom = chrome.work_area(combo.winfo_toplevel())
        if y+self.height > bottom: y=combo.winfo_rooty()-self.height-self.dp(4)
        x=max(left,min(x,right-self.width)); y=max(top,min(y,bottom-self.height))
        self.top.geometry(f'{self.width}x{self.height}{x:+d}{y:+d}')
        combo.state(["pressed"])
        self.top.deiconify(); self.top.lift()
        self.list.focus_set()
        self.top.grab_set()
        self._ensure_visible()

    def _draw(self):
        self.list.delete('all')
        width=max(1,self.width-2*self.pad-self.scrollw-2*self.dp(1))
        for i, text in enumerate(self.values):
            y=i*self.row
            if i == self.index:
                _rounded(self.list,0,y,width,y+self.row,self.dp(4),fill=ui_theme.TOKENS['selected'],outline='')
            self.list.create_text(self.dp(10),y+self.row/2,text=text,font=self.font,
                                  fill=ui_theme.TOKENS['text'],anchor='w')
    def _ensure_visible(self):
        if not self.scroll: return
        top=self.list.canvasy(0); bottom=top+self.height_rows*self.row
        y=self.index*self.row
        if y<top: self.list.yview_moveto(y/(len(self.values)*self.row))
        elif y+self.row>bottom: self.list.yview_moveto((y+self.row-self.height_rows*self.row)/(len(self.values)*self.row))
    def _goto(self, i):
        self.index=max(0,min(len(self.values)-1,i)); self._draw(); self._ensure_visible(); return 'break'
    def _move(self, amount): return self._goto(self.index+amount)
    def _hover(self,e):
        i=int(self.list.canvasy(e.y)//self.row)
        if 0 <= i < len(self.values) and i != self.index: self.index=i; self._draw()
    def _click(self,e):
        i=int(self.list.canvasy(e.y)//self.row)
        if 0 <= i < len(self.values): self.index=i; self._choose()
        return 'break'
    def _choose(self,e=None):
        try:
            self.combo.current(self.index)
            self.close()
            self.combo.event_generate('<<ComboboxSelected>>', when='tail')
        except tk.TclError: self.close()
        return 'break'
    def _cancel(self,e=None): self.close(); return 'break'
    def _scroll(self, amount): self.list.yview_scroll(amount,'units'); return 'break'
    def _wheel(self,e): return self._scroll(-1 if e.delta>0 else 1)
    def _key(self,e):
        if e.char and e.char.isprintable() and not (e.state & 0x4):
            prefix=e.char.casefold()
            for off in range(1,len(self.values)+1):
                i=(self.index+off)%len(self.values)
                if self.values[i].casefold().startswith(prefix): return self._goto(i)
        return None
    def _tab(self,e):
        target=self.combo.tk_focusNext(); self.close(); target.focus_set(); return 'break'
    def _backtab(self,e):
        target=self.combo.tk_focusPrev(); self.close(); target.focus_set(); return 'break'
    def _outside(self,e):
        x,y=e.x_root-self.top.winfo_rootx(),e.y_root-self.top.winfo_rooty()
        if not (0<=x<self.width and 0<=y<self.height): self.close(); return 'break'
    def _focus_out(self,e):
        def check():
            try:
                focus=self.top.focus_get()
                if focus not in (self.top,self.list,getattr(self,'bar',None)): self.close()
            except tk.TclError: pass
        try: self.top.after_idle(check)
        except tk.TclError: pass
    def _destroyed(self,e):
        if e.widget is self.top and getattr(self.root,'_dolly_combo_popup',None) is self:
            self.root._dolly_combo_popup=None
    def close(self):
        try:
            if not self.top.winfo_exists(): return
            self.combo.state(["!pressed"])
            self.top.grab_release(); self.top.destroy()
            if self.previous_grab and self.previous_grab.winfo_exists(): self.previous_grab.grab_set()
            if self.combo.winfo_exists(): self.combo.focus_set()
        except tk.TclError: pass


def install(root):
    """Replace the Tk list only; on unsupported states fall back to Tk's native popup."""
    root=root._root()
    if getattr(root,'_dolly_dropdown_installed',False): return
    root.tk.call('auto_load','ttk::combobox::Post')
    if not root.tk.call('info','commands','ttk::combobox::Post'): return
    root.tk.call('rename','ttk::combobox::Post','ttk::combobox::DollyNativePost')
    def post(path):
        try:
            combo=root.nametowidget(path)
            if combo.instate(('disabled',)): return '1'
            old=getattr(root,'_dolly_combo_popup',None)
            if old: old.close()
            cmd=combo.cget('postcommand')
            if cmd: root.tk.call('eval',cmd)
            if not combo.cget('values'): return '0'
            root._dolly_combo_popup=Popup(combo)
            return '1'
        except Exception:
            return '0'
    root.createcommand('::dolly_combobox_post',post)
    root.tk.eval('proc ttk::combobox::Post {w} {if {![::dolly_combobox_post $w]} {ttk::combobox::DollyNativePost $w}}')
    root._dolly_dropdown_installed=True
