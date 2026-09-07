"""爬虫基类：Playwright 持久化上下文 + 反爬基础处理 + XHR拦截"""
import os
import re
import json
import random
import time
import logging
from contextlib import contextmanager
from typing import List, Optional, Callable

from playwright.sync_api import sync_playwright, BrowserContext, Page, Response

from core.models import FlightPrice
from core.flights import parse_itinerary_nos, format_itinerary, parse_legs, normalize_offer, guess_legs
from core.fareplan import build_fare_plan, format_leg_label


# 桌面/手机 UA
DESKTOP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0.0.0 Safari/537.36")
MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) "
             "Version/16.6 Mobile/15E148 Safari/604.1")

# 三字码 → 中文城市名。去哪儿等平台用城市名搜，不能把 HKG 原样当 depCity。
CITY_NAME = {
    "SZX": "深圳", "KMG": "昆明", "PEK": "北京", "PKX": "北京", "BJS": "北京",
    "SHA": "上海", "PVG": "上海", "CAN": "广州", "HGH": "杭州",
    "CTU": "成都", "TFU": "成都", "SIA": "西安", "CKG": "重庆", "NKG": "南京",
    "WUH": "武汉", "CSX": "长沙", "XMN": "厦门", "SYX": "三亚",
    "HAK": "海口", "LJG": "丽江", "DLU": "大理", "JHG": "西双版纳",
    "DIG": "香格里拉", "TCZ": "腾冲", "YNT": "烟台",
    "HKG": "香港", "MAC": "澳门", "MFM": "澳门",
    "ICN": "首尔", "SEL": "首尔", "GMP": "首尔",
    "TPE": "台北", "TSA": "台北", "KHH": "高雄",
}

# 去哪儿把港澳台算「国际/中国港澳台」，不能走国内 flightlist
INTER_CODES = {"HKG", "MAC", "MFM", "TPE", "TSA", "KHH", "RMQ", "TNN"}
INTER_NAMES = {"香港", "澳门", "台北", "高雄", "台中", "台南"}


