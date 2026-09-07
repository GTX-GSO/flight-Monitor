"""平台代码 ↔ 中文名（日志 / 推送 / 界面统一使用）。"""

PLATFORM_NAMES = {
    "ctrip": "携程",
    "fliggy": "飞猪",
    "tongcheng": "同程",
    "qunar": "去哪儿",
    "tuniu": "途牛",
}


def platform_label(code: str) -> str:
    return PLATFORM_NAMES.get((code or "").strip().lower(), code or "未知")


def join_platform_labels(codes) -> str:
    return "、".join(platform_label(c) for c in (codes or []) if c)
