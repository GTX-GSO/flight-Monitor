"""低价提醒 + 微信推送（带去抖）"""
import logging
from typing import List, Optional

from .models import FlightPrice, Route
from .fareplan import clip_text, load_fare_plan, render_plan_desp, render_plan_short


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
        self._compare_dates(route, prices)
        self._log_fare_plans(route, prices)
        if route.alert_threshold and route.alert_threshold > 0:
            self._handle_threshold(route, prices)

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
                f"{p.platform}: ¥{p.price:.0f}" + (f" {p.flight_no}" if p.flight_no else "")
                for p in lst
            )
            self.logger.info(
                "[对比] %s%s%s %s 最低=%s ¥%.0f%s  (%s)",
                route.from_name, self._arrow(route), route.to_name, date,
                best.platform, best.price,
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
            cheapest_p.price, cheapest_p.platform,
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

            if bp.price > route.alert_threshold:
                if self.storage:
                    self.storage.clear_alert_state(route_key)
                continue

            last = self.storage.get_alert_state(route_key) if self.storage else None
            should_push, reason = self._should_push(bp.price, last)

            self.logger.warning(
                "[低价] %s%s%s %s ¥%.0f%s (阈值¥%.0f, 上次推送¥%s) -> %s",
                route.from_name, self._arrow(route), route.to_name, date,
                bp.price,
                f" {bp.flight_no}" if bp.flight_no else "",
                route.alert_threshold,
                f"{last:.0f}" if last else "-",
                "推送" if should_push else f"跳过({reason})",
            )

            if should_push and self.notifier:
                ok = self._push(route, date, bp, last)
                if ok and self.storage:
                    self.storage.set_alert_state(route_key, bp.price)

    def _should_push(self, cur: float, last: Optional[float]):
        if last is None:
            return True, "首次"
        diff = cur - last
        if diff <= -self.push_drop_min:
            return True, f"降¥{-diff:.0f}"
        if diff >= self.push_rise_min:
            return True, f"涨¥{diff:.0f}"
        return False, f"波动¥{diff:+.0f}(<阈值)"

    def _compact_date(self, date: str) -> str:
        parts = []
        for d in (date or "").split("/"):
            d = d.strip()
            parts.append(d[5:] if len(d) >= 10 else d)
        return "/".join(p for p in parts if p)

    def _push_title(self, route: Route, date: str, price: float,
                    last: Optional[float]) -> str:
        emoji = "✈️"
        if last is not None:
            emoji = "📉" if price <= last else "📈"
        compact = self._compact_date(date)
        core = f"{route.from_name}{self._arrow(route)}{route.to_name}"
        if compact:
            titled = f"{emoji}{core} {compact} ¥{price:.0f}"
        else:
            titled = f"{emoji}{core} ¥{price:.0f}"
        if len(titled) <= 32:
            return titled
        titled = f"{core} ¥{price:.0f}"
        return clip_text(titled, 32)

    def _push(self, route: Route, date: str, p: FlightPrice,
              last: Optional[float]) -> bool:
        diff_txt = ""
        if last is not None:
            diff = p.price - last
            if diff <= 0:
                diff_txt = f"（较上次降 ¥{-diff:.0f}）"
            else:
                diff_txt = f"（较上次涨 ¥{diff:.0f}）"

        title = self._push_title(route, date, p.price, last)
        plan = load_fare_plan(p.extra)
        short = render_plan_short(plan) if plan else ""
        if not short:
            short = clip_text(
                f"{date} ¥{p.price:.0f} 低于阈值¥{route.alert_threshold:.0f}{diff_txt}",
                64,
            )

        view_url = self._build_view_url(route, date, p.platform)
        flight_line = ""
        if p.flight_no:
            flight_line = f"- **航班**：{p.flight_no}"
            if p.airline:
                flight_line += f"（{p.airline}）"
            if p.depart_time:
                flight_line += f" {p.depart_time}"
                if p.arrive_time:
                    flight_line += f"→{p.arrive_time}"
            flight_line += "\n"
        if route.flight_nos:
            flight_line += f"- **指定去程**：{' / '.join(route.flight_nos)}\n"
        if route.return_flight_nos:
            flight_line += f"- **指定返程**：{' / '.join(route.return_flight_nos)}\n"
        trip_line = ""
        if route.is_round:
            trip_line = "- **行程**：往返\n"
        desp = (
            f"## 机票低价提醒\n\n"
            f"- **航线**：{route.from_name}（{route.from_code}） "
            f"{self._arrow(route)} "
            f"{route.to_name}（{route.to_code}）\n"
            f"- **日期**：{date}\n"
            f"{trip_line}"
            f"{flight_line}"
            f"- **当前最低价**：**¥{p.price:.0f}**\n"
            f"- **设定阈值**：¥{route.alert_threshold:.0f}\n"
            f"- **来源**：{p.platform}\n"
            f"- **抓取时间**：{p.fetched_at}\n"
        )
        if last is not None:
            desp += f"- **上次推送价**：¥{last:.0f}{diff_txt}\n"
        if plan:
            desp += "\n" + render_plan_desp(plan)
        desp += f"\n[👉 在 {p.platform} 查看详情]({view_url})\n"
        return self.notifier.send(title, desp, short=short)

    def _build_view_url(self, route: Route, date: str, platform: str) -> str:
        if platform == "ctrip":
            return (
                "https://m.ctrip.com/html5/flight/taro/first?from=inner"
                "&tripType=ONE_WAY"
                f"&dcity={route.from_code}&acity={route.to_code}&ddate={date}"
            )
        if platform == "tongcheng":
            return (
                "https://m.ly.com/ft/touch/book1"
                f"?date={date}&an=1&cn=0&baby=0"
                f"&fromcitycode={route.from_code}&fromCode={route.from_code}"
                f"&tocitycode={route.to_code}&toCode={route.to_code}"
                "&cabin=0&platcode=518&frompage=HOME"
            )
        if platform == "qunar":
            inter_codes = {"HKG", "MAC", "MFM", "TPE", "TSA", "KHH"}
            inter_names = {"香港", "澳门", "台北", "高雄", "台中", "台南"}
            codes = {route.from_code.upper(), route.to_code.upper()}
            names = {route.from_name, route.to_name}
            page = "interlist" if (codes & inter_codes or names & inter_names) else "flightlist"
            go = date.split("/")[0]
            back = date.split("/")[1] if "/" in date else (route.return_dates[0] if route.return_dates else "")
            if route.is_round and back:
                return (
                    f"https://touch.qunar.com/ncs/page/{page}"
                    f"?depCity={route.from_name}&arrCity={route.to_name}"
                    f"&goDate={go}&backDate={back}"
                    f"&flightType=roundWay&from=touch_index_search"
                    "&child=0&baby=0&cabinType=0&isTotal=true"
                )
            return (
                f"https://touch.qunar.com/ncs/page/{page}"
                f"?depCity={route.from_name}&arrCity={route.to_name}"
                f"&goDate={go}&from=touch_index_search"
                "&child=0&baby=0&cabinType=0"
            )
        if platform == "tuniu":
            return (
                "https://m.tuniu.com/flight/domestic/new/"
                f"{route.from_code}_{route.to_code}_OW_1_0_0"
                f"?deptDate={date}&isGo=0"
            )
        # fliggy 默认走飞猪 H5
        return (
            "https://outfliggys.m.taobao.com/app/trip/rx-flight-eco/pages/listing"
            f"?depCityCode={route.from_code}&arrCityCode={route.to_code}"
            f"&leaveDate={date}&adultPassengerNum=1&searchType=1"
        )
