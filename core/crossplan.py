"""跨平台航段比价：每个航段在各平台报价里取最低，再相加。"""
import json
from typing import List, Optional

from .models import FlightPrice
from .platforms import platform_label


def leg_key(leg: dict) -> tuple:
    return (
        (leg.get("flight_no") or "").upper(),
        (leg.get("from_code") or "").upper(),
        (leg.get("to_code") or "").upper(),
        (leg.get("date") or "")[:10],
    )


def _legs_from_extra(extra: str, platform: str) -> List[dict]:
    if not extra:
        return []
    try:
        obj = json.loads(extra)
    except Exception:
        return []
    if not isinstance(obj, dict):
        return []

    legs: List[dict] = []
    lq = obj.get("leg_quotes")
    if isinstance(lq, dict):
        for bucket in ("outbound", "inbound"):
            for leg in lq.get(bucket) or []:
                if isinstance(leg, dict) and leg.get("price"):
                    item = dict(leg)
                    item.setdefault("platform", platform)
                    legs.append(item)

    if legs:
        return legs

    plan = obj.get("fare_plan") or {}
    for opt in plan.get("options") or []:
        if opt.get("id") not in ("split_all", "ow_split", "out_split_in_combo", "out_combo_in_split"):
            continue
        for part in opt.get("parts") or []:
            if not isinstance(part, dict):
                continue
            if not (part.get("flight_no") and part.get("from_code") and part.get("price")):
                continue
            item = dict(part)
            item.setdefault("platform", platform)
            legs.append(item)
    return legs


def merge_cross_platform_legs(prices: List[FlightPrice]) -> Optional[dict]:
    """同一日期、多平台航段报价合并：每段取最低价。

    至少 2 个航段且涉及 2 个及以上平台时才返回方案。
    """
    by_leg: dict = {}
    for p in prices or []:
        for leg in _legs_from_extra(p.extra, p.platform):
            try:
                price = float(leg["price"])
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            leg = dict(leg)
            leg["price"] = price
            leg.setdefault("platform", p.platform)
            k = leg_key(leg)
            if not k[0] or not k[1] or not k[2]:
                continue
            by_leg.setdefault(k, []).append(leg)

    if len(by_leg) < 2:
        return None

    parts = []
    for k in sorted(by_leg.keys()):
        best = min(by_leg[k], key=lambda x: x["price"])
        plat = platform_label(best.get("platform", ""))
        label = (best.get("label") or best.get("flight_no") or "").strip()
        best["display"] = f"{label}（{plat}）" if label else plat
        parts.append(best)

    platforms = {p.get("platform") for p in parts if p.get("platform")}
    if len(platforms) < 2:
        return None

    total = sum(float(p["price"]) for p in parts)
    return {
        "id": "cross_platform_split",
        "label": "跨平台拆段最优",
        "price": total,
        "parts": parts,
    }


def render_cross_plan_desp(plan: dict) -> str:
    if not plan or not plan.get("parts"):
        return ""
    lines = [
        f"### 跨平台拆段（每段取最低价）合计 **¥{plan['price']:.0f}**",
        "",
    ]
    for part in plan["parts"]:
        disp = part.get("display") or part.get("label") or part.get("flight_no")
        lines.append(f"- {disp}：**¥{float(part['price']):.0f}**")
    lines.append("")
    return "\n".join(lines)


def render_cross_plan_short(plan: dict) -> str:
    if not plan:
        return ""
    bits = []
    for part in plan.get("parts") or []:
        fn = (part.get("flight_no") or "").strip()
        plat = platform_label(part.get("platform", ""))
        if fn:
            bits.append(f"{fn}@{plat}")
    head = f"跨平台拆段¥{plan['price']:.0f}"
    if bits:
        return f"{head} ({' + '.join(bits[:3])})"
    return head