class BaseCrawler:
    name: str = "base"
    # 子类覆盖：是否使用手机端模拟
    use_mobile: bool = False
    CITY_NAME = CITY_NAME

    def __init__(self, config: dict, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.headless: bool = config.get("headless", True)
        self.timeout_ms: int = int(config.get("timeout_seconds", 45)) * 1000
        self.delay_min: float = float(config.get("delay_min", 30))
        self.delay_max: float = float(config.get("delay_max", 60))
        self.debug: bool = bool(config.get("debug", False))
        self.debug_dir: str = config.get("debug_dir", "debug")
        self.user_data_root: str = config.get("user_data_dir", "user_data")
        self._route_from_name: Optional[str] = None
        self._route_to_name: Optional[str] = None
        self._route_from_code: Optional[str] = None
        self._route_to_code: Optional[str] = None
        self._route_flight_nos: list = []
        self._route_return_date: Optional[str] = None
        self._route_return_flight_nos: list = []
        self._route_trip: str = "one_way"
        self._route_monitor_mode: str = "lowest"

    def city_name(self, code: str, fallback: Optional[str] = None) -> str:
        """三字码转中文城市名。优先用配置里的中文名，避免 HKG 被原样传给去哪儿。"""
        mapping = getattr(self, "CITY_NAME", {}) or {}
        fb = (fallback or "").strip()
        if fb and not re.fullmatch(r"[A-Za-z]{3}", fb):
            return fb
        name = mapping.get((code or "").upper())
        if name:
            return name
        if fb:
            return fb
        self.logger.warning("[%s] 城市码 %s 无中文名映射，请在 config 填写 from_name/to_name",
                            self.name, code)
        return code

    def filter_offers(self, offers: list, flight_nos=None, exact: bool = False) -> list:
        """按航班号筛选报价。flight_nos=None 时用配置的去程航班号。"""
        from core.flights import (
            parse_wanted_flight_nos, parse_itinerary_nos,
            itinerary_matches, format_wanted,
        )
        raw = self._route_flight_nos if flight_nos is None else flight_nos
        wanted = parse_wanted_flight_nos(raw)
        if not wanted:
            return offers
        matched = []
        for o in offers:
            nos = o.get("flight_nos") or parse_itinerary_nos(o.get("flight_no"))
            if itinerary_matches(nos, wanted, exact=exact):
                matched.append(o)
        if not matched:
            available = [o.get("flight_no") for o in offers if o.get("flight_no")]
            self.logger.warning(
                "[%s] 未找到指定航班 %s；当日航班: %s",
                self.name, format_wanted(wanted),
                available[:20] or "(未能解析航班号)",
            )
        else:
            preview = [
                (o.get("flight_no"), o.get("return_flight_no") or "", o.get("price"))
                for o in matched[:8]
            ]
            self.logger.info(
                "[%s] 指定航班候选 %d 条（同航班不同产品/舱位价，稍后取最低）: %s",
                self.name, len(matched), preview,
            )
        return matched

    def names_for(self, from_city: str, to_city: str):
        """三字码 → 中文名。主航线/对向用 config 名，中转段用 CITY_NAME。"""
        def fb_for(code: str) -> Optional[str]:
            c = (code or "").upper()
            if c and c == (getattr(self, "_route_from_code", None) or "").upper():
                return getattr(self, "_route_from_name", None)
            if c and c == (getattr(self, "_route_to_code", None) or "").upper():
                return getattr(self, "_route_to_name", None)
            return None
        return (
            self.city_name(from_city, fb_for(from_city)),
            self.city_name(to_city, fb_for(to_city)),
        )

    def skip_if_unsupported_round(self) -> bool:
        """兼容旧调用：往返已改为去程+返程分别询价，不再跳过。"""
        return False

    @contextmanager
    def quote_session(self):
        """子类可在此复用浏览器页 / http 会话。"""
        yield

    def quote_oneway(self, from_city: str, to_city: str, date: str,
                     flight_nos=None, exact: bool = False) -> Optional[dict]:
        """单程询价。flight_nos=[] 表示不过滤。返回 normalize 后的 offer。"""
        raise NotImplementedError

    def quote_round(self, from_city: str, to_city: str, date: str, return_date: str,
                    flight_nos=None, return_flight_nos=None) -> Optional[dict]:
        """原生往返套票。默认不支持，穷举时用去程+返程单程相加。"""
        return None

    def _leg_key(self, leg: dict):
        return (
            (leg.get("flight_no") or "").upper(),
            (leg.get("from_code") or "").upper(),
            (leg.get("to_code") or "").upper(),
            leg.get("date") or "",
        )

    def collect_legs(self, offer, from_city: str = "", to_city: str = "",
                     date: str = "") -> list:
        if not offer:
            return []
        legs = list(offer.get("legs") or [])
        if not legs:
            legs = guess_legs(offer, from_city, to_city, date)
        out, seen = [], set()
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            if not (leg.get("flight_no") and leg.get("from_code")
                    and leg.get("to_code") and leg.get("date")):
                continue
            key = self._leg_key(leg)
            if key in seen:
                continue
            seen.add(key)
            out.append(leg)
        return out

    def pick_best(self, offers: list, flight_nos=None, exact: bool = False) -> Optional[dict]:
        offers = [normalize_offer(o) for o in (offers or []) if o]
        offers = [o for o in offers if o.get("price")]
        offers = self.filter_offers(offers, flight_nos=flight_nos or [], exact=exact)
        if not offers:
            return None
        return min(offers, key=lambda o: o["price"])

    def enumerate_fares(self, from_city: str, to_city: str, date: str) -> Optional[dict]:
        """往返套票 / 去程返程分别买 / 拆段单买，选出最低。"""
        return_date = getattr(self, "_route_return_date", None)
        go_nos = list(self._route_flight_nos or [])
        back_nos = list(self._route_return_flight_nos or [])
        self.logger.info("[%s] 开始穷举买法 %s→%s %s%s",
                         self.name, from_city, to_city, date,
                         f"/{return_date}" if return_date else "")
        quotes = {}
        cache = {}

        def quote(dep, arr, go, ret=None, fns=None, rfns=None, exact=False):
            key = (
                (dep or "").upper(), (arr or "").upper(), go or "",
                ret or "", tuple(fns or []), tuple(rfns or []), exact,
            )
            if key in cache:
                return cache[key]
            if cache:
                self._sleep()
            result = None
            try:
                if ret:
                    result = self.quote_round(
                        dep, arr, go, ret,
                        flight_nos=list(fns or []),
                        return_flight_nos=list(rfns or []),
                    )
                else:
                    result = self.quote_oneway(
                        dep, arr, go,
                        flight_nos=list(fns or []), exact=exact,
                    )
            except Exception as e:
                self.logger.warning("[%s] 询价失败 %s→%s %s: %s",
                                    self.name, dep, arr, go, e)
            if result:
                result = normalize_offer(result)
            cache[key] = result
            return result

        def quote_legs(legs: list) -> list:
            out = []
            for leg in legs:
                q = quote(
                    leg["from_code"], leg["to_code"], leg["date"],
                    fns=[leg["flight_no"]], exact=True,
                )
                item = dict(leg)
                item["price"] = q["price"] if q else None
                item["platform"] = self.name
                item["label"] = format_leg_label(item, lambda c: self.city_name(c))
                if item["price"] is None:
                    self.logger.warning(
                        "[%s] 航段未报价 %s %s→%s %s",
                        self.name, leg.get("flight_no"), leg.get("from_code"),
                        leg.get("to_code"), leg.get("date"),
                    )
                else:
                    self.logger.info("[%s] 航段 %s ¥%.0f", self.name, item["label"], item["price"])
                out.append(item)
            return out

        if return_date:
            quotes["round_pkg"] = quote(
                from_city, to_city, date, ret=return_date,
                fns=go_nos, rfns=back_nos,
            )
        quotes["outbound_combo"] = quote(from_city, to_city, date, fns=go_nos)
        if return_date:
            quotes["inbound_combo"] = quote(to_city, from_city, return_date, fns=back_nos)

        out_legs = self.collect_legs(
            quotes.get("outbound_combo"), from_city, to_city, date,
        )
        if not out_legs:
            out_legs = self.collect_legs(
                quotes.get("round_pkg"), from_city, to_city, date,
            )
        in_legs = self.collect_legs(
            quotes.get("inbound_combo"), to_city, from_city, return_date or "",
        )
        if not in_legs:
            pkg = quotes.get("round_pkg") or {}
            in_legs = self.collect_legs({"legs": pkg.get("return_legs") or []})

        out_leg_quotes = quote_legs(out_legs) if len(out_legs) >= 2 else []
        in_leg_quotes = quote_legs(in_legs) if len(in_legs) >= 2 else []

        plan = build_fare_plan(
            trip="round" if return_date else "one_way",
            round_pkg=quotes.get("round_pkg"),
            outbound_combo=quotes.get("outbound_combo"),
            inbound_combo=quotes.get("inbound_combo"),
            outbound_leg_quotes=out_leg_quotes,
            inbound_leg_quotes=in_leg_quotes,
        )
        for o in plan.get("options") or []:
            self.logger.info("[%s] 买法 %s ¥%.0f", self.name, o["label"], o["price"])
        best = plan.get("best")
        if best:
            self.logger.info("[%s] 建议买法 %s ¥%.0f", self.name, best["label"], best["price"])

        base = quotes.get("round_pkg") or quotes.get("outbound_combo")
        if base is None and quotes.get("inbound_combo"):
            base = dict(quotes["inbound_combo"])
        if base is None:
            return None
        offer = dict(base)
        if quotes.get("inbound_combo") and not offer.get("return_flight_no"):
            offer["return_flight_no"] = quotes["inbound_combo"].get("flight_no") or ""
        if quotes.get("outbound_combo") and not offer.get("flight_no"):
            offer["flight_no"] = quotes["outbound_combo"].get("flight_no") or ""
        offer["fare_plan"] = plan
        offer["leg_quotes"] = {
            "outbound": out_leg_quotes,
            "inbound": in_leg_quotes,
        }
        if best:
            offer["price"] = best["price"]
        return offer

    def fetch_lowest(self, from_city: str, to_city: str, date: str) -> Optional[dict]:
        """最低价模式：每平台最多少量查询，不穷举买法。"""
        ret = getattr(self, "_route_return_date", None)
        go_nos = list(self._route_flight_nos or [])
        back_nos = list(self._route_return_flight_nos or [])

        if ret:
            pkg = self.quote_round(
                from_city, to_city, date, ret,
                flight_nos=go_nos, return_flight_nos=back_nos,
            )
            if pkg and pkg.get("price"):
                return normalize_offer(pkg)
            out = self.quote_oneway(from_city, to_city, date, flight_nos=go_nos)
            if out:
                self._sleep()
            inn = self.quote_oneway(to_city, from_city, ret, flight_nos=back_nos)
            if out and inn and out.get("price") and inn.get("price"):
                merged = dict(out)
                merged["price"] = float(out["price"]) + float(inn["price"])
                merged["return_flight_no"] = inn.get("flight_no") or ""
                if not merged.get("flight_no"):
                    merged["flight_no"] = out.get("flight_no") or ""
                return normalize_offer(merged)
            return normalize_offer(out) if out else (normalize_offer(inn) if inn else None)

        offer = self.quote_oneway(
            from_city, to_city, date,
            flight_nos=go_nos,
        )
        return normalize_offer(offer) if offer else None

    def fetch_one_date(self, from_city: str, to_city: str, date: str) -> Optional[dict]:
        mode = getattr(self, "_route_monitor_mode", "lowest") or "lowest"
        if mode == "fare_plan":
            if not (self._route_flight_nos or self._route_return_flight_nos):
                self.logger.warning(
                    "[%s] 指定航程买法模式需填写航班号，已按最低价查询",
                    self.name,
                )
                return self.fetch_lowest(from_city, to_city, date)
            return self.enumerate_fares(from_city, to_city, date)
        return self.fetch_lowest(from_city, to_city, date)

    def offer_to_flightprice(self, offer: Optional[dict], from_city: str,
                             to_city: str, date: str) -> Optional[FlightPrice]:
        if not offer or offer.get("price") is None:
            return None
        import json
        ret_date = getattr(self, "_route_return_date", None) or ""
        go_no = offer.get("flight_no") or ""
        back_no = offer.get("return_flight_no") or ""
        flight_label = go_no
        if back_no:
            flight_label = f"{go_no}|{back_no}" if go_no else back_no
        payload = {"monitor_mode": getattr(self, "_route_monitor_mode", "lowest")}
        if ret_date:
            payload.update({
                "trip": "round",
                "return_date": ret_date,
                "return_flight_no": back_no,
            })
        plan = offer.get("fare_plan")
        if plan:
            payload["fare_plan"] = plan
        lq = offer.get("leg_quotes")
        if lq:
            payload["leg_quotes"] = lq
        extra = json.dumps(payload, ensure_ascii=False) if payload else ""
        return FlightPrice(
            platform=self.name,
            from_city=from_city, to_city=to_city,
            depart_date=date, price=float(offer["price"]),
            airline=offer.get("airline") or "",
            flight_no=flight_label,
            depart_time=offer.get("depart_time") or "",
            arrive_time=offer.get("arrive_time") or "",
            extra=extra,
            return_date=ret_date,
        )

    def fetch_quoted(self, from_city: str, to_city: str, dates: List[str]) -> List[FlightPrice]:
        results: List[FlightPrice] = []
        with self.quote_session():
            for date in dates:
                try:
                    offer = self.fetch_one_date(from_city, to_city, date)
                except Exception as e:
                    self.logger.exception("[%s] %s 抓取异常: %s", self.name, date, e)
                    offer = None
                fp = self.offer_to_flightprice(offer, from_city, to_city, date)
                if fp:
                    ret = fp.return_date
                    self.logger.info(
                        "[%s] %s 最低价 ¥%.0f %s %s",
                        self.name,
                        f"{date}/{ret}" if ret else date,
                        fp.price, fp.airline, fp.flight_no,
                    )
                    results.append(fp)
                else:
                    self.logger.warning("[%s] %s 未解析到价格", self.name, date)
                self._sleep()
        return results

    # ---------- 浏览器 ----------
    @property
    def user_data_dir(self) -> str:
        # 不同平台不同目录，避免互相污染
        path = os.path.join(self.user_data_root, self.name)
        os.makedirs(path, exist_ok=True)
        return path

    def _ua(self) -> str:
        if self.use_mobile:
            return self.config.get("mobile_user_agent") or MOBILE_UA
        return self.config.get("user_agent") or DESKTOP_UA

    def _viewport(self) -> dict:
        if self.use_mobile:
            return {"width": 390, "height": 844}
        return {"width": 1366, "height": 900}

    @contextmanager
    def browser(self, headless: Optional[bool] = None):
        """启动持久化浏览器上下文。headless=None 时使用配置。"""
        hl = self.headless if headless is None else headless
        if self.debug and hl:
            self.logger.info("[%s] 调试模式已开启，自动切换为有头浏览器", self.name)
            hl = False
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=self.user_data_dir,
                headless=hl,
                user_agent=self._ua(),
                viewport=self._viewport(),
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                is_mobile=self.use_mobile,
                has_touch=self.use_mobile,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            )
            try:
                yield ctx
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass

    def new_page(self, ctx: BrowserContext) -> Page:
        page = ctx.new_page()
        page.set_default_timeout(self.timeout_ms)
        page.set_default_navigation_timeout(self.timeout_ms)
        return page

    # ---------- 工具 ----------
    def _sleep(self):
        time.sleep(random.uniform(self.delay_min, self.delay_max))

    def _debug_snapshot(self, page: Page, tag: str):
        if not self.debug:
            return
        try:
            os.makedirs(self.debug_dir, exist_ok=True)
            base = os.path.join(self.debug_dir, f"{self.name}_{tag}")
            try:
                page.screenshot(path=base + ".png", full_page=True)
            except Exception:
                pass
            try:
                with open(base + ".html", "w", encoding="utf-8") as f:
                    f.write(page.content())
            except Exception:
                pass
            self.logger.info("[%s] 已保存调试快照: %s.{png,html}", self.name, base)
        except Exception as e:
            self.logger.warning("[%s] 保存快照失败: %s", self.name, e)

    # ---------- XHR 拦截：通用工具 ----------
    def attach_xhr_collector(self, page: Page, url_patterns: List[str]):
        """监听匹配的 XHR/Fetch 响应，缓存其 JSON 文本。

        返回一个 list，调用方在抓取过程中读取它。
        """
        captured: list = []

        def on_response(resp: Response):
            try:
                url = resp.url
                if not any(p in url for p in url_patterns):
                    return
                # 仅尝试 JSON
                ct = (resp.headers or {}).get("content-type", "")
                if "json" not in ct and not url.endswith(".json"):
                    # 也允许文本，部分平台返回 application/javascript
                    pass
                try:
                    text = resp.text()
                except Exception:
                    return
                captured.append({"url": url, "text": text})
            except Exception:
                pass

        page.on("response", on_response)
        return captured

    @staticmethod
    def extract_prices_from_text(text: str) -> List[int]:
        """从任意文本/JSON 字符串中抽取看起来像机票价格的数字。

        优先级：lowestPrice / salePrice / TotalPrice 等"含税总价"字段；
        退一步再用 price / adultPrice / minPrice；
        最后兜底从 ¥xxxx 文本里取。
        """
        prices: list = []
        if not text:
            return prices

        # 第 1 档：明确的最低价/销售总价字段（最可信）
        for m in re.finditer(
            r'"(lowestPrice|salePrice|TotalPrice|displayPrice|cabinTotalPrice)"\s*:\s*"?(\d{3,5})',
            text,
        ):
            try:
                prices.append(int(m.group(2)))
            except Exception:
                pass
        if prices:
            return [p for p in prices if 100 <= p <= 50000]

        # 第 2 档：通用价格字段（飞猪 H5 里 price/totalPrice 可能是附加产品价，谨慎）
        for m in re.finditer(
            r'"(minPrice|adultPrice|Price)"\s*:\s*"?(\d{3,5})',
            text,
        ):
            try:
                prices.append(int(m.group(2)))
            except Exception:
                pass
        if prices:
            return [p for p in prices if 100 <= p <= 50000]

        # 第 3 档：兜底，¥xxx 文本
        for m in re.finditer(r"[¥￥]\s*(\d{3,5})", text):
            prices.append(int(m.group(1)))
        return [p for p in prices if 100 <= p <= 50000]

    # ---------- 子类必须实现 ----------
    def fetch(self, from_city: str, to_city: str, dates: List[str]) -> List[FlightPrice]:
        raise NotImplementedError

    def login_url(self) -> str:
        """登录引导页 URL，被 --login 模式使用。"""
        raise NotImplementedError

    # ---------- 公共调用入口 ----------
    def safe_fetch(self, from_city: str, to_city: str, dates: List[str],
                   from_name: Optional[str] = None,
                   to_name: Optional[str] = None,
                   flight_nos: Optional[list] = None,
                   return_date: Optional[str] = None,
                   return_flight_nos: Optional[list] = None,
                   trip: str = "one_way",
                   monitor_mode: str = "lowest") -> List[FlightPrice]:
        self._route_from_name = from_name
        self._route_to_name = to_name
        self._route_from_code = from_city
        self._route_to_code = to_city
        self._route_flight_nos = list(flight_nos or [])
        self._route_return_date = (return_date or "").strip() or None
        self._route_return_flight_nos = list(return_flight_nos or [])
        self._route_trip = trip or "one_way"
        self._route_monitor_mode = monitor_mode or "lowest"
        try:
            bits = []
            if self._route_monitor_mode == "fare_plan":
                bits.append("买法穷举")
            else:
                bits.append("最低价")
            if self._route_return_date:
                bits.append("往返 %s/%s" % (dates[0] if dates else "?", self._route_return_date))
            if self._route_flight_nos:
                bits.append("去程=%s" % "/".join(str(x) for x in self._route_flight_nos))
            if self._route_return_flight_nos:
                bits.append("返程=%s" % "/".join(str(x) for x in self._route_return_flight_nos))
            extra = (" " + " ".join(bits)) if bits else ""
            self.logger.info("[%s] 开始抓取 %s->%s 日期=%s%s",
                             self.name, from_city, to_city, dates, extra)
            results = self.fetch(from_city, to_city, dates) or []
            self.logger.info("[%s] 抓取完成，得到 %d 条记录", self.name, len(results))
            return results
        except Exception as e:
            self.logger.exception("[%s] 抓取失败: %s", self.name, e)
            return []
        finally:
            self._route_from_name = None
            self._route_to_name = None
            self._route_from_code = None
            self._route_to_code = None
            self._route_flight_nos = []
            self._route_return_date = None
            self._route_return_flight_nos = []
            self._route_trip = "one_way"
            self._route_monitor_mode = "lowest"

    # ---------- 登录模式 ----------
    def interactive_login(self):
        """打开可见浏览器引导用户登录，按回车后保存会话退出。"""
        url = self.login_url()
        self.logger.info("[%s] 启动登录浏览器：%s", self.name, url)
        with self.browser(headless=False) as ctx:
            page = self.new_page(ctx)
            page.goto(url, wait_until="domcontentloaded")
            print()
            print("=" * 60)
            print(f"  请在浏览器里完成 [{self.name}] 登录（手机号验证码等）。")
            print(f"  登录完成后回到这里，按 Enter 保存会话并关闭浏览器。")
            print("=" * 60)
            try:
                input(">>> 完成后按 Enter：")
            except EOFError:
                time.sleep(60)
            self.logger.info("[%s] 会话已保存到 %s", self.name, self.user_data_dir)


