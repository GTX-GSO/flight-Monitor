"""去哪儿机票：httpx 优先 + 浏览器兜底（混合模式）

针对去哪儿 touchInnerList 接口的 Ctrip ubtrms / chloroFp 风控：
- Bella token + caf7be/pre 等指纹头 + cookies 由浏览器跑风控 JS 生成，
  无法纯 httpx 离线生成（服务端校验指纹自洽性后下发 token）
- token 本身可复用（实测有效期 >5分钟，可能 24h）
- 但接口有全局频率限流（约5分钟/次），与航线无关，httpx 连续请求必 1999

混合策略（省约50%浏览器开销）：
1. 每轮第一条航线优先 httpx（用缓存的 token）
2. httpx 成功 → 直接返回；后续航线也尝试 httpx，失败则回退浏览器
3. httpx 1999/无token/异常 → 浏览器单条查（拦请求刷新 token + 解析响应拿价）
4. 浏览器每次跑都会刷新 token，供下次 httpx 使用

实测：浏览器握手后立即 httpx 可拿到真实价格（minPrice 与 DOM 一致）；
但 httpx 第二次（5分钟内）必被风控，故多航线场景第二条起回退浏览器。
"""
import os
import re
import json
import time
import urllib.parse
from contextlib import contextmanager
from typing import List, Optional

import httpx

from core.models import FlightPrice
from core.flights import (
    parse_itinerary_nos, format_itinerary, parse_wanted_flight_nos, parse_legs,
    itinerary_matches,
)
from core.fareplan import build_fare_plan, format_leg_label
from .base import BaseCrawler, INTER_CODES, INTER_NAMES, CITY_NAME


class _Capture:
    def __init__(self):
        self.headers = None
        self.body_template = None
        self.cookies = {}
        self.response_text = None
        self.responses = []

    def reset(self):
        self.responses = []
        self.response_text = None


