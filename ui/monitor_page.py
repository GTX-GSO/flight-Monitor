"""价格监控首页：当前航线看板 + 开始盯价 / 立即查一次。"""
import json
import tkinter as tk
from tkinter import ttk

from core.models import FlightPrice, Route
from core.crossplan import merge_cross_platform_legs
from core.platforms import platform_label
from core.airport_lookup import names_from_routes, route_label
from main import build_routes
from . import dpi
from . import theme as t
from .widgets import EmptyState, RoutePill, tree
from .settings_page import push_status
from .quote_dialog import show_quote_detail

class MonitorPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=t.BG)
        self.app = app

        inner = tk.Frame(self, bg=t.BG)
        inner.pack(fill="both", expand=True, padx=dpi.px(24), pady=dpi.px(20))

        self.hero = t.card(inner, accent=True)
        self.hero.pack(fill="x", pady=(0, dpi.px(16)))
        pad = tk.Frame(self.hero, bg=t.WHITE)
        pad.pack(fill="x", padx=dpi.px(20), pady=dpi.px(18))

        top = tk.Frame(pad, bg=t.WHITE)
        top.pack(fill="x")
        title_col = tk.Frame(top, bg=t.WHITE)
        title_col.pack(side="left", fill="x", expand=True)
        tk.Label(title_col, text="当前盯的航线", bg=t.WHITE, fg=t.TEXT, font=t.FONT_BOLD).pack(anchor="w")
        self.status = tk.Label(
            title_col, text="未开始",
            bg=t.WHITE, fg=t.MUTED, font=t.FONT_SMALL,
            wraplength=dpi.px(720), justify="left",
        )
        self.status.pack(anchor="w", pady=(dpi.px(2), 0))

        self.push_chip = tk.Frame(top, bg=t.WHITE, cursor="hand2")
        self.push_chip.pack(side="right", padx=(dpi.px(12), 0))
        self._push_box = tk.Frame(
            self.push_chip, bg=t.CHIP_OFF_BG, highlightbackground=t.BORDER, highlightthickness=1, bd=0,
        )
        self._push_box.pack()
        push_inner = tk.Frame(self._push_box, bg=t.CHIP_OFF_BG)
        push_inner.pack(padx=dpi.px(12), pady=dpi.px(8))
        self._push_title = tk.Label(
            push_inner, text="微信推送", bg=t.CHIP_OFF_BG, fg=t.MUTED, font=t.FONT_SMALL, anchor="w",
        )
        self._push_title.pack(anchor="w")
        self._push_state = tk.Label(
            push_inner, text="未开启", bg=t.CHIP_OFF_BG, fg=t.TEXT, font=t.FONT_BOLD, anchor="w",
        )
        self._push_state.pack(anchor="w")
        self._push_go = tk.Label(
            push_inner, text="去设置 →", bg=t.CHIP_OFF_BG, fg=t.BLUE, font=t.FONT_SMALL, anchor="w",
        )
        self._push_go.pack(anchor="w", pady=(dpi.px(2), 0))
        self._push_inner = push_inner
        for w in (self.push_chip, self._push_box, push_inner, self._push_title, self._push_state, self._push_go):
            w.bind("<Button-1>", lambda _e: self.app.open_push_settings())

        self.route_box = tk.Frame(pad, bg=t.WHITE)
        self.route_box.pack(fill="x", pady=(dpi.px(12), dpi.px(14)))

        btns = tk.Frame(pad, bg=t.WHITE)
        btns.pack(fill="x")
        self.btn_toggle = t.primary_button(btns, "开始后台盯价", self._toggle_schedule, width=14)
        self.btn_toggle.pack(side="left", padx=(0, dpi.px(10)))
        self.btn_once = t.secondary_button(btns, "立即查一次", app.run_once, width=12)
        self.btn_once.pack(side="left", padx=(0, dpi.px(10)))
        t.ghost_button(btns, "去改航线 →", lambda: app.show_tab("routes"), width=10).pack(side="left")

        self.progress = ttk.Progressbar(pad, mode="indeterminate")
        self._progress_packed = False

        table_card = t.card(inner)
        table_card.pack(fill="both", expand=True)
        head = tk.Frame(table_card, bg=t.WHITE)
        head.pack(fill="x", padx=dpi.px(18), pady=(dpi.px(14), dpi.px(6)))
        title_row = tk.Frame(head, bg=t.WHITE)
        title_row.pack(fill="x")
        tk.Label(title_row, text="最新报价", bg=t.WHITE, fg=t.TEXT, font=t.FONT_BOLD).pack(side="left")
        t.ghost_button(title_row, "刷新", self.refresh, width=6).pack(side="right")
        tk.Label(
            head, text="每条航线显示各平台最低价，双击查看推送同款详情",
            bg=t.WHITE, fg=t.MUTED, font=t.FONT_SMALL,
        ).pack(anchor="w", pady=(dpi.px(2), 0))
        self.cross_hint = tk.Label(head, text="", bg=t.WHITE, fg=t.BLUE, font=t.FONT_SMALL, anchor="w")
        self.cross_hint.pack(anchor="w", pady=(dpi.px(4), 0))

        body = tk.Frame(table_card, bg=t.WHITE)
        body.pack(fill="both", expand=True, padx=dpi.px(18), pady=(0, dpi.px(14)))
        cols = {
            "platform": ("平台", 90),
            "route": ("航线", 180),
            "date": ("日期", 150),
            "price": ("价格", 80),
            "flight": ("航班", 200),
            "airline": ("航司", 80),
            "fetched": ("查价时间", 170),
        }
        self.table_wrap, self.tree = tree(body, cols)
        self.empty = EmptyState(body, "还没有报价", "点「立即查一次」或开始盯价。", icon="◎")
        self.table_wrap.pack(fill="both", expand=True)
        self._quote_rows: dict = {}
        self.tree.bind("<Double-1>", self._open_quote_detail)
        self.refresh_routes()
        self.refresh_push()

    def refresh_routes(self):
        self.refresh_push()
        for w in self.route_box.winfo_children():
            w.destroy()
        routes = self.app.runtime.cfg.get("routes") or []
        if not routes:
            tk.Label(
                self.route_box, text="还没设置航线。先到左侧「航线」里加上出发地、目的地和日期。",
                bg=t.WHITE, fg=t.MUTED, font=t.FONT, wraplength=dpi.px(760), justify="left",
            ).pack(anchor="w")
            return
        plats = self.app.runtime.cfg.get("platforms") or []
        plat_txt = "、".join(t.PLATFORM_NAMES.get(p, p) for p in plats) or "未选平台"
        for r in routes:
            trip = str(r.get("trip", "")).lower()
            is_round = trip in ("round", "往返")
            arrow = "⇄" if is_round else "→"
            name = f"{r.get('from_name') or r.get('from')} {arrow} {r.get('to_name') or r.get('to')}"
            dates = r.get("dates") or []
            backs = r.get("return_dates") or []
            if is_round and dates and backs:
                when = f"{dates[0]} 去 / {backs[0]} 回"
            elif dates:
                when = "、".join(str(x) for x in dates[:3])
            else:
                when = "未填日期"
            thr = float(r.get("alert_threshold") or 0)
            thr_txt = f"低于 ¥{thr:.0f} 提醒" if thr else "未设低价提醒"
            mode = "指定航班" if str(r.get("monitor_mode", "lowest")) == "fare_plan" else "最低价"
            fns = r.get("flight_nos") or []
            extra = f" · {fns[0]}" if fns and mode == "指定航班" else ""
            RoutePill(
                self.route_box,
                route_text=name,
                meta_text=f"{when}  ·  {mode}  ·  {thr_txt}{extra}",
                platforms=plat_txt,
            ).pack(fill="x")

    def refresh_push(self):
        text, kind = push_status(self.app.runtime.cfg)
        if kind == "ok":
            bg, fg, border = t.CHIP_OK_BG, t.OK, t.CHIP_OK_BG
        elif kind == "warn":
            bg, fg, border = t.CHIP_WARN_BG, t.WARN, t.CHIP_WARN_BG
        else:
            bg, fg, border = t.CHIP_OFF_BG, t.MUTED, t.BORDER
        self._push_box.configure(bg=bg, highlightbackground=border)
        self._push_inner.configure(bg=bg)
        self._push_title.configure(bg=bg, fg=t.MUTED)
        self._push_state.configure(bg=bg, text=text, fg=fg)
        self._push_go.configure(bg=bg, fg=t.BLUE)

    def _toggle_schedule(self):
        if self.app._sched is not None:
            self.app.stop_schedule()
        else:
            self.app.start_schedule()

    def sync_controls(self, busy: bool, running: bool):
        if running:
            self.btn_toggle.configure(text="停止盯价")
        else:
            self.btn_toggle.configure(text="开始后台盯价")
        state = "disabled" if busy else "normal"
        self.btn_once.configure(state=state)
        self.btn_toggle.configure(state=state)
        if busy:
            self.btn_once.configure(bg=t.BG_ALT, fg=t.MUTED)
        else:
            self.btn_once.configure(bg=t.SURFACE, fg=t.BLUE)

    def set_busy(self, busy: bool, text: str = ""):
        if busy:
            if not self._progress_packed:
                self.progress.pack(fill="x", pady=(dpi.px(12), 0))
                self._progress_packed = True
            self.progress.start(12)
            self.status.configure(text=text or "正在查价…", fg=t.WARN)
        else:
            self.progress.stop()
            if self._progress_packed:
                self.progress.pack_forget()
                self._progress_packed = False
            self.status.configure(text=text or "未开始", fg=t.MUTED)
        self.sync_controls(busy, self.app._sched is not None)

    def set_schedule_on(self, on: bool, extra: str = ""):
        if extra:
            self.status.configure(text=extra, fg=t.OK if on else t.MUTED)
        self.sync_controls(self.app._busy, on)

    def refresh(self):
        self.refresh_routes()
        self._quote_rows.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)
        try:
            rows = self.app.runtime.storage.list_latest_snapshot(80)
        except Exception:
            rows = []
        if not rows:
            self.table_wrap.pack_forget()
            self.empty.pack(fill="both", expand=True)
            self.cross_hint.configure(text="")
            return
        self.empty.pack_forget()
        self.table_wrap.pack(fill="both", expand=True)
        extra = names_from_routes(self.app.runtime.cfg.get("routes") or [])
        by_key: dict = {}
        for r in rows:
            key = (r["from_city"], r["to_city"], r["depart_date"])
            by_key.setdefault(key, []).append(r)

        hints = []
        for (fc, tc, _dt), raw_rows in sorted(by_key.items()):
            fps = [self._row_to_fp(r) for r in raw_rows]
            eff_price, best, cross = self._pick_best(fps)
            if best is None:
                continue
            route = self._route_for(fc, tc, extra)
            plat_txt = self._platform_label(fps, best, cross)
            date_txt = best.date_label()
            route_txt = route_label(fc, tc, extra)
            fetched = max((fp.fetched_at or "") for fp in fps)
            iid = self.tree.insert("", "end", values=(
                plat_txt,
                route_txt,
                date_txt,
                f"¥{eff_price:.0f}",
                best.flight_no or "—",
                best.airline or "—",
                fetched,
            ))
            self._quote_rows[iid] = {
                "route": route,
                "fp": best,
                "cross_plan": cross,
                "effective_price": eff_price,
            }
            if cross and route.is_fare_plan:
                bits = " + ".join(
                    (p.get("display") or p.get("flight_no") or "") + f" ¥{p['price']:.0f}"
                    for p in cross.get("parts") or []
                )
                hints.append(f"{route_txt} {date_txt}  分开买更省 ¥{cross['price']:.0f}（{bits}）")
        if hints:
            self.cross_hint.configure(text=hints[0] if len(hints) == 1 else "；".join(hints[:2]))
        else:
            self.cross_hint.configure(text="")

    def _open_quote_detail(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        meta = self._quote_rows.get(sel[0])
        if not meta:
            return
        last = None
        try:
            route_key = self._alert_route_key(meta["route"], meta["fp"])
            last = self.app.runtime.storage.get_alert_state(route_key)
        except Exception:
            pass
        show_quote_detail(
            self.winfo_toplevel(),
            meta["route"],
            meta["fp"],
            cross_plan=meta.get("cross_plan"),
            effective_price=meta.get("effective_price"),
            last_price=last,
        )

    @staticmethod
    def _row_to_fp(row) -> FlightPrice:
        extra = row["extra"] or ""
        ret = ""
        try:
            obj = json.loads(extra)
            ret = str(obj.get("return_date") or "")
        except Exception:
            pass
        return FlightPrice(
            platform=row["platform"],
            from_city=row["from_city"],
            to_city=row["to_city"],
            depart_date=row["depart_date"],
            price=float(row["price"]),
            airline=row["airline"] or "",
            flight_no=row["flight_no"] or "",
            depart_time=row["depart_time"] or "",
            arrive_time=row["arrive_time"] or "",
            fetched_at=row["fetched_at"] or "",
            extra=extra,
            return_date=ret,
        )

    @staticmethod
    def _pick_best(fps: list):
        if not fps:
            return 0.0, None, None
        ordered = sorted(fps, key=lambda x: (x.price, 0 if (x.airline or "").strip() else 1))
        best = ordered[0]
        cross = None
        if any(MonitorPage._is_fare_plan_fp(p) for p in fps):
            cross = merge_cross_platform_legs(fps)
        eff = best.price
        if cross and cross["price"] < eff:
            eff = float(cross["price"])
        return eff, best, cross

    @staticmethod
    def _platform_label(fps: list, best: FlightPrice, cross) -> str:
        if cross:
            return "跨平台"
        winners = sorted({platform_label(p.platform) for p in fps if p.price == best.price})
        return winners[0] if len(winners) == 1 else "/".join(winners)

    def _route_for(self, from_code: str, to_code: str, extra: dict) -> Route:
        fc, tc = from_code.upper(), to_code.upper()
        for r in build_routes(self.app.runtime.cfg):
            if r.from_code == fc and r.to_code == tc:
                return r
        return Route(
            from_code=fc,
            from_name=extra.get(fc, fc),
            to_code=tc,
            to_name=extra.get(tc, tc),
            dates=[],
        )

    @staticmethod
    def _alert_route_key(route: Route, fp: FlightPrice) -> str:
        date = fp.date_label()
        key = f"{route.from_code}-{route.to_code}-{date}"
        if route.is_round:
            key = f"RT-{key}"
        if route.flight_nos:
            from core.flights import parse_wanted_flight_nos, format_wanted
            key += "-" + format_wanted(parse_wanted_flight_nos(route.flight_nos))
        if route.return_flight_nos:
            from core.flights import parse_wanted_flight_nos, format_wanted
            key += "-R" + format_wanted(parse_wanted_flight_nos(route.return_flight_nos))
        return key

    @staticmethod
    def _is_fare_plan_fp(fp: FlightPrice) -> bool:
        import json
        try:
            obj = json.loads(fp.extra or "{}")
            return obj.get("monitor_mode") == "fare_plan" or bool(obj.get("fare_plan"))
        except Exception:
            return False