class BrowserQuoteMixin:
    """同一 Playwright 会话里多次 goto，穷举买法时复用浏览器。"""

    @contextmanager
    def quote_session(self):
        if getattr(self, "_quote_page", None) is not None:
            yield
            return
        with self.browser() as ctx:
            stealth = getattr(self, "_STEALTH_JS", None)
            if stealth:
                try:
                    ctx.add_init_script(stealth)
                except Exception:
                    pass
            page = self.new_page(ctx)
            self._quote_page = page
            self._xhr_bucket = []
            keys = getattr(self, "XHR_KEYS", None) or []

            def on_response(resp: Response):
                try:
                    url = resp.url or ""
                    if keys and not any(p in url for p in keys):
                        return
                    try:
                        text = resp.text()
                    except Exception:
                        return
                    self._xhr_bucket.append({"url": url, "text": text})
                except Exception:
                    pass

            page.on("response", on_response)
            try:
                yield
            finally:
                self._quote_page = None
                self._xhr_bucket = []

    def _xhr_payload_ready(self, captured: list) -> bool:
        """子类可覆盖：XHR 桶里是否已有可解析的航班数据。"""
        return False

    def _xhr_wait_rounds(self) -> int:
        return int(getattr(self, "_xhr_poll_rounds", 14) or 14)

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
        for _ in range(self._xhr_wait_rounds()):
            page.wait_for_timeout(2000)
            try:
                page.mouse.wheel(0, 2000)
            except Exception:
                pass
            if self._xhr_payload_ready(bucket):
                break
        return list(bucket)

    def quote_oneway(self, from_city: str, to_city: str, date: str,
                     flight_nos=None, exact: bool = False):
        if getattr(self, "_quote_page", None) is None:
            with self.quote_session():
                return self.quote_oneway(
                    from_city, to_city, date,
                    flight_nos=flight_nos, exact=exact,
                )
        return self._quote_oneway_on_page(
            from_city, to_city, date,
            flight_nos=flight_nos, exact=exact,
        )

    def _quote_oneway_on_page(self, from_city: str, to_city: str, date: str,
                              flight_nos=None, exact: bool = False):
        raise NotImplementedError
