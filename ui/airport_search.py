"""机场/城市自动补全输入框（防抖 + 下拉联想）。"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from core.airport_lookup import AirportHit, resolve_iata, search_airports, search_local

from . import dpi
from . import theme as t


class AirportAutocomplete(tk.Frame):
    """输入中文地名，异步联想并回填城市名 + IATA 三字码。"""

    def __init__(
        self,
        parent,
        name_var: tk.StringVar,
        code_var: tk.StringVar,
        placeholder: str = "",
        on_select: Optional[Callable[[], None]] = None,
    ):
        super().__init__(parent, bg=t.WHITE)
        self.name_var = name_var
        self.code_var = code_var
        self._on_select = on_select
        self._debounce_ms = 320
        self._after_id: Optional[str] = None
        self._seq = 0
        self._popup: Optional[tk.Toplevel] = None
        self._listbox: Optional[tk.Listbox] = None
        self._hits: list[AirportHit] = []
        self._selecting = False

        row = tk.Frame(self, bg=t.WHITE)
        row.pack(fill="x")
        self.entry = ttk.Entry(row, textvariable=name_var)
        self.entry.pack(side="left", fill="x", expand=True)
        self.code_entry = ttk.Entry(row, textvariable=code_var, width=6)
        self.code_entry.pack(side="left", padx=(dpi.px(6), 0))

        if placeholder:
            self._placeholder = placeholder
            self._show_placeholder()
            self.entry.bind("<FocusIn>", self._on_focus_in)
            self.entry.bind("<FocusOut>", self._on_focus_out)

        self.entry.bind("<KeyRelease>", self._on_key)
        self.entry.bind("<Down>", self._focus_popup)
        self.entry.bind("<Escape>", lambda _e: self._hide_popup())
        self.entry.bind("<Return>", self._on_return)
        self.bind("<Destroy>", lambda _e: self._hide_popup())

    def _show_placeholder(self):
        if not self.name_var.get().strip():
            self.entry.configure(foreground=t.MUTED)
            self.name_var.set(self._placeholder)
        else:
            self.entry.configure(foreground=t.TEXT)

    def _on_focus_in(self, _e=None):
        if self.name_var.get() == getattr(self, "_placeholder", ""):
            self.name_var.set("")
            self.entry.configure(foreground=t.TEXT)

    def _on_focus_out(self, _e=None):
        if getattr(self, "_placeholder", "") and not self.name_var.get().strip():
            self._show_placeholder()
        self.after(150, self._maybe_hide_popup)

    def _on_key(self, _e=None):
        if self._selecting:
            return
        if self._after_id:
            self.after_cancel(self._after_id)
        self._after_id = self.after(self._debounce_ms, self._run_search)

    def _run_search(self):
        self._after_id = None
        q = self.name_var.get().strip()
        if getattr(self, "_placeholder", "") and q == self._placeholder:
            q = ""
        if len(q) < 1:
            self._hide_popup()
            return
        code = resolve_iata(q)
        if code:
            self.code_var.set(code)
        self._seq += 1
        seq = self._seq
        local = search_local(q)
        if local:
            self._apply_hits(local, seq)
        threading.Thread(target=self._search_bg, args=(q, seq), daemon=True).start()

    def _search_bg(self, q: str, seq: int):
        hits = search_airports(q)
        self.after(0, lambda: self._apply_hits(hits, seq))

    def _apply_hits(self, hits: list[AirportHit], seq: int):
        if seq != self._seq:
            return
        self._hits = hits
        if not hits:
            self._hide_popup()
            return
        self._show_popup(hits)

    def _show_popup(self, hits: list[AirportHit]):
        self._hide_popup()
        popup = tk.Toplevel(self)
        popup.wm_overrideredirect(True)
        popup.configure(bg=t.BORDER)
        self._popup = popup
        self.update_idletasks()
        x = self.entry.winfo_rootx()
        y = self.entry.winfo_rooty() + self.entry.winfo_height()
        w = max(self.entry.winfo_width() + self.code_entry.winfo_width() + dpi.px(8), dpi.px(220))
        popup.geometry(f"{w}x{dpi.px(min(8, len(hits)) * 28 + 8)}+{x}+{y}")

        shell = tk.Frame(
            popup, bg=t.WHITE, highlightbackground=t.BORDER, highlightthickness=1, bd=0,
        )
        shell.pack(fill="both", expand=True)
        lb = tk.Listbox(
            shell, font=t.FONT, bd=0, highlightthickness=0,
            selectbackground=t.BLUE_SEL, selectforeground=t.BLUE_HOVER,
            bg=t.WHITE, fg=t.TEXT, activestyle="none", height=min(8, len(hits)),
        )
        lb.pack(fill="both", expand=True, padx=dpi.px(2), pady=dpi.px(2))
        self._listbox = lb
        for hit in hits:
            lb.insert("end", hit.label)
        lb.bind("<ButtonRelease-1>", self._pick_from_list)
        lb.bind("<Return>", self._pick_from_list)
        popup.bind("<FocusOut>", lambda _e: self.after(80, self._maybe_hide_popup))

    def _pick_from_list(self, _e=None):
        if not self._listbox:
            return
        sel = self._listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if 0 <= idx < len(self._hits):
            self._apply_hit(self._hits[idx])

    def _apply_hit(self, hit: AirportHit):
        self._selecting = True
        self.name_var.set(hit.name)
        self.code_var.set(hit.code)
        self.entry.configure(foreground=t.TEXT)
        self._hide_popup()
        self._selecting = False
        if self._on_select:
            self._on_select()

    def _on_return(self, _e=None):
        if self._popup and self._listbox and self._listbox.curselection():
            self._pick_from_list()
            return "break"
        self._hide_popup()

    def _focus_popup(self, _e=None):
        if self._listbox:
            self._listbox.focus_set()
            if self._listbox.size():
                self._listbox.selection_set(0)
            return "break"

    def _maybe_hide_popup(self):
        if self._popup and not self._popup.focus_get():
            focused = self.focus_displayof()
            if focused is None or str(focused).find("listbox") < 0:
                w = self.winfo_containing(self.winfo_pointerx(), self.winfo_pointery())
                if w is None or (self._popup not in (w, w.master)):
                    self._hide_popup()

    def _hide_popup(self):
        if self._popup:
            try:
                self._popup.destroy()
            except tk.TclError:
                pass
        self._popup = None
        self._listbox = None
