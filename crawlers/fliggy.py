"""飞猪 (Fliggy/淘宝旅行) 机票：纯 httpx 逆向，无需浏览器。

逆向要点 (实测):
  - 走阿里 MTOP h5 网关: https://h5api.m.taobao.com/h5/{api}/{ver}/
  - 列表 API: mtop.trip.flight.flightSearch v1.0 (GET, data=JSON)
  - 签名: sign = md5(token + "&" + t + "&" + appKey + "&" + data)
      token = cookie _m_h5_tk 的第一段 (下划线前)。
      首次无 token 调用会返回 FAIL_SYS_TOKEN_EMPTY 并下发 _m_h5_tk cookie, 重签后即可。
  - appKey = 12574478 (h5 淘宝默认)。
  - 返回 data.items[]{itemType: DIRECT/TRANSFER/...}.itemDatas[] 为真实航班,
    含 flightName / airlineChineseName / bestPrice 等; data.lowestPrice 为最低价。

"hoop": 一圈一圈重试 (可设上限), 直到真正拿到真实价格 (items/lowestPrice) 才停止。
"""
from __future__ import annotations

import os
import hashlib
import json
import threading
import time
from typing import Any, List, Optional

import httpx

from core.models import FlightPrice
from core.flights import parse_itinerary_nos, format_itinerary, first_iata
from .base import BaseCrawler, INTER_CODES, CITY_NAME

UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)

APPKEY = "12574478"
MTOP_GW = "https://h5api.m.taobao.com/h5/{api}/{ver}/"
SEARCH_API = "mtop.trip.flight.flightSearch"
SEARCH_VER = "1.0"
REFERER = "https://h5.m.taobao.com/trip/flight/search/index.html"

_FLIGHT_ITEM_TYPES = {"DIRECT", "TRANSFER", "TRANSFER_RECOMMEND", "STOP"}


