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
    "DIG": "香格里拉", "TCZ": "腾冲",
    "HKG": "香港", "MAC": "澳门", "MFM": "澳门",
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
        self.delay_min: float = float(config.get("delay_min", 2))
        self.delay_max: float = float(config.get("delay_max", 5))
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
            preview = [(o.get("flight_no"), o.get("price")) for o in matched[:8]]
            self.logger.info("[%s] 指定航班命中 %d 条: %s", self.name, len(matched), preview)
        return matched

    def skip_if_unsupported_round(self) -> bool:
        if getattr(self, "_route_trip", "one_way") == "round" or getattr(self, "_route_return_date", None):
            self.logger.warning("[%s] 暂不支持往返查询（目前仅去哪儿支持），已跳过", self.name)
            return True
        return False

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
                   trip: str = "one_way") -> List[FlightPrice]:
        self._route_from_name = from_name
        self._route_to_name = to_name
        self._route_from_code = from_city
        self._route_to_code = to_city
        self._route_flight_nos = list(flight_nos or [])
        self._route_return_date = (return_date or "").strip() or None
        self._route_return_flight_nos = list(return_flight_nos or [])
        self._route_trip = trip or "one_way"
        try:
            bits = []
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
