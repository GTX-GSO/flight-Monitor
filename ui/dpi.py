"""Windows DPI：先声明感知，再按实际缩放设置 Tk，避免系统拉伸导致模糊。"""
from __future__ import annotations

import sys

SCALE = 1.0


def enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        # Per-Monitor v2：多屏不同缩放时也不会被系统位图拉伸
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
            return
        except Exception:
            pass
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            return
        except Exception:
            pass
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def attach(root) -> float:
    """在 Tk() 之后调用：按显示器 DPI 缩放 Tk 坐标系与字体。"""
    global SCALE
    dpi_val = 96.0
    try:
        dpi_val = float(root.winfo_fpixels("1i") or 96.0)
    except Exception:
        pass
    if dpi_val < 48:
        dpi_val = 96.0
    SCALE = max(1.0, dpi_val / 96.0)
    # 已声明 Per-Monitor DPI 时不再改 tk scaling，避免与 px() 双重放大导致布局错位
    return SCALE


def px(value: int | float) -> int:
    return max(1, int(round(float(value) * SCALE)))