class FliggyCrawler(BaseCrawler):
    name = "fliggy"
    use_mobile = True

    RATE_MAX = 5
    RATE_WINDOW = 60.0

    def __init__(self, config: dict, logger):
        super().__init__(config, logger)
        self.max_attempts: int = int(config.get("max_attempts", 5))
        self.max_poll: int = int(config.get("fliggy_max_poll", 3))
        self.backoff_s: float = float(config.get("backoff_seconds", 5))
        self.rate_limit: bool = bool(config.get("rate_limit", True))
        self._success_times: list = []
        self._rate_lock = threading.Lock()

    def login_url(self) -> str:
        return "https://login.taobao.com/member/login.jhtml"

    def _rate_acquire(self):
        while True:
            with self._rate_lock:
                now = time.time()
                self._success_times[:] = [t for t in self._success_times if now - t < self.RATE_WINDOW]
                if len(self._success_times) < self.RATE_MAX:
                    return
                wait = self.RATE_WINDOW - (now - self._success_times[0]) + 0.5
            time.sleep(min(wait, 30))

    def _rate_record(self):
        with self._rate_lock:
            self._success_times.append(time.time())

    @staticmethod
    def _sign(token: str, t: str, data: str) -> str:
        return hashlib.md5(f"{token}&{t}&{APPKEY}&{data}".encode("utf-8")).hexdigest()

    @staticmethod
    def _token(client: httpx.Client) -> str:
        tk = client.cookies.get("_m_h5_tk", "") or ""
        return tk.split("_")[0] if tk else ""

    def _mtop_get(self, client: httpx.Client, api: str, ver: str, data_obj: dict) -> httpx.Response:
        data = json.dumps(data_obj, ensure_ascii=False, separators=(",", ":"))
        t = str(int(time.time() * 1000))
        token = self._token(client)
        params = {
            "jsv": "2.7.0", "appKey": APPKEY, "t": t, "sign": self._sign(token, t, data),
            "api": api, "v": ver, "type": "originaljson", "dataType": "json",
            "data": data,
        }
        return client.get(MTOP_GW.format(api=api, ver=ver), params=params, timeout=25)

    @staticmethod
    def _looks_like_real_price(body: str) -> bool:
        if not body or "SUCCESS" not in body:
            return False
        try:
            obj = json.loads(body)
        except Exception:
            return False
        if "SUCCESS" not in str(obj.get("ret", "")):
            return False
        data = obj.get("data") or {}
        if not data.get("success"):
            return False
        return bool(data.get("items")) or bool(data.get("lowestPrice"))

    def _one_attempt(self, depart_code, arrive_code, depart_date, back_date=None):
        client = httpx.Client(
            headers={
                "User-Agent": self._ua() or UA,
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": REFERER,
                "sec-ch-ua-platform": '"iOS"',
                "sec-ch-ua-mobile": "?1",
            },
            timeout=25,
            follow_redirects=True,
        )
        try:
            data_obj = {
                "searchType": 2 if back_date else 1,
                "depCityCode": depart_code,
                "arrCityCode": arrive_code,
                "leaveDate": depart_date,
                "itineraryFilter": "0",
                "leaveCabinClass": "0",
                "useAcrossAgent": 1,
            }
            if back_date:
                data_obj["backDate"] = back_date
            last = ""
            blocked = False
            for _ in range(max(self.max_poll, 2)):
                r = self._mtop_get(client, SEARCH_API, SEARCH_VER, data_obj)
                last = r.text
                if r.status_code != 200:
                    blocked = True
                    time.sleep(1.2)
                    continue
                if self._looks_like_real_price(last):
                    return True, json.loads(last), blocked
                if "TOKEN_EMPTY" in last or "TOKEN_EXPIRED" in last or "令牌" in last:
                    time.sleep(0.4)
                    continue
                if "FAIL_SYS_ILLEGAL_ACCESS" in last or "x5sec" in last or "RGV587" in last:
                    blocked = True
                time.sleep(1.0)
            return False, None, blocked
        except Exception as ex:
            self.logger.warning("[fliggy] 请求异常: %s", ex)
            return False, None, False
        finally:
            client.close()

    def _hoop(self, fc, tc, date, back_date=None) -> Optional[dict]:
        attempt = 0
        tag = f"{date}/{back_date}" if back_date else date
        while True:
            attempt += 1
            if self.rate_limit:
                self._rate_acquire()
            ok, raw, blocked = self._one_attempt(fc, tc, date, back_date=back_date)
            if ok:
                if self.rate_limit:
                    self._rate_record()
                self.logger.info("[fliggy] %s 第 %d 圈拿到真实价格", tag, attempt)
                return raw
            if self.max_attempts and attempt >= self.max_attempts:
                self.logger.warning("[fliggy] %s 达到最大重试圈数 %d 仍未拿到价格%s",
                                     tag, self.max_attempts, "(疑似风控)" if blocked else "")
                return None
            time.sleep(self.backoff_s * (2 if blocked else 1))

    def fetch(self, from_city: str, to_city: str, dates: List[str]) -> List[FlightPrice]:
        return self.fetch_quoted(from_city, to_city, dates)

    def _is_inter_route(self, from_city: str, to_city: str) -> bool:
        codes = {(from_city or "").upper(), (to_city or "").upper()}
        if codes & INTER_CODES:
            return True
        mainland = {c for c in CITY_NAME if c not in INTER_CODES}
        return bool(codes - mainland)

    def quote_oneway(self, from_city: str, to_city: str, date: str,
                     flight_nos=None, exact: bool = False) -> Optional[dict]:
        if self._is_inter_route(from_city, to_city):
            self.logger.info("[fliggy] 国际航线 %s→%s，国内 MTOP 接口不支持", from_city, to_city)
            return None
        raw = self._hoop(from_city.upper(), to_city.upper(), date)
        self._dump_raw(raw, f"fliggy_raw_{from_city}_{to_city}_{date}")
        if raw is None:
            return None
        offers = self._parse_offers(raw)
        best = self.pick_best(offers, flight_nos=flight_nos or [], exact=exact)
        if best:
            return best
        if parse_itinerary_nos(flight_nos or []):
            return None
        lp = (raw.get("data") or {}).get("lowestPrice")
        try:
            if lp and float(lp) > 0:
                return {"price": float(lp), "flight_no": "", "airline": ""}
        except Exception:
            pass
        return None

    def quote_round(self, from_city: str, to_city: str, date: str, return_date: str,
                    flight_nos=None, return_flight_nos=None) -> Optional[dict]:
        # 飞猪往返接口通常先出去程列表，套票总价不可靠；交给穷举用去程+返程单程相加
        return None

    @staticmethod
    def _to_price(v: Any) -> Optional[float]:
        try:
            f = float(v)
            return f if f > 0 else None
        except (TypeError, ValueError):
            return None

    @classmethod
    def _harvest_item(cls, ds: dict) -> dict:
        nos: List[str] = []
        trans = ""
        dep = arr = ""
        arr_date = ""

        def walk(node):
            nonlocal trans, dep, arr, arr_date
            if isinstance(node, dict):
                for key in ("flightName", "flightNo", "marketingFlightNo", "flightCode"):
                    nos.extend(parse_itinerary_nos(node.get(key)))
                t = first_iata(
                    node, "transferCityCode", "transitCityCode",
                    "stopCityCode", "transferAirportCode",
                )
                if t:
                    trans = t
                d = first_iata(
                    node, "depCityCode", "depAirportCode", "departCityCode",
                    "dCityCode", "orgCityCode",
                )
                a = first_iata(
                    node, "arrCityCode", "arrAirportCode", "arriveCityCode",
                    "aCityCode", "dstCityCode",
                )
                if d:
                    dep = dep or d
                if a:
                    arr = a
                for dk in ("arrDate", "arriveDate", "arrDateTime"):
                    v = str(node.get(dk) or "")
                    if len(v) >= 10 and v[4] == "-":
                        arr_date = v[:10]
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(ds)
        uniq = []
        seen = set()
        for n in nos:
            if n not in seen:
                seen.add(n)
                uniq.append(n)
        return {
            "flight_nos": uniq,
            "trans_code": trans,
            "from_code": dep,
            "to_code": arr,
            "arrive_date": arr_date,
        }

    @classmethod
    def _parse_offers(cls, raw: Optional[dict]) -> List[dict]:
        if not raw:
            return []
        data = raw.get("data", raw) or {}
        items = data.get("items") or []
        offers: List[dict] = []
        seen = set()
        for group in items:
            if group.get("itemType") not in _FLIGHT_ITEM_TYPES:
                continue
            for ds in group.get("itemDatas") or []:
                harvested = cls._harvest_item(ds)
                nos = harvested["flight_nos"] or parse_itinerary_nos(ds.get("flightName"))
                if not nos:
                    continue
                price = cls._to_price(ds.get("bestPrice"))
                offer = {
                    "airline": ds.get("airlineChineseName") or ds.get("airlineChineseShortName"),
                    "flight_no": format_itinerary(nos),
                    "flight_nos": nos,
                    "depart_time": ds.get("depTime") or ds.get("depTimeShow"),
                    "arrive_time": ds.get("arrTime") or ds.get("arrTimeShow"),
                    "price": price,
                    "trans_code": harvested.get("trans_code") or "",
                    "arrive_date": harvested.get("arrive_date") or "",
                }
                key = (offer["flight_no"], offer["depart_time"], offer["price"])
                if key not in seen:
                    seen.add(key)
                    offers.append(offer)
        return offers

    @staticmethod
    def _lowest_offer(offers: List[dict]) -> Optional[dict]:
        priced = [o for o in offers if o.get("price")]
        return min(priced, key=lambda o: o["price"]) if priced else None

    def _dump_raw(self, raw: Optional[dict], tag: str):
        if not self.debug or not raw:
            return
        os.makedirs(self.debug_dir, exist_ok=True)
        path = os.path.join(self.debug_dir, tag + ".json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
            self.logger.info("[fliggy] 已保存原始响应: %s", path)
        except Exception:
            pass
