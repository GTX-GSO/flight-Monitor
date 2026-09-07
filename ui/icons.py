"""应用图标：运行时绘制，任务栏 / 托盘共用。"""
from __future__ import annotations

from typing import Optional


def render_icon(size: int = 64):
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = max(1, size // 16)
    radius = max(6, size // 5)
    d.rounded_rectangle(
        [pad, pad, size - pad - 1, size - pad - 1],
        radius=radius,
        fill=(123, 163, 212, 255),
    )
    s = size
    # 简易飞机剪影
    wing = [
        (int(s * 0.18), int(s * 0.58)),
        (int(s * 0.48), int(s * 0.42)),
        (int(s * 0.38), int(s * 0.62)),
    ]
    body = [
        (int(s * 0.22), int(s * 0.62)),
        (int(s * 0.72), int(s * 0.28)),
        (int(s * 0.78), int(s * 0.36)),
        (int(s * 0.42), int(s * 0.70)),
    ]
    tail = [
        (int(s * 0.62), int(s * 0.30)),
        (int(s * 0.78), int(s * 0.18)),
        (int(s * 0.74), int(s * 0.34)),
    ]
    d.polygon(body, fill=(255, 255, 255, 255))
    d.polygon(wing, fill=(219, 234, 254, 255))
    d.polygon(tail, fill=(255, 255, 255, 255))
    return img


def tray_image():
    return render_icon(64)


def tk_photo(root, size: int = 32) -> Optional[object]:
    img = render_icon(size)
    if img is None:
        return None
    try:
        from PIL import ImageTk
        return ImageTk.PhotoImage(img, master=root)
    except Exception:
        return None
