"""往返套票解析与买法逻辑回归（使用 debug dump，无网络）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crawlers.qunar import QunarCrawler
from core.fareplan import build_fare_plan


def test_round_package_times_and_price():
    dumps = sorted(ROOT.joinpath("debug").glob("qunar_raw_browser_*_2026_11_20_2026_11_29.txt"))
    if not dumps:
        return  # CI 无 dump 时跳过
    text = dumps[-1].read_text(encoding="utf-8", errors="ignore")
    pkgs = QunarCrawler._parse_round_packages(text)
    want = [
        o for o in pkgs
        if o.get("flight_no") == "MU9658+MU5931"
        and o.get("return_flight_no") == "MU5940+MU9657"
    ]
    assert want
    o = min(want, key=lambda x: x["price"])
    assert o["price"] == 1932
    assert o["depart_time"] == "21:15"
    assert o["arrive_time"] == "08:05"
    assert o["depart_date"] == "2026-11-20"
    assert o["arrive_date"] == "2026-11-21"
    assert o["return_depart_time"] == "14:50"
    assert o["return_arrive_time"] == "20:15"
    assert o["return_depart_date"] == "2026-11-29"
    assert not any(
        x.get("depart_time") and x.get("return_depart_time") == x.get("depart_time")
        for x in pkgs
    )


def test_fare_plan_distinct_and_reconcile():
    plan = build_fare_plan(
        trip="round",
        round_pkg={"price": 1932, "flight_no": "MU9658+MU5931"},
        outbound_combo={"price": 1307, "flight_no": "MU9658+MU5931"},
        inbound_combo={"price": 1253, "flight_no": "MU5940+MU9657"},
        outbound_leg_quotes=[
            {"flight_no": "MU9658", "price": 1257},
            {"flight_no": "MU5931", "price": 1290},
        ],
        inbound_leg_quotes=[
            {"flight_no": "MU5940", "price": 1350},
            {"flight_no": "MU9657", "price": 1183},
        ],
    )
    prices = [round(o["price"]) for o in plan["options"]]
    assert 1932 in prices
    assert len(set(prices)) >= 3
    assert plan["best"]["price"] == 1932

    c = QunarCrawler.__new__(QunarCrawler)
    c.logger = __import__("logging").getLogger("t")
    assert c._reconcile_combo_price(
        {"price": 1307}, [{"price": 1257}, {"price": 1290}], "x",
    )["price"] == 1307
    assert c._reconcile_combo_price(
        {"price": 500}, [{"price": 1257}, {"price": 1290}], "x",
    )["price"] == 2547


if __name__ == "__main__":
    test_round_package_times_and_price()
    test_fare_plan_distinct_and_reconcile()
    print("OK")