class QunarCrawler(BaseCrawler):
    name = "qunar"
    use_mobile = True

    # 去哪儿单程列表 H5：国内用 flightlist，港澳台/国际用 interlist
    URL_TPL = (
        "https://touch.qunar.com/ncs/page/flightlist"
        "?depCity={from_name}&arrCity={to_name}"
        "&goDate={date}&from=touch_index_search"
        "&child=0&baby=0&cabinType=0"
    )
    URL_TPL_INTER = (
        "https://touch.qunar.com/ncs/page/interlist"
        "?depCity={from_name}&arrCity={to_name}"
        "&goDate={date}&from=touch_index_search"
        "&flightType=oneWay&adultNum=1&child=0&baby=0"
    )
    URL_TPL_ROUND = (
        "https://touch.qunar.com/ncs/page/flightlist"
        "?depCity={from_name}&arrCity={to_name}"
        "&goDate={date}&backDate={return_date}"
        "&flightType=roundWay&from=touch_index_search"
        "&child=0&baby=0&cabinType=0"
    )
    URL_TPL_INTER_ROUND = (
        "https://touch.qunar.com/ncs/page/interlist"
        "?depCity={from_name}&arrCity={to_name}"
        "&goDate={date}&backDate={return_date}"
        "&flightType=roundWay&from=touch_index_search"
        "&adultNum=1&child=0&baby=0&isTotal=true"
    )
    # 列表接口：国内 touchInnerList，国际/港澳台 touchInterList
    # 往返第二程可能是 touchInterBackList
    # 不要用裸的 InnerList/InterList，会误匹配页面 URL 和打包 JS
    _LIST_API_HINTS = (
        "touchInnerList", "touchInterList",
        "touchInterBackList", "touchInnerBackList",
    )

    # 反自动化指纹脚本：让设备指纹与 iPhone UA 自洽，骗过 chloroFp 服务端校验
    # 关键：Ctrip 风控发现「iPhone UA + Win32/NVIDIA WebGL」矛盾会拒发指纹 token
    _STEALTH_JS = r"""
    (function(){
      const define = (obj, prop, val) => {
        try { Object.defineProperty(obj, prop, {get: () => val, configurable: true}); } catch(e){}
      };
      // 基础导航器：对齐 iPhone Safari
      define(navigator, 'webdriver', undefined);
      define(navigator, 'languages', ['zh-CN','zh']);
      define(navigator, 'platform', 'iPhone');
      define(navigator, 'maxTouchPoints', 5);
      define(navigator, 'hardwareConcurrency', 6);
      define(navigator, 'vendor', 'Apple Computer, Inc.');
      try { delete navigator.deviceMemory; } catch(e){}
      // plugins 真机 Safari 为空
      define(navigator, 'plugins', []);
      window.chrome = undefined;

      // WebGL：把 NVIDIA/ANGLE 伪装成 Apple GPU
      const APPLE_VENDOR = 'Apple Inc.';
      const APPLE_RENDERER = 'Apple GPU';
      const patchGL = (proto) => {
        if (!proto || !proto.getParameter) return;
        const orig = proto.getParameter;
        proto.getParameter = function(p){
          // UNMASKED_VENDOR_WEBGL=37445, UNMASKED_RENDERER_WEBGL=37446
          if (p === 37445) return APPLE_VENDOR;
          if (p === 37446) return APPLE_RENDERER;
          // VENDOR=7936, RENDERER=7937
          if (p === 7936) return 'WebKit';
          if (p === 7937) return 'WebKit WebGL';
          return orig.call(this, p);
        };
      };
      try { patchGL(WebGLRenderingContext.prototype); } catch(e){}
      try { patchGL(WebGL2RenderingContext.prototype); } catch(e){}

      // 触摸事件支持标记
      try {
        define(window, 'ontouchstart', null);
      } catch(e){}
    })();
    """

    # 去哪儿单程列表真实数据接口
    API_URL = "https://touch.qunar.com/flight/api/touchInnerList"

    # 风控占位响应特征
    RISK_CODE = 1999
    # token 有效期（保守取 20h，实际约 24h）
    TOKEN_TTL_S = 20 * 3600

    def login_url(self) -> str:
        # 触屏版登录入口，登录后 cookie 作用于 touch.qunar.com，与抓取同源
        return "https://user.qunar.com/mobile/login.jsp"

    # ==================== 主流程 ====================
    def fetch(self, from_city: str, to_city: str, dates: List[str]) -> List[FlightPrice]:
        return self.fetch_quoted(from_city, to_city, dates)

    def fetch_one_date(self, from_city: str, to_city: str, date: str) -> Optional[dict]:
        mode = getattr(self, "_route_monitor_mode", "lowest") or "lowest"
        if mode == "fare_plan":
            if not (self._route_flight_nos or self._route_return_flight_nos):
                self.logger.warning(
                    "[qunar] 指定航程买法模式需填写航班号，已按最低价查询",
                )
                return self._fetch_one(from_city, to_city, date)
            return self._fetch_enumerated(from_city, to_city, date)
        return self._fetch_one(from_city, to_city, date)

    def _city_pair(self, from_city: str, to_city: str):
        return self._names_for(from_city, to_city)

    def _names_for(self, from_city: str, to_city: str):
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

    def _is_inter(self, from_city: str, to_city: str, from_name: str, to_name: str) -> bool:
        codes = {(from_city or "").upper(), (to_city or "").upper()}
        names = {from_name, to_name}
        if codes & INTER_CODES or names & INTER_NAMES:
            return True
        mainland = {c for c in CITY_NAME if c not in INTER_CODES}
        return bool(codes - mainland)

    def _fetch_one(self, from_city: str, to_city: str, date: str) -> Optional[dict]:
        """单日期抓取：优先 httpx，失败回退浏览器。港澳台/往返走浏览器。"""
        from_name, to_name = self._city_pair(from_city, to_city)
        return_date = getattr(self, "_route_return_date", None)
        if return_date or self._is_inter(from_city, to_city, from_name, to_name):
            kind = "往返" if return_date else "港澳台/国际"
            self.logger.info("[qunar] %s航线 %s→%s，走浏览器", kind, from_name, to_name)
            offer = self._fetch_via_browser(from_city, to_city, date)
            if offer and offer.get("price"):
                return offer
            if return_date:
                self.logger.info("[qunar] 往返套票未命中，拆成去程+返程单程")
                saved_ret = self._route_return_date
                self._route_return_date = None
                try:
                    out = self._fetch_one(from_city, to_city, date)
                    self._sleep()
                    inn = self._fetch_one(to_city, from_city, return_date)
                finally:
                    self._route_return_date = saved_ret
                if out and inn and out.get("price") and inn.get("price"):
                    merged = dict(out)
                    merged["price"] = float(out["price"]) + float(inn["price"])
                    go_no = out.get("flight_no") or ""
                    back_no = inn.get("flight_no") or ""
                    merged["flight_no"] = f"{go_no}|{back_no}" if go_no and back_no else (go_no or back_no)
                    merged["return_flight_no"] = back_no
                    return merged
            return offer
        offer = self._fetch_via_httpx(from_city, to_city, date)
        if offer is not None:
            self.logger.info("[qunar] httpx 命中，省去浏览器")
            return offer
        self.logger.info("[qunar] httpx 未命中，回退浏览器")
        return self._fetch_via_browser(from_city, to_city, date)

    # ==================== httpx 续航 ====================
    def _fetch_via_httpx(self, from_city: str, to_city: str, date: str) -> Optional[dict]:
        """用缓存 token 直接 httpx POST，返回价格或 None。"""
        token = self._load_token()
        if not token:
            return None

        from_name, to_name = self._city_pair(from_city, to_city)

        # 基于模板构造新 body：替换城市/日期/时间戳，保留 Bella
        body = dict(token["body_template"])
        body["depCity"] = from_name
        body["arrCity"] = to_name
        body["goDate"] = date
        body["firstRequest"] = True
        body["startNum"] = 0
        body["sort"] = 5
        body["_v"] = 2
        body["underageOption"] = ""
        now_ms = int(time.time() * 1000)
        body["r"] = now_ms
        body["st"] = now_ms - 1

        headers = dict(token["headers"])
        headers["referer"] = "https://touch.qunar.com/ncs/page/flightlist"
        cookies = dict(token["cookies"])

        try:
            r = httpx.post(
                self.API_URL,
                headers=headers,
                content=json.dumps(body, ensure_ascii=False, separators=(",", ":")),
                cookies=cookies,
                timeout=25,
            )
        except Exception as e:
            self.logger.warning("[qunar] httpx 请求异常: %s", e)
            return None

        if r.status_code != 200:
            self.logger.warning("[qunar] httpx status=%d", r.status_code)
            return None

        text = r.text
        self._dump_raw_text(text, f"httpx_{from_name}_{to_name}_{date}")

        if self._is_risk_text(text):
            self.logger.warning("[qunar] httpx 命中风控(1999)，可能是限流或token过期")
            return None

        offer = self._pick_offer(text)
        if not offer:
            self.logger.warning("[qunar] httpx 响应无价格，len=%d", len(text))
            return None
        return offer

    @staticmethod
    def _is_risk_text(text: str) -> bool:
        try:
            obj = json.loads(text)
        except Exception:
            return False
        bstatus = obj.get("bstatus") or {}
        if bstatus.get("code") == QunarCrawler.RISK_CODE:
            return True
        if obj.get("ret") is False and obj.get("data") is None:
            return True
        return False

    # ==================== 浏览器单条查（兜底 + 刷新 token） ====================
    def _list_url(self, from_name: str, to_name: str, date: str,
                  is_inter: bool, return_date: Optional[str]) -> str:
        kw = dict(
            from_name=urllib.parse.quote(from_name),
            to_name=urllib.parse.quote(to_name),
            date=date,
        )
        if return_date:
            kw["return_date"] = urllib.parse.quote(return_date)
            tpl = self.URL_TPL_INTER_ROUND if is_inter else self.URL_TPL_ROUND
        else:
            tpl = self.URL_TPL_INTER if is_inter else self.URL_TPL
        return tpl.format(**kw)

    def _click_flight(self, page, offer) -> bool:
        nos = offer.get("flight_nos") or parse_itinerary_nos(offer.get("flight_no"))
        for no in nos:
            try:
                loc = page.get_by_text(no, exact=False)
                if loc.count() > 0:
                    loc.first.click(timeout=5000)
                    self.logger.info("[qunar] 点击去程 %s，进入返程列表", no)
                    return True
            except Exception as e:
                self.logger.warning("[qunar] 点击航班 %s 失败: %s", no, e)
        return False

    def _pick_from_responses(self, texts: list, flight_nos=None,
                             exact: bool = False,
                             return_flight_nos=None) -> Optional[dict]:
        """从多条 XHR 响应里取最低价；指定航班时优先用最新一条可解析响应。"""
        if not texts:
            return None
        wanted_raw = flight_nos if flight_nos is not None else self._route_flight_nos
        if parse_wanted_flight_nos(wanted_raw) or parse_wanted_flight_nos(return_flight_nos):
            for text in reversed(texts):
                picked = self._pick_offer(
                    text, flight_nos=flight_nos, silent=True, exact=exact,
                    return_flight_nos=return_flight_nos,
                )
                if picked:
                    return picked
            return None
        best = None
        for text in texts:
            picked = self._pick_offer(
                text, flight_nos=flight_nos, silent=True, exact=exact,
                return_flight_nos=return_flight_nos,
            )
            if picked and (best is None or picked["price"] < best["price"]):
                best = picked
        return best

    @staticmethod
    def _best_matched_offer(offers: list) -> Optional[dict]:
        """同一航班号多条报价时，去掉混淆解析带来的异常低价。"""
        if not offers:
            return None
        if len(offers) == 1:
            return offers[0]
        # 列表套票已是真实 go+back 报价，直接取最低，勿按混淆低价启发式过滤
        if any(o.get("is_round_pkg") for o in offers):
            return min(offers, key=lambda x: float(x.get("price") or 0))
        by_fn: dict = {}
        for o in offers:
            fn = o.get("flight_no") or ""
            ret = o.get("return_flight_no") or ""
            by_fn.setdefault(f"{fn}|{ret}", []).append(o)
        candidates = []
        for group in by_fn.values():
            prices = sorted(g["price"] for g in group)
            if len(prices) == 1:
                candidates.append(group[0])
                continue
            hi, lo = prices[-1], prices[0]
            if hi > lo * 1.12:
                floor = hi * 0.88
                group = [g for g in group if g["price"] >= floor]
            candidates.append(min(group, key=lambda x: x["price"]))
        return min(candidates, key=lambda x: x["price"])

    @contextmanager
    def _browser_session(self):
        cap = _Capture()
        with self.browser() as ctx:
            try:
                ctx.add_init_script(self._STEALTH_JS)
            except Exception:
                pass
            page = self.new_page(ctx)
            self._attach_capture(page, cap)
            try:
                yield page, cap
            finally:
                try:
                    cap.cookies = {c["name"]: c["value"] for c in ctx.cookies()}
                except Exception:
                    cap.cookies = {}
        if cap.headers and cap.body_template and cap.body_template.get("Bella"):
            self._save_token(cap)
            self.logger.info("[qunar] token 已刷新，有效期 %dh", self.TOKEN_TTL_S // 3600)

    def _attach_capture(self, page, cap: _Capture):
        def _is_list_api(u: str) -> bool:
            return any(h.lower() in (u or "").lower() for h in self._LIST_API_HINTS)

        def on_request(req):
            if not _is_list_api(req.url):
                return
            if cap.headers is None:
                cap.headers = dict(req.headers)
                try:
                    cap.body_template = json.loads(req.post_data or "{}")
                except Exception:
                    cap.body_template = {}
                self.logger.info("[qunar] 拦截请求 %s，Bella长度=%d",
                                 req.url,
                                 len((cap.body_template or {}).get("Bella", "")))

        def on_response(resp):
            u = resp.url or ""
            if self.debug and "qunar.com" in u and (
                "api" in u.lower() or "list" in u.lower() or "search" in u.lower()
            ) and not _is_list_api(u):
                self.logger.info("[qunar] 其他接口 %s", u[:220])
            if not _is_list_api(u):
                return
            try:
                text = resp.text()
            except Exception:
                return
            if not text:
                return
            cap.responses.append(text)
            if cap.response_text is None or "minPrice" in text:
                cap.response_text = text
                self.logger.info("[qunar] 拦截响应 %s len=%d (#%d)",
                                 resp.url, len(text), len(cap.responses))

        page.on("request", on_request)
        page.on("response", on_response)

    def _is_login_page(self, page) -> bool:
        url = (page.url or "").lower()
        return any(x in url for x in ("login", "passport", "user.qunar.com"))

    def _wait_list(self, page, cap: _Capture, is_inter: bool, return_date: Optional[str]):
        wait_rounds = 20 if (is_inter or return_date) else 12
        for _ in range(wait_rounds):
            page.wait_for_timeout(2000)
            try:
                page.mouse.wheel(0, 1500)
            except Exception:
                pass
            if cap.response_text is not None:
                if self._response_failed(cap.response_text):
                    cap.response_text = None
                    continue
                if "minPrice" in cap.response_text or not (is_inter or return_date):
                    break
            if cap.responses and self._pick_from_responses(cap.responses):
                break

    def _quote_on_page(self, page, cap: _Capture, from_city: str, to_city: str,
                       date: str, return_date: Optional[str] = None,
                       flight_nos=None, return_flight_nos=None,
                       exact: bool = False) -> Optional[dict]:
        """在已打开的浏览器页上搜一次，返回最低匹配报价。

        flight_nos 必须显式传入（[] 表示不过滤），避免误用配置里的去程航班号。
        """
        nos = list(flight_nos) if flight_nos is not None else []
        back_nos = list(return_flight_nos) if return_flight_nos else []
        from_name, to_name = self._names_for(from_city, to_city)
        is_inter = self._is_inter(from_city, to_city, from_name, to_name)
        url = self._list_url(from_name, to_name, date, is_inter, return_date)
        self.logger.info("[qunar] 浏览器 GET %s", url)

        cap.reset()
        try:
            page.goto(url, wait_until="load")
            if self._is_login_page(page):
                self.logger.warning("[qunar] 检测到登录页，经 touch 首页重试")
                page.goto("https://touch.qunar.com/", wait_until="domcontentloaded")
                page.wait_for_timeout(1500)
                cap.reset()
                page.goto(url, wait_until="load")
            self._wait_list(page, cap, is_inter, return_date)
        except Exception as e:
            self.logger.warning("[qunar] 浏览器页面异常: %s", e)

        picked = self._pick_from_responses(
            cap.responses, flight_nos=nos, exact=exact,
            return_flight_nos=back_nos if (return_date and back_nos) else None,
        )
        if return_date and back_nos and picked and picked.get("is_round_pkg") and picked.get("return_flight_nos"):
            go_day = ""
            if picked.get("depart_date") and picked.get("arrive_date") and (
                picked.get("arrive_date") != picked.get("depart_date")
            ):
                go_day = f"（到达 {picked.get('arrive_date')}）"
            back_day = ""
            if picked.get("return_depart_date") and picked.get("return_arrive_date") and (
                picked.get("return_arrive_date") != picked.get("return_depart_date")
            ):
                back_day = f"（到达 {picked.get('return_arrive_date')}）"
            self.logger.info(
                "[qunar] 列表套票命中 去程 %s %s→%s%s + 返程 %s %s→%s%s 合计 ¥%.0f",
                picked.get("flight_no"),
                picked.get("depart_time") or "?",
                picked.get("arrive_time") or "?",
                go_day,
                picked.get("return_flight_no"),
                picked.get("return_depart_time") or "?",
                picked.get("return_arrive_time") or "?",
                back_day,
                picked.get("price") or 0,
            )
        elif return_date and back_nos and picked:
            n_before = len(cap.responses)
            if self._click_flight(page, picked):
                inbound = None
                for _ in range(20):
                    page.wait_for_timeout(2000)
                    inbound = self._pick_from_responses(
                        cap.responses[n_before:], flight_nos=back_nos,
                    )
                    if inbound:
                        picked = dict(picked)
                        out_price = float(picked.get("price") or 0)
                        in_price = float(inbound.get("price") or 0)
                        # 返程页价格可能是「加价」也可能是「往返总价」
                        if out_price > 0 and in_price > 0 and in_price < out_price * 0.85:
                            total = out_price + in_price
                        else:
                            total = in_price or out_price
                        picked["price"] = total
                        picked["return_flight_no"] = inbound.get("flight_no") or ""
                        picked["return_flight_nos"] = inbound.get("flight_nos") or []
                        picked["return_legs"] = inbound.get("legs") or []
                        self.logger.info(
                            "[qunar] 往返套票 去程 %s + 返程 %s 合计 ¥%.0f",
                            picked.get("flight_no"), inbound.get("flight_no"),
                            total,
                        )
                        break
                if inbound is None:
                    self.logger.warning("[qunar] 已点击去程，但未解析到指定返程航班")
            else:
                self.logger.warning(
                    "[qunar] 未能点进去程看返程，使用去程套票起价 ¥%.0f",
                    picked.get("price") or 0,
                )
        elif return_date and picked:
            self.logger.info("[qunar] 往返套票起价（未指定返程航班）¥%.0f %s",
                             picked["price"], picked.get("flight_no") or "")

        if cap.responses:
            tag = f"browser_{from_name}_{to_name}_{date}"
            if return_date:
                tag += f"_{return_date}"
            self._dump_raw_text(cap.responses[-1], tag)
        return picked

    def _fetch_via_browser(self, from_city: str, to_city: str, date: str) -> Optional[dict]:
        """浏览器跑一次页面：拦请求刷新 token + 解析响应拿价。"""
        return_date = getattr(self, "_route_return_date", None)
        with self._browser_session() as (page, cap):
            return self._quote_on_page(
                page, cap, from_city, to_city, date,
                return_date=return_date,
                flight_nos=list(self._route_flight_nos or []),
                return_flight_nos=list(self._route_return_flight_nos or []),
            )

    def _leg_key(self, leg: dict):
        return (
            (leg.get("flight_no") or "").upper(),
            (leg.get("from_code") or "").upper(),
            (leg.get("to_code") or "").upper(),
            leg.get("date") or "",
        )

    def _collect_legs(self, offer, extra_key: str = "") -> list:
        if not offer:
            return []
        legs = list(offer.get("legs") or [])
        if extra_key:
            legs.extend(offer.get(extra_key) or [])
        out, seen = [], set()
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            if not (leg.get("flight_no") and leg.get("from_code") and leg.get("to_code") and leg.get("date")):
                continue
            key = self._leg_key(leg)
            if key in seen:
                continue
            seen.add(key)
            out.append(leg)
        return out

    def _reconcile_combo_price(self, combo: Optional[dict], leg_quotes: list, label: str):
        """仅当组合价低于任一航段单买价时才修正（联程通票常远低于航段之和，属正常）。"""
        if not combo:
            return combo
        try:
            combo_price = float(combo.get("price"))
        except (TypeError, ValueError):
            return combo
        if combo_price <= 0:
            return combo
        leg_prices = []
        for leg in leg_quotes or []:
            try:
                p = float(leg.get("price"))
            except (TypeError, ValueError):
                continue
            if p > 0:
                leg_prices.append(p)
        if not leg_prices:
            return combo
        floor = min(leg_prices) * 0.98
        if combo_price >= floor:
            return combo
        leg_total = sum(leg_prices)
        self.logger.warning(
            "[qunar] %s 组合价 ¥%.0f 低于最低航段单买 ¥%.0f，疑似串价，按航段合计 ¥%.0f 修正",
            label, combo_price, min(leg_prices), leg_total,
        )
        fixed = dict(combo)
        fixed["price"] = float(leg_total)
        return fixed

    def _fetch_enumerated(self, from_city: str, to_city: str, date: str) -> Optional[dict]:
        """指定航班时穷举：套票 / 去程返程组合票 / 各航段单买。"""
        self.logger.info("[qunar] 开始穷举买法")
        return_date = getattr(self, "_route_return_date", None)
        go_nos = list(self._route_flight_nos or [])
        back_nos = list(self._route_return_flight_nos or [])
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
            result = self._quote_on_page(
                page, cap, dep, arr, go,
                return_date=ret,
                flight_nos=list(fns or []),
                return_flight_nos=list(rfns or []),
                exact=exact,
            )
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
                item["label"] = format_leg_label(
                    item, lambda c: self.city_name(c),
                )
                if item["price"] is None:
                    self.logger.warning(
                        "[qunar] 航段未报价 %s %s→%s %s",
                        leg.get("flight_no"), leg.get("from_code"),
                        leg.get("to_code"), leg.get("date"),
                    )
                else:
                    self.logger.info(
                        "[qunar] 航段 %s ¥%.0f", item["label"], item["price"],
                    )
                out.append(item)
            return out

        with self._browser_session() as (page, cap):
            if return_date:
                quotes["round_pkg"] = quote(
                    from_city, to_city, date, ret=return_date,
                    fns=go_nos, rfns=back_nos, exact=True,
                )
            quotes["outbound_combo"] = quote(
                from_city, to_city, date, fns=go_nos, exact=True,
            )
            if return_date:
                quotes["inbound_combo"] = quote(
                    to_city, from_city, return_date, fns=back_nos, exact=True,
                )

            out_legs = self._collect_legs(quotes.get("outbound_combo"))
            if not out_legs:
                out_legs = self._collect_legs(quotes.get("round_pkg"))
            in_legs = self._collect_legs(quotes.get("inbound_combo"))
            if not in_legs:
                pkg = quotes.get("round_pkg") or {}
                in_legs = self._collect_legs({"legs": pkg.get("return_legs") or []})

            out_leg_quotes = quote_legs(out_legs) if len(out_legs) >= 2 else []
            in_leg_quotes = quote_legs(in_legs) if len(in_legs) >= 2 else []

            quotes["outbound_combo"] = self._reconcile_combo_price(
                quotes.get("outbound_combo"), out_leg_quotes, "去程组合票",
            )
            quotes["inbound_combo"] = self._reconcile_combo_price(
                quotes.get("inbound_combo"), in_leg_quotes, "返程组合票",
            )

        plan = build_fare_plan(
            trip="round" if return_date else "one_way",
            round_pkg=quotes.get("round_pkg"),
            outbound_combo=quotes.get("outbound_combo"),
            inbound_combo=quotes.get("inbound_combo"),
            outbound_leg_quotes=out_leg_quotes,
            inbound_leg_quotes=in_leg_quotes,
        )
        for o in plan.get("options") or []:
            self.logger.info("[qunar] 买法 %s ¥%.0f", o["label"], o["price"])
        best = plan.get("best")
        if best:
            self.logger.info("[qunar] 建议买法 %s ¥%.0f", best["label"], best["price"])

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

    # ==================== token 持久化 ====================
    @property
    def _token_path(self) -> str:
        return os.path.join(self.user_data_root, self.name, "token.json")

    def _load_token(self) -> Optional[dict]:
        try:
            with open(self._token_path, "r", encoding="utf-8") as f:
                obj = json.load(f)
        except Exception:
            return None
        updated_at = obj.get("updated_at", 0)
        if time.time() - updated_at > self.TOKEN_TTL_S:
            return None
        return obj

    def _save_token(self, snap):
        if not isinstance(snap, dict):
            snap = {
                "headers": getattr(snap, "headers", None),
                "body_template": getattr(snap, "body_template", None),
                "cookies": getattr(snap, "cookies", {}) or {},
            }
        os.makedirs(os.path.dirname(self._token_path), exist_ok=True)
        data = {
            "headers": snap["headers"],
            "body_template": snap["body_template"],
            "cookies": snap["cookies"],
            "updated_at": time.time(),
        }
        try:
            with open(self._token_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.warning("[qunar] 保存 token 失败: %s", e)

    def _dump_raw_text(self, text: str, tag: str):
        """保存原始响应文本到 debug 目录，用于排查价格异常。"""
        if not self.debug or not text:
            return
        os.makedirs(self.debug_dir, exist_ok=True)
        safe_tag = re.sub(r'[^\w]', '_', tag)
        path = os.path.join(self.debug_dir, f"qunar_raw_{safe_tag}.txt")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            self.logger.info("[qunar] 已保存响应: %s (len=%d)", path, len(text))
        except Exception:
            pass

    @staticmethod
    def _response_failed(text: str) -> bool:
        if not text:
            return True
        try:
            obj = json.loads(text)
        except Exception:
            return False
        if obj.get("ret") is False:
            return True
        code = obj.get("code")
        try:
            if code not in (None, 0, "0"):
                return True
        except Exception:
            pass
        return False

    def _pick_offer(self, text: str, flight_nos=None, silent: bool = False,
                    exact: bool = False, return_flight_nos=None) -> Optional[dict]:
        """从列表响应里挑出最低价报价（可按去程/返程航班号过滤）。"""
        if self._response_failed(text):
            if not silent:
                self.logger.warning("[qunar] 接口返回失败/空航线，跳过该响应")
            return None
        offers = self._parse_offers(text)
        if not offers:
            wanted_raw = self._route_flight_nos if flight_nos is None else flight_nos
            if parse_wanted_flight_nos(wanted_raw) or parse_wanted_flight_nos(return_flight_nos):
                if not silent:
                    self.logger.warning("[qunar] 指定了航班号但响应里没有可解析的航班列表")
                return None
            prices = self._extract_qunar_prices(text)
            if not prices:
                return None
            if not silent:
                self.logger.info("[qunar] 提取价格列表: %s", sorted(set(prices)))
            return {"price": float(min(prices)), "flight_no": "", "airline": "",
                    "depart_time": "", "arrive_time": ""}

        wanted_go = parse_wanted_flight_nos(
            self._route_flight_nos if flight_nos is None else flight_nos
        )
        wanted_back = parse_wanted_flight_nos(return_flight_nos)

        if wanted_back:
            round_pkgs = [o for o in offers if o.get("is_round_pkg") and o.get("return_flight_nos")]
            matched = []
            for o in round_pkgs:
                go_ok = (not wanted_go) or itinerary_matches(
                    o.get("flight_nos") or [], wanted_go, exact=exact,
                )
                back_ok = itinerary_matches(
                    o.get("return_flight_nos") or [], wanted_back, exact=exact,
                )
                if go_ok and back_ok:
                    matched.append(o)
            if matched:
                best = min(matched, key=lambda x: float(x.get("price") or 0))
                if not silent:
                    self.logger.info(
                        "[qunar] 往返套票命中 %d 条，最低 ¥%.0f %s + %s",
                        len(matched), best["price"],
                        best.get("flight_no"), best.get("return_flight_no"),
                    )
                return best
            if wanted_go:
                # 列表里没有完整 go+back：仅保留去程信息，供点击回退（勿把其他返程当成命中）
                offers = self.filter_offers(offers, flight_nos=flight_nos, exact=exact)
                if not offers:
                    return None
                best = self._best_matched_offer(offers)
                if not best:
                    return None
                best = dict(best)
                for k in (
                    "return_flight_no", "return_flight_nos", "return_legs",
                    "return_depart_time", "return_arrive_time",
                    "return_depart_date", "return_arrive_date", "return_cross_day",
                ):
                    best.pop(k, None)
                best["is_round_pkg"] = False
                if not silent:
                    self.logger.info(
                        "[qunar] 仅命中去程 %s，列表无指定返程，待点选回退",
                        best.get("flight_no"),
                    )
                return best
            return None

        if wanted_go:
            offers = self.filter_offers(offers, flight_nos=flight_nos, exact=exact)
            if not offers:
                return None
            best = self._best_matched_offer(offers)
            if not silent and best:
                self.logger.info(
                    "[qunar] 提取航班: %s",
                    [(o.get("flight_no"), o.get("return_flight_no"), o.get("price"))
                     for o in sorted(offers, key=lambda x: x["price"])[:10]],
                )
            return best

        best = self._best_matched_offer(offers) if offers else None
        if not silent and offers:
            self.logger.info(
                "[qunar] 提取航班: %s",
                [(o.get("flight_no"), o.get("price"))
                 for o in sorted(offers, key=lambda x: x["price"])[:10]],
            )
        return best

    @staticmethod
    def _nearby_price(text: str, idx: int, window: int = 3500) -> Optional[float]:
        before = text[max(0, idx - 900):idx]
        after = text[idx:idx + window]
        prices = re.findall(r'minPrice\\":\\"(\d+)', before)
        if not prices:
            prices = re.findall(r'minPrice\\":\\"(\d+)', after)
        if not prices:
            prices = re.findall(r'totalPrice\\":\\"(\d+)', after)
        if not prices:
            return None
        try:
            # 优先取紧邻该航班块前的 minPrice，避免误用邻近航班低价
            price = float(prices[-1] if before and re.search(r'minPrice\\":\\"', before) else min(int(p) for p in prices))
        except Exception:
            return None
        if 100 <= price <= 100000:
            return price
        return None

    @staticmethod
    def _nearby_airline(text: str, idx: int, window: int = 1800) -> str:
        """从混淆字符串中取紧邻报价块的 binfo.name（code 在 name 之后）。"""
        before = text[max(0, idx - window):idx]
        arrays = re.findall(r'name\\":\[(.*?)\]', before)
        if not arrays:
            return ""
        raw = arrays[-1]
        names = re.findall(r'\\"([^"\\]+)\\"', raw)
        if not names:
            return ""
        seen: set = set()
        out: list = []
        for n in names:
            n = str(n).strip()
            if not n or n in seen:
                continue
            if re.fullmatch(r"[A-Z]{3}", n.upper()):
                continue
            if n.endswith("机场") or n.endswith("国际机场"):
                continue
            seen.add(n)
            out.append(n)
        return "/".join(out)

    @staticmethod
    def _brace_object(text: str, open_idx: int) -> Optional[str]:
        if open_idx < 0 or open_idx >= len(text) or text[open_idx] != "{":
            return None
        depth = 0
        in_str = False
        esc = False
        for i in range(open_idx, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[open_idx: i + 1]
        return None

    @staticmethod
    def _qunar_plain_payload(text: str) -> str:
        """取出可直接搜 flights/go/back 的明文片段。"""
        try:
            obj = json.loads(text)
            data = obj.get("data")
            if isinstance(data, str):
                return data
            if isinstance(data, dict):
                return json.dumps(data, ensure_ascii=False)
        except Exception:
            pass
        if '\\"' in text:
            return text.replace('\\"', '"')
        return text

    @staticmethod
    def _parse_dir_block(block: dict) -> dict:
        air = block.get("airCode") or []
        legs = parse_legs(air)
        nos = parse_itinerary_nos(air)
        name = block.get("name") or ""
        if isinstance(name, list):
            name = "/".join(str(x) for x in name if x)
        dep_time = str(block.get("depTime") or "")
        arr_time = str(block.get("arrTime") or "")
        if not re.fullmatch(r"\d{2}:\d{2}", dep_time):
            dep_time = ""
        if not re.fullmatch(r"\d{2}:\d{2}", arr_time):
            arr_time = ""
        return {
            "flight_nos": nos,
            "flight_no": format_itinerary(nos),
            "legs": legs,
            "depart_time": dep_time,
            "arrive_time": arr_time,
            # 日期以 airCode 航段为准，避免串包把去程日写到返程
            "depart_date": (legs[0]["date"] if legs else "") or str(block.get("depDate") or ""),
            "arrive_date": (legs[-1]["date"] if legs else "") or str(block.get("arrDate") or ""),
            "cross_day": str(block.get("crossDayDesc") or ""),
            "airline": str(name or ""),
        }

    @staticmethod
    def _parse_dir_from_code_blob(code_part: str, blob: str) -> dict:
        """code 航段 + go/back 文本碎片（JSON 可能损坏）提取航班与时刻。"""
        legs = parse_legs(code_part)
        nos = parse_itinerary_nos(code_part)
        # 截断到明显串包/损坏点，避免吃到邻近航班的 depTime
        safe = blob or ""
        for stop in ('"flightKey"', "imitPrice", '\\"', '"extparams"'):
            cut = safe.find(stop)
            if cut >= 0:
                safe = safe[:cut]
        # 只相信 airCode 之后的字段，降低串到上一段的概率
        air_at = safe.find('"airCode"')
        if air_at >= 0:
            safe = safe[air_at:]

        def grab(key: str) -> str:
            m = re.search(rf'"{key}"\s*:\s*"([^"]*)"', safe)
            return m.group(1) if m else ""

        airline = ""
        for arr in re.findall(r'"name"\s*:\s*\[(.*?)\]', safe):
            names = re.findall(r'"([^"\\]+)"', arr)
            cleaned = []
            for n in names:
                n = str(n).strip()
                if not n or re.fullmatch(r"[A-Z]{3}", n.upper()):
                    continue
                if n.endswith("机场") or n.endswith("国际机场"):
                    continue
                cleaned.append(n)
            if cleaned:
                airline = "/".join(cleaned)
                break
        # 日期优先用 code 航段（可靠），对象里的 depDate 仅作补充
        dep_date = (legs[0]["date"] if legs else "") or grab("depDate")
        arr_date = (legs[-1]["date"] if legs else "") or grab("arrDate")
        dep_time = grab("depTime")
        arr_time = grab("arrTime")
        if not re.fullmatch(r"\d{2}:\d{2}", dep_time or ""):
            dep_time = ""
        if not re.fullmatch(r"\d{2}:\d{2}", arr_time or ""):
            arr_time = ""
        return {
            "flight_nos": nos,
            "flight_no": format_itinerary(nos),
            "legs": legs,
            "depart_time": dep_time,
            "arrive_time": arr_time,
            "depart_date": dep_date,
            "arrive_date": arr_date,
            "cross_day": grab("crossDayDesc"),
            "airline": airline,
        }

    @staticmethod
    def _offer_from_round_parts(price: float, go_info: dict, back_info: dict) -> Optional[dict]:
        if not (100 <= price <= 100000):
            return None
        if not go_info.get("flight_nos") or not back_info.get("flight_nos"):
            return None
        return {
            "price": float(price),
            "flight_no": go_info["flight_no"],
            "flight_nos": go_info["flight_nos"],
            "legs": go_info["legs"],
            "airline": go_info.get("airline") or "",
            "depart_time": go_info.get("depart_time") or "",
            "arrive_time": go_info.get("arrive_time") or "",
            "depart_date": go_info.get("depart_date") or "",
            "arrive_date": go_info.get("arrive_date") or "",
            "cross_day": go_info.get("cross_day") or "",
            "return_flight_no": back_info["flight_no"],
            "return_flight_nos": back_info["flight_nos"],
            "return_legs": back_info["legs"],
            "return_depart_time": back_info.get("depart_time") or "",
            "return_arrive_time": back_info.get("arrive_time") or "",
            "return_depart_date": back_info.get("depart_date") or "",
            "return_arrive_date": back_info.get("arrive_date") or "",
            "return_cross_day": back_info.get("cross_day") or "",
            "is_round_pkg": True,
        }

    @staticmethod
    def _offer_from_round_flight(f: dict) -> Optional[dict]:
        go = f.get("go") if isinstance(f.get("go"), dict) else None
        back = f.get("back") if isinstance(f.get("back"), dict) else None
        try:
            price = float(f.get("minPrice") or f.get("totalPrice") or 0)
        except Exception:
            return None
        code = str(f.get("code") or "")
        if "/" not in code:
            if go and back:
                return QunarCrawler._offer_from_round_parts(
                    price,
                    QunarCrawler._parse_dir_block(go),
                    QunarCrawler._parse_dir_block(back),
                )
            return None
        go_c, back_c = code.split("/", 1)
        go_from_code = QunarCrawler._parse_dir_from_code_blob(go_c, "")
        back_from_code = QunarCrawler._parse_dir_from_code_blob(back_c, "")
        if not go_from_code.get("flight_nos") or not back_from_code.get("flight_nos"):
            return None

        def merge_dir(code_info: dict, block: Optional[dict]) -> dict:
            """航班号/日期以 code 为准；时刻仅在 JSON airCode 与 code 一致时采信。"""
            out = dict(code_info)
            if not isinstance(block, dict):
                return out
            info = QunarCrawler._parse_dir_block(block)
            if set(info.get("flight_nos") or []) != set(code_info.get("flight_nos") or []):
                return out
            if info.get("depart_time"):
                out["depart_time"] = info["depart_time"]
            if info.get("arrive_time"):
                out["arrive_time"] = info["arrive_time"]
            if info.get("cross_day"):
                out["cross_day"] = info["cross_day"]
            if info.get("airline"):
                out["airline"] = info["airline"]
            return out

        go_info = merge_dir(go_from_code, go)
        back_info = merge_dir(back_from_code, back)
        # 无可靠 JSON 时，用文本碎片补时刻（仍受 sanitize 约束）
        if not go_info.get("depart_time") or not back_info.get("depart_time"):
            # 调用方若只给了 code，无 blob；保持空时刻待回填
            pass
        return QunarCrawler._offer_from_round_parts(price, go_info, back_info)

    @staticmethod
    def _iter_round_flight_dicts(text: str):
        """遍历往返列表里的 flights[]（含 data 为残缺 JSON 字符串的情况）。"""
        try:
            obj = json.loads(text)
            data = obj.get("data")
            if isinstance(data, dict):
                for f in data.get("flights") or []:
                    if isinstance(f, dict) and isinstance(f.get("go"), dict) and isinstance(f.get("back"), dict):
                        yield f
                return
            if isinstance(data, str):
                try:
                    parsed = json.loads(data)
                except Exception:
                    parsed = None
                if isinstance(parsed, dict):
                    for f in parsed.get("flights") or []:
                        if isinstance(f, dict) and isinstance(f.get("go"), dict) and isinstance(f.get("back"), dict):
                            yield f
                    return
                plain = data
            else:
                plain = QunarCrawler._qunar_plain_payload(text)
        except Exception:
            plain = QunarCrawler._qunar_plain_payload(text)

        for m in re.finditer(r'"code"\s*:\s*"([^"]+)"', plain):
            code = m.group(1)
            # 往返 code：去程,/返程；航段须为 FN|AAA-BBB|YYYY-MM-DD
            if "/" not in code or "|" not in code:
                continue
            go_c, back_c = code.split("/", 1)
            if not parse_legs(go_c) or not parse_legs(back_c):
                continue
            window = plain[m.start(): m.start() + 6000]
            # minPrice 须紧跟本条 code，避免窗太宽吃到下一条报价
            pm = re.search(r'"code"\s*:\s*"[^"]+"\s*,\s*"minPrice"\s*:\s*"?(\d+)"?', window)
            if not pm:
                pm = re.search(r'"minPrice"\s*:\s*"?(\d+)"?', window[:400])
            if not pm:
                continue
            go_m = re.search(r'"go"\s*:\s*\{', window)
            back_m = re.search(r'"back"\s*:\s*\{', window)
            go_obj = back_obj = None
            go_blob = back_blob = ""
            if go_m and back_m and go_m.start() < back_m.start():
                go_blob = window[go_m.start(): back_m.start()]
                back_blob = window[back_m.start(): back_m.start() + 1200]
                go_s = QunarCrawler._brace_object(window, go_m.end() - 1)
                back_s = QunarCrawler._brace_object(window, back_m.end() - 1)
                if go_s:
                    try:
                        go_obj = json.loads(go_s)
                    except Exception:
                        go_obj = None
                if back_s:
                    try:
                        back_obj = json.loads(back_s)
                    except Exception:
                        back_obj = None
            if go_obj is not None and back_obj is not None:
                yield {
                    "code": code,
                    "minPrice": pm.group(1),
                    "go": go_obj,
                    "back": back_obj,
                }
            else:
                # JSON 损坏时只信任 code 航段，时刻留给同航班回填，避免 blob 串时
                yield {
                    "code": code,
                    "minPrice": pm.group(1),
                    "go": {
                        "airCode": [x.strip() for x in go_c.split(",") if x.strip()],
                    },
                    "back": {
                        "airCode": [x.strip() for x in back_c.split(",") if x.strip()],
                    },
                }

    @staticmethod
    def _sanitize_round_offer(o: dict) -> dict:
        """以 code 航段日期为准；清掉 go/back 串包导致的错误时刻。"""
        legs = o.get("legs") or []
        rlegs = o.get("return_legs") or []
        if legs:
            o["depart_date"] = legs[0].get("date") or o.get("depart_date") or ""
            o["arrive_date"] = legs[-1].get("date") or o.get("arrive_date") or ""
        if rlegs:
            o["return_depart_date"] = rlegs[0].get("date") or o.get("return_depart_date") or ""
            o["return_arrive_date"] = rlegs[-1].get("date") or o.get("return_arrive_date") or ""

        go_dep = o.get("depart_time") or ""
        go_arr = o.get("arrive_time") or ""
        back_dep = o.get("return_depart_time") or ""
        back_arr = o.get("return_arrive_time") or ""

        # 返程起飞时刻与去程起飞相同 → 高概率串包（即便到达时刻不同）
        if go_dep and back_dep == go_dep:
            o["return_depart_time"] = ""
            o["return_arrive_time"] = ""
            o["return_cross_day"] = ""
        # 返程到达=去程到达也不可信
        elif go_arr and back_arr == go_arr and back_dep:
            o["return_depart_time"] = ""
            o["return_arrive_time"] = ""
            o["return_cross_day"] = ""

        return o

    @staticmethod
    def _parse_round_packages(text: str) -> list:
        """解析国际/往返列表中的 go+back 完整套票（含时刻与跨天日期）。"""
        offers: list = []
        seen: set = set()
        for f in QunarCrawler._iter_round_flight_dicts(text):
            o = QunarCrawler._offer_from_round_flight(f)
            if not o:
                continue
            o = QunarCrawler._sanitize_round_offer(o)
            key = (
                o.get("flight_no") or "",
                o.get("return_flight_no") or "",
                float(o.get("price") or 0),
                o.get("depart_time") or "",
                o.get("return_depart_time") or "",
            )
            if key in seen:
                continue
            seen.add(key)
            offers.append(o)

        # 同去程/同返程若有完整且未串包的时刻，回填损坏片段
        go_times: dict = {}
        back_times: dict = {}
        for o in offers:
            gk = o.get("flight_no") or ""
            bk = o.get("return_flight_no") or ""
            if gk and o.get("depart_time") and o.get("arrive_time"):
                go_times.setdefault(gk, (o["depart_time"], o["arrive_time"],
                                         o.get("depart_date") or "", o.get("arrive_date") or "",
                                         o.get("cross_day") or ""))
            # 回填源：返程起飞 ≠ 去程起飞，且返程日期与航段一致
            if (
                bk
                and o.get("return_depart_time")
                and o.get("return_arrive_time")
                and o.get("return_depart_time") != o.get("depart_time")
                and o.get("return_arrive_time") != o.get("arrive_time")
            ):
                rlegs = o.get("return_legs") or []
                rd = (o.get("return_depart_date") or "")[:10]
                if rlegs and rd and rd != (rlegs[0].get("date") or "")[:10]:
                    continue
                back_times.setdefault(bk, (o["return_depart_time"], o["return_arrive_time"],
                                           o.get("return_depart_date") or "",
                                           o.get("return_arrive_date") or "",
                                           o.get("return_cross_day") or ""))
        for o in offers:
            gk = o.get("flight_no") or ""
            bk = o.get("return_flight_no") or ""
            if gk and not o.get("depart_time") and gk in go_times:
                dt, at, dd, ad, cd = go_times[gk]
                o["depart_time"], o["arrive_time"] = dt, at
                o["depart_date"] = o.get("depart_date") or dd
                o["arrive_date"] = o.get("arrive_date") or ad
                o["cross_day"] = o.get("cross_day") or cd
            if bk and not o.get("return_depart_time") and bk in back_times:
                dt, at, dd, ad, cd = back_times[bk]
                if dt == (o.get("depart_time") or "") or at == (o.get("arrive_time") or ""):
                    pass
                else:
                    o["return_depart_time"], o["return_arrive_time"] = dt, at
                    rlegs = o.get("return_legs") or []
                    if rlegs:
                        o["return_depart_date"] = rlegs[0].get("date") or dd
                        o["return_arrive_date"] = rlegs[-1].get("date") or ad
                    else:
                        o["return_depart_date"] = o.get("return_depart_date") or dd
                        o["return_arrive_date"] = o.get("return_arrive_date") or ad
                    o["return_cross_day"] = o.get("return_cross_day") or cd
            QunarCrawler._sanitize_round_offer(o)
        return offers

    @staticmethod
    def _parse_offers_obfuscated(text: str) -> list:
        """touchInnerList / touchInterList 混淆 data 字符串的兜底解析。"""
        if not text:
            return []
        # 往返列表优先走完整套票，避免把 go/back 拆成散落航班号
        round_pkgs = QunarCrawler._parse_round_packages(text)
        if round_pkgs:
            return round_pkgs

        offers: list = []
        seen: set = set()
        leg_pat = r"[A-Z0-9]{2,3}\d{3,4}\|[A-Z]{3}-[A-Z]{3}\|\d{4}-\d{2}-\d{2}"

        def add_offer(fn_str: str, price: float, at: int = 0):
            nos = parse_itinerary_nos(fn_str)
            if not nos or price is None:
                return
            key = (format_itinerary(nos), float(price))
            if key in seen:
                return
            seen.add(key)
            offers.append({
                "price": float(price),
                "flight_no": format_itinerary(nos),
                "flight_nos": nos,
                "legs": parse_legs(fn_str) or [],
                "airline": QunarCrawler._nearby_airline(text, at),
                "depart_time": "",
                "arrive_time": "",
            })

        # 国际中转：code 数组两段 ["MU9658|...","MU5931|..."]
        for m in re.finditer(rf'({leg_pat})\\\",\\\"({leg_pat})', text):
            fn_str = f"{m.group(1)}/{m.group(2)}"
            chunk = text[m.end(): m.end() + 200]
            pm = re.search(r'minPrice\\":\\"(\d+)', chunk)
            if pm:
                price = float(pm.group(1))
            else:
                price = QunarCrawler._nearby_price(text, m.start())
            if price is not None:
                add_offer(fn_str, price, m.start())

        # 国际中转：flightKey / flightCode 多段（MU9658|HKG-KMG|.../MU5931|KMG-DIG|...）
        multi = rf"({leg_pat}(?:/{leg_pat})+)"
        for m in re.finditer(multi, text):
            fn_str = m.group(1)
            price = QunarCrawler._nearby_price(text, m.start())
            if price is not None:
                add_offer(fn_str, price, m.start())

        # 国际：extparams 里 flightNo 为 MU9658/MU5931
        for m in re.finditer(
            r'flightNo\\":\\"([A-Z0-9]{2,3}\d{3,4}(?:/[A-Z0-9]{2,3}\d{3,4})+)\\"',
            text,
        ):
            fn_str = m.group(1).replace("/", "+")
            price = QunarCrawler._nearby_price(text, m.start())
            if price is not None:
                add_offer(fn_str, price, m.start())

        # 国际：UO614|HKG-ICN|2026-11-01 ...（单段）
        for m in re.finditer(r"\b([A-Z0-9]{2,3}\d{3,4})\|([A-Z]{3})-([A-Z]{3})\|", text):
            fn = m.group(1)
            # 跳过已作为多段组合的一部分处理过的位置
            start = m.start()
            before = text[max(0, start - 80):start]
            if re.search(rf"{leg_pat}/$", before):
                continue
            price = QunarCrawler._nearby_price(text, start)
            if price is not None:
                add_offer(fn, price, start)

        # 国内 / 中转组合：CZ8595 或 CZ8595+MU9067 后随 minPrice
        for m in re.finditer(
            r"([A-Z0-9]{2,3}\d{3,4}(?:\+[A-Z0-9]{2,3}\d{3,4})*)",
            text,
        ):
            fn_str = m.group(1)
            if "|" in fn_str:
                continue
            price = QunarCrawler._nearby_price(text, m.start())
            if price is not None:
                add_offer(fn_str, price, m.start())

        return offers

    @staticmethod
    def _parse_offers(text: str) -> list:
        """解析 flights[] 为带航班号的报价；往返优先完整 go+back 套票。"""
        if not text:
            return []
        round_pkgs = QunarCrawler._parse_round_packages(text)
        if round_pkgs:
            return round_pkgs

        try:
            obj = json.loads(text)
        except Exception:
            return QunarCrawler._parse_offers_obfuscated(text)

        data = obj.get("data")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                return QunarCrawler._parse_offers_obfuscated(text)
        if not isinstance(data, dict):
            return QunarCrawler._parse_offers_obfuscated(text)

        flights = data.get("flights") or []
        if not flights:
            flights = list(data.get("directFlights") or []) + list(data.get("transFlights") or [])
        offers = []
        for f in flights:
            if not isinstance(f, dict):
                continue
            if isinstance(f.get("go"), dict) and isinstance(f.get("back"), dict):
                pkg = QunarCrawler._offer_from_round_flight(f)
                if pkg:
                    offers.append(pkg)
                continue
            binfo = f.get("binfo") if isinstance(f.get("binfo"), dict) else {}
            code = f.get("code") or binfo.get("airCode") or f.get("flightCode")
            if isinstance(code, list):
                code = "/".join(str(x) for x in code if x)
            nos = parse_itinerary_nos(code)
            legs = parse_legs(code)
            price = None
            for k in ("minPrice", "totalPrice"):
                try:
                    iv = int(float(f.get(k)))
                    if 100 <= iv <= 100000:
                        price = iv
                        break
                except Exception:
                    pass
            if price is None:
                continue
            name = binfo.get("name") or f.get("name") or ""
            if isinstance(name, list):
                name = "/".join(str(x) for x in name if x)
            offers.append({
                "price": float(price),
                "flight_no": format_itinerary(nos),
                "flight_nos": nos,
                "legs": legs,
                "airline": str(name or ""),
                "depart_time": str(binfo.get("depTime") or f.get("depTime") or ""),
                "arrive_time": str(binfo.get("arrTime") or f.get("arrTime") or ""),
                "depart_date": str(binfo.get("depDate") or f.get("depDate") or ""),
                "arrive_date": str(binfo.get("arrDate") or f.get("arrDate") or ""),
            })
        if not offers:
            offers = QunarCrawler._parse_offers_obfuscated(text)
        return offers

    # ==================== 价格解析 ====================
    @staticmethod
    def _extract_qunar_prices(text: str) -> list:
        """解析 touchInnerList JSON，提取航班最低价。

        去哪儿接口 data 字段是字符串化的 JSON（引号被转义为 \\"）。
        实测只有 minPrice 字段可信（航班最低价，与 DOM 渲染价一致）；
        totalPrice 字段含非价格数据（如 "127b"、4、42 等编码/附加费），
        不可作为价格候选。

        解析优先级：
        1) json.loads 全量解析 data 字符串，递归取 minPrice
        2) 兜底正则（兼容转义引号 \\" 形式）只取 minPrice
        """
        if not text:
            return []
        try:
            obj = json.loads(text)
        except Exception:
            return QunarCrawler._regex_extract_prices(text)

        data = obj.get("data")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                return QunarCrawler._regex_extract_prices(text)
        if not isinstance(data, dict):
            return QunarCrawler._regex_extract_prices(text)

        prices: list = []

        # 1) 顶层 minPrice / lowestPrice（最可信，实测与 DOM 渲染价一致）
        for key in ("minPrice", "lowestPrice"):
            v = data.get(key)
            try:
                iv = int(float(v))
                if 100 <= iv <= 50000:
                    prices.append(iv)
                    break
            except Exception:
                pass
        if prices:
            return prices

        # 2) 递归遍历航班节点，只取 minPrice / lowestPrice（totalPrice 含非价格数据）
        def scan(node):
            if isinstance(node, dict):
                for k in ("minPrice", "lowestPrice"):
                    v = node.get(k)
                    if v is not None:
                        try:
                            iv = int(float(v))
                            if 100 <= iv <= 50000:
                                prices.append(iv)
                        except Exception:
                            pass
                for v in node.values():
                    if isinstance(v, (dict, list)):
                        scan(v)
            elif isinstance(node, list):
                for v in node:
                    scan(v)

        scan(data)
        if prices:
            return prices

        # 3) 兜底正则
        return QunarCrawler._regex_extract_prices(text)

    @staticmethod
    def _regex_extract_prices(text: str) -> list:
        """正则兜底：只取 minPrice（兼容转义引号、截断 JSON、数字未加引号）。"""
        prices: list = []
        patterns = [
            # minPrice":"910" 或 minPrice\":\"910\"
            r'minPrice\\?["\']\s*:\s*\\?["\'](\d{3,5})\\?["\']',
            # minPrice":2607,  /  minPrice\":2607,  （截断或数字未引号）
            r'minPrice\\?["\']\s*:\s*(\d{3,5})(?=[,}\]]|\\?["\'])',
        ]
        for pat in patterns:
            for m in re.finditer(pat, text):
                try:
                    prices.append(int(m.group(1)))
                except Exception:
                    pass
        return [p for p in prices if 100 <= p <= 50000]
