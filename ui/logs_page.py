"""运行日志。"""
import tkinter as tk

from . import dpi
from . import theme as t


class LogsPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=t.BG)
        self.app = app
        inner = tk.Frame(self, bg=t.BG)
        inner.pack(fill="both", expand=True, padx=dpi.px(24), pady=dpi.px(20))
        card = t.card(inner)
        card.pack(fill="both", expand=True)
        pad = tk.Frame(card, bg=t.WHITE)
        pad.pack(fill="both", expand=True, padx=dpi.px(18), pady=dpi.px(16))
        head = tk.Frame(pad, bg=t.WHITE)
        head.pack(fill="x")
        tk.Label(head, text="运行日志", bg=t.WHITE, fg=t.TEXT, font=t.FONT_BOLD).pack(side="left")
        tk.Label(
            head, text="查不到价时看这里",
            bg=t.WHITE, fg=t.MUTED, font=t.FONT_SMALL,
        ).pack(side="left", padx=(dpi.px(10), 0))
        t.ghost_button(head, "清空显示", self.clear, width=10).pack(side="right")
        wrap = tk.Frame(
            pad, bg=t.LOG_BG, highlightbackground=t.BORDER, highlightthickness=1, bd=0,
        )
        wrap.pack(fill="both", expand=True, pady=(dpi.px(12), 0))
        log_inner = tk.Frame(wrap, bg=t.LOG_BG)
        log_inner.pack(fill="both", expand=True)
        self.text = tk.Text(
            log_inner, wrap="word", font=t.mono(10), bg=t.LOG_BG, fg=t.LOG_FG,
            insertbackground=t.BLUE, bd=0, highlightthickness=0, state="disabled",
            padx=dpi.px(12), pady=dpi.px(8), selectbackground=t.BLUE_SEL,
        )
        vsb = tk.Scrollbar(log_inner, orient="vertical", command=self.text.yview, bg=t.BORDER, troughcolor=t.LOG_BG)
        self.text.configure(yscrollcommand=vsb.set)
        self.text.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

    def append(self, line: str):
        self.text.configure(state="normal")
        self.text.insert("end", line.rstrip() + "\n")
        self.text.see("end")
        self.text.configure(state="disabled")

    def clear(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def load_file(self):
        path = (self.app.runtime.cfg.get("output") or {}).get("log_path", "logs/monitor.log")
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 80000))
                data = f.read()
        except OSError:
            return
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("end", data)
        self.text.see("end")
        self.text.configure(state="disabled")
