"""可复用界面零件。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import dpi
from . import theme as t
from .round import RADIUS_SM, draw_round_rect


class StatusChip(tk.Frame):
    """右上角状态指示。"""

    def __init__(self, parent):
        super().__init__(parent, bg=parent.cget("bg"))
        self._box = tk.Frame(
            self, bg=t.CHIP_OFF_BG, highlightbackground=t.BORDER, highlightthickness=1, bd=0,
        )
        self._box.pack()
        inner = tk.Frame(self._box, bg=t.CHIP_OFF_BG)
        inner.pack(padx=dpi.px(12), pady=dpi.px(6))
        self.dot = tk.Canvas(
            inner, width=dpi.px(8), height=dpi.px(8),
            bg=t.CHIP_OFF_BG, highlightthickness=0, bd=0,
        )
        self.dot.pack(side="left", padx=(0, dpi.px(6)))
        self.label = tk.Label(
            inner, text="未开始", bg=t.CHIP_OFF_BG, fg=t.MUTED, font=t.FONT_BOLD,
            wraplength=dpi.px(220), justify="right",
        )
        self.label.pack(side="left")
        self._inner = inner
        self.set_idle("未开始")

    def _apply(self, bg: str, dot_color: str, text: str, fg: str):
        self._box.configure(bg=bg, highlightbackground=bg)
        self._inner.configure(bg=bg)
        self.dot.configure(bg=bg)
        self.label.configure(bg=bg, text=text, fg=fg)
        self.dot.delete("all")
        r = dpi.px(3)
        self.dot.create_oval(1, 1, r * 2 + 1, r * 2 + 1, fill=dot_color, outline=dot_color)

    def set_idle(self, text: str = "未开始"):
        self._apply(t.CHIP_OFF_BG, "#b0b7c0", text, t.MUTED)

    def set_ok(self, text: str):
        self._apply(t.CHIP_OK_BG, t.OK, text, t.TEXT_SECONDARY)

    def set_busy(self, text: str):
        self._apply(t.CHIP_BUSY_BG, t.WARN, text, t.TEXT_SECONDARY)

    def set_err(self, text: str):
        self._apply(t.DANGER_SOFT, t.DANGER, text, t.TEXT_SECONDARY)


class NavItem(tk.Frame):
    """侧栏导航项：圆角底色 + 图标 + 文字。"""

    def __init__(self, parent, key: str, icon: str, label: str, command):
        super().__init__(parent, bg=t.SIDEBAR, cursor="hand2")
        self.key = key
        self._command = command
        self._active = False
        self._bg = t.SIDEBAR
        self._fg = t.TEXT_SECONDARY

        self.canvas = tk.Canvas(self, bg=t.SIDEBAR, highlightthickness=0, bd=0, height=dpi.px(38))
        self.canvas.pack(fill="x", padx=dpi.px(4))
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Enter>", self._hover_in)
        self.canvas.bind("<Leave>", self._hover_out)
        self.canvas.bind("<Configure>", self._draw)

        self.icon_lbl = tk.Label(
            self.canvas, text=icon, bg=t.SIDEBAR, fg=t.MUTED,
            font=t.font(12), width=2, anchor="center",
        )
        self.text_lbl = tk.Label(
            self.canvas, text=label, bg=t.SIDEBAR, fg=t.TEXT_SECONDARY,
            font=t.FONT, anchor="w",
        )
        self.icon_lbl.bind("<Button-1>", self._click)
        self.text_lbl.bind("<Button-1>", self._click)
        self.icon_lbl.bind("<Enter>", self._hover_in)
        self.text_lbl.bind("<Enter>", self._hover_in)
        self.icon_lbl.bind("<Leave>", self._hover_out)
        self.text_lbl.bind("<Leave>", self._hover_out)

    def _click(self, _e=None):
        self._command(self.key)

    def _hover_in(self, _e=None):
        if not self._active:
            self._paint(t.SIDE_HOVER, t.TEXT)

    def _hover_out(self, _e=None):
        if not self._active:
            self._paint(t.SIDEBAR, t.TEXT_SECONDARY)

    def _paint(self, bg: str, fg: str):
        self._bg = bg
        self._fg = fg
        self.icon_lbl.configure(bg=bg, fg=t.BLUE if self._active else t.MUTED)
        self.text_lbl.configure(bg=bg, fg=fg)
        self.canvas.configure(bg=bg)
        self._draw()

    def _draw(self, _e=None):
        self.canvas.delete("shape")
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w <= 2:
            return
        r = RADIUS_SM
        draw_round_rect(self.canvas, 2, 2, w - 2, h - 2, r, fill=self._bg, outline=self._bg)
        if self._active:
            self.canvas.create_rectangle(2, 8, 4, h - 8, fill=t.SIDE_INDICATOR, outline="", tags="shape")
        self.canvas.create_window(dpi.px(14), h // 2, window=self.icon_lbl, anchor="w")
        self.canvas.create_window(dpi.px(40), h // 2, window=self.text_lbl, anchor="w")

    def set_active(self, active: bool):
        self._active = active
        if active:
            self._paint(t.SIDE_ACTIVE_BG, t.TEXT)
            self.icon_lbl.configure(fg=t.BLUE)
        else:
            self._paint(t.SIDEBAR, t.TEXT_SECONDARY)
            self.icon_lbl.configure(fg=t.MUTED)


class RoutePill(tk.Frame):
    """监控页航线标签。"""

    def __init__(self, parent, route_text: str, meta_text: str, platforms: str):
        super().__init__(parent, bg=t.WHITE)
        body = tk.Frame(
            self, bg=t.SURFACE, highlightbackground=t.BORDER, highlightthickness=1, bd=0,
        )
        body.pack(fill="x", pady=dpi.px(4))
        row = tk.Frame(body, bg=t.SURFACE)
        row.pack(fill="x", padx=dpi.px(12), pady=dpi.px(10))

        left = tk.Frame(row, bg=t.SURFACE)
        left.pack(side="left", fill="x", expand=True)
        tk.Label(
            left, text=route_text, bg=t.SURFACE, fg=t.TEXT,
            font=t.FONT_BOLD, anchor="w",
        ).pack(anchor="w")
        tk.Label(
            left, text=meta_text, bg=t.SURFACE, fg=t.MUTED,
            font=t.FONT_SMALL, anchor="w",
        ).pack(anchor="w", pady=(dpi.px(2), 0))

        tag = tk.Frame(
            row, bg=t.WHITE, highlightbackground=t.BORDER, highlightthickness=1, bd=0,
        )
        tag.pack(side="right", padx=(dpi.px(8), 0))
        tk.Label(
            tag, text=platforms, bg=t.WHITE, fg=t.TEXT_SECONDARY,
            font=t.FONT_SMALL, padx=dpi.px(8), pady=dpi.px(3),
        ).pack()


class OptionCard(tk.Frame):
    """二选一卡片：选中用蓝底，未选用浅灰。"""

    def __init__(self, parent, title: str, hint: str, command=None):
        super().__init__(
            parent, bg=t.SURFACE, highlightbackground=t.BORDER, highlightthickness=1,
            bd=0, cursor="hand2",
        )
        self._command = command
        self._inner = tk.Frame(self, bg=t.SURFACE)
        self._inner.pack(fill="both", expand=True, padx=dpi.px(12), pady=dpi.px(10))
        self._title = tk.Label(
            self._inner, text=title, bg=t.SURFACE, fg=t.TEXT, font=t.FONT_BOLD, anchor="w",
        )
        self._title.pack(anchor="w")
        self._hint = tk.Label(
            self._inner, text=hint, bg=t.SURFACE, fg=t.MUTED, font=t.FONT_SMALL,
            anchor="w", justify="left", wraplength=dpi.px(240),
        )
        self._hint.pack(anchor="w", pady=(dpi.px(2), 0))
        for w in (self, self._inner, self._title, self._hint):
            w.bind("<Button-1>", self._click)

    def _click(self, _e=None):
        if self._command:
            self._command()

    def set_selected(self, on: bool):
        bg = t.BLUE_SOFT if on else t.SURFACE
        border = t.BLUE if on else t.BORDER
        title_fg = t.TEXT if on else t.TEXT_SECONDARY
        self.configure(bg=bg, highlightbackground=border)
        self._inner.configure(bg=bg)
        self._title.configure(bg=bg, fg=title_fg)
        self._hint.configure(bg=bg, fg=t.TEXT_SECONDARY if on else t.MUTED)


class EmptyState(tk.Frame):
    def __init__(self, parent, title: str, hint: str, icon: str = "✈"):
        super().__init__(parent, bg=t.WHITE)
        wrap = tk.Frame(
            self, bg=t.SURFACE, highlightbackground=t.BORDER, highlightthickness=1, bd=0,
        )
        wrap.pack(expand=True, fill="both", padx=dpi.px(40), pady=dpi.px(40))
        inner = tk.Frame(wrap, bg=t.SURFACE)
        inner.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(
            inner, text=icon, bg=t.SURFACE, fg="#d5dae0", font=t.font(28),
        ).pack(pady=(0, dpi.px(8)))
        tk.Label(inner, text=title, bg=t.SURFACE, fg=t.TEXT, font=t.FONT_BOLD).pack()
        tk.Label(
            inner, text=hint, bg=t.SURFACE, fg=t.MUTED, font=t.FONT_SMALL,
            justify="center", wraplength=dpi.px(360),
        ).pack(pady=(dpi.px(6), 0))


class Field(tk.Frame):
    """标签在上、说明在下的表单行。"""

    def __init__(self, parent, label: str, hint: str = ""):
        super().__init__(parent, bg=t.WHITE)
        tk.Label(self, text=label, bg=t.WHITE, fg=t.TEXT, font=t.FONT_BOLD, anchor="w").pack(fill="x")
        if hint:
            tk.Label(self, text=hint, bg=t.WHITE, fg=t.MUTED, font=t.FONT_SMALL, anchor="w").pack(fill="x")
        self.body = tk.Frame(self, bg=t.WHITE)
        self.body.pack(fill="x", pady=(dpi.px(4), dpi.px(10)))


class ScrollBody(tk.Frame):
    """设置等长页用的滚动容器。"""

    def __init__(self, parent, bg=None):
        super().__init__(parent, bg=bg or t.BG)
        self.canvas = tk.Canvas(self, bg=bg or t.BG, highlightthickness=0, bd=0)
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg or t.BG)
        self.inner.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.canvas.bind("<Configure>", self._on_canvas)
        self.inner.bind("<Enter>", self._bind_wheel)
        self.inner.bind("<Leave>", self._unbind_wheel)

    def _on_canvas(self, event):
        self.canvas.itemconfigure(self._win, width=event.width)

    def _on_wheel(self, event):
        delta = -1 if event.delta > 0 else 1
        if event.delta == 0:
            delta = -1 if getattr(event, "num", 0) == 4 else 1
        self.canvas.yview_scroll(delta, "units")

    def _bind_wheel(self, _e=None):
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        self.canvas.bind_all("<Button-4>", self._on_wheel)
        self.canvas.bind_all("<Button-5>", self._on_wheel)

    def _unbind_wheel(self, _e=None):
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def scroll_to(self, widget):
        self.update_idletasks()
        y = 0
        w = widget
        while w is not None and w is not self.inner:
            try:
                y += int(w.winfo_y())
            except tk.TclError:
                break
            w = getattr(w, "master", None)
        total = max(1, self.inner.winfo_height())
        self.canvas.yview_moveto(min(1.0, max(0.0, y / total)))


def tree(parent, columns: dict):
    body = tk.Frame(parent, bg=t.WHITE, highlightbackground=t.BORDER, highlightthickness=1, bd=0)
    inner = tk.Frame(body, bg=t.WHITE)
    inner.pack(fill="both", expand=True)
    tv = ttk.Treeview(inner, columns=tuple(columns), show="headings", selectmode="browse")
    keys = list(columns)
    for i, (key, (title, width)) in enumerate(columns.items()):
        tv.heading(key, text=title)
        stretch = i == len(keys) - 1
        tv.column(
            key, width=dpi.px(width), minwidth=dpi.px(max(48, width // 2)),
            anchor="w", stretch=stretch,
        )
    vsb = ttk.Scrollbar(inner, orient="vertical", command=tv.yview)
    tv.configure(yscrollcommand=vsb.set)
    tv.pack(side="left", fill="both", expand=True)
    vsb.pack(side="right", fill="y")
    return body, tv


class PrettySelect(tk.Frame):
    """浅色下拉：显示中文标签，取值用内部 key。"""

    def __init__(self, parent, options: list[tuple[str, str]], value: str = "", width: int = 10):
        super().__init__(parent, bg=parent.cget("bg") if hasattr(parent, "cget") else t.WHITE)
        self.options = list(options)
        self._popup = None
        labels = {k: lab for k, lab in self.options}
        initial = value if value in labels else (self.options[0][0] if self.options else "")
        self.var = tk.StringVar(value=initial)

        self._box = tk.Frame(
            self, bg=t.SURFACE, highlightbackground=t.BORDER, highlightthickness=1, bd=0, cursor="hand2",
        )
        self._box.pack()
        inner = tk.Frame(self._box, bg=t.SURFACE)
        inner.pack(fill="x", padx=dpi.px(12), pady=dpi.px(6))
        self._label = tk.Label(
            inner, text=labels.get(initial, initial), bg=t.SURFACE, fg=t.TEXT,
            font=t.FONT, width=width, anchor="w",
        )
        self._label.pack(side="left")
        self._caret = tk.Label(inner, text="▾", bg=t.SURFACE, fg=t.MUTED, font=t.FONT_SMALL)
        self._caret.pack(side="left", padx=(dpi.px(8), 0))
        for w in (self._box, inner, self._label, self._caret):
            w.bind("<Button-1>", self._toggle)
            w.bind("<Enter>", self._hover_in)
            w.bind("<Leave>", self._hover_out)
        self.bind("<Destroy>", lambda _e: self._hide())

    def get(self) -> str:
        return self.var.get()

    def _hover_in(self, _e=None):
        for w in (self._box, self._label, self._caret):
            try:
                w.configure(bg=t.BLUE_SOFT)
            except tk.TclError:
                pass
        self._label.configure(fg=t.TEXT)
        self._box.configure(highlightbackground=t.BORDER_FOCUS)

    def _hover_out(self, _e=None):
        if self._popup:
            return
        for w in (self._box, self._label, self._caret):
            try:
                w.configure(bg=t.SURFACE)
            except tk.TclError:
                pass
        try:
            self._box.configure(highlightbackground=t.BORDER)
        except tk.TclError:
            pass

    def _toggle(self, _e=None):
        if self._popup:
            self._hide()
            return
        self._show()

    def _show(self):
        self._hide()
        popup = tk.Toplevel(self)
        popup.wm_overrideredirect(True)
        popup.configure(bg=t.BORDER)
        self._popup = popup
        self.update_idletasks()
        x = self._box.winfo_rootx()
        y = self._box.winfo_rooty() + self._box.winfo_height() + 2
        w = max(self._box.winfo_width(), dpi.px(120))
        shell = tk.Frame(popup, bg=t.WHITE, highlightbackground=t.BORDER, highlightthickness=1, bd=0)
        shell.pack(fill="both", expand=True)
        current = self.var.get()
        for key, lab in self.options:
            row = tk.Frame(shell, bg=t.BLUE_SOFT if key == current else t.WHITE, cursor="hand2")
            row.pack(fill="x")
            lbl = tk.Label(
                row, text=lab, bg=row.cget("bg"), fg=t.BLUE if key == current else t.TEXT,
                font=t.FONT, anchor="w", padx=dpi.px(12), pady=dpi.px(7),
            )
            lbl.pack(fill="x")

            def _pick(k=key, n=lab):
                self.var.set(k)
                self._label.configure(text=n)
                self._hide()

            def _in(e, r=row, l=lbl, k=key):
                r.configure(bg=t.BLUE_SOFT)
                l.configure(bg=t.BLUE_SOFT, fg=t.TEXT)

            def _out(e, r=row, l=lbl, k=key):
                bg = t.BLUE_SOFT if k == self.var.get() else t.WHITE
                r.configure(bg=bg)
                l.configure(bg=bg, fg=t.BLUE if k == self.var.get() else t.TEXT)

            for wdg in (row, lbl):
                wdg.bind("<Button-1>", lambda e, fn=_pick: fn())
                wdg.bind("<Enter>", _in)
                wdg.bind("<Leave>", _out)
        popup.update_idletasks()
        h = shell.winfo_reqheight()
        popup.geometry(f"{w}x{h}+{x}+{y}")
        popup.bind("<FocusOut>", lambda _e: self.after(80, self._hide))
        popup.bind("<Escape>", lambda _e: self._hide())
        try:
            popup.focus_set()
        except tk.TclError:
            pass

    def _hide(self):
        if self._popup:
            try:
                self._popup.destroy()
            except tk.TclError:
                pass
        self._popup = None
        try:
            if self.winfo_exists():
                self._hover_out()
        except tk.TclError:
            pass
