"""圆角绘制与容器（Canvas 实现，适配 tkinter）。"""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont

from . import dpi

RADIUS_SM = dpi.px(6)
RADIUS_MD = dpi.px(10)
RADIUS_LG = dpi.px(14)
RADIUS_PILL = dpi.px(18)


def _rounded_points(x1: float, y1: float, x2: float, y2: float, r: float) -> list[float]:
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    return [
        x1 + r, y1,
        x2 - r, y1,
        x2, y1,
        x2, y1 + r,
        x2, y2 - r,
        x2, y2,
        x2 - r, y2,
        x1 + r, y2,
        x1, y2,
        x1, y2 - r,
        x1, y1 + r,
        x1, y1,
    ]


def draw_round_rect(
    canvas: tk.Canvas,
    x1: float, y1: float, x2: float, y2: float,
    r: float,
    fill: str = "",
    outline: str = "",
    width: int = 1,
    tags: str = "shape",
) -> None:
    if x2 - x1 < 2 or y2 - y1 < 2:
        return
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    if outline and width > 0:
        canvas.create_polygon(
            _rounded_points(x1, y1, x2, y2, r),
            smooth=True, fill=outline, outline="", tags=tags,
        )
        inset = width
        ri = max(0, r - inset)
        canvas.create_polygon(
            _rounded_points(x1 + inset, y1 + inset, x2 - inset, y2 - inset, ri),
            smooth=True, fill=fill, outline="", tags=tags,
        )
    else:
        canvas.create_polygon(
            _rounded_points(x1, y1, x2, y2, r),
            smooth=True, fill=fill, outline="", tags=tags,
        )


def _parent_bg(parent) -> str:
    try:
        return parent.cget("bg")
    except Exception:
        return "#f6f7f9"


def _forward_pack(host: tk.Widget, body: tk.Widget):
    def pack(**opts):
        host.pack(**opts)

    def pack_forget():
        host.pack_forget()

    def place(**opts):
        host.place(**opts)

    def grid(**opts):
        host.grid(**opts)

    body.pack = pack  # type: ignore[method-assign]
    body.pack_forget = pack_forget  # type: ignore[method-assign]
    body.place = place  # type: ignore[method-assign]
    body.grid = grid  # type: ignore[method-assign]
    body._round_host = host  # type: ignore[attr-defined]


