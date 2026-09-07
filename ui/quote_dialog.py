"""报价详情弹窗（Markdown 正文）。"""
import tkinter as tk
from tkinter import ttk

from core.models import FlightPrice, Route
from core.quote_detail import quote_title, render_quote_markdown
from . import dpi
from . import theme as t


class QuoteDetailDialog(tk.Toplevel):
    def __init__(
        self,
        parent,
        route: Route,
        fp: FlightPrice,
        *,
        cross_plan=None,
        effective_price: float | None = None,
        last_price: float | None = None,
    ):
        super().__init__(parent)
        price = float(effective_price if effective_price is not None else fp.price)
        self.title(quote_title(route, fp.date_label(), price, last_price))
        self.configure(bg=t.BG)
        self.transient(parent)
        self.geometry(f"{dpi.px(560)}x{dpi.px(520)}")
        self.minsize(dpi.px(420), dpi.px(320))

        pad = tk.Frame(self, bg=t.BG)
        pad.pack(fill="both", expand=True, padx=dpi.px(16), pady=dpi.px(14))

        head = tk.Frame(pad, bg=t.BG)
        head.pack(fill="x", pady=(0, dpi.px(8)))
        tk.Label(
            head, text="报价详情", bg=t.BG, fg=t.TEXT, font=t.FONT_BOLD,
        ).pack(side="left")
        tk.Label(
            head, text="与微信推送格式一致", bg=t.BG, fg=t.MUTED, font=t.FONT_SMALL,
        ).pack(side="left", padx=(dpi.px(8), 0))

        body = tk.Frame(pad, bg=t.WHITE, highlightbackground=t.BORDER, highlightthickness=1)
        body.pack(fill="both", expand=True)

        text = tk.Text(
            body, wrap="word", font=t.FONT, bd=0,
            bg=t.WHITE, fg=t.TEXT, insertbackground=t.BLUE,
            highlightthickness=0, padx=dpi.px(12), pady=dpi.px(10),
        )
        scroll = ttk.Scrollbar(body, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)

        md = render_quote_markdown(
            route, fp,
            cross_plan=cross_plan,
            effective_price=effective_price,
            last_price=last_price,
            include_threshold=True,
        )
        text.insert("1.0", md)
        text.configure(state="disabled")

        foot = tk.Frame(pad, bg=t.BG)
        foot.pack(fill="x", pady=(dpi.px(10), 0))
        t.secondary_button(foot, "关闭", self.destroy, width=10).pack(side="right")

        self.bind("<Escape>", lambda _e: self.destroy())
        self.after(50, self.grab_set)


def show_quote_detail(parent, route: Route, fp: FlightPrice, **kwargs):
    dlg = QuoteDetailDialog(parent, route, fp, **kwargs)
    dlg.focus_set()
