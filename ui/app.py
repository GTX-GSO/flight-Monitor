"""桌面主窗口：浅色侧栏 + 内容区，支持托盘后台运行。"""
import logging
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path

from core.scheduler import make_background_scheduler
from main import build_runtime, dump_config, load_config

from . import dpi
from . import icons
from . import theme as t
from .about_page import AboutPage
from .history_page import HistoryPage
from .logs_page import LogsPage
from .monitor_page import MonitorPage
from .routes_page import RoutesPage
from .settings_page import SettingsPage
from .tray import TrayController
from .widgets import NavItem, StatusChip

APP_TITLE = "机票价格监控"
TABS = [
    ("monitor", "价格监控", "看报价、开始盯价"),
    ("routes", "航线", "城市、日期、航班"),
    ("history", "历史", "查过的价格"),
    ("logs", "日志", "抓取过程"),
    ("settings", "设置", "平台、间隔、推送"),
    ("about", "关于", "用法与免责"),
]


class QueueLogHandler(logging.Handler):
    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q

    def emit(self, record):
        try:
            self.q.put_nowait(self.format(record))
        except Exception:
            pass


class MainApp:
    def __init__(self, config_path: str):
        dpi.enable_dpi_awareness()
        self.config_path = str(Path(config_path).resolve())
        self.runtime = build_runtime(load_config(self.config_path), self.config_path)
        self._log_handler = None
        self.log_queue = queue.Queue()
        self._busy = False
        self._sched = None
        self._job_lock = threading.Lock()
        self._hidden = False
        self._quitting = False
        self._current_tab = "monitor"
        self._status_after = None

        handler = QueueLogHandler(self.log_queue)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S",
        ))
        self.runtime.logger.addHandler(handler)
        self._log_handler = handler

        self.root = tk.Tk()
        dpi.attach(self.root)
        t.apply_theme(self.root)
        self.root.title(APP_TITLE)
        self.root.geometry(f"{dpi.px(1180)}x{dpi.px(760)}")
        self.root.minsize(dpi.px(960), dpi.px(620))
        self.root.configure(bg=t.BG)

        self._photo = icons.tk_photo(self.root, 32)
        if self._photo is not None:
            try:
                self.root.iconphoto(True, self._photo)
            except Exception:
                pass

        self.statusbar = tk.Frame(self.root, bg=t.HEADER_BG)
        self.statusbar.pack(side="bottom", fill="x")
        tk.Frame(self.statusbar, bg=t.BORDER, height=1).pack(fill="x")
        bar_inner = tk.Frame(self.statusbar, bg=t.HEADER_BG)
        bar_inner.pack(fill="x", padx=dpi.px(16), pady=dpi.px(6))
        self.status_msg = tk.Label(
            bar_inner, text="", bg=t.HEADER_BG, fg=t.MUTED, font=t.FONT_SMALL, anchor="w",
        )
        self.status_msg.pack(side="left", fill="x", expand=True)

        shell = tk.Frame(self.root, bg=t.BG)
        shell.pack(fill="both", expand=True)

        self.sidebar = tk.Frame(shell, bg=t.SIDEBAR, width=dpi.px(220))
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        tk.Frame(shell, bg=t.BORDER, width=1).pack(side="left", fill="y")
        self._build_sidebar()

        right = tk.Frame(shell, bg=t.BG)
        right.pack(side="left", fill="both", expand=True)

        self.header = tk.Frame(right, bg=t.HEADER_BG)
        self.header.pack(fill="x")
        head_pad = tk.Frame(self.header, bg=t.HEADER_BG)
        head_pad.pack(fill="x", padx=dpi.px(24), pady=dpi.px(14))
        titles = tk.Frame(head_pad, bg=t.HEADER_BG)
        titles.pack(side="left", fill="both", expand=True)
        self.header_title = tk.Label(titles, text="价格监控", bg=t.HEADER_BG, fg=t.TEXT, font=t.FONT_TITLE)
        self.header_title.pack(anchor="w")
        self.header_sub = tk.Label(
            titles, text="看报价、开始盯价", bg=t.HEADER_BG, fg=t.MUTED, font=t.FONT_SUBTITLE,
        )
        self.header_sub.pack(anchor="w", pady=(dpi.px(2), 0))
        chip_wrap = tk.Frame(head_pad, bg=t.HEADER_BG)
        chip_wrap.pack(side="right", anchor="ne")
        self.chip = StatusChip(chip_wrap)
        self.chip.pack(anchor="e")
        t.divider(right).pack(fill="x")

        self.content = tk.Frame(right, bg=t.BG)
        self.content.pack(fill="both", expand=True)

        self.pages = {
            "monitor": MonitorPage(self.content, self),
            "routes": RoutesPage(self.content, self),
            "history": HistoryPage(self.content, self),
            "logs": LogsPage(self.content, self),
            "settings": SettingsPage(self.content, self),
            "about": AboutPage(self.content, self),
        }

        self.tray = TrayController(
            on_show=lambda: self.root.after(0, self.show_window),
            on_fetch=lambda: self.root.after(0, self.run_once),
            on_start=lambda: self.root.after(0, self.start_schedule),
            on_stop=lambda: self.root.after(0, self.stop_schedule),
            on_quit=lambda: self.root.after(0, self.quit_app),
            running=lambda: self._sched is not None,
        )

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<Unmap>", self._on_unmap)
        self.root.bind("<Map>", self._on_map)
        self.show_tab("monitor")
        self.pages["monitor"].refresh()
        self.pages["logs"].load_file()
        self._poll_logs()
        self.refresh_status()
        self.runtime.logger.info("图形界面已启动")

    def _build_sidebar(self):
        brand = tk.Frame(self.sidebar, bg=t.SIDEBAR_TOP)
        brand.pack(side="top", fill="x")
        brand_inner = tk.Frame(brand, bg=t.SIDEBAR_TOP)
        brand_inner.pack(fill="x", padx=dpi.px(18), pady=(dpi.px(16), dpi.px(12)))
        logo_row = tk.Frame(brand_inner, bg=t.SIDEBAR_TOP)
        logo_row.pack(anchor="w")
        tk.Label(
            logo_row, text="✈", bg=t.BLUE_SOFT, fg=t.BLUE, font=t.font(14),
            padx=dpi.px(8), pady=dpi.px(4),
        ).pack(side="left", padx=(0, dpi.px(10)))
        title_col = tk.Frame(logo_row, bg=t.SIDEBAR_TOP)
        title_col.pack(side="left")
        tk.Label(
            title_col, text="机票监控", bg=t.SIDEBAR_TOP, fg=t.TEXT, font=t.font(15, True),
        ).pack(anchor="w")
        tk.Label(
            title_col, text="盯价 · 比价 · 推送", bg=t.SIDEBAR_TOP, fg=t.MUTED, font=t.FONT_SMALL,
        ).pack(anchor="w", pady=(1, 0))
        t.divider(self.sidebar).pack(side="top", fill="x")

        self.nav_btns = {}
        nav = tk.Frame(self.sidebar, bg=t.SIDEBAR)
        nav.pack(side="top", fill="x", pady=(dpi.px(8), 0), padx=dpi.px(8))
        for key, label, _hint in TABS:
            item = NavItem(
                nav, key, t.NAV_ICONS.get(key, "·"), label,
                command=lambda k: self.show_tab(k),
            )
            item.pack(fill="x", pady=dpi.px(1))
            self.nav_btns[key] = item

    def show_tab(self, key: str):
        self._current_tab = key
        for k, page in self.pages.items():
            if k == key:
                page.place(relx=0, rely=0, relwidth=1, relheight=1)
                page.lift()
            else:
                page.place_forget()
        title, hint = next(((n, h) for k, n, h in TABS if k == key), ("", ""))
        self.header_title.configure(text=title)
        self.header_sub.configure(text=hint)
        for k, item in self.nav_btns.items():
            item.set_active(k == key)
        if key == "monitor":
            self.pages["monitor"].refresh()
        elif key == "history":
            self.pages["history"].refresh()
        elif key == "logs":
            self.pages["logs"].load_file()
        elif key == "routes":
            self.pages["routes"].reload()
        elif key == "settings":
            self.pages["settings"].reload()

    def open_push_settings(self):
        self.show_tab("settings")
        self.root.after(120, self.pages["settings"].focus_push)

    def gui_cfg(self) -> dict:
        return self.runtime.cfg.setdefault("gui", {})

    def _monitoring(self) -> bool:
        return self._sched is not None

    def close_to_tray(self) -> bool:
        return bool(self.gui_cfg().get("close_to_tray", True))

    def minimize_to_tray(self) -> bool:
        return bool(self.gui_cfg().get("minimize_to_tray", True))

    def _detach_log_handler(self):
        if self._log_handler is not None:
            try:
                self.runtime.logger.removeHandler(self._log_handler)
            except Exception:
                pass
            self._log_handler = None

    def reload_runtime(self, attach_log: bool = True):
        self._detach_log_handler()
        cfg = load_config(self.config_path)
        self.runtime = build_runtime(cfg, self.config_path)
        if attach_log:
            handler = QueueLogHandler(self.log_queue)
            handler.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S",
            ))
            self.runtime.logger.addHandler(handler)
            self._log_handler = handler

    def save_config(self, reload: bool = True):
        dump_config(self.config_path, self.runtime.cfg)
        if reload and not self._busy and self._sched is None:
            self.reload_runtime()
        self.pages["monitor"].refresh_routes()

    def refresh_status(self):
        if self._busy:
            self.chip.set_busy("正在查价…")
            self.root.title(f"{APP_TITLE} · 正在查价")
        elif self._sched is not None:
            self.chip.set_ok("后台盯价中")
            self.root.title(f"{APP_TITLE} · 后台盯价中")
        else:
            self.chip.set_idle("未开始")
            self.root.title(APP_TITLE)
        self.pages["monitor"].sync_controls(self._busy, self._sched is not None)

    def flash(self, text: str, kind: str = "ok", ms: int = 4000):
        """左下角提示，不影响右上角盯价状态。"""
        if self._status_after is not None:
            try:
                self.root.after_cancel(self._status_after)
            except Exception:
                pass
        colors = {
            "err": t.DANGER,
            "ok": t.OK,
            "busy": t.WARN,
        }
        self.status_msg.configure(text=text or "", fg=colors.get(kind, t.MUTED))
        self._status_after = self.root.after(ms, self._clear_flash)

    def _clear_flash(self):
        self._status_after = None
        self.status_msg.configure(text="", fg=t.MUTED)

    def _invoke_fetch(self, source: str = "manual"):
        """立即查价 / 定时盯价共用，互斥且每次读最新 config.yaml。"""
        if self._busy:
            if source != "manual":
                self.runtime.logger.info("上一轮查价尚未结束，跳过本次定时任务")
            return
        self._busy = True
        self.refresh_status()
        if source == "manual":
            self.pages["monitor"].set_busy(True, "正在各平台查价，窗口可以先去干别的")

        def work():
            err = None
            try:
                with self._job_lock:
                    self.reload_runtime(attach_log=False)
                    self.runtime.job()
            except Exception as e:
                err = e
                self.runtime.logger.exception("抓取失败: %s", e)

            def done():
                self._busy = False
                if source == "manual":
                    if err:
                        self.pages["monitor"].set_busy(False, "查价失败，请打开「日志」查看原因")
                        self.flash(f"查价失败：{str(err)[:48]}", "err")
                        if self._hidden:
                            self.tray.notify("查价失败", str(err)[:80])
                    else:
                        self.pages["monitor"].set_busy(False, "本轮查价完成")
                        self.flash("本轮查价完成", "ok")
                        if self._hidden:
                            self.tray.notify("机票监控", "本轮查价完成")
                elif err:
                    self.runtime.logger.error("定时查价失败: %s", err)
                else:
                    self.runtime.logger.info("定时查价完成")
                self.refresh_status()
                if not err:
                    self.pages["monitor"].refresh()
                    self.pages["history"].refresh()

            self.root.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    def run_once(self):
        self._invoke_fetch("manual")

    def start_schedule(self):
        if self._sched is not None:
            self.flash("已经在盯价，关窗会进托盘", "ok")
            return
        if self._busy:
            self.flash("请等这一轮查完再开始", "err")
            return
        self.reload_runtime()
        sched_cfg = self.runtime.cfg.get("schedule") or {}
        interval = int(sched_cfg.get("interval_minutes", 60))
        jitter = int(sched_cfg.get("jitter_minutes", 15))
        self._sched = make_background_scheduler(lambda: self._invoke_fetch("schedule"), interval, jitter)
        try:
            job = self._sched.get_job("price_monitor")
            if job:
                from datetime import datetime, timedelta
                job.modify(next_run_time=datetime.now(self._sched.timezone) + timedelta(seconds=1))
        except Exception:
            pass
        self._sched.start()
        self.pages["monitor"].set_schedule_on(True, f"已开始后台盯价，大约每 {interval} 分钟查一轮")
        self.runtime.logger.info("GUI 定时监控已启动，间隔 %s 分钟", interval)
        self._ensure_tray()
        self.refresh_status()
        self.flash("已开始盯价，关窗会进托盘", "ok")

    def stop_schedule(self, *, restore_window: bool = True):
        if self._sched is None:
            if not self._quitting:
                self.pages["monitor"].set_schedule_on(False, "已停止")
                self.refresh_status()
            return
        try:
            self._sched.shutdown(wait=False)
        except Exception:
            pass
        self._sched = None
        if not self._quitting:
            self.pages["monitor"].set_schedule_on(False, "已停止后台盯价")
            self.runtime.logger.info("GUI 定时监控已停止")
            self._stop_tray(restore_window=restore_window)
            self.flash("已停止后台盯价", "ok")
        else:
            self._stop_tray(restore_window=False)

    def login_platform(self, name: str):
        cmd = [sys.executable, str(Path(__file__).resolve().parents[1] / "main.py"),
               "-c", self.config_path, "--login", name]
        flags = 0
        if sys.platform == "win32":
            flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        subprocess.Popen(cmd, creationflags=flags)
        self.flash("已打开登录窗口，完成后在控制台按 Enter", "ok", ms=6000)

    def _poll_logs(self):
        if self._quitting:
            return
        try:
            while True:
                line = self.log_queue.get_nowait()
                self.pages["logs"].append(line)
        except queue.Empty:
            pass
        if not self._quitting:
            self.root.after(300, self._poll_logs)

    def _ensure_tray(self) -> bool:
        if not self._monitoring():
            return False
        if self.tray.active:
            return True
        ok = self.tray.start()
        if not ok:
            self.flash("托盘不可用，请安装 pystray 和 Pillow", "err", ms=6000)
        return ok

    def _stop_tray(self, restore_window: bool = False):
        if self.tray.active:
            self.tray.stop()
        if restore_window and self._hidden:
            self.show_window()

    def hide_to_tray(self):
        if not self._monitoring():
            return
        if not self._ensure_tray():
            return
        self._hidden = True
        self.root.withdraw()
        self.runtime.logger.info("窗口已隐藏，程序在系统托盘继续运行")

    def show_window(self):
        self._hidden = False
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.state("normal")
        except Exception:
            pass
        self.root.focus_force()

    def _on_unmap(self, event):
        if event.widget is not self.root or self._quitting or self._hidden:
            return
        try:
            if self.root.state() == "iconic" and self._monitoring() and self.minimize_to_tray():
                self.hide_to_tray()
        except Exception:
            pass

    def _on_map(self, event):
        if event.widget is self.root:
            self._hidden = False

    def on_close(self):
        if self._monitoring() and self.close_to_tray() and not self._quitting:
            self.hide_to_tray()
            return
        self.quit_app()

    def _shutdown_resources(self):
        """停止调度/托盘/日志句柄，尽量不碰已销毁的 Tk 控件。"""
        self._quitting = True
        sched = self._sched
        self._sched = None
        if sched is not None:
            try:
                sched.shutdown(wait=False)
            except Exception:
                pass
        try:
            self.tray.stop()
        except Exception:
            pass
        self._detach_log_handler()

    def quit_app(self):
        if self._quitting:
            return
        self._quitting = True
        self._shutdown_resources()
        try:
            self.root.quit()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self):
        try:
            self.root.mainloop()
        finally:
            self._shutdown_resources()


def run_gui(config_path: str = "config.yaml"):
    dpi.enable_dpi_awareness()
    app = MainApp(config_path)
    try:
        app.run()
    finally:
        # 查价线程池 / Playwright 驱动 / pystray 等常留下非守护线程，
        # 导致 mainloop 结束后进程仍挂起；强制结束当前进程。
        os._exit(0)
