"""机票价格监控 主入口

用法:
    python main.py                # 按 config.yaml 配置启动定时监控
    python main.py --once         # 立即跑一次后退出
    python main.py -c other.yaml  # 指定配置文件
"""
import argparse
import sys
from pathlib import Path

import yaml

from core.logger import setup_logger
from core.models import Route
from core.storage import PriceStorage
from core.alerter import Alerter
from core.scheduler import run_scheduler
from core.notifier import build_notifier
from crawlers import REGISTRY


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _route_flight_nos(r: dict) -> list:
    raw = r.get("flight_nos", r.get("flight_no"))
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    return [str(x).strip() for x in raw if str(x).strip()]


def _as_date_list(raw) -> list:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    return [str(x).strip() for x in raw if str(x).strip()]


def _parse_trip(r: dict) -> str:
    t = str(r.get("trip", r.get("trip_type", "one_way")) or "one_way").strip().lower()
    if t in ("round", "roundtrip", "round_way", "roundway", "rt", "往返"):
        return "round"
    return "one_way"


def build_routes(cfg: dict):
    routes = []
    for r in cfg.get("routes", []):
        trip = _parse_trip(r)
        return_dates = _as_date_list(r.get("return_dates", r.get("return_date")))
        routes.append(Route(
            from_code=str(r["from"]).strip().upper(),
            from_name=str(r.get("from_name", r["from"])).strip(),
            to_code=str(r["to"]).strip().upper(),
            to_name=str(r.get("to_name", r["to"])).strip(),
            dates=_as_date_list(r.get("dates")),
            alert_threshold=float(r.get("alert_threshold", 0) or 0),
            flight_nos=_route_flight_nos(r),
            trip=trip,
            return_dates=return_dates,
            return_flight_nos=_route_flight_nos({
                "flight_nos": r.get("return_flight_nos", r.get("return_flight_no")),
            }),
        ))
    return routes


def make_job(cfg: dict, logger, storage: PriceStorage, alerter: Alerter):
    crawler_cfg = cfg.get("crawler", {})
    platforms = cfg.get("platforms", ["ctrip", "fliggy", "tongcheng"])
    routes = build_routes(cfg)

    crawlers = []
    for name in platforms:
        cls = REGISTRY.get(name)
        if cls is None:
            logger.warning("未知平台: %s，已跳过", name)
            continue
        crawlers.append(cls(crawler_cfg, logger))

    def job():
        logger.info("===== 开始一轮抓取 =====")
        for route in routes:
            pairs = route.date_pairs()
            if route.is_round and not pairs:
                logger.warning("往返航线 %s->%s 缺少 dates/return_dates，已跳过",
                               route.from_code, route.to_code)
                continue
            all_prices = []
            for go, back in pairs:
                for c in crawlers:
                    prices = c.safe_fetch(
                        route.from_code, route.to_code, [go],
                        from_name=route.from_name, to_name=route.to_name,
                        flight_nos=route.flight_nos,
                        return_date=back or None,
                        return_flight_nos=route.return_flight_nos,
                        trip=route.trip,
                    )
                    storage.save_many(prices)
                    all_prices.extend(prices)
            alerter.check_and_alert(route, all_prices)
        logger.info("===== 本轮抓取结束 =====")

    return job


def main():
    ap = argparse.ArgumentParser(description="机票价格监控工具")
    ap.add_argument("-c", "--config", default="config.yaml", help="配置文件路径")
    ap.add_argument("--once", action="store_true", help="只运行一次后退出")
    ap.add_argument("--login", metavar="PLATFORM",
                    help="登录指定平台(ctrip/fliggy/tongcheng)，弹出可见浏览器，登录完成后回车保存会话")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"配置文件不存在: {cfg_path}", file=sys.stderr)
        sys.exit(1)
    cfg = load_config(str(cfg_path))

    out = cfg.get("output", {})
    logger = setup_logger(out.get("log_path", "logs/monitor.log"))
    storage = PriceStorage(out.get("db_path", "data/prices.db"))
    notifier = build_notifier(cfg.get("notifier"), logger)
    if notifier:
        logger.info("已启用推送: %s", type(notifier).__name__)
    notify_cfg = cfg.get("notifier") or {}
    alerter = Alerter(
        logger,
        notifier=notifier,
        storage=storage,
        push_drop_min=float(notify_cfg.get("push_drop_min", 30)),
        push_rise_min=float(notify_cfg.get("push_rise_min", 50)),
    )

    # ---- 登录模式 ----
    if args.login:
        name = args.login.strip().lower()
        cls = REGISTRY.get(name)
        if cls is None:
            print(f"未知平台: {name}，可选: {list(REGISTRY)}", file=sys.stderr)
            sys.exit(2)
        crawler = cls(cfg.get("crawler", {}), logger)
        crawler.interactive_login()
        return

    job = make_job(cfg, logger, storage, alerter)

    if args.once:
        job()
        return

    sched_cfg = cfg.get("schedule", {})
    interval = int(sched_cfg.get("interval_minutes", 30))
    jitter = int(sched_cfg.get("jitter_minutes", 0))
    run_on_start = bool(sched_cfg.get("run_on_start", True))
    logger.info("启动定时调度，每 %d 分钟一次 (±%d 分钟随机扰动, 首次立即运行=%s)",
                interval, jitter, run_on_start)
    try:
        run_scheduler(job, interval, run_on_start, jitter_minutes=jitter)
    except (KeyboardInterrupt, SystemExit):
        logger.info("收到退出信号，停止监控")


if __name__ == "__main__":
    main()
