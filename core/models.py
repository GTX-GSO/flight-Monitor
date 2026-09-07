"""数据模型"""
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


@dataclass
class FlightPrice:
    platform: str           # ctrip / fliggy / tongcheng / qunar
    from_city: str          # 出发地（三字码）
    to_city: str            # 目的地（三字码）
    depart_date: str        # YYYY-MM-DD 去程
    price: float            # 最低价（往返为套票总价）
    airline: str = ""       # 航司
    flight_no: str = ""     # 航班号（往返为 去程|返程）
    depart_time: str = ""   # HH:MM
    arrive_time: str = ""   # HH:MM
    fetched_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    extra: str = ""         # 备用字段（JSON 字符串等）
    return_date: str = ""   # 返程日期，空表示单程

    def to_dict(self) -> dict:
        return asdict(self)

    def date_label(self) -> str:
        if self.return_date:
            return f"{self.depart_date}/{self.return_date}"
        return self.depart_date


@dataclass
class Route:
    from_code: str
    from_name: str
    to_code: str
    to_name: str
    dates: list
    alert_threshold: float = 0
    flight_nos: list = field(default_factory=list)  # 空=整条航线；否则只监控这些航班
    trip: str = "one_way"  # one_way | round
    return_dates: list = field(default_factory=list)
    return_flight_nos: list = field(default_factory=list)

    @property
    def is_round(self) -> bool:
        return (self.trip or "one_way") == "round"

    def date_pairs(self):
        """[(去程日期, 返程日期)]，单程返程日期为空字符串。"""
        goes = list(self.dates or [])
        if not self.is_round:
            return [(d, "") for d in goes]
        backs = list(self.return_dates or [])
        if not goes or not backs:
            return []
        if len(goes) == len(backs):
            return list(zip(goes, backs))
        return [(g, b) for g in goes for b in backs]
