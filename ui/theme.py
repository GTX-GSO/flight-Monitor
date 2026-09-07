"""界面主题：统一浅色、低对比，字号随 DPI 缩放。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import dpi
from .round import RADIUS_SM, RoundedButton

# ── 调色板（柔和浅色）──────────────────────────────────────────
BG = "#f6f7f9"
BG_ALT = "#eef0f3"
WHITE = "#ffffff"
SURFACE = "#f3f4f6"
TEXT = "#404650"
TEXT_SECONDARY = "#5f6672"
MUTED = "#9098a4"
# 侧栏与内容区同色系，仅用浅灰区分
SIDEBAR = "#f0f1f4"
SIDEBAR_TOP = "#f0f1f4"
SIDE_HOVER = "#e8eaee"
SIDE_ACTIVE = "#dde5f0"
SIDE_ACTIVE_BG = "#e8eef6"
SIDE_INDICATOR = "#9bb8d9"
BLUE = "#7ba3d4"
BLUE_HOVER = "#6a94c6"
BLUE_LIGHT = "#9bb8d9"
BLUE_SOFT = "#f0f4f9"
BLUE_SEL = "#e6edf6"
BORDER = "#e4e7ec"
BORDER_FOCUS = "#c5d5e8"
TRACK = "#e8eaee"
SHADOW = "#dce0e6"
DANGER = "#c45c5c"
DANGER_SOFT = "#faf3f3"
OK = "#5a9a72"
OK_SOFT = "#f0f6f2"
WARN = "#b08850"
WARN_SOFT = "#faf6f0"
CHIP_OK_BG = "#eef6f1"
CHIP_WARN_BG = "#faf6ee"
CHIP_OFF_BG = "#f0f1f4"
CHIP_BUSY_BG = "#faf3ee"
HEADER_BG = "#fafbfc"
LOG_BG = "#f3f4f6"
LOG_FG = "#6b7280"
ACCENT = SIDE_INDICATOR
CARD_BORDER = "#e8eaee"

from core.platforms import PLATFORM_NAMES

NAV_ICONS = {
    "monitor": "◎",
    "routes": "✈",
    "history": "↻",
    "logs": "≡",
    "settings": "⚙",
    "about": "ⓘ",
}


def font(size: int = 10, bold: bool = False):
    px = max(8, int(round(size * dpi.SCALE)))
    family = "Microsoft YaHei UI"
    return (family, px, "bold") if bold else (family, px)


def mono(size: int = 10):
    px = max(8, int(round(size * dpi.SCALE)))
    return ("Cascadia Mono", px) if _has_cascadia() else ("Consolas", px)


def _has_cascadia() -> bool:
    try:
        import tkinter.font as tkfont
        return "Cascadia Mono" in tkfont.families()
    except Exception:
        return False


FONT = font(10)
FONT_BOLD = font(10, True)
FONT_TITLE = font(17, True)
FONT_SUBTITLE = font(11)
FONT_SMALL = font(9)
FONT_TAB = font(10, True)
FONT_DISPLAY = font(22, True)


def refresh_fonts():
    global FONT, FONT_BOLD, FONT_TITLE, FONT_SUBTITLE, FONT_SMALL, FONT_TAB, FONT_DISPLAY
    FONT = font(10)
    FONT_BOLD = font(10, True)
    FONT_TITLE = font(17, True)
    FONT_SUBTITLE = font(11)
    FONT_SMALL = font(9)
    FONT_TAB = font(10, True)
    FONT_DISPLAY = font(22, True)


def _parent_bg(parent) -> str:
    try:
        return parent.cget("bg")
    except Exception:
        return BG


def apply_theme(root: tk.Tk) -> ttk.Style:
    refresh_fonts()
    root.configure(bg=BG)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    rh = dpi.px(34)
    pad_entry = (dpi.px(10), dpi.px(7))
    style.configure(".", font=FONT, background=BG, foreground=TEXT)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=WHITE)
    style.configure("TLabel", background=BG, foreground=TEXT, font=FONT)
    style.configure("Card.TLabel", background=WHITE, foreground=TEXT, font=FONT)
    style.configure("Muted.TLabel", background=WHITE, foreground=MUTED, font=FONT_SMALL)
    style.configure("Title.TLabel", background=WHITE, foreground=TEXT, font=FONT_TITLE)
    style.configure(
        "TEntry",
        fieldbackground=SURFACE,
        foreground=TEXT,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        padding=pad_entry,
        insertcolor=BLUE,
    )
    style.map(
        "TEntry",
        fieldbackground=[("focus", WHITE), ("readonly", SURFACE)],
        bordercolor=[("focus", BORDER_FOCUS)],
        lightcolor=[("focus", BORDER_FOCUS)],
        darkcolor=[("focus", BORDER_FOCUS)],
    )
    style.configure(
        "TCombobox",
        fieldbackground=SURFACE,
        foreground=TEXT,
        bordercolor=BORDER,
        padding=pad_entry,
        arrowsize=dpi.px(14),
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", SURFACE)],
        bordercolor=[("focus", BORDER_FOCUS)],
    )
    style.configure("TCheckbutton", background=WHITE, foreground=TEXT, font=FONT)
    style.configure("TRadiobutton", background=WHITE, foreground=TEXT, font=FONT)
    style.configure(
        "TProgressbar",
        troughcolor=TRACK,
        background=BLUE,
        bordercolor=TRACK,
        lightcolor=BLUE_LIGHT,
        darkcolor=BLUE,
        thickness=dpi.px(4),
    )
    style.configure(
        "Treeview",
        background=WHITE,
        fieldbackground=WHITE,
        foreground=TEXT,
        bordercolor=BORDER,
        rowheight=rh,
        font=FONT,
    )
    style.configure(
        "Treeview.Heading",
        background=SURFACE,
        foreground=TEXT_SECONDARY,
        font=FONT_BOLD,
        bordercolor=BORDER,
        relief="flat",
        padding=(dpi.px(8), dpi.px(6)),
    )
    style.map(
        "Treeview",
        background=[("selected", BLUE_SEL)],
        foreground=[("selected", BLUE_HOVER)],
    )
    style.map("Treeview.Heading", background=[("active", BLUE_SOFT)])
    style.configure("TScrollbar", background=WHITE, troughcolor=BG_ALT, bordercolor=BORDER, arrowsize=dpi.px(12))
    style.configure("Vertical.TScrollbar", background="#d5dae0", troughcolor=BG_ALT, width=dpi.px(10))
    return style


def card(parent, accent: bool = False, expandable: bool = False, **kw) -> tk.Frame:
    """浅色卡片（Frame 边框，避免 Canvas 叠层穿透）。"""
    host = tk.Frame(parent, bg=_parent_bg(parent))
    box = tk.Frame(
        host, bg=WHITE, highlightbackground=CARD_BORDER, highlightthickness=1, bd=0,
    )
    box.pack(fill="both", expand=True)
    if accent:
        tk.Frame(box, bg=ACCENT, height=max(2, dpi.px(3))).pack(fill="x")
    inner = tk.Frame(box, bg=WHITE)
    inner.pack(fill="both", expand=True)
    from .round import _forward_pack
    _forward_pack(host, inner)
    return inner


def divider(parent, color: str = BORDER) -> tk.Frame:
    return tk.Frame(parent, bg=color, height=1)


def section_label(parent, text: str, hint: str = ""):
    """卡片内区块标题。"""
    tk.Label(parent, text=text, bg=WHITE, fg=TEXT, font=FONT_BOLD).pack(anchor="w")
    if hint:
        tk.Label(
            parent, text=hint, bg=WHITE, fg=MUTED, font=FONT_SMALL,
            wraplength=dpi.px(820), justify="left",
        ).pack(anchor="w", pady=(dpi.px(2), dpi.px(8)))


def primary_button(parent, text, command, width=12) -> RoundedButton:
    return RoundedButton(
        parent, text=text, command=command,
        bg=BLUE, fg=WHITE, hover_bg=BLUE_HOVER,
        font=FONT, width_chars=width, radius=RADIUS_SM,
    )


def secondary_button(parent, text, command, width=12) -> RoundedButton:
    return RoundedButton(
        parent, text=text, command=command,
        bg=SURFACE, fg=BLUE, hover_bg=BLUE_SOFT,
        font=FONT, width_chars=width, radius=RADIUS_SM,
        outline=BORDER, outline_width=1,
    )


def danger_button(parent, text, command, width=10) -> RoundedButton:
    return RoundedButton(
        parent, text=text, command=command,
        bg=WHITE, fg=DANGER, hover_bg=DANGER_SOFT,
        font=FONT, width_chars=width, radius=RADIUS_SM,
        outline="#e8d4d4", outline_width=1,
    )


def ghost_button(parent, text, command, width=10) -> RoundedButton:
    return RoundedButton(
        parent, text=text, command=command,
        bg=WHITE, fg=MUTED, hover_bg=SURFACE, hover_fg=TEXT,
        font=FONT, width_chars=width, radius=RADIUS_SM,
        outline=WHITE, outline_width=0,
    )


def labeled_entry(parent, label, width=28):
    row = tk.Frame(parent, bg=WHITE)
    tk.Label(row, text=label, bg=WHITE, fg=TEXT_SECONDARY, font=FONT_SMALL, width=12, anchor="e").pack(
        side="left", padx=(0, 8),
    )
    var = tk.StringVar()
    ent = ttk.Entry(row, textvariable=var, width=width)
    ent.pack(side="left", fill="x", expand=True)
    return row, var, ent


def modern_text(parent, height=2, **kw) -> tk.Frame:
    """多行输入：返回容器，文本控件在 `.text`。"""
    shell = tk.Frame(
        parent, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1, bd=0,
    )
    box = tk.Text(
        shell, height=height, font=FONT, bd=0, wrap="word",
        bg=SURFACE, fg=TEXT, insertbackground=BLUE,
        highlightthickness=0, padx=dpi.px(8), pady=dpi.px(6),
        **kw,
    )
    box.pack(fill="x")
    shell.text = box  # type: ignore[attr-defined]

    def on_focus_in(_e=None):
        box.configure(bg=WHITE)
        shell.configure(bg=WHITE, highlightbackground=BORDER_FOCUS)

    def on_focus_out(_e=None):
        box.configure(bg=SURFACE)
        shell.configure(bg=SURFACE, highlightbackground=BORDER)

    box.bind("<FocusIn>", on_focus_in)
    box.bind("<FocusOut>", on_focus_out)
    return shell