class RoundedPanel(tk.Frame):
    """圆角面板，内容放在 `.inner`。"""

    def __init__(
        self,
        parent,
        radius: int | None = None,
        fill: str = "#ffffff",
        outline: str = "#e8eaee",
        outline_width: int = 1,
        accent_color: str | None = None,
        pad: int | None = None,
        bg: str | None = None,
        expandable: bool = False,
    ):
        super().__init__(parent, bg=bg or _parent_bg(parent))
        self._radius = radius or RADIUS_MD
        self._fill = fill
        self._outline = outline
        self._ow = outline_width
        self._accent = accent_color
        self._pad = pad if pad is not None else dpi.px(4)
        self._expandable = expandable

        self.canvas = tk.Canvas(self, bg=self.cget("bg"), highlightthickness=0, bd=0)
        if expandable:
            self.canvas.pack(fill="both", expand=True)
        else:
            self.canvas.pack()
        self.inner = tk.Frame(self.canvas, bg=fill)
        self._win = self.canvas.create_window(self._pad, self._pad, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)

    def _on_inner(self, event):
        ch = event.height + 2 * self._pad
        cw = event.width + 2 * self._pad
        self.canvas.configure(height=ch, width=max(cw, 1))
        self.canvas.configure(scrollregion=(0, 0, max(cw, self.canvas.winfo_width()), ch))
        self._redraw(max(cw, self.canvas.winfo_width()), ch)

    def _on_canvas(self, event):
        inner_w = max(1, event.width - 2 * self._pad)
        self.canvas.itemconfigure(self._win, width=inner_w)
        if self._expandable and event.height > 2 * self._pad + 8:
            inner_h = max(1, event.height - 2 * self._pad)
            self.canvas.itemconfigure(self._win, height=inner_h)
        self._redraw(event.width, max(event.height, self.canvas.winfo_height()))

    def _redraw(self, w: int, h: int):
        if w <= 2:
            return
        h = max(h, 2)
        self.canvas.delete("shape")
        r = min(self._radius, w // 2, h // 2)
        draw_round_rect(
            self.canvas, 1, 1, w - 1, h - 1, r,
            fill=self._fill, outline=self._outline, width=self._ow, tags="shape",
        )
        if self._accent:
            bar_h = dpi.px(3)
            self.canvas.create_rectangle(
                r, 1, w - r, bar_h, fill=self._accent, outline="", tags="shape",
            )


def rounded_body(
    parent,
    radius: int | None = None,
    fill: str = "#ffffff",
    outline: str = "#e8eaee",
    outline_width: int = 1,
    accent_color: str | None = None,
    pad: int | None = None,
    bg: str | None = None,
    expandable: bool = False,
) -> tk.Frame:
    """创建圆角面板并返回 `.inner`（pack/place 会作用在外层）。"""
    panel = RoundedPanel(
        parent, radius=radius, fill=fill, outline=outline,
        outline_width=outline_width, accent_color=accent_color, pad=pad, bg=bg,
        expandable=expandable,
    )
    _forward_pack(panel, panel.inner)
    return panel.inner


class RoundedButton(tk.Canvas):
    """圆角按钮。"""

    def __init__(
        self,
        parent,
        text: str,
        command,
        bg: str,
        fg: str,
        hover_bg: str,
        font=None,
        padx: int | None = None,
        pady: int | None = None,
        radius: int | None = None,
        outline: str | None = None,
        outline_width: int = 0,
        width_chars: int = 0,
        disabled_bg: str = "#e8eaee",
        disabled_fg: str = "#c5cad2",
        hover_fg: str | None = None,
    ):
        super().__init__(parent, highlightthickness=0, bd=0, bg=_parent_bg(parent), cursor="hand2")
        self._text = text
        self._command = command
        self._bg = bg
        self._hover_bg = hover_bg
        self._fg = fg
        self._font = font or ("Microsoft YaHei UI", 10)
        self._padx = padx if padx is not None else dpi.px(16)
        self._pady = pady if pady is not None else dpi.px(8)
        self._radius = radius or RADIUS_SM
        self._outline = outline
        self._outline_w = outline_width
        self._disabled_bg = disabled_bg
        self._disabled_fg = disabled_fg
        self._hover_fg = hover_fg
        self._state = "normal"
        self._hovering = False
        self._width_chars = width_chars

        self.bind("<Configure>", self._redraw)
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self._fit()
        self._redraw()

    def _fit(self):
        f = tkfont.Font(font=self._font)
        tw = f.measure(self._text)
        if self._width_chars > 0:
            cw = f.measure("0") * self._width_chars
            tw = max(tw, cw)
        th = f.metrics("linespace")
        # 直接调父类 configure，避免触发本类 configure → _redraw → _fit 的死循环
        super().configure(width=tw + 2 * self._padx, height=th + 2 * self._pady)

    def _fill_color(self) -> str:
        if self._state == "disabled":
            return self._disabled_bg
        return self._hover_bg if self._hovering else self._bg

    def _text_color(self) -> str:
        if self._state == "disabled":
            return self._disabled_fg
        if self._hovering and self._hover_fg:
            return self._hover_fg
        return self._fg

    def _redraw(self, _event=None):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 2:
            self._fit()
            w, h = self.winfo_width(), self.winfo_height()
        if w <= 2:
            return
        r = min(self._radius, w // 2, h // 2)
        ol = self._outline if self._outline is not None else self._fill_color()
        draw_round_rect(
            self, 1, 1, w - 1, h - 1, r,
            fill=self._fill_color(), outline=ol, width=self._outline_w,
        )
        self.create_text(w // 2, h // 2, text=self._text, fill=self._text_color(), font=self._font)

    def _click(self, _event=None):
        if self._state == "normal" and self._command:
            self._command()

    def _enter(self, _event=None):
        if self._state == "normal":
            self._hovering = True
            self._redraw()

    def _leave(self, _event=None):
        self._hovering = False
        self._redraw()

    def configure(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        if "state" in kw:
            self._state = kw.pop("state")
        if "text" in kw:
            self._text = kw.pop("text")
            self._fit()
        if "bg" in kw:
            self._bg = kw.pop("bg")
        if "fg" in kw:
            self._fg = kw.pop("fg")
        if kw:
            super().configure(kw)
        self._redraw()

    config = configure
