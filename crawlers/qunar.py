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
)
from core.fareplan import build_fare_plan, format_leg_label
from .base import BaseCrawler, INTER_CODES, INTER_NAMES


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
        results: List[FlightPrice] = []
        want_plan = bool(self._route_flight_nos or self._route_return_flight_nos)
        for date in dates:
            try:
                if want_plan:
                    offer = self._fetch_enumerated(from_city, to_city, date)
                else:
                    offer = self._fetch_one(from_city, to_city, date)
            except Exception as e:
                self.logger.exception("[qunar] %s 抓取异常: %s", date, e)
                offer = None
            if offer is not None:
                ret_date = getattr(self, "_route_return_date", None) or ""
                go_no = offer.get("flight_no") or ""
                back_no = offer.get("return_flight_no") or ""
                flight_label = go_no
                if back_no:
                    flight_label = f"{go_no}|{back_no}" if go_no else back_no
                payload = {}
                if ret_date:
                    payload.update({
                        "trip": "round",
                        "return_date": ret_date,
                        "return_flight_no": back_no,
                    })
                plan = offer.get("fare_plan")
                if plan:
                    payload["fare_plan"] = plan
                extra = json.dumps(payload, ensure_ascii=False) if payload else ""
                results.append(FlightPrice(
                    platform=self.name,
                    from_city=from_city, to_city=to_city,
                    depart_date=date, price=float(offer["price"]),
                    airline=offer.get("airline") or "",
                    flight_no=flight_label,
                    depart_time=offer.get("depart_time") or "",
                    arrive_time=offer.get("arrive_time") or "",
                    extra=extra,
                    return_date=ret_date,
                ))
                self.logger.info("[qunar] %s 最低价 ¥%.0f %s %s",
                                 f"{date}/{ret_date}" if ret_date else date,
                                 offer["price"],
                                 offer.get("airline") or "",
                                 flight_label)
            else:
                self.logger.warning("[qunar] %s 未解析到价格", date)
            self._sleep()
        return results

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
        return bool(codes & INTER_CODES) or bool(names & INTER_NAMES)

    def _fetch_one(self, from_city: str, to_city: str, date: str) -> Optional[dict]:
        """单日期抓取：优先 httpx，失败回退浏览器。港澳台/往返走浏览器。"""
        from_name, to_name = self._city_pair(from_city, to_city)
        return_date = getattr(self, "_route_return_date", None)
        if return_date or self._is_inter(from_city, to_city, from_name, to_name):
            kind = "往返" if return_date else "港澳台/国际"
            self.logger.info("[qunar] %s航线 %s→%s，走浏览器", kind, from_name, to_name)
            return self._fetch_via_browser(from_city, to_city, date)
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
                             exact: bool = False) -> Optional[dict]:
        best = None
        for text in texts:
            offers = self._parse_offers(text)
            if not offers:
                continue
            offers = self.filter_offers(offers, flight_nos=flight_nos, exact=exact)
            if not offers:
                continue
            cur = min(offers, key=lambda o: o["price"])
            if best is None or cur["price"] < best["price"]:
                best = cur
        return best

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

    def _wait_list(self, page, cap: _Capture, is_inter: bool, return_date: Optional[str]):
        wait_rounds = 20 if (is_inter or return_date) else 12
        for _ in range(wait_rounds):
            page.wait_for_timeout(2000)
            try:
                page.mouse.wheel(0, 1500)
            except Exception:
                pass
            if cap.response_text is not None:
                if "minPrice" in cap.response_text or not (is_inter or return_date):
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
            self._wait_list(page, cap, is_inter, return_date)
        except Exception as e:
            self.logger.warning("[qunar] 浏览器页面异常: %s", e)

        picked = self._pick_from_responses(
            cap.responses, flight_nos=nos, exact=exact,
        )
        if return_date and back_nos and picked:
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
                        picked["price"] = inbound["price"]
                        picked["return_flight_no"] = inbound.get("flight_no") or ""
                        picked["return_flight_nos"] = inbound.get("flight_nos") or []
                        picked["return_legs"] = inbound.get("legs") or []
                        self.logger.info(
                            "[qunar] 往返套票 去程 %s + 返程 %s ¥%.0f",
                            picked.get("flight_no"), inbound.get("flight_no"),
                            inbound["price"],
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

    def _fetch_enumerated(self, from_city: str, to_city: str, date: str) -> Optional[dict]:
        """指定航班时穷举：套票 / 去程返程组合票 / 各航段单买。"""
        self.logger.info("[qunar] 指定航班，开始穷举买法")
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
                    fns=go_nos, rfns=back_nos,
                )
            quotes["outbound_combo"] = quote(
                from_city, to_city, date, fns=go_nos,
            )
            if return_date:
                quotes["inbound_combo"] = quote(
                    to_city, from_city, return_date, fns=back_nos,
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

    def _pick_offer(self, text: str, flight_nos=None, silent: bool = False) -> Optional[dict]:
        """从列表响应里挑出最低价报价（可按 flight_nos 过滤）。"""
        offers = self._parse_offers(text)
        if offers:
            offers = self.filter_offers(offers, flight_nos=flight_nos)
            if not offers:
                return None
            best = min(offers, key=lambda o: o["price"])
            if not silent:
                self.logger.info(
                    "[qunar] 提取航班: %s",
                    [(o.get("flight_no"), o.get("price"))
                     for o in sorted(offers, key=lambda x: x["price"])[:10]],
                )
            return best
        wanted_raw = self._route_flight_nos if flight_nos is None else flight_nos
        if parse_wanted_flight_nos(wanted_raw):
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

    @staticmethod
    def _parse_offers(text: str) -> list:
        """解析 flights[] 为带航班号的报价。"""
        if not text:
            return []
        try:
            obj = json.loads(text)
        except Exception:
            return []
        data = obj.get("data")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                return []
        if not isinstance(data, dict):
            return []

        flights = data.get("flights") or []
        if not flights:
            flights = list(data.get("directFlights") or []) + list(data.get("transFlights") or [])
        offers = []
        for f in flights:
            if not isinstance(f, dict):
                continue
            binfo = f.get("binfo") if isinstance(f.get("binfo"), dict) else {}
            code = f.get("code") or binfo.get("airCode") or f.get("flightCode")
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
                "depart_time": str(binfo.get("depTime") or ""),
                "arrive_time": str(binfo.get("arrTime") or ""),
            })
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
        """正则兜底：只取 minPrice，要求值后跟引号闭合（排除 "127b" 等非数字值）。

        实测 totalPrice 字段含 "127b"、"4" 等非价格数据，
        故正则只匹配 minPrice 且要求数字后紧跟引号。
        """
        prices: list = []
        # minPrice":"910" 或 minPrice\":\"910\"  要求数字后有引号闭合
        for m in re.finditer(
            r'minPrice\\?["\']\s*:\s*\\?["\'](\d{3,5})\\?["\']',
            text,
        ):
            try:
                prices.append(int(m.group(1)))
            except Exception:
                pass
        return [p for p in prices if 100 <= p <= 50000]
