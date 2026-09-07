"""指定航班时穷举买法，选出最低价方案，并生成推送文案。"""
import json
from typing import Callable, Optional


def clip_text(s: str, n: int) -> str:
    s = (s or "").replace("\n", " ").strip()
    if n <= 0:
        return ""
    if len(s) <= n:
        return s
    if n == 1:
        return "…"
    return s[: n - 1] + "…"


def _price_of(offer) -> Optional[float]:
    if not offer:
        return None
    try:
        p = float(offer.get("price"))
    except Exception:
        return None
    return p if p > 0 else None


def _combo_label(prefix: str, offer) -> str:
    no = ((offer or {}).get("flight_no") or "").strip()
    return f"{prefix} {no}".strip() if no else prefix


def _total(parts) -> Optional[float]:
    if not parts:
        return None
    acc = 0.0
    for p in parts:
        v = p.get("price") if isinstance(p, dict) else None
        if v is None:
            return None
        try:
            acc += float(v)
        except Exception:
            return None
    return acc


def format_leg_label(leg: dict, city_name: Callable[[str], str]) -> str:
    fn = (leg.get("flight_no") or "").strip()
    dep = city_name(leg.get("from_code") or "")
    arr = city_name(leg.get("to_code") or "")
    date = (leg.get("date") or "")
    if len(date) >= 10:
        date = date[5:]
    bits = [x for x in (fn, f"{dep}→{arr}" if dep or arr else "", date) if x]
    return " ".join(bits)


def build_fare_plan(
    *,
    trip: str = "one_way",
    round_pkg=None,
    outbound_combo=None,
    inbound_combo=None,
    outbound_leg_quotes=None,
    inbound_leg_quotes=None,
) -> dict:
    """根据已报价的套票 / 组合票 / 航段，穷举可加总的买法。缺价的方案直接跳过。"""
    options = []
    out_legs = list(outbound_leg_quotes or [])
    in_legs = list(inbound_leg_quotes or [])
    out_multi = len(out_legs) >= 2
    in_multi = len(in_legs) >= 2
    rp = _price_of(round_pkg)
    oc = _price_of(outbound_combo)
    ic = _price_of(inbound_combo)

    def add(oid: str, label: str, price: Optional[float], parts=None):
        if price is None:
            return
        options.append({
            "id": oid,
            "label": label,
            "price": float(price),
            "parts": list(parts or []),
        })

    is_round = (trip or "one_way") == "round"

    if is_round:
        add("round_pkg", "往返套票", rp, [
            {"label": _combo_label("往返套票", round_pkg), "price": rp},
        ] if rp is not None else None)

        if oc is not None and ic is not None:
            add("two_combo", "去程组合票+返程组合票", oc + ic, [
                {"label": _combo_label("去程组合票", outbound_combo), "price": oc},
                {"label": _combo_label("返程组合票", inbound_combo), "price": ic},
            ])

        if oc is not None and in_multi:
            t = _total(in_legs)
            if t is not None:
                add("out_combo_in_split", "去程组合票+返程拆段单买", oc + t, [
                    {"label": _combo_label("去程组合票", outbound_combo), "price": oc},
                    *in_legs,
                ])

        if out_multi and ic is not None:
            t = _total(out_legs)
            if t is not None:
                add("out_split_in_combo", "去程拆段单买+返程组合票", t + ic, [
                    *out_legs,
                    {"label": _combo_label("返程组合票", inbound_combo), "price": ic},
                ])

        if out_multi and in_multi:
            parts = [*out_legs, *in_legs]
            add("split_all", "全部拆段单买", _total(parts), parts)
    else:
        add("ow_combo", "指定航班组合票", oc, [
            {"label": _combo_label("组合票", outbound_combo), "price": oc},
        ] if oc is not None else None)
        if out_multi:
            add("ow_split", "各航段单买合计", _total(out_legs), out_legs)

    options.sort(key=lambda o: (o["price"], o["id"]))
    best = options[0] if options else None
    return {
        "trip": "round" if is_round else "one_way",
        "best": best,
        "options": options,
    }


def load_fare_plan(extra: str) -> Optional[dict]:
    if not extra:
        return None
    try:
        obj = json.loads(extra)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    plan = obj.get("fare_plan")
    if isinstance(plan, dict) and plan.get("options"):
        return plan
    return None


def render_plan_desp(plan: dict) -> str:
    if not plan or not plan.get("options"):
        return ""
    best = plan.get("best") or plan["options"][0]
    lines = [
        f"## 建议买法：{best['label']} **¥{best['price']:.0f}**",
        "",
    ]
    pkg = next((o for o in plan["options"] if o["id"] == "round_pkg"), None)
    combo = next((o for o in plan["options"] if o["id"] in ("two_combo", "ow_combo")), None)
    if pkg and best["id"] != "round_pkg" and best["price"] < pkg["price"]:
        lines.append(f"比往返套票省 **¥{pkg['price'] - best['price']:.0f}**")
        lines.append("")
    elif combo and best["id"] not in ("two_combo", "ow_combo") and best["price"] < combo["price"]:
        lines.append(f"比组合票省 **¥{combo['price'] - best['price']:.0f}**")
        lines.append("")

    lines.append("### 买法比价")
    for o in plan["options"]:
        mark = " ✅" if o["id"] == best["id"] else ""
        lines.append(f"- {o['label']}：**¥{o['price']:.0f}**{mark}")
        for part in o.get("parts") or []:
            if part.get("price") is None:
                continue
            label = (part.get("label") or "").strip()
            if not label:
                continue
            lines.append(f"  - {label} ¥{float(part['price']):.0f}")
    lines.append("")

    parts = best.get("parts") or []
    if parts:
        lines.append("### 推荐拆解")
        for part in parts:
            if part.get("price") is None:
                continue
            label = (part.get("label") or "").strip()
            if not label:
                continue
            lines.append(f"- {label}：**¥{float(part['price']):.0f}**")
        lines.append("")
    return "\n".join(lines)


def render_plan_short(plan: dict) -> str:
    if not plan or not plan.get("best"):
        return ""
    best = plan["best"]
    s = f"建议{best['label']}¥{best['price']:.0f}"
    pkg = next((o for o in plan.get("options") or [] if o["id"] == "round_pkg"), None)
    if pkg and best["id"] != "round_pkg" and best["price"] < pkg["price"]:
        s += f"，比套票省¥{pkg['price'] - best['price']:.0f}"
    return clip_text(s, 64)
