"""历史价格。"""
import re
import tkinter as tk
from tkinter import ttk

from core.airport_lookup import names_from_routes, resolve_iata, route_label
from . import dpi
from . import theme as t
from .widgets import EmptyState, tree


class HistoryPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=t.BG)
        self.app = app

        inner = tk.Frame(self, bg=t.BG)
        inner.pack(fill="both", expand=True, padx=dpi.px(24), pady=dpi.px(20))
        card = t.card(inner)
        card.pack(fill="both", expand=True)
        pad = tk.Frame(card, bg=t.WHITE)
        pad.pack(fill="both", expand=True, padx=dpi.px(16), pady=dpi.px(14))

        tk.Label(
            pad, text="可按城市名或三字码筛选",
            bg=t.WHITE, fg=t.MUTED, font=t.FONT_SMALL,
        ).pack(anchor="w")

        bar = tk.Frame(pad, bg=t.WHITE)
        bar.pack(fill="x", pady=(dpi.px(10), dpi.px(8)))
        tk.Label(bar, text="出发", bg=t.WHITE, fg=t.MUTED, font=t.FONT_SMALL).pack(side="left")
        self.from_var = tk.StringVar()
        ttk.Entry(bar, textvariable=self.from_var, width=10).pack(side="left", padx=(6, 12))
        tk.Label(bar, text="到达", bg=t.WHITE, fg=t.MUTED, font=t.FONT_SMALL).pack(side="left")
        self.to_var = tk.StringVar()
        ttk.Entry(bar, textvariable=self.to_var, width=10).pack(side="left", padx=(6, 12))
        tk.Label(bar, text="最多条数", bg=t.WHITE, fg=t.MUTED, font=t.FONT_SMALL).pack(side="left")
        self.limit_var = tk.StringVar(value="200")
        ttk.Entry(bar, textvariable=self.limit_var, width=6).pack(side="left", padx=(6, 12))
        t.primary_button(bar, "查询", self.refresh, width=8).pack(side="left")
        t.danger_button(bar, "清空历史", self.clear_history, width=10).pack(side="right")

        body = tk.Frame(pad, bg=t.WHITE)
        body.pack(fill="both", expand=True)
        cols = {
            "platform": ("平台", 90),
            "route": ("航线", 180),
            "date": ("日期", 140),
            "price": ("价格", 80),
            "flight": ("航班", 240),
            "fetched": ("查价时间", 160),
        }
        self.table_wrap, self.tree = tree(body, cols)
        self.empty = EmptyState(body, "还没有历史记录", "先查一轮价，这里会留下记录。", icon="↻")
        self.table_wrap.pack(fill="both", expand=True)

    def _filter_code(self, raw: str) -> str:
        s = (raw or "").strip()
        if not s:
            return ""
        if re.fullmatch(r"[A-Za-z]{3}", s):
            return s.upper()
        return resolve_iata(s) or s

    def refresh(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        try:
            limit = int(self.limit_var.get().strip() or 200)
        except ValueError:
            limit = 200
        try:
            rows = self.app.runtime.storage.list_recent(
                limit=limit,
                from_city=self._filter_code(self.from_var.get()),
                to_city=self._filter_code(self.to_var.get()),
            )
        except Exception:
            rows = []
        if not rows:
            self.table_wrap.pack_forget()
            self.empty.pack(fill="both", expand=True)
            return
        self.empty.pack_forget()
        self.table_wrap.pack(fill="both", expand=True)
        extra = names_from_routes(self.app.runtime.cfg.get("routes") or [])
        for r in rows:
            self.tree.insert("", "end", values=(
                t.PLATFORM_NAMES.get(r["platform"], r["platform"]),
                route_label(r["from_city"], r["to_city"], extra),
                r["depart_date"],
                f"¥{float(r['price']):.0f}",
                r["flight_no"] or "—",
                r["fetched_at"] or "",
            ))

    def clear_history(self):
        try:
            self.app.runtime.storage.clear_prices()
        except Exception as e:
            self.app.flash(f"清空失败：{e}", "err")
            return
        self.refresh()
        self.app.pages["monitor"].refresh()
        self.app.flash("已清空全部历史报价", "ok")
