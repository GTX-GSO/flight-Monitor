"""系统托盘：仅在后台盯价运行时显示，关闭窗口后可继续查价。"""
from __future__ import annotations

import threading
from typing import Callable, Optional


class TrayController:
    def __init__(
        self,
        *,
        on_show: Callable[[], None],
        on_fetch: Callable[[], None],
        on_start: Callable[[], None],
        on_stop: Callable[[], None],
        on_quit: Callable[[], None],
        running: Callable[[], bool],
    ):
        self.on_show = on_show
        self.on_fetch = on_fetch
        self.on_start = on_start
        self.on_stop = on_stop
        self.on_quit = on_quit
        self.running = running
        self._icon = None
        self._thread: Optional[threading.Thread] = None
        self.available = False
        self._error = ""

    def start(self) -> bool:
        if self._icon is not None:
            return True
        try:
            import pystray
            from pystray import Menu, MenuItem
            from .icons import tray_image
        except Exception as e:
            self._error = str(e)
            return False
        image = tray_image()
        if image is None:
            self._error = "缺少 Pillow，无法绘制托盘图标"
            return False

        def _running(_item=None):
            try:
                return bool(self.running())
            except Exception:
                return False

        menu = Menu(
            MenuItem("打开窗口", lambda: self.on_show(), default=True),
            MenuItem("立即查一次", lambda: self.on_fetch()),
            Menu.SEPARATOR,
            MenuItem("开始后台盯价", lambda: self.on_start(), enabled=lambda item: not _running(item)),
            MenuItem("停止盯价", lambda: self.on_stop(), enabled=lambda item: _running(item)),
            Menu.SEPARATOR,
            MenuItem("退出", lambda: self.on_quit()),
        )
        self._icon = pystray.Icon("jipiao-monitor", image, "机票价格监控", menu)
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()
        self.available = True
        return True

    def stop(self, join_timeout: float = 2.0):
        icon = self._icon
        thread = self._thread
        self._icon = None
        self._thread = None
        self.available = False
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            try:
                thread.join(timeout=join_timeout)
            except Exception:
                pass

    @property
    def active(self) -> bool:
        return self._icon is not None

    def notify(self, title: str, message: str):
        if self._icon is None:
            return
        try:
            self._icon.notify(message, title)
        except Exception:
            pass

    @property
    def error(self) -> str:
        return self._error
