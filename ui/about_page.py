"""关于。"""
import tkinter as tk

from . import dpi
from . import theme as t

BLOCKS = [
    ("这是什么", "按航线和日期，在多家平台查机票价；低于阈值可推微信。"),
    ("怎么用", "航线填城市和日期 → 设置勾选平台 → 价格监控里开始盯价。"),
    ("往返 / 指定航班", "往返优先查套票。如果指定了航班号，会比较套票、分开买哪种更省。"),
    ("平台可用性", (
        "国内：去哪儿、飞猪、同程可用（途牛容易限流）。\n"
        "国际/港澳台：目前主要靠去哪儿。\n"
        "指定航班：国内去哪儿/飞猪/同程可用，国际仅去哪儿。\n"
        "建议国内勾去哪儿+飞猪+同程，国际只勾去哪儿。"
    )),
    ("城市名", "国际/港澳台须填中文名，如「香港」「首尔」，不能只填三字码。"),
    ("查询频率", "建议 sequential_platforms、两次查询间隔 30～60 秒、盯价间隔 60±15 分钟，避免触发限流。"),
    ("免责", "仅供个人出行参考，请控制查询频率，遵守各平台服务条款。"),
]


class AboutPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=t.BG)
        inner = tk.Frame(self, bg=t.BG)
        inner.pack(fill="both", expand=True, padx=dpi.px(24), pady=dpi.px(20))
        for title, body in BLOCKS:
            card = t.card(inner)
            card.pack(fill="x", pady=(0, dpi.px(12)))
            pad = tk.Frame(card, bg=t.WHITE)
            pad.pack(fill="x", padx=dpi.px(18), pady=dpi.px(14))
            row = tk.Frame(pad, bg=t.WHITE)
            row.pack(fill="x")
            tk.Frame(row, bg=t.ACCENT, width=dpi.px(2)).pack(side="left", fill="y", padx=(0, dpi.px(12)))
            col = tk.Frame(row, bg=t.WHITE)
            col.pack(side="left", fill="x", expand=True)
            tk.Label(col, text=title, bg=t.WHITE, fg=t.TEXT, font=t.FONT_BOLD).pack(anchor="w")
            tk.Label(
                col, text=body, bg=t.WHITE, fg=t.MUTED, font=t.FONT,
                justify="left", wraplength=dpi.px(820),
            ).pack(anchor="w", pady=(dpi.px(4), 0))
        tk.Label(
            inner, text="机票价格监控  ·  仅供个人出行参考",
            bg=t.BG, fg=t.MUTED, font=t.FONT_SMALL,
        ).pack(pady=(dpi.px(8), 0))
