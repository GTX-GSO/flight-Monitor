"""报价详情 Markdown（与微信推送正文一致，供 GUI 展示）。"""
from typing import Optional

from .models import FlightPrice, Route
from .fareplan import clip_text, load_fare_plan, render_plan_desp
from .platforms import platform_label
from .crossplan import render_cross_plan_desp


def compact_date(date: str) -> str:
    parts = []
    for d in (date or "").split("/"):
        d = d.strip()
        parts.append(d[5:] if len(d) >= 10 else d)
    return "/".join(p for p in parts if p)


def route_arrow(route: Route) -> str:
    return "⇄" if route.is_round else "->"


def build_view_url(route: Route, date: str, platform: str) -> str:
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
    return (
        "https://outfliggys.m.taobao.com/app/trip/rx-flight-eco/pages/listing"
        f"?depCityCode={route.from_code}&arrCityCode={route.to_code}"
        f"&leaveDate={date}&adultPassengerNum=1&searchType=1"
    )


def quote_title(route: Route, date: str, price: float,
                last: Optional[float] = None) -> str:
    emoji = "✈️"
    if last is not None:
        emoji = "📉" if price <= last else "📈"
    compact = compact_date(date)
    core = f"{route.from_name}{route_arrow(route)}{route.to_name}"
    if compact:
        titled = f"{emoji}{core} {compact} ¥{price:.0f}"
    else:
        titled = f"{emoji}{core} ¥{price:.0f}"
    if len(titled) <= 32:
        return titled
    return clip_text(f"{core} ¥{price:.0f}", 32)


def render_quote_markdown(
    route: Route,
    p: FlightPrice,
    *,
    cross_plan=None,
    effective_price: Optional[float] = None,
    last_price: Optional[float] = None,
    include_threshold: bool = True,
) -> str:
    """生成与推送 desp 一致的 Markdown 正文。"""
    price = float(effective_price if effective_price is not None else p.price)
    date = p.date_label()
    diff_txt = ""
    if last_price is not None:
        diff = price - last_price
        if diff <= 0:
            diff_txt = f"（较上次降 ¥{-diff:.0f}）"
        else:
            diff_txt = f"（较上次涨 ¥{diff:.0f}）"

    plan = load_fare_plan(p.extra)
    flight_line = ""
    if p.flight_no and not cross_plan:
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
    trip_line = "- **行程**：往返\n" if route.is_round else ""
    source = platform_label(p.platform)
    if cross_plan:
        source = "跨平台拆段（见下方拆解）"
    desp = (
        f"## 机票低价提醒\n\n"
        f"- **航线**：{route.from_name}（{route.from_code}） "
        f"{route_arrow(route)} "
        f"{route.to_name}（{route.to_code}）\n"
        f"- **日期**：{date}\n"
        f"{trip_line}"
        f"{flight_line}"
        f"- **当前最低价**：**¥{price:.0f}**\n"
    )
    if include_threshold and route.alert_threshold and route.alert_threshold > 0:
        desp += f"- **设定阈值**：¥{route.alert_threshold:.0f}\n"
    desp += (
        f"- **来源**：{source}\n"
        f"- **抓取时间**：{p.fetched_at}\n"
    )
    if last_price is not None:
        desp += f"- **上次推送价**：¥{last_price:.0f}{diff_txt}\n"
    if cross_plan:
        desp += "\n" + render_cross_plan_desp(cross_plan)
    elif plan:
        desp += "\n" + render_plan_desp(plan)
    if not cross_plan:
        desp += f"\n[👉 在 {platform_label(p.platform)} 查看详情]({build_view_url(route, date, p.platform)})\n"
    return desp
