"""低价提醒 + 微信推送（带去抖）"""
import logging
from typing import List, Optional

from .models import FlightPrice, Route
from .fareplan import clip_text, load_fare_plan, render_plan_short
from .platforms import platform_label
from .crossplan import merge_cross_platform_legs, render_cross_plan_short
from .quote_detail import quote_title, render_quote_markdown


class Alerter:
    def __init__(self, logger: logging.Logger, notifier=None, storage=None,
                 push_drop_min: float = 30, push_rise_min: float = 50):
        """
        notifier:        推送器（None 表示不推送）
        storage:         用于读写 alert_state（去抖）
        push_drop_min:   触发"再次推送"的最小降幅(¥)
        push_rise_min:   触发"再次推送"的最小涨幅(¥)
        """
        self.logger = logger
        self.notifier = notifier
        self.storage = storage
        self.push_drop_min = push_drop_min
        self.push_rise_min = push_rise_min

    def _arrow(self, route: Route) -> str:
        return "⇄" if route.is_round else "->"

    def _date_key(self, p: FlightPrice) -> str:
        return p.date_label()

    def check_and_alert(self, route: Route, prices: List[FlightPrice]):
        if not prices:
            return
        self._compare_platforms(route, prices)
        if route.is_fare_plan:
            self._compare_cross_platform(route, prices)
            self._log_fare_plans(route, prices)
        self._compare_dates(route, prices)
        if route.alert_threshold and route.alert_threshold > 0:
            self._handle_threshold(route, prices)

    def _compare_cross_platform(self, route: Route, prices: List[FlightPrice]):
        by_date = {}
        for p in prices:
            by_date.setdefault(self._date_key(p), []).append(p)
        for date, lst in sorted(by_date.items()):
            cross = merge_cross_platform_legs(lst)
            if not cross:
                continue
            plat_best = min(lst, key=lambda x: x.price)
            bits = [
                f"{(p.get('display') or p.get('label') or p.get('flight_no'))} ¥{p['price']:.0f}"
                for p in cross.get("parts") or []
            ]
            save = ""
            if cross["price"] < plat_best.price:
                save = f"，比单平台最低省¥{plat_best.price - cross['price']:.0f}"
            self.logger.info(
                "[跨平台] %s%s%s %s 拆段最优 ¥%.0f%s  (%s)",
                route.from_name, self._arrow(route), route.to_name, date,
                cross["price"], save, " + ".join(bits),
            )

    def _log_fare_plans(self, route: Route, prices: List[FlightPrice]):
        for p in prices:
            plan = load_fare_plan(p.extra)
            if not plan or not plan.get("best"):
                continue
            bits = [f"{o['label']}¥{o['price']:.0f}" for o in plan.get("options") or []]
            self.logger.info(
                "[买法] %s%s%s %s 建议=%s ¥%.0f  (%s)",
                route.from_name, self._arrow(route), route.to_name,
                p.date_label(),
                plan["best"]["label"], plan["best"]["price"],
                " | ".join(bits),
            )

    def _compare_platforms(self, route: Route, prices: List[FlightPrice]):
        by_date = {}
        for p in prices:
            by_date.setdefault(self._date_key(p), []).append(p)
        for date, lst in sorted(by_date.items()):
            lst = sorted(lst, key=lambda x: x.price)
            best = lst[0]
            line = " | ".join(
                f"{platform_label(p.platform)}: ¥{p.price:.0f}"
                + (f" {p.flight_no}" if p.flight_no else "")
                for p in lst
            )
            self.logger.info(
                "[对比] %s%s%s %s 最低=%s ¥%.0f%s  (%s)",
                route.from_name, self._arrow(route), route.to_name, date,
                platform_label(best.platform), best.price,
                f" {best.flight_no}" if best.flight_no else "",
                line,
            )

    def _compare_dates(self, route: Route, prices: List[FlightPrice]):
        keys = {self._date_key(p) for p in prices}
        if len(keys) <= 1:
            return
        best_by_date = {}
        for p in prices:
            k = self._date_key(p)
            cur = best_by_date.get(k)
            if cur is None or p.price < cur.price:
                best_by_date[k] = p
        ordered = sorted(best_by_date.items(), key=lambda kv: kv[1].price)
        if not ordered:
            return
        cheapest_date, cheapest_p = ordered[0]
        self.logger.info(
            "[多日期] %s%s%s 最便宜日期=%s ¥%.0f (%s)",
            route.from_name, self._arrow(route), route.to_name, cheapest_date,
            cheapest_p.price, platform_label(cheapest_p.platform),
        )

    def _handle_threshold(self, route: Route, prices: List[FlightPrice]):
        best_by_date = {}
        for p in prices:
            k = self._date_key(p)
            cur = best_by_date.get(k)
            if cur is None or p.price < cur.price:
                best_by_date[k] = p

        for date, bp in sorted(best_by_date.items()):
            route_key = f"{route.from_code}-{route.to_code}-{date}"
            if route.is_round:
                route_key = f"RT-{route_key}"
            if route.flight_nos:
                from .flights import parse_wanted_flight_nos, format_wanted
                route_key += "-" + format_wanted(parse_wanted_flight_nos(route.flight_nos))
            if route.return_flight_nos:
                from .flights import parse_wanted_flight_nos, format_wanted
                route_key += "-R" + format_wanted(parse_wanted_flight_nos(route.return_flight_nos))

            day_prices = [p for p in prices if self._date_key(p) == date]
            cross = merge_cross_platform_legs(day_prices) if route.is_fare_plan else None
            effective_price = bp.price
            cross_plan = None
            if cross and cross["price"] < effective_price:
                effective_price = cross["price"]
                cross_plan = cross

            if effective_price > route.alert_threshold:
                if self.storage:
                    self.storage.clear_alert_state(route_key)
                continue

            last = self.storage.get_alert_state(route_key) if self.storage else None
            should_push, reason = self._should_push(effective_price, last)

            self.logger.warning(
                "[低价] %s%s%s %s ¥%.0f%s (阈值¥%.0f, 上次推送¥%s) -> %s",
                route.from_name, self._arrow(route), route.to_name, date,
                effective_price,
                f" {bp.flight_no}" if bp.flight_no and not cross_plan else "",
                route.alert_threshold,
                f"{last:.0f}" if last else "-",
                "推送" if should_push else f"跳过({reason})",
            )

            if should_push and self.notifier:
                ok = self._push(route, date, bp, last, cross_plan=cross_plan,
                                effective_price=effective_price)
                if ok and self.storage:
                    self.storage.set_alert_state(route_key, effective_price)

    def _should_push(self, cur: float, last: Optional[float]):
        if last is None:
            return True, "首次"
        diff = cur - last
        if diff <= -self.push_drop_min:
            return True, f"降¥{-diff:.0f}"
        if diff >= self.push_rise_min:
            return True, f"涨¥{diff:.0f}"
        return False, f"波动¥{diff:+.0f}(<阈值)"

    def _push_title(self, route: Route, date: str, price: float,
                    last: Optional[float]) -> str:
        return quote_title(route, date, price, last)

    def _push(self, route: Route, date: str, p: FlightPrice,
              last: Optional[float], cross_plan=None,
              effective_price: Optional[float] = None) -> bool:
        price = float(effective_price if effective_price is not None else p.price)
        title = self._push_title(route, date, price, last)
        plan = load_fare_plan(p.extra)
        short = ""
        if cross_plan:
            short = clip_text(render_cross_plan_short(cross_plan), 64)
        elif plan:
            short = render_plan_short(plan)
        if not short:
            diff_txt = ""
            if last is not None:
                diff = price - last
                diff_txt = f"（较上次{'降' if diff <= 0 else '涨'} ¥{abs(diff):.0f}）"
            short = clip_text(
                f"{date} ¥{price:.0f} 低于阈值¥{route.alert_threshold:.0f}{diff_txt}",
                64,
            )
        desp = render_quote_markdown(
            route, p,
            cross_plan=cross_plan,
            effective_price=price,
            last_price=last,
            include_threshold=True,
        )
        return self.notifier.send(title, desp, short=short)

    def _build_view_url(self, route: Route, date: str, platform: str) -> str:
        from .quote_detail import build_view_url
        return build_view_url(route, date, platform)
