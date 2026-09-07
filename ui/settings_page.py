"""系统设置：分组说明 + 托盘行为。"""
import tkinter as tk
from tkinter import ttk
import webbrowser

from crawlers import REGISTRY
from . import dpi
from . import theme as t
from .widgets import Field, PrettySelect, ScrollBody

SERVERCHAN_KEY_URL = "https://sct.ftqq.com/sendkey/"


def push_status(cfg: dict) -> tuple[str, str]:
    """返回 (文案, ok|warn|off)。"""
    sc = ((cfg or {}).get("notifier") or {}).get("serverchan") or {}
    enabled = bool(sc.get("enabled"))
    key = str(sc.get("send_key") or "").strip()
    if enabled and key:
        return "已开启", "ok"
    if enabled:
        return "未填密钥", "warn"
    return "未开启", "off"


class SettingsPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=t.BG)
        self.app = app
        self.plat_vars = {}

        self.scroll = ScrollBody(self)
        self.scroll.pack(fill="both", expand=True)
        inner = tk.Frame(self.scroll.inner, bg=t.BG)
        inner.pack(fill="both", expand=True, padx=dpi.px(24), pady=dpi.px(20))

        self._card(inner, "查哪些网站", "勾得越多越慢。")
        plat_row = tk.Frame(self._last, bg=t.WHITE)
        plat_row.pack(anchor="w", pady=(4, 0))
        for key in REGISTRY:
            var = tk.BooleanVar()
            ttk.Checkbutton(
                plat_row, text=t.PLATFORM_NAMES.get(key, key), variable=var,
            ).pack(side="left", padx=(0, dpi.px(14)))
            self.plat_vars[key] = var
        login_row = tk.Frame(self._last, bg=t.WHITE)
        login_row.pack(anchor="w", pady=(dpi.px(10), 0))
        tk.Label(login_row, text="登录", bg=t.WHITE, fg=t.MUTED, font=t.FONT_SMALL).pack(side="left")
        plat_opts = [(k, t.PLATFORM_NAMES.get(k, k)) for k in REGISTRY]
        self.login_select = PrettySelect(login_row, plat_opts, value="qunar", width=8)
        self.login_select.pack(side="left", padx=8)
        t.secondary_button(login_row, "打开登录窗口", self._login, width=12).pack(side="left")

        self.vars = {}
        self._card(inner, "多久查一轮", "间隔越大越稳。")
        self._row(self._last, "interval_minutes", "间隔（分钟）", "60")
        self._row(self._last, "jitter_minutes", "随机加减（分钟）", "15")

        self._card(inner, "怎么抓", "登录时可临时关掉无头，方便过验证码。")
        self.headless = tk.BooleanVar(value=True)
        ttk.Checkbutton(self._last, text="无头模式（不弹出浏览器窗口）", variable=self.headless).pack(anchor="w")
        self.debug = tk.BooleanVar()
        ttk.Checkbutton(self._last, text="调试：保存截图和原始数据到 debug 文件夹", variable=self.debug).pack(anchor="w")
        self._row(self._last, "delay_min", "两次查询最少间隔（秒）", "30")
        self._row(self._last, "delay_max", "两次查询最多间隔（秒）", "60")
        self._row(self._last, "timeout_seconds", "超时（秒）", "45")

        self._card(inner, "微信提醒", "低价时推到微信，密钥在 Server酱 领取。", key="push")
        self.push_on = tk.BooleanVar()
        ttk.Checkbutton(self._last, text="启用微信推送", variable=self.push_on).pack(anchor="w")
        key_field = Field(self._last, "推送密钥")
        key_field.pack(fill="x")
        key_row = tk.Frame(key_field.body, bg=t.WHITE)
        key_row.pack(fill="x")
        self.vars["send_key"] = tk.StringVar()
        ttk.Entry(key_row, textvariable=self.vars["send_key"], width=40).pack(
            side="left", fill="x", expand=True,
        )
        t.secondary_button(key_row, "打开 Server酱", self._open_serverchan, width=12).pack(
            side="left", padx=(dpi.px(8), 0),
        )
        self._row(self._last, "push_drop_min", "再降多少元才重推", "30")
        self._row(self._last, "push_rise_min", "再涨多少元才重推", "50")

        self._card(inner, "窗口与托盘", "盯价中关窗可进托盘。")
        self.close_tray = tk.BooleanVar(value=True)
        self.min_tray = tk.BooleanVar(value=True)
        ttk.Checkbutton(self._last, text="盯价中点关闭时缩到托盘（不退出）", variable=self.close_tray).pack(anchor="w")
        ttk.Checkbutton(self._last, text="盯价中点最小化时也缩到托盘", variable=self.min_tray).pack(anchor="w")
        self._loading = False
        self._save_after = None
        self.reload()
        self._bind_autosave()

    def _card(self, parent, title, hint, key=None):
        box = t.card(parent)
        box.pack(fill="x", pady=(0, dpi.px(12)))
        pad = tk.Frame(box, bg=t.WHITE)
        pad.pack(fill="x", padx=dpi.px(16), pady=dpi.px(12))
        tk.Label(pad, text=title, bg=t.WHITE, fg=t.TEXT, font=t.FONT_BOLD).pack(anchor="w")
        tk.Label(pad, text=hint, bg=t.WHITE, fg=t.MUTED, font=t.FONT_SMALL, wraplength=dpi.px(820), justify="left").pack(anchor="w", pady=(2, 6))
        self._last = pad
        if key == "push":
            self._push_pad = pad
            self._push_box = getattr(box, "master", box)

    def _row(self, parent, key, label, default, width=18):
        f = Field(parent, label)
        f.pack(fill="x")
        var = tk.StringVar(value=default)
        ttk.Entry(f.body, textvariable=var, width=width).pack(anchor="w")
        self.vars[key] = var

    def _open_serverchan(self):
        webbrowser.open(SERVERCHAN_KEY_URL)
        self.app.flash("已打开 Server酱，登录后复制密钥回来", "ok")

    def focus_push(self):
        pad = getattr(self, "_push_pad", None)
        if pad is None:
            return
        self.scroll.scroll_to(pad)
        box = getattr(self, "_push_box", None)
        if box is None:
            return
        try:
            box.configure(highlightbackground=t.BLUE)
            self.after(1600, lambda: box.configure(highlightbackground=t.CARD_BORDER))
        except tk.TclError:
            pass

    def _login(self):
        self.app.login_platform(self.login_select.get() or "qunar")

    def _bind_autosave(self):
        watched = [
            *self.plat_vars.values(),
            *self.vars.values(),
            self.headless, self.debug, self.push_on,
            self.close_tray, self.min_tray,
        ]
        for var in watched:
            var.trace_add("write", self._on_change)

    def _on_change(self, *_args):
        if self._loading:
            return
        if self._save_after is not None:
            try:
                self.after_cancel(self._save_after)
            except Exception:
                pass
        self._save_after = self.after(450, self._autosave)

    def _autosave(self):
        self._save_after = None
        self.save(silent=True)

    def reload(self):
        self._loading = True
        cfg = self.app.runtime.cfg
        plats = set(cfg.get("platforms") or [])
        for key, var in self.plat_vars.items():
            var.set(key in plats)
        sched = cfg.get("schedule") or {}
        self.vars["interval_minutes"].set(str(sched.get("interval_minutes", 60)))
        self.vars["jitter_minutes"].set(str(sched.get("jitter_minutes", 15)))
        crawler = cfg.get("crawler") or {}
        self.headless.set(bool(crawler.get("headless", True)))
        self.debug.set(bool(crawler.get("debug", False)))
        self.vars["delay_min"].set(str(crawler.get("delay_min", 30)))
        self.vars["delay_max"].set(str(crawler.get("delay_max", 60)))
        self.vars["timeout_seconds"].set(str(crawler.get("timeout_seconds", 45)))
        notify = cfg.get("notifier") or {}
        sc = notify.get("serverchan") or {}
        self.push_on.set(bool(sc.get("enabled")))
        self.vars["send_key"].set(str(sc.get("send_key") or ""))
        self.vars["push_drop_min"].set(str(notify.get("push_drop_min", 30)))
        self.vars["push_rise_min"].set(str(notify.get("push_rise_min", 50)))
        gui = cfg.get("gui") or {}
        self.close_tray.set(bool(gui.get("close_to_tray", True)))
        self.min_tray.set(bool(gui.get("minimize_to_tray", True)))
        self._loading = False

    def save(self, silent: bool = False):
        cfg = self.app.runtime.cfg
        plats = [k for k, v in self.plat_vars.items() if v.get()]
        if not plats:
            self.app.flash("请至少勾选一个网站", "err")
            return
        cfg["platforms"] = plats
        cfg.setdefault("schedule", {})
        try:
            cfg["schedule"]["interval_minutes"] = int(float(self.vars["interval_minutes"].get() or 60))
            cfg["schedule"]["jitter_minutes"] = int(float(self.vars["jitter_minutes"].get() or 15))
            cfg.setdefault("crawler", {})
            cfg["crawler"]["headless"] = bool(self.headless.get())
            cfg["crawler"]["debug"] = bool(self.debug.get())
            cfg["crawler"]["delay_min"] = float(self.vars["delay_min"].get() or 30)
            cfg["crawler"]["delay_max"] = float(self.vars["delay_max"].get() or 60)
            cfg["crawler"]["timeout_seconds"] = int(float(self.vars["timeout_seconds"].get() or 45))
            cfg.setdefault("notifier", {})
            cfg["notifier"]["push_drop_min"] = float(self.vars["push_drop_min"].get() or 30)
            cfg["notifier"]["push_rise_min"] = float(self.vars["push_rise_min"].get() or 50)
        except ValueError:
            if not silent:
                self.app.flash("请填写有效数字", "err")
            return
        cfg["notifier"].setdefault("serverchan", {})
        cfg["notifier"]["serverchan"]["enabled"] = bool(self.push_on.get())
        cfg["notifier"]["serverchan"]["send_key"] = self.vars["send_key"].get().strip()
        cfg.setdefault("gui", {})
        cfg["gui"]["close_to_tray"] = bool(self.close_tray.get())
        cfg["gui"]["minimize_to_tray"] = bool(self.min_tray.get())
        self.app.save_config(reload=not silent)
        extra = ""
        if self.app._sched is not None:
            extra = " 盯价中改间隔需先停止再开始才生效。"
        self.app.flash("设置已保存" + extra, "ok")
