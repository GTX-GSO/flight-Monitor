"""日期选择：日历弹层 + 已选日期标签。"""
from __future__ import annotations

import calendar
from datetime import date, datetime
from typing import Optional

import tkinter as tk

from . import dpi
from . import theme as t

_WEEK = ("日", "一", "二", "三", "四", "五", "六")
_MONTHS = ("一月", "二月", "三月", "四月", "五月", "六月",
           "七月", "八月", "九月", "十月", "十一月", "十二月")


def _parse(s: str) -> Optional[date]:
    try:
        return datetime.strptime(str(s).strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


class DateListPicker(tk.Frame):
    """点选日历添加日期。multi=True 可多选；往返用单选。"""

    def __init__(self, parent, *, min_today: bool = True, multi: bool = True, on_change=None):
        super().__init__(parent, bg=t.WHITE)
        self._min_today = min_today
        self._multi = multi
        self._min_extra: Optional[date] = None
        self._on_change = on_change
        self._dates: list[str] = []
        self._popup: Optional[tk.Toplevel] = None
        self._view = date.today().replace(day=1)

        row = tk.Frame(self, bg=t.WHITE)
        row.pack(fill="x")
        btn = tk.Frame(
            row, bg=t.SURFACE, highlightbackground=t.BORDER, highlightthickness=1,
            bd=0, cursor="hand2",
        )
        btn.pack(side="left")
        inner = tk.Frame(btn, bg=t.SURFACE)
        inner.pack(padx=dpi.px(12), pady=dpi.px(6))
        tk.Label(inner, text="选择日期", bg=t.SURFACE, fg=t.BLUE, font=t.FONT).pack(side="left")
        tk.Label(inner, text="▾", bg=t.SURFACE, fg=t.MUTED, font=t.FONT_SMALL).pack(side="left", padx=(dpi.px(8), 0))
        for w in (btn, inner, *inner.winfo_children()):
            w.bind("<Button-1>", lambda _e: self._toggle())

        self.chips = tk.Frame(self, bg=t.WHITE)
        self.chips.pack(fill="x", pady=(dpi.px(6), 0))
        self._empty = tk.Label(
            self.chips, text="还没选日期", bg=t.WHITE, fg=t.MUTED, font=t.FONT_SMALL, anchor="w",
        )
        self._empty.pack(anchor="w")
        self.bind("<Destroy>", lambda _e: self._hide())

    def get(self) -> list[str]:
        return list(self._dates)

    def _effective_min(self) -> date:
        parts = []
        if self._min_today:
            parts.append(date.today())
        if self._min_extra:
            parts.append(self._min_extra)
        return max(parts) if parts else date(2000, 1, 1)

    def _fire(self):
        if self._on_change:
            self._on_change(self.get())

    def set_min_date(self, value) -> None:
        if isinstance(value, date):
            self._min_extra = value
        else:
            self._min_extra = _parse(value) if value else None
        floor = self._effective_min()
        kept = []
        for iso in self._dates:
            d = _parse(iso)
            if d and d >= floor:
                kept.append(iso)
        if kept != self._dates:
            self._dates = kept
            self._render_chips()
        floor_month = floor.replace(day=1)
        if self._view < floor_month:
            self._view = floor_month
        if self._popup is not None:
            self._draw_cal()

    def set(self, values) -> None:
        floor = self._effective_min()
        out = []
        seen = set()
        for raw in values or []:
            d = _parse(raw)
            if not d or d < floor:
                continue
            s = d.isoformat()
            if s in seen:
                continue
            seen.add(s)
            out.append(s)
        out.sort()
        if not self._multi and out:
            out = out[:1]
        self._dates = out
        self._render_chips()
        self._fire()

    def set_multi(self, multi: bool) -> None:
        self._multi = bool(multi)
        if not self._multi and len(self._dates) > 1:
            self._dates = self._dates[:1]
            self._render_chips()
            self._fire()
            if self._popup is not None:
                self._draw_cal()

    def clear(self) -> None:
        self.set([])

    def _toggle(self):
        if self._popup:
            self._hide()
        else:
            self._show()

    def _hide(self):
        if self._popup:
            try:
                self._popup.destroy()
            except tk.TclError:
                pass
        self._popup = None

    def _show(self):
        self._hide()
        floor = self._effective_min()
        chosen = _parse(self._dates[0]) if self._dates else None
        self._view = (chosen or floor).replace(day=1)
        popup = tk.Toplevel(self)
        popup.wm_overrideredirect(True)
        popup.configure(bg=t.BORDER)
        self._popup = popup
        self.update_idletasks()
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_children()[0].winfo_height() + 4
        self._cal_host = tk.Frame(popup, bg=t.WHITE, highlightbackground=t.BORDER, highlightthickness=1)
        self._cal_host.pack()
        self._draw_cal()
        popup.update_idletasks()
        popup.geometry(f"+{x}+{y}")
        popup.bind("<FocusOut>", lambda _e: self.after(120, self._maybe_hide))
        popup.bind("<Escape>", lambda _e: self._hide())
        try:
            popup.focus_set()
        except tk.TclError:
            pass

    def _maybe_hide(self):
        if not self._popup:
            return
        try:
            w = self.winfo_containing(self.winfo_pointerx(), self.winfo_pointery())
        except tk.TclError:
            w = None
        if w is None:
            self._hide()
            return
        cur = w
        while cur is not None:
            if cur is self._popup:
                return
            cur = getattr(cur, "master", None)
        self._hide()

    def _draw_cal(self):
        host = self._cal_host
        for w in host.winfo_children():
            w.destroy()
        pad = tk.Frame(host, bg=t.WHITE)
        pad.pack(padx=dpi.px(10), pady=dpi.px(10))

        head = tk.Frame(pad, bg=t.WHITE)
        head.pack(fill="x", pady=(0, dpi.px(8)))
        def nav(delta_m: int):
            y, m = self._view.year, self._view.month + delta_m
            while m < 1:
                m += 12
                y -= 1
            while m > 12:
                m -= 12
                y += 1
            self._view = date(y, m, 1)
            self._draw_cal()

        tk.Label(head, text="‹", bg=t.WHITE, fg=t.BLUE, font=t.FONT_BOLD, cursor="hand2", width=2).pack(side="left")
        head.winfo_children()[-1].bind("<Button-1>", lambda _e: nav(-1))
        tk.Label(
            head, text=f"{self._view.year}  {_MONTHS[self._view.month - 1]}",
            bg=t.WHITE, fg=t.TEXT, font=t.FONT_BOLD,
        ).pack(side="left", expand=True)
        tk.Label(head, text="›", bg=t.WHITE, fg=t.BLUE, font=t.FONT_BOLD, cursor="hand2", width=2).pack(side="right")
        head.winfo_children()[-1].bind("<Button-1>", lambda _e: nav(1))

        grid = tk.Frame(pad, bg=t.WHITE)
        grid.pack()
        for i, name in enumerate(_WEEK):
            tk.Label(grid, text=name, bg=t.WHITE, fg=t.MUTED, font=t.FONT_SMALL, width=4).grid(row=0, column=i)

        cal = calendar.Calendar(firstweekday=6)
        weeks = cal.monthdatescalendar(self._view.year, self._view.month)
        today = date.today()
        min_d = self._effective_min()
        selected = set(self._dates)

        for r, week in enumerate(weeks, start=1):
            for c, day in enumerate(week):
                iso = day.isoformat()
                in_month = day.month == self._view.month
                disabled = day < min_d
                is_sel = iso in selected
                is_today = day == today
                if disabled:
                    bg, fg = t.WHITE, "#d0d4da"
                elif is_sel:
                    bg, fg = t.BLUE, t.WHITE
                elif is_today:
                    bg, fg = t.BLUE_SOFT, t.BLUE
                elif in_month:
                    bg, fg = t.WHITE, t.TEXT
                else:
                    bg, fg = t.WHITE, "#c5cad2"
                cell = tk.Label(
                    grid, text=str(day.day), bg=bg, fg=fg, font=t.FONT_SMALL,
                    width=4, pady=dpi.px(4),
                    cursor="hand2" if in_month and not disabled else "arrow",
                )
                cell.grid(row=r, column=c, padx=1, pady=1)
                if in_month and not disabled:
                    cell.bind("<Button-1>", lambda _e, s=iso: self._toggle_day(s))
                    if not is_sel:
                        cell.bind("<Enter>", lambda _e, w=cell: w.configure(bg=t.BLUE_SOFT))
                        cell.bind("<Leave>", lambda _e, w=cell, b=bg: w.configure(bg=b))

        if self._multi:
            tk.Label(
                pad, text="可多选", bg=t.WHITE, fg=t.MUTED, font=t.FONT_SMALL,
            ).pack(anchor="w", pady=(dpi.px(8), 0))

    def _toggle_day(self, iso: str):
        d = _parse(iso)
        if not d or d < self._effective_min():
            return
        if iso in self._dates:
            self._dates = [x for x in self._dates if x != iso]
            picked = False
        elif self._multi:
            self._dates.append(iso)
            self._dates.sort()
            picked = True
        else:
            self._dates = [iso]
            picked = True
        self._render_chips()
        self._fire()
        if self._popup is not None:
            if picked and not self._multi:
                self._hide()
            else:
                self._draw_cal()

    def _render_chips(self):
        for w in self.chips.winfo_children():
            w.destroy()
        if not self._dates:
            self._empty = tk.Label(
                self.chips, text="还没选日期", bg=t.WHITE, fg=t.MUTED, font=t.FONT_SMALL, anchor="w",
            )
            self._empty.pack(anchor="w")
            return
        wrap = tk.Frame(self.chips, bg=t.WHITE)
        wrap.pack(fill="x")
        for iso in self._dates:
            chip = tk.Frame(
                wrap, bg=t.BLUE_SOFT, highlightbackground=t.BORDER, highlightthickness=1, bd=0,
            )
            chip.pack(side="left", padx=(0, dpi.px(6)), pady=(0, dpi.px(4)))
            tk.Label(chip, text=iso, bg=t.BLUE_SOFT, fg=t.TEXT, font=t.FONT_SMALL).pack(
                side="left", padx=(dpi.px(8), dpi.px(4)), pady=dpi.px(3),
            )
            x = tk.Label(chip, text="×", bg=t.BLUE_SOFT, fg=t.MUTED, font=t.FONT_SMALL, cursor="hand2")
            x.pack(side="left", padx=(0, dpi.px(6)))
            x.bind("<Button-1>", lambda _e, s=iso: self._toggle_day(s))
