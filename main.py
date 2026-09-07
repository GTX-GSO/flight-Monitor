"""机票价格监控 主入口

用法:
    python main.py                # 按 config.yaml 配置启动定时监控
    python main.py --once         # 立即跑一次后退出
    python main.py --gui          # 打开图形界面
    python gui.py                 # 同上
    python main.py -c other.yaml  # 指定配置文件
"""
import argparse
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

from core.logger import setup_logger
from core.models import Route
from core.storage import PriceStorage
from core.alerter import Alerter
from core.scheduler import run_scheduler
from core.notifier import build_notifier
from core.airport_lookup import city_label
from crawlers import REGISTRY


class AppRuntime:
    def __init__(self, cfg: dict, logger, storage: PriceStorage, alerter: Alerter, job,
                 config_path: str = "config.yaml"):
        self.cfg = cfg
        self.logger = logger
        self.storage = storage
        self.alerter = alerter
        self.job = job
        self.config_path = config_path


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def dump_config(path: str, cfg: dict):
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            cfg, f, allow_unicode=True, sort_keys=False, default_flow_style=False,
        )


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


def _parse_monitor_mode(r: dict) -> str:
    raw = str(r.get("monitor_mode", r.get("mode", "lowest")) or "lowest").strip().lower()
    if raw in ("fare_plan", "fareplan", "plan", "买法", "指定航程", "specified", "itinerary"):
        return "fare_plan"
    return "lowest"


def _route_display_name(code: str, raw_name: str) -> str:
    """配置里若只填三字码，自动换成中文城市名（如 HKG → 香港）。"""
    name = str(raw_name or "").strip()
    c = str(code or "").strip().upper()
    if name and not re.fullmatch(r"[A-Z]{3}", name):
        return name
    label = city_label(c)
    return label if label and label != c else (name or c)


def build_routes(cfg: dict):
    routes = []
    for r in cfg.get("routes", []):
        trip = _parse_trip(r)
        return_dates = _as_date_list(r.get("return_dates", r.get("return_date")))
        from_code = str(r["from"]).strip().upper()
        to_code = str(r["to"]).strip().upper()
        routes.append(Route(
            from_code=from_code,
            from_name=_route_display_name(from_code, r.get("from_name", r.get("from"))),
            to_code=to_code,
            to_name=_route_display_name(to_code, r.get("to_name", r.get("to"))),
            dates=_as_date_list(r.get("dates")),
            alert_threshold=float(r.get("alert_threshold", 0) or 0),
            flight_nos=_route_flight_nos(r),
            trip=trip,
            return_dates=return_dates,
            return_flight_nos=_route_flight_nos({
                "flight_nos": r.get("return_flight_nos", r.get("return_flight_no")),
            }),
            monitor_mode=_parse_monitor_mode(r),
        ))
    return routes


def make_job(config_path: str, logger, storage: PriceStorage):
    """每次执行都从配置文件重建航线/平台/提醒器，避免 GUI 改配置后仍用旧快照。"""

    def job():
        cfg = load_config(config_path)
        notify_cfg = cfg.get("notifier") or {}
        notifier = build_notifier(cfg.get("notifier"), logger)
        alerter = Alerter(
            logger,
            notifier=notifier,
            storage=storage,
            push_drop_min=float(notify_cfg.get("push_drop_min", 30)),
            push_rise_min=float(notify_cfg.get("push_rise_min", 50)),
        )
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

        logger.info("===== 开始一轮抓取 =====")
        workers = max(1, len(crawlers))
        for route in routes:
            pairs = route.date_pairs()
            if route.is_round and not pairs:
                logger.warning("往返航线 %s->%s 缺少 dates/return_dates，已跳过",
                               route.from_code, route.to_code)
                continue
            all_prices = []
            for go, back in pairs:
                logger.info("并行抓取 %d 个平台 %s->%s %s%s",
                            workers, route.from_code, route.to_code, go,
                            f"/{back}" if back else "")

                def _fetch(c, go=go, back=back, route=route):
                    return c.safe_fetch(
                        route.from_code, route.to_code, [go],
                        from_name=route.from_name, to_name=route.to_name,
                        flight_nos=route.flight_nos,
                        return_date=back or None,
                        return_flight_nos=route.return_flight_nos,
                        trip=route.trip,
                        monitor_mode=route.monitor_mode,
                    )

                sequential = bool(crawler_cfg.get("sequential_platforms", False))
                if sequential:
                    for c in crawlers:
                        try:
                            prices = _fetch(c) or []
                        except Exception as e:
                            logger.exception("[%s] 抓取线程异常: %s", c.name, e)
                            prices = []
                        storage.save_many(prices)
                        all_prices.extend(prices)
                else:
                    with ThreadPoolExecutor(max_workers=workers) as pool:
                        futs = {pool.submit(_fetch, c): c for c in crawlers}
                        for fut in as_completed(futs):
                            c = futs[fut]
                            try:
                                prices = fut.result() or []
                            except Exception as e:
                                logger.exception("[%s] 抓取线程异常: %s", c.name, e)
                                prices = []
                            storage.save_many(prices)
                            all_prices.extend(prices)
            alerter.check_and_alert(route, all_prices)
        logger.info("===== 本轮抓取结束 =====")

    return job


def build_runtime(cfg: dict, config_path: str = "config.yaml") -> AppRuntime:
    config_path = str(config_path)
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
    job = make_job(config_path, logger, storage)
    return AppRuntime(cfg, logger, storage, alerter, job, config_path=config_path)


def main():
    ap = argparse.ArgumentParser(description="机票价格监控工具")
    ap.add_argument("-c", "--config", default="config.yaml", help="配置文件路径")
    ap.add_argument("--once", action="store_true", help="只运行一次后退出")
    ap.add_argument("--gui", action="store_true", help="打开图形界面")
    ap.add_argument("--login", metavar="PLATFORM",
                    help="登录指定平台(ctrip/fliggy/tongcheng/qunar)，弹出可见浏览器，登录完成后回车保存会话")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"配置文件不存在: {cfg_path}", file=sys.stderr)
        sys.exit(1)

    if args.gui:
        from ui.app import run_gui
        run_gui(str(cfg_path))
        return

    cfg = load_config(str(cfg_path))
    runtime = build_runtime(cfg, str(cfg_path))
    logger, storage, alerter = runtime.logger, runtime.storage, runtime.alerter

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

    job = runtime.job

    if args.once:
        job()
        return

    sched_cfg = cfg.get("schedule", {})
    interval = int(sched_cfg.get("interval_minutes", 60))
    jitter = int(sched_cfg.get("jitter_minutes", 15))
    run_on_start = bool(sched_cfg.get("run_on_start", True))
    logger.info("启动定时调度，每 %d 分钟一次 (±%d 分钟随机扰动, 首次立即运行=%s)",
                interval, jitter, run_on_start)
    try:
        run_scheduler(job, interval, run_on_start, jitter_minutes=jitter)
    except (KeyboardInterrupt, SystemExit):
        logger.info("收到退出信号，停止监控")


if __name__ == "__main__":
    main()
