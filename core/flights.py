"""航班号规范化与匹配。

配置示例:
    flight_nos:
      - MU9658            # 含此航班的任意行程（含中转）
      - MU734+MU5931      # 指定中转组合（须同时包含）
"""
import re
from typing import Iterable, List, Optional, Sequence, Union


# 航司代码至少含一个字母，避免把日期 2026 当成航班号
_FLIGHT_NO_RE = re.compile(r"^(?=[A-Z0-9]*[A-Z])[A-Z0-9]{2}\d{2,4}$")


def parse_itinerary_nos(code) -> List[str]:
    """从接口字段抽出航班号列表。

    兼容: 'MU9658' / 'MU9658+MU5931' / 'MU9658-MU5931'
          ['MU9658|HKG-KMG|2026-11-27', 'MU5931|KMG-DIG|2026-11-28']
    """
    if code is None:
        return []
    if isinstance(code, (list, tuple)):
        out: List[str] = []
        for item in code:
            out.extend(parse_itinerary_nos(item))
        return out
    s = str(code).upper()
    nos: List[str] = []
    for chunk in re.split(r"[/+,]", s):
        token = chunk.split("|")[0].split("@")[0]
        for part in re.split(r"[\s\-]+", token):
            part = part.strip().upper()
            if _FLIGHT_NO_RE.match(part):
                nos.append(part)
    return nos


def parse_wanted_flight_nos(raw: Optional[Union[str, Sequence]]) -> List[List[str]]:
    """配置值 → 航班号组。每组内须全部命中（中转组合）；组与组之间是或关系。"""
    if not raw:
        return []
    if isinstance(raw, str):
        items: Iterable = [raw]
    else:
        items = raw
    groups: List[List[str]] = []
    for item in items:
        nos = parse_itinerary_nos(item)
        if nos:
            groups.append(nos)
    return groups


_LEG_RE = re.compile(
    r"(?P<fn>(?=[A-Z0-9]*[A-Z])[A-Z0-9]{2}\d{2,4})"
    r"\|(?P<dep>[A-Z]{3})-(?P<arr>[A-Z]{3})"
    r"\|(?P<date>\d{4}-\d{2}-\d{2})",
    re.I,
)


def parse_legs(code) -> List[dict]:
    """从接口 code 抽出航段：航班号、起降三字码、日期（含隔夜）。

    例: ['MU9658|HKG-KMG|2026-11-27', 'MU5931|KMG-DIG|2026-11-28']
    """
    if code is None:
        return []
    if isinstance(code, (list, tuple)):
        out: List[dict] = []
        for item in code:
            out.extend(parse_legs(item))
        return out
    s = str(code).upper().replace(" ", "")
    legs: List[dict] = []
    seen = set()
    for m in _LEG_RE.finditer(s):
        leg = {
            "flight_no": m.group("fn").upper(),
            "from_code": m.group("dep").upper(),
            "to_code": m.group("arr").upper(),
            "date": m.group("date"),
        }
        key = (leg["flight_no"], leg["from_code"], leg["to_code"], leg["date"])
        if key in seen:
            continue
        seen.add(key)
        legs.append(leg)
    return legs


def itinerary_matches(itinerary_nos: Sequence[str], wanted: Sequence[Sequence[str]],
                      exact: bool = False) -> bool:
    if not wanted:
        return True
    have = [str(x).upper() for x in itinerary_nos]
    have_set = set(have)
    for group in wanted:
        g = [str(x).upper() for x in group]
        if exact:
            if have_set == set(g) and len(have) == len(g):
                return True
        elif all(n in have_set for n in g):
            return True
    return False


def format_itinerary(nos: Sequence[str]) -> str:
    return "+".join(nos)


def format_wanted(wanted: Sequence[Sequence[str]]) -> str:
    return ", ".join(format_itinerary(g) for g in wanted)


def iata3(value) -> str:
    s = str(value or "").strip().upper()
    return s if re.fullmatch(r"[A-Z]{3}", s) else ""


def first_iata(node: Optional[dict], *keys: str) -> str:
    if not isinstance(node, dict):
        return ""
    for key in keys:
        code = iata3(node.get(key))
        if code:
            return code
    return ""


def normalize_offer(offer: Optional[dict]) -> dict:
    """补齐 flight_nos / legs，方便筛选和拆段询价。"""
    if not offer:
        return {}
    o = dict(offer)
    nos = o.get("flight_nos") or parse_itinerary_nos(o.get("flight_no"))
    o["flight_nos"] = nos
    if not o.get("flight_no") and nos:
        o["flight_no"] = format_itinerary(nos)
    if not o.get("legs"):
        o["legs"] = parse_legs(
            o.get("code") or o.get("flightKey") or o.get("flight_no")
        )
    return o


def guess_legs(offer: dict, from_code: str, to_code: str, date: str) -> List[dict]:
    """组合票若无完整航段，用中转城市码拼两段（隔夜日期未知时共用去程日）。"""
    o = normalize_offer(offer)
    if o.get("legs"):
        return list(o["legs"])
    nos = o.get("flight_nos") or []
    trans = (o.get("trans_code") or "").strip().upper()
    if len(nos) < 2 or not trans:
        return []
    arr_date = (o.get("arrive_date") or date or "")[:10] or date
    return [
        {
            "flight_no": nos[0],
            "from_code": (from_code or "").upper(),
            "to_code": trans,
            "date": date,
        },
        {
            "flight_no": nos[-1],
            "from_code": trans,
            "to_code": (to_code or "").upper(),
            "date": arr_date,
        },
    ]
