"""携程机票：m.ctrip.com H5 (taro) + 手机UA + XHR 拦截

数据源接口 flightListSearchForH5 返回 JSON。
每个航班(fltitem)的 policyinfo 内含多个销售政策，
每个政策有 tprice(含税价) 与 quantity(余票)。quantity 为 null/0 表示
无票(诱饵价)，必须剔除，否则会拿到买不到的超低价。
"""
import os
import re
import json
import urllib.parse
from typing import List, Optional

from core.models import FlightPrice
from core.flights import parse_itinerary_nos, format_itinerary, first_iata
from .base import BaseCrawler, BrowserQuoteMixin, INTER_CODES, CITY_NAME


class CtripCrawler(BrowserQuoteMixin, BaseCrawler):
    name = "ctrip"
    use_mobile = True  # 移动端 H5
    _xhr_poll_rounds = 18

    # 国内 taro 单程列表
    URL_TPL = ("https://m.ctrip.com/html5/flight/taro/first?from=inner"
               "&tripType=ONE_WAY"
               "&dcity={from_city}&dcityName={from_name}"
               "&acity={to_city}&acityName={to_name}"
               "&ddate={date}")

    # 国际/港澳台 swift 列表（往返可带 rdate）
    URL_TPL_INTER = (
        "https://m.ctrip.com/html5/flight/swift/international/list"
        "?dcity={from_city}&acity={to_city}&ddate={date}{rdate_qs}"
    )

    # 航班列表接口；swift 列表页 HTML 内嵌 tprice
    XHR_KEYS = [
        "flightListSearchForH5",
        "flightListSearch",
        "FlightListSearch",
        "soa2/Flight",
        "swift/international/list",
        "swift/list",
    ]

    _STEALTH_JS = r"""
    (function(){
      const define = (obj, prop, val) => {
        try { Object.defineProperty(obj, prop, {get: () => val, configurable: true}); } catch(e){}
      };
      define(navigator, 'webdriver', undefined);
      define(navigator, 'languages', ['zh-CN','zh']);
      define(navigator, 'platform', 'iPhone');
      define(navigator, 'maxTouchPoints', 5);
      define(navigator, 'hardwareConcurrency', 6);
      define(navigator, 'vendor', 'Apple Computer, Inc.');
      try { delete navigator.deviceMemory; } catch(e){}
      define(navigator, 'plugins', []);
      window.chrome = undefined;
      const patchGL = (proto) => {
        if (!proto || !proto.getParameter) return;
        const orig = proto.getParameter;
        proto.getParameter = function(p){
          if (p === 37445) return 'Apple Inc.';
          if (p === 37446) return 'Apple GPU';
          if (p === 7936) return 'WebKit';
          if (p === 7937) return 'WebKit WebGL';
          return orig.call(this, p);
        };
      };
      try { patchGL(WebGLRenderingContext.prototype); } catch(e){}
      try { patchGL(WebGL2RenderingContext.prototype); } catch(e){}
      try { define(window, 'ontouchstart', null); } catch(e){}
    })();
    """

    def login_url(self) -> str:
        return "https://accounts.ctrip.com/H5login/login_ctrip"

    def fetch(self, from_city: str, to_city: str, dates: List[str]) -> List[FlightPrice]:
        return self.fetch_quoted(from_city, to_city, dates)

    def _is_inter_route(self, from_city: str, to_city: str) -> bool:
        codes = {(from_city or "").upper(), (to_city or "").upper()}
        if codes & INTER_CODES:
            return True
        mainland = {c for c in CITY_NAME if c not in INTER_CODES}
        return bool(codes - mainland)

    def _list_url(self, from_city: str, to_city: str, date: str,
                  return_date: Optional[str] = None) -> str:
        fc, tc = from_city.upper(), to_city.upper()
        if self._is_inter_route(fc, tc):
            rdate_qs = f"&rdate={return_date}" if return_date else ""
            return self.URL_TPL_INTER.format(
                from_city=fc, to_city=tc, date=date, rdate_qs=rdate_qs,
            )
        from_name, to_name = self.names_for(from_city, to_city)
        return self.URL_TPL.format(
            from_city=fc,
            to_city=tc,
            from_name=urllib.parse.quote(from_name),
            to_name=urllib.parse.quote(to_name),
            date=date,
        )

    def quote_round(self, from_city: str, to_city: str, date: str, return_date: str,
                    flight_nos=None, return_flight_nos=None) -> Optional[dict]:
        if not self._is_inter_route(from_city, to_city):
            return None
        url = self._list_url(from_city, to_city, date, return_date=return_date)
        captured = self.xhr_goto(url)
        self._dump_xhr(captured, f"ctrip_xhr_{from_city}_{to_city}_{date}_{return_date}")
        offers = self._parse_captured(captured, date)
        return self.pick_best(offers, flight_nos=flight_nos or [], exact=False)

    def _quote_oneway_on_page(self, from_city: str, to_city: str, date: str,
                              flight_nos=None, exact: bool = False) -> Optional[dict]:
        url = self._list_url(from_city, to_city, date)
        captured = self.xhr_goto(url)
        self._dump_xhr(captured, f"ctrip_xhr_{from_city}_{to_city}_{date}")
        offers = self._parse_captured(captured, date)
        if not offers:
            try:
                offers = self._parse_offers(self._quote_page.content())
            except Exception:
                pass
        best = self.pick_best(offers, flight_nos=flight_nos or [], exact=exact)
        if best:
            return best
        if parse_itinerary_nos(flight_nos or []):
            self._debug_snapshot(self._quote_page, f"nopx_{date}")
            return None
        prices = [o["price"] for o in offers if o.get("price")]
        if prices:
            return {"price": min(prices), "flight_no": "", "airline": ""}
        self._debug_snapshot(self._quote_page, f"nopx_{date}")
        return None

    def _dismiss_error(self, page) -> bool:
        for label in ("重试", "刷新", "再试一次", "重新加载"):
            try:
                loc = page.get_by_text(label, exact=False)
                if loc.count() > 0:
                    loc.first.click(timeout=3000)
                    page.wait_for_timeout(2500)
                    self.logger.info("[ctrip] 已点击「%s」", label)
                    return True
            except Exception:
                pass
        return False

    def xhr_goto(self, url: str) -> list:
        page = getattr(self, "_quote_page", None)
        if page is None:
            raise RuntimeError("quote_session 未启动")
        bucket = getattr(self, "_xhr_bucket", None)
        if bucket is None:
            self._xhr_bucket = []
            bucket = self._xhr_bucket
        bucket.clear()
        self.logger.info("[%s] GET %s", self.name, url)
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        self._dismiss_error(page)
        for i in range(self._xhr_wait_rounds()):
            page.wait_for_timeout(2000)
            try:
                page.mouse.wheel(0, 2000)
            except Exception:
                pass
            if self._xhr_payload_ready(bucket):
                break
            if i in (4, 9) and self._dismiss_error(page):
                bucket.clear()
        return list(bucket)

    def _xhr_payload_ready(self, captured: list) -> bool:
        for item in captured or []:
            if self._parse_offers(item.get("text") or ""):
                return True
        return False

    def _parse_captured(self, captured: list, date: str = "") -> List[dict]:
        offers: List[dict] = []
        for item in captured:
            offers.extend(self._parse_offers(item.get("text") or ""))
        if date:
            for offer in offers:
                for leg in offer.get("legs") or []:
                    if not leg.get("date"):
                        leg["date"] = date
        return offers

    @classmethod
    def _basinfo(cls, node: dict) -> dict:
        if not isinstance(node, dict):
            return {}
        if isinstance(node.get("basinfo"), dict):
            return node["basinfo"]
        return node

    @classmethod
    def _leg_from_bas(cls, bas: dict, default_date: str = "") -> Optional[dict]:
        nos = parse_itinerary_nos(
            bas.get("flgno") or bas.get("flightNo") or bas.get("flightno")
            or bas.get("fn") or bas.get("no")
        )
        if not nos:
            return None
        dep = first_iata(
            bas, "dport", "dcity", "dportcode", "dcitycode", "dCityCode",
        )
        if not dep and isinstance(bas.get("dportinfo"), dict):
            dep = first_iata(bas["dportinfo"], "aport", "city", "code", "aportcode")
        arr = first_iata(
            bas, "aport", "acity", "aportcode", "acitycode", "aCityCode",
        )
        if not arr and isinstance(bas.get("aportinfo"), dict):
            arr = first_iata(bas["aportinfo"], "aport", "city", "code", "aportcode")
        date = default_date
        for key in ("ddate", "date", "depdate"):
            v = str(bas.get(key) or "")
            if len(v) >= 10 and v[4] == "-":
                date = v[:10]
                break
        return {
            "flight_no": nos[0],
            "from_code": dep,
            "to_code": arr,
            "date": date,
        }

    @classmethod
    def _item_legs(cls, flt: dict) -> list:
        dateinfo = flt.get("dateinfo") if isinstance(flt.get("dateinfo"), dict) else {}
        date = ""
        for key in ("ddate", "date"):
            v = str(dateinfo.get(key) or "")
            if len(v) >= 10 and v[4] == "-":
                date = v[:10]
                break
        legs = []
        seen = set()
        stations = flt.get("mutilstn") or flt.get("mutilstns") or []
        if not isinstance(stations, list) or not stations:
            stations = [flt]
        for st in stations:
            bas = cls._basinfo(st if isinstance(st, dict) else {})
            leg = cls._leg_from_bas(bas, date)
            if not leg:
                continue
            key = (leg["flight_no"], leg["from_code"], leg["to_code"], leg["date"])
            if key in seen:
                continue
            seen.add(key)
            legs.append(leg)
        if not legs:
            leg = cls._leg_from_bas(cls._basinfo(flt), date)
            if leg:
                legs.append(leg)
        return legs

    @classmethod
    def _item_price(cls, flt: dict) -> Optional[float]:
        prices: list = []

        def scan(node):
            if isinstance(node, dict):
                if "tprice" in node:
                    tp = node.get("tprice")
                    qty = node.get("quantity", None)
                    try:
                        tpv = float(tp)
                    except Exception:
                        tpv = None
                    has_ticket = qty is not None
                    if has_ticket:
                        try:
                            has_ticket = int(qty) > 0
                        except Exception:
                            has_ticket = True
                    if tpv is not None and has_ticket and 100 <= tpv <= 50000:
                        prices.append(tpv)
                for v in node.values():
                    scan(v)
            elif isinstance(node, list):
                for v in node:
                    scan(v)

        scan(flt.get("policyinfo") or flt)
        return min(prices) if prices else None

    @classmethod
    def _parse_offers(cls, text: str) -> List[dict]:
        if not text:
            return []
        try:
            obj = json.loads(text)
        except Exception:
            prices = cls._regex_fallback(text)
            return [{"price": float(p), "flight_no": ""} for p in prices]

        flts = None
        if isinstance(obj, dict):
            flts = obj.get("fltitem")
            if not isinstance(flts, list):
                data = obj.get("data")
                if isinstance(data, dict):
                    flts = data.get("fltitem")
                elif isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and isinstance(item.get("fltitem"), list):
                            flts = item.get("fltitem")
                            break
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict) and isinstance(item.get("fltitem"), list):
                    flts = item.get("fltitem")
                    break

        if not isinstance(flts, list):
            prices = cls._regex_fallback(text)
            return [{"price": float(p), "flight_no": ""} for p in prices]

        offers: List[dict] = []
        seen = set()
        for flt in flts:
            if not isinstance(flt, dict):
                continue
            price = cls._item_price(flt)
            if price is None:
                continue
            legs = cls._item_legs(flt)
            nos = [lg["flight_no"] for lg in legs] or parse_itinerary_nos(
                cls._basinfo(flt).get("flgno")
            )
            trans = ""
            if len(legs) >= 2:
                trans = legs[0].get("to_code") or ""
            bas = cls._basinfo(flt)
            offer = {
                "airline": bas.get("airsname") or bas.get("airname") or "",
                "flight_no": format_itinerary(nos) if nos else "",
                "flight_nos": nos,
                "depart_time": str(bas.get("dtime") or bas.get("deptime") or ""),
                "arrive_time": str(bas.get("atime") or bas.get("arrtime") or ""),
                "price": price,
                "legs": [lg for lg in legs if lg.get("from_code") and lg.get("to_code") and lg.get("date")],
                "trans_code": trans,
            }
            key = (offer["flight_no"], offer["depart_time"], offer["price"])
            if key in seen:
                continue
            seen.add(key)
            offers.append(offer)
        return offers

    @staticmethod
    def _regex_fallback(text: str) -> list:
        """JSON 解析失败时的兜底：仅取 tprice 紧跟 quantity 非 null 的。"""
        prices: list = []
        for m in re.finditer(
            r'"tprice"\s*:\s*(\d+(?:\.\d+)?)\s*,\s*"quantity"\s*:\s*(null|"?\d+"?)',
            text,
        ):
            qty = m.group(2)
            if qty == "null":
                continue
            try:
                if int(qty.strip('"')) <= 0:
                    continue
            except Exception:
                pass
            prices.append(int(float(m.group(1))))
        return [p for p in prices if 100 <= p <= 50000]

    def _dump_xhr(self, captured: list, tag: str):
        if not self.debug or not captured:
            return
        os.makedirs(self.debug_dir, exist_ok=True)
        path = os.path.join(self.debug_dir, tag + ".txt")
        try:
            with open(path, "w", encoding="utf-8") as f:
                for i, item in enumerate(captured):
                    f.write(f"\n----- [{i}] {item['url']} -----\n")
                    f.write((item.get("text") or "")[:200000])
                    f.write("\n")
            self.logger.info("[ctrip] 已保存 XHR: %s", path)
        except Exception:
            pass
