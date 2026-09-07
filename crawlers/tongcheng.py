"""同程旅行机票：m.ly.com H5 + 手机UA + XHR 拦截

同程旅行（17u / ly.com）与智行同源，价格与携程/飞猪存在差异。
H5 入口为 m.ly.com 机票预订页 book1，列表通过 XHR 异步加载。

首跑请保持 debug=true，解析不到价格会把所有捕获的 XHR dump 到
debug/tongcheng_xhr_<date>.txt，据此再精确化字段解析。
"""
import os
import json
import urllib.parse
from typing import List, Optional

from core.models import FlightPrice
from core.flights import parse_itinerary_nos, format_itinerary, first_iata
from .base import BaseCrawler, BrowserQuoteMixin, INTER_CODES, CITY_NAME


class TongchengCrawler(BrowserQuoteMixin, BaseCrawler):
    name = "tongcheng"
    use_mobile = True

    # 同程旅行 H5 机票单程预订页（用户提供的真实入口）
    URL_TPL = (
        "https://m.ly.com/ft/touch/book1"
        "?date={date}&childticket=0,0&an=1&cn=0&baby=0"
        "&fromCity={from_name}&toCity={to_name}"
        "&fromcitycode={from_city}&fromCode={from_city}"
        "&tocitycode={to_city}&toCode={to_city}"
        "&acn={to_name}&dcn={from_name}"
        "&refId=&cabin=0&platcode=518&direct=0&thirdMemberId="
        "&fPassType=&nametype=0,0&frompage=HOME&outrefid="
    )

    URL_TPL_INTER = (
        "https://m.ly.com/iflight/touch/book1"
        "?date={date}&fromcitycode={from_city}&tocitycode={to_city}"
        "&fromCity={from_name}&toCity={to_name}&an=1&cn=0&baby=0&cabin=Y"
    )

    # 同程机票真实列表接口（book1/flights 仅日历；航班在 connection/flights）
    XHR_KEYS = [
        "flightbffv2/book1/",
        "iflightbff",
    ]

    def login_url(self) -> str:
        return "https://m.ly.com/passport/login.html"

    def fetch(self, from_city: str, to_city: str, dates: List[str]) -> List[FlightPrice]:
        return self.fetch_quoted(from_city, to_city, dates)

    def _is_inter_route(self, from_city: str, to_city: str) -> bool:
        codes = {(from_city or "").upper(), (to_city or "").upper()}
        if codes & INTER_CODES:
            return True
        mainland = {c for c in CITY_NAME if c not in INTER_CODES}
        return bool(codes - mainland)

    def _list_url(self, from_city: str, to_city: str, date: str) -> str:
        from_name, to_name = self.names_for(from_city, to_city)
        fc, tc = from_city.upper(), to_city.upper()
        tpl = self.URL_TPL_INTER if self._is_inter_route(fc, tc) else self.URL_TPL
        return tpl.format(
            from_city=fc,
            to_city=tc,
            from_name=urllib.parse.quote(from_name),
            to_name=urllib.parse.quote(to_name),
            date=date,
        )

    def _quote_oneway_on_page(self, from_city: str, to_city: str, date: str,
                              flight_nos=None, exact: bool = False) -> Optional[dict]:
        url = self._list_url(from_city, to_city, date)
        captured = self.xhr_goto(url)
        self._dump_xhr(captured, f"tongcheng_xhr_{from_city}_{to_city}_{date}")
        offers = self._parse_captured(captured, date)
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

    def _xhr_payload_ready(self, captured: list) -> bool:
        for item in captured or []:
            if self._parse_offers(item.get("text") or ""):
                return True
        return False

    @classmethod
    def _fps_price(cls, fp: dict) -> Optional[float]:
        prices = []
        for policy in fp.get("lps") or []:
            if not isinstance(policy, dict):
                continue
            if not cls._has_ticket(policy.get("brs")):
                continue
            for key in ("sp", "atp", "tp", "lp"):
                try:
                    v = float(policy.get(key))
                    if 100 <= v <= 50000:
                        prices.append(v)
                except Exception:
                    pass
        return min(prices) if prices else None

    @classmethod
    def _fps_legs(cls, fp: dict, date: str) -> list:
        legs = []
        for seg in fp.get("ss") or []:
            if not isinstance(seg, dict):
                continue
            nos = cls._flight_nos_of(seg)
            fn = nos[0] if nos else ""
            dep = first_iata(seg, "dac", "dc", "dcc", "fromCode", "dport")
            arr = first_iata(seg, "aac", "ac", "acc", "toCode", "aport")
            d = str(seg.get("dt") or date or "")
            if len(d) >= 10 and d[4] == "-":
                d = d[:10]
            else:
                d = date
            if fn and dep and arr and d:
                legs.append({
                    "flight_no": fn,
                    "from_code": dep,
                    "to_code": arr,
                    "date": d,
                })
        return legs

    @classmethod
    def _parse_fps(cls, data: dict, date: str) -> List[dict]:
        offers: List[dict] = []
        seen = set()
        for fp in data.get("fps") or []:
            if not isinstance(fp, dict):
                continue
            price = cls._fps_price(fp)
            if price is None:
                continue
            legs = cls._fps_legs(fp, date)
            nos = [lg["flight_no"] for lg in legs]
            trans = legs[0].get("to_code") or "" if len(legs) >= 2 else (fp.get("sc") or "")
            ss = fp.get("ss") or []
            first_seg = ss[0] if ss else {}
            last_seg = ss[-1] if ss else {}
            offer = {
                "airline": first_seg.get("asn") or first_seg.get("ac") or "",
                "flight_no": format_itinerary(nos) if nos else "",
                "flight_nos": nos,
                "depart_time": str(first_seg.get("dst") or first_seg.get("dt") or ""),
                "arrive_time": str(last_seg.get("ast") or last_seg.get("at") or ""),
                "price": price,
                "legs": legs,
                "trans_code": trans,
            }
            key = (offer["flight_no"], offer["depart_time"], offer["price"])
            if key in seen:
                continue
            seen.add(key)
            offers.append(offer)
        return offers

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

    @staticmethod
    def _has_ticket(brs) -> bool:
        """brs[].al 为余票数，-1 表示充足，>0 表示有票，0 表示无票。"""
        if not isinstance(brs, list) or not brs:
            return True  # 无余票信息时不误杀
        for b in brs:
            if isinstance(b, dict):
                al = b.get("al")
                try:
                    if al is None or int(al) != 0:
                        return True
                except Exception:
                    return True
        return False

    @classmethod
    def _flight_nos_of(cls, flt: dict) -> List[str]:
        for key in ("fn", "fno", "flightNo", "flightno", "no", "flno"):
            nos = parse_itinerary_nos(flt.get(key))
            if nos:
                return nos
        for key in ("fns", "flns", "flightNos"):
            nos = parse_itinerary_nos(flt.get(key))
            if nos:
                return nos
        return []

    @classmethod
    def _legs_of(cls, flt: dict, date: str) -> list:
        nos = cls._flight_nos_of(flt)
        segs = flt.get("sts") or flt.get("stops") or flt.get("ts") or flt.get("seg") or []
        legs = []
        if isinstance(segs, list) and segs:
            for i, seg in enumerate(segs):
                if not isinstance(seg, dict):
                    continue
                seg_nos = parse_itinerary_nos(
                    seg.get("fn") or seg.get("fno") or seg.get("flightNo")
                )
                fn = (seg_nos[0] if seg_nos else (nos[i] if i < len(nos) else ""))
                dep = first_iata(seg, "dc", "dcc", "dac", "fromCode", "dport")
                arr = first_iata(seg, "ac", "acc", "aac", "toCode", "aport")
                d = str(seg.get("dt") or seg.get("date") or date or "")
                if len(d) >= 10 and d[4] == "-":
                    d = d[:10]
                else:
                    d = date
                if fn and dep and arr and d:
                    legs.append({
                        "flight_no": fn,
                        "from_code": dep,
                        "to_code": arr,
                        "date": d,
                    })
        if not legs and len(nos) >= 1:
            dep = first_iata(flt, "dc", "dcc", "fromCode", "dport", "dac")
            arr = first_iata(flt, "ac", "acc", "toCode", "aport", "aac")
            trans = first_iata(flt, "tsc", "sc", "tc", "transferCityCode")
            d = str(flt.get("dt") or date or "")
            if len(d) >= 10 and d[4] == "-":
                d = d[:10]
            else:
                d = date
            if len(nos) == 1 and dep and arr and d:
                legs.append({
                    "flight_no": nos[0],
                    "from_code": dep,
                    "to_code": arr,
                    "date": d,
                })
            elif len(nos) >= 2 and trans and dep and arr and d:
                legs = [
                    {"flight_no": nos[0], "from_code": dep, "to_code": trans, "date": d},
                    {"flight_no": nos[-1], "from_code": trans, "to_code": arr, "date": d},
                ]
        return legs

    @classmethod
    def _item_price(cls, flt: dict) -> Optional[float]:
        prices = []
        for key in ("atp", "lp", "tp", "price"):
            try:
                v = float(flt.get(key))
                if 100 <= v <= 50000:
                    prices.append(v)
            except Exception:
                pass
        lps = flt.get("lps")
        if isinstance(lps, list):
            for policy in lps:
                if not isinstance(policy, dict):
                    continue
                if not cls._has_ticket(policy.get("brs")):
                    continue
                try:
                    v = float(policy.get("atp"))
                    if 100 <= v <= 50000:
                        prices.append(v)
                except Exception:
                    pass
        return min(prices) if prices else None

    @classmethod
    def _parse_offers(cls, text: str) -> List[dict]:
        if not text:
            return []
        try:
            obj = json.loads(text)
        except Exception:
            return []
        data = obj.get("data")
        if not isinstance(data, dict):
            return []

        date = str(data.get("dt") or data.get("date") or data.get("dd") or "")[:10]
        offers: List[dict] = []
        seen = set()
        offers.extend(cls._parse_fps(data, date))
        fl = data.get("fl")
        if isinstance(fl, list):
            for flt in fl:
                if not isinstance(flt, dict):
                    continue
                price = cls._item_price(flt)
                if price is None:
                    continue
                nos = cls._flight_nos_of(flt)
                legs = cls._legs_of(flt, date)
                trans = ""
                if len(legs) >= 2:
                    trans = legs[0].get("to_code") or ""
                elif not trans:
                    trans = first_iata(flt, "tsc", "sc", "tc", "transferCityCode")
                offer = {
                    "airline": flt.get("alc") or flt.get("an") or flt.get("airline") or "",
                    "flight_no": format_itinerary(nos) if nos else "",
                    "flight_nos": nos,
                    "depart_time": str(flt.get("dt") or flt.get("deptime") or ""),
                    "arrive_time": str(flt.get("at") or flt.get("arrtime") or ""),
                    "price": price,
                    "legs": legs,
                    "trans_code": trans,
                }
                key = (offer["flight_no"], offer["depart_time"], offer["price"])
                if key in seen:
                    continue
                seen.add(key)
                offers.append(offer)
        if not offers:
            lp = data.get("lp")
            try:
                lpv = float(lp)
                if 100 <= lpv <= 50000:
                    offers.append({"price": lpv, "flight_no": "", "airline": ""})
            except Exception:
                pass
        return offers

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
            self.logger.info("[tongcheng] 已保存 XHR: %s", path)
        except Exception:
            pass
