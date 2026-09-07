"""SQLite 价格存储"""
import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import List, Optional

from .models import FlightPrice


SCHEMA = """
CREATE TABLE IF NOT EXISTS flight_prices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    platform    TEXT NOT NULL,
    from_city   TEXT NOT NULL,
    to_city     TEXT NOT NULL,
    depart_date TEXT NOT NULL,
    price       REAL NOT NULL,
    airline     TEXT,
    flight_no   TEXT,
    depart_time TEXT,
    arrive_time TEXT,
    fetched_at  TEXT NOT NULL,
    extra       TEXT
);
CREATE INDEX IF NOT EXISTS idx_route_date
    ON flight_prices (from_city, to_city, depart_date, platform);
CREATE INDEX IF NOT EXISTS idx_fetched_at
    ON flight_prices (fetched_at);

CREATE TABLE IF NOT EXISTS alert_state (
    route_key   TEXT PRIMARY KEY,   -- from-to-date 组合
    last_price  REAL NOT NULL,
    last_sent_at TEXT NOT NULL
);
"""


class PriceStorage:
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_schema()

    @contextmanager
    def _conn(self):
        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=30000")
                yield conn
                conn.commit()
            finally:
                conn.close()

    def _init_schema(self):
        with self._conn() as c:
            c.executescript(SCHEMA)

    def save(self, fp: FlightPrice):
        with self._conn() as c:
            c.execute(
                """INSERT INTO flight_prices
                (platform, from_city, to_city, depart_date, price,
                 airline, flight_no, depart_time, arrive_time, fetched_at, extra)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (fp.platform, fp.from_city, fp.to_city, fp.depart_date, fp.price,
                 fp.airline, fp.flight_no, fp.depart_time, fp.arrive_time,
                 fp.fetched_at, fp.extra),
            )

    def save_many(self, prices: List[FlightPrice]):
        for p in prices:
            self.save(p)

    def list_recent(self, limit: int = 200, from_city: str = "",
                    to_city: str = "") -> List[sqlite3.Row]:
        sql = "SELECT * FROM flight_prices WHERE 1=1"
        args: list = []
        if from_city:
            sql += " AND from_city=?"
            args.append(from_city.strip().upper())
        if to_city:
            sql += " AND to_city=?"
            args.append(to_city.strip().upper())
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        with self._conn() as c:
            return list(c.execute(sql, args).fetchall())

    def list_latest_snapshot(self, limit: int = 50) -> List[sqlite3.Row]:
        """每个 平台+航线+日期 的最近一条。"""
        sql = """
        SELECT fp.* FROM flight_prices fp
        INNER JOIN (
            SELECT platform, from_city, to_city, depart_date, MAX(id) AS max_id
            FROM flight_prices
            GROUP BY platform, from_city, to_city, depart_date
        ) t ON fp.id = t.max_id
        ORDER BY fp.fetched_at DESC, fp.id DESC
        LIMIT ?
        """
        with self._conn() as c:
            return list(c.execute(sql, (int(limit),)).fetchall())

    def last_lowest(self, from_city: str, to_city: str, depart_date: str,
                    platform: Optional[str] = None) -> Optional[sqlite3.Row]:
        sql = ("SELECT * FROM flight_prices WHERE from_city=? AND to_city=? "
               "AND depart_date=?")
        args = [from_city, to_city, depart_date]
        if platform:
            sql += " AND platform=?"
            args.append(platform)
        sql += " ORDER BY price ASC LIMIT 1"
        with self._conn() as c:
            return c.execute(sql, args).fetchone()

    # ---- alert_state: 记录上次已推送价格，用于去抖 ----
    def get_alert_state(self, route_key: str) -> Optional[float]:
        with self._conn() as c:
            row = c.execute(
                "SELECT last_price FROM alert_state WHERE route_key=?",
                (route_key,),
            ).fetchone()
            return float(row["last_price"]) if row else None

    def set_alert_state(self, route_key: str, price: float):
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._conn() as c:
            c.execute(
                "INSERT INTO alert_state (route_key, last_price, last_sent_at) "
                "VALUES (?,?,?) ON CONFLICT(route_key) DO UPDATE SET "
                "last_price=excluded.last_price, last_sent_at=excluded.last_sent_at",
                (route_key, price, now),
            )

    def clear_prices(self):
        """清空全部历史报价和推送去抖状态。"""
        with self._conn() as c:
            c.execute("DELETE FROM flight_prices")
            c.execute("DELETE FROM alert_state")
