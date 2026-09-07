"""携程公开联想 API + 本地三字码索引，供 GUI 机场自动补全。"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

import httpx

from crawlers.base import CITY_NAME

CTRIP_SUGGEST_URL = "https://m.ctrip.com/restapi/soa2/21881/json/gaHotelSearchEngine"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_SUGGEST_TYPES = {"City", "Airport", "Location", "District"}
_IATA_RE = re.compile(r"^[A-Z]{3}$")
_CACHE_TTL = 300.0
_search_cache: dict[str, tuple[float, list]] = {}
_cache_lock = threading.Lock()


@dataclass(frozen=True)
class AirportHit:
    name: str
    code: str
    display: str
    kind: str = ""
    country: str = ""

    @property
    def label(self) -> str:
        if self.code:
            return f"{self.name} ({self.code})"
        return self.name


def _data_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "city_codes.json"


@lru_cache(maxsize=1)
def _load_name_index() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for code, name in CITY_NAME.items():
        if name and code:
            mapping.setdefault(name, code.upper())
    path = _data_path()
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for name, code in raw.items():
                    n = str(name).strip()
                    c = str(code).strip().upper()
                    if n and len(c) == 3:
                        mapping[n] = c
        except Exception:
            pass
    return mapping


def _normalize_name(name: str) -> str:
    s = (name or "").strip()
    for suffix in ("国际机场", "机场", "市", "县", "区", "自治州", "地区", "特别行政区"):
        if s.endswith(suffix) and len(s) > len(suffix) + 1:
            s = s[: -len(suffix)]
    return s


def resolve_iata(name: str, *, airport_word: str = "") -> str:
    """中文名 / 机场名 → 三字码（尽力匹配，可能为空）。"""
    index = _load_name_index()
    candidates = [name, _normalize_name(name), airport_word, _normalize_name(airport_word)]
    for c in candidates:
        c = (c or "").strip()
        if not c:
            continue
        if c in index:
            return index[c]
        if _IATA_RE.match(c.upper()):
            return c.upper()
    # 最长子串命中：如「东京成田」→ 成田 / 东京
    blob = " ".join(x for x in candidates if x)
    best = ""
    best_len = 0
    for key, code in index.items():
        if key and key in blob and len(key) > best_len:
            best, best_len = code, len(key)
    if best:
        return best
    en = (name or "").strip().lower()
    en_map = {
        "hong kong": "HKG", "macau": "MFM", "taipei": "TPE",
        "seoul": "ICN", "incheon": "ICN", "tokyo": "TYO", "osaka": "OSA",
        "singapore": "SIN", "bangkok": "BKK", "kuala lumpur": "KUL",
        "beijing": "BJS", "shanghai": "SHA", "guangzhou": "CAN", "shenzhen": "SZX",
    }
    return en_map.get(en, "")


def city_label(code: str, extra: Optional[dict] = None) -> str:
    """三字码 → 中文名；找不到则原样返回。"""
    c = (code or "").strip().upper()
    if not c:
        return ""
    if extra and c in extra:
        return str(extra[c])
    if c in CITY_NAME:
        return CITY_NAME[c]
    names = [n for n, k in _load_name_index().items() if k == c]
    if not names:
        return c
    names.sort(key=len)
    return names[0]


def route_label(from_code: str, to_code: str, extra: Optional[dict] = None) -> str:
    a, b = city_label(from_code, extra), city_label(to_code, extra)
    return f"{a} → {b}"


def names_from_routes(routes: Optional[list] = None) -> dict:
    extra: dict[str, str] = {}
    for r in routes or []:
        fc = str(r.get("from") or "").strip().upper()
        tc = str(r.get("to") or "").strip().upper()
        fn = str(r.get("from_name") or "").strip()
        tn = str(r.get("to_name") or "").strip()
        if fc and fn:
            extra[fc] = fn
        if tc and tn:
            extra[tc] = tn
    return extra


def search_local(keyword: str, *, limit: int = 12) -> List[AirportHit]:
    """只查本地索引，带三字码。"""
    q = (keyword or "").strip()
    if not q:
        return []
    qn = _normalize_name(q)
    index = _load_name_index()
    exact: List[AirportHit] = []
    prefix: List[AirportHit] = []
    contain: List[AirportHit] = []
    for name, code in index.items():
        if name == q or name == qn:
            exact.append(AirportHit(name=name, code=code, display=name, kind="Local"))
        elif name.startswith(q) or name.startswith(qn):
            prefix.append(AirportHit(name=name, code=code, display=name, kind="Local"))
        elif q in name or qn in name or name in q or name in qn:
            contain.append(AirportHit(name=name, code=code, display=name, kind="Local"))
    hits = exact + prefix + contain
    return _dedupe_hits(hits)[:limit]


def _item_code(item: dict) -> str:
    for key in ("code", "airportCode", "cityCode", "geoCode", "iata", "airport"):
        v = item.get(key)
        if isinstance(v, str) and _IATA_RE.match(v.strip().upper()):
            return v.strip().upper()
    for nested in (item.get("city") or {}, item.get("airport") or {}):
        if isinstance(nested, dict):
            c = _item_code(nested)
            if c:
                return c
    return ""


def _parse_item(item: dict) -> Optional[AirportHit]:
    kind = str(item.get("type") or "")
    if kind not in _SUGGEST_TYPES:
        return None
    word = str(item.get("word") or item.get("displayName") or "").strip()
    city_name = str(item.get("cityName") or word).strip()
    name = _normalize_name(city_name or word) or city_name or word
    if not name:
        return None
    code = _item_code(item) or resolve_iata(city_name, airport_word=word)
    country = str(item.get("countryName") or "").strip()
    return AirportHit(name=name, code=code, display=word or name, kind=kind or "City", country=country)


def _dedupe_hits(hits: List[AirportHit]) -> List[AirportHit]:
    seen = set()
    out: List[AirportHit] = []
    for hit in hits:
        key = (hit.name, hit.code)
        if key in seen:
            continue
        seen.add(key)
        out.append(hit)
    out.sort(key=lambda h: (0 if h.code else 1, len(h.name)))
    return out


def search_airports(keyword: str, *, limit: int = 12) -> List[AirportHit]:
    """本地索引优先（含三字码），再合并携程联想。"""
    q = (keyword or "").strip()
    if len(q) < 1:
        return []
    now = time.monotonic()
    with _cache_lock:
        cached = _search_cache.get(q)
        if cached and now - cached[0] < _CACHE_TTL:
            return list(cached[1])

    hits = search_local(q, limit=limit)

    body = {
        "keyword": q,
        "searchType": "D",
        "platform": "online",
        "pageID": "102001",
        "head": {
            "Locale": "zh-CN",
            "LocaleController": "zh_cn",
            "Currency": "CNY",
            "PageId": "102001",
            "clientID": "jipiao-gui",
            "group": "ctrip",
            "Frontend": {"sessionID": 1, "pvid": 1},
            "HotelExtension": {"group": "CTRIP", "WebpSupport": False},
        },
    }
    try:
        with httpx.Client(timeout=8.0, headers={
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
        }) as client:
            resp = client.post(CTRIP_SUGGEST_URL, json=body)
            resp.raise_for_status()
            payload = resp.json()
        results = (payload.get("Response") or {}).get("searchResults") or []
        for item in results:
            if not isinstance(item, dict):
                continue
            hit = _parse_item(item)
            if hit:
                hits.append(hit)
    except Exception:
        pass

    hits = _dedupe_hits(hits)[:limit]
    with _cache_lock:
        _search_cache[q] = (now, hits)
    return hits
