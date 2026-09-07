"""航线配置：用中文选项，往返时才显示返程。"""
import tkinter as tk
from tkinter import ttk

from . import dpi
from . import theme as t
from .airport_search import AirportAutocomplete
from .date_picker import DateListPicker
from .widgets import OptionCard, ScrollBody


def _join_list(raw) -> str:
    if not raw:
        return ""
    if isinstance(raw, str):
        return raw
    return "\n".join(str(x) for x in raw if str(x).strip())


def _split_lines(text: str) -> list:
    out = []
    for part in (text or "").replace(",", "\n").replace("，", "\n").splitlines():
        s = part.strip()
        if s:
            out.append(s)
    return out


class RoutesPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=t.BG)
        self.app = app
        self._index = None

        inner = tk.Frame(self, bg=t.BG)
        inner.pack(fill="both", expand=True, padx=dpi.px(24), pady=dpi.px(20))

        left = t.card(inner)
        left.pack(side="left", fill="y", padx=(0, dpi.px(12)))
        host = getattr(left, "_round_host", None)
        if host is not None:
            host.configure(width=dpi.px(260))
            host.pack_propagate(False)
        tk.Label(left, text="我的航线", bg=t.WHITE, fg=t.TEXT, font=t.FONT_BOLD).pack(
            anchor="w", padx=dpi.px(14), pady=(dpi.px(12), dpi.px(8)),
        )
        lb_wrap = tk.Frame(
            left, bg=t.SURFACE, highlightbackground=t.BORDER, highlightthickness=1, bd=0,
        )
        lb_wrap.pack(fill="both", expand=True, padx=dpi.px(10), pady=dpi.px(8))
        self.listbox = tk.Listbox(
            lb_wrap, width=28, height=18, font=t.FONT, bd=0, highlightthickness=0,
            selectbackground=t.BLUE, selectforeground=t.WHITE,
            bg=t.SURFACE, fg=t.TEXT, activestyle="none",
        )
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        lb_btns = tk.Frame(left, bg=t.WHITE)
        lb_btns.pack(fill="x", padx=dpi.px(10), pady=(0, dpi.px(12)))
        t.primary_button(lb_btns, "新增航线", self.add_route, width=10).pack(side="left", padx=(0, 6))
        t.danger_button(lb_btns, "删除", self.delete_route, width=8).pack(side="left")

        right = t.card(inner)
        right.pack(side="left", fill="both", expand=True)

        foot = tk.Frame(right, bg=t.SURFACE)
        foot.pack(fill="x", side="bottom")
        tk.Frame(foot, bg=t.BORDER, height=1).pack(fill="x")
        foot_inner = tk.Frame(foot, bg=t.SURFACE)
        foot_inner.pack(fill="x", padx=dpi.px(18), pady=dpi.px(10))
        self.form_error = tk.Label(
            foot_inner, text="", bg=t.SURFACE, fg=t.DANGER, font=t.FONT_SMALL,
            wraplength=dpi.px(520), justify="left", anchor="w",
        )
        self.form_error.pack(side="left", fill="x", expand=True, padx=(0, dpi.px(12)))
        t.primary_button(foot_inner, "保存这条航线", self.save_route, width=14).pack(side="right")

        scroll = ScrollBody(right, bg=t.WHITE)
        scroll.pack(fill="both", expand=True)
        form = tk.Frame(scroll.inner, bg=t.WHITE)
        form.pack(fill="both", expand=True, padx=dpi.px(18), pady=dpi.px(14))

        head = tk.Frame(form, bg=t.WHITE)
        head.pack(fill="x", pady=(0, dpi.px(12)))
        tk.Frame(head, bg=t.BLUE, width=dpi.px(3)).pack(side="left", fill="y", padx=(0, dpi.px(10)))
        tk.Label(head, text="编辑航线", bg=t.WHITE, fg=t.TEXT, font=t.FONT_BOLD).pack(side="left")

        self.vars = {}
        self._section(form, "去哪")
        grid = tk.Frame(form, bg=t.WHITE)
        grid.pack(fill="x")
        airport_fields = [
            ("from_name", "from", "出发地", "如 香港"),
            ("to_name", "to", "到达地", "如 首尔"),
        ]
        self._field_errors = {}
        for i, (name_key, code_key, label, ph) in enumerate(airport_fields):
            cell = tk.Frame(grid, bg=t.WHITE)
            cell.grid(row=0, column=i, sticky="ew", padx=(0, dpi.px(16)), pady=dpi.px(4))
            tk.Label(cell, text=label, bg=t.WHITE, fg=t.TEXT, font=t.FONT).pack(anchor="w")
            self.vars[name_key] = tk.StringVar()
            self.vars[code_key] = tk.StringVar()
            AirportAutocomplete(
                cell,
                name_var=self.vars[name_key],
                code_var=self.vars[code_key],
                placeholder=ph,
            ).pack(fill="x", pady=(dpi.px(2), 0))
            err = tk.Label(cell, text="", bg=t.WHITE, fg=t.DANGER, font=t.FONT_SMALL, anchor="w")
            err.pack(anchor="w")
            self._field_errors[name_key] = err
            self._field_errors[code_key] = err
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        self._section(form, "行程")
        trip_row = tk.Frame(form, bg=t.WHITE)
        trip_row.pack(fill="x", pady=(0, 4))
        self.vars["trip"] = tk.StringVar(value="round")
        radios = tk.Frame(trip_row, bg=t.WHITE)
        radios.pack(anchor="w")
        ttk.Radiobutton(
            radios, text="往返", variable=self.vars["trip"], value="round", command=self._sync_trip,
        ).pack(side="left", padx=(0, 16))
        ttk.Radiobutton(
            radios, text="单程", variable=self.vars["trip"], value="one_way", command=self._sync_trip,
        ).pack(side="left")

        self.go_frame = tk.Frame(form, bg=t.WHITE)
        self.go_frame.pack(fill="x", pady=(8, 0))
        self.texts = {}
        self.date_pickers = {}
        self._field_labels = {}
        self._date_field(
            self.go_frame, "dates", "出发日期", "", multi=False, on_change=self._on_go_dates,
        )

        self.return_frame = tk.Frame(form, bg=t.WHITE)
        self.return_frame.pack(fill="x")
        self._date_field(self.return_frame, "return_dates", "返程日期", "", multi=False)

        self._section(form, "怎么盯")
        self.vars["monitor_mode"] = tk.StringVar(value="lowest")
        self.mode_row = tk.Frame(form, bg=t.WHITE)
        self.mode_row.pack(fill="x")
        mode_cards = tk.Frame(self.mode_row, bg=t.WHITE)
        mode_cards.pack(fill="x")
        self._mode_cards = {}
        self._mode_cards["lowest"] = OptionCard(
            mode_cards, "查最低价", "不管哪班，只要最便宜",
            command=lambda: self._pick_mode("lowest"),
        )
        self._mode_cards["lowest"].pack(side="left", fill="both", expand=True, padx=(0, dpi.px(8)))
        self._mode_cards["fare_plan"] = OptionCard(
            mode_cards, "盯指定航班", "填航班号，比较套票还是分开买更省",
            command=lambda: self._pick_mode("fare_plan"),
        )
        self._mode_cards["fare_plan"].pack(side="left", fill="both", expand=True)

        self.flight_frame = tk.Frame(form, bg=t.WHITE)
        self._text_field(
            self.flight_frame, "flight_nos", "去程航班号", "中转用 + 连接，如 MU9658+MU5931",
            key_hint="flight_nos",
        )
        self.return_flight_frame = tk.Frame(form, bg=t.WHITE)
        self._text_field(
            self.return_flight_frame, "return_flight_nos", "返程航班号", "中转用 + 连接",
            key_hint="return_flight_nos",
        )

        self._section(form, "低价提醒", muted=True)
        thr = tk.Frame(form, bg=t.WHITE)
        thr.pack(fill="x", pady=(0, dpi.px(8)))
        tk.Label(thr, text="低于此价时提醒（元，0 为关闭）", bg=t.WHITE, fg=t.MUTED, font=t.FONT_SMALL).pack(anchor="w")
        self.vars["alert_threshold"] = tk.StringVar(value="0")
        ttk.Entry(thr, textvariable=self.vars["alert_threshold"], width=16).pack(anchor="w", pady=(dpi.px(4), 0))

        self.reload()
        self._sync_trip()
        self._sync_mode()

    def _section(self, parent, title, muted=False):
        tk.Label(
            parent, text=title, bg=t.WHITE,
            fg=t.MUTED if muted else t.TEXT,
            font=t.FONT_SMALL if muted else t.FONT_BOLD,
        ).pack(anchor="w", pady=(dpi.px(14), dpi.px(6)))

    def _pick_mode(self, value: str):
        self.vars["monitor_mode"].set(value)
        self._sync_mode()

    def _fail(self, msg: str, field: str | None = None):
        self.form_error.configure(text=msg, fg=t.DANGER)
        self.app.flash(msg, "err")
        if field and field in self._field_errors:
            self._field_errors[field].configure(text=msg)
        go = self._field_labels.get(field or "", {})
        if go.get("hint"):
            go["hint"].configure(fg=t.DANGER, text=msg)

    def _clear_errors(self):
        self.form_error.configure(text="")
        for lbl in self._field_errors.values():
            lbl.configure(text="")

    def _sync_mode(self):
        mode = self.vars.get("monitor_mode") and self.vars["monitor_mode"].get()
        fare_plan = mode == "fare_plan"
        is_round = self.vars.get("trip") and self.vars["trip"].get() == "round"
        for key, card in getattr(self, "_mode_cards", {}).items():
            card.set_selected(key == mode)
        if fare_plan:
            if not self.flight_frame.winfo_ismapped():
                try:
                    self.flight_frame.pack(fill="x", after=self.mode_row, pady=(dpi.px(8), 0))
                except tk.TclError:
                    self.flight_frame.pack(fill="x", pady=(dpi.px(8), 0))
            if is_round:
                if not self.return_flight_frame.winfo_ismapped():
                    try:
                        self.return_flight_frame.pack(fill="x", after=self.flight_frame)
                    except tk.TclError:
                        self.return_flight_frame.pack(fill="x")
            else:
                self.return_flight_frame.pack_forget()
        else:
            self.flight_frame.pack_forget()
            self.return_flight_frame.pack_forget()
        go = self._field_labels.get("flight_nos", {})
        if go:
            go["label"].configure(text="去程航班号（必填）", fg=t.TEXT)
            go["hint"].configure(text="中转用 + 连接，如 MU9658+MU5931", fg=t.MUTED)
        back = self._field_labels.get("return_flight_nos", {})
        if back:
            back["label"].configure(text="返程航班号", fg=t.TEXT)
            back["hint"].configure(text="中转用 + 连接", fg=t.MUTED)

    def _text_field(self, parent, key, label, hint, key_hint=None):
        lbl = tk.Label(parent, text=label, bg=t.WHITE, fg=t.TEXT, font=t.FONT)
        lbl.pack(anchor="w", pady=(8, 0))
        hint_lbl = tk.Label(parent, text=hint, bg=t.WHITE, fg=t.MUTED, font=t.FONT_SMALL)
        hint_lbl.pack(anchor="w")
        shell = t.modern_text(parent, height=2)
        shell.pack(fill="x", pady=(2, 0))
        self.texts[key] = shell.text
        store_key = key_hint or key
        self._field_labels[store_key] = {"label": lbl, "hint": hint_lbl, "parent": parent}

    def _date_field(self, parent, key, label, hint, multi=True, on_change=None):
        lbl = tk.Label(parent, text=label, bg=t.WHITE, fg=t.TEXT, font=t.FONT)
        lbl.pack(anchor="w", pady=(8, 0))
        hint_lbl = tk.Label(parent, text=hint, bg=t.WHITE, fg=t.MUTED, font=t.FONT_SMALL)
        if hint:
            hint_lbl.pack(anchor="w")
        picker = DateListPicker(parent, multi=multi, on_change=on_change)
        picker.pack(fill="x", pady=(dpi.px(4), 0))
        self.date_pickers[key] = picker
        self._field_labels[key] = {"label": lbl, "hint": hint_lbl, "parent": parent}

    def _on_go_dates(self, dates):
        back = self.date_pickers.get("return_dates")
        if back:
            back.set_min_date(dates[0] if dates else None)

    def _sync_trip(self):
        is_round = self.vars["trip"].get() == "round"
        go = self.date_pickers.get("dates")
        if go:
            go.set_multi(not is_round)
        back = self.date_pickers.get("return_dates")
        if back:
            back.set_multi(False)
        if is_round:
            self._on_go_dates(go.get() if go else [])
            if not self.return_frame.winfo_ismapped():
                try:
                    self.return_frame.pack(fill="x", after=self.go_frame)
                except tk.TclError:
                    self.return_frame.pack(fill="x")
        else:
            self.return_frame.pack_forget()
        self._sync_mode()

    def reload(self):
        self.listbox.delete(0, "end")
        for r in self.app.runtime.cfg.get("routes") or []:
            trip = "往返" if str(r.get("trip", "")).lower() in ("round", "往返") else "单程"
            mode = "指定航班" if str(r.get("monitor_mode", "lowest")) == "fare_plan" else "最低价"
            name = f"{r.get('from_name') or r.get('from')} → {r.get('to_name') or r.get('to')}  [{mode}/{trip}]"
            self.listbox.insert("end", name)
        if self.listbox.size() == 0:
            if self._index is not None:
                self._reset_form()
            return
        if self._index is None:
            self.listbox.selection_set(0)
            self._load(0)
        elif 0 <= self._index < self.listbox.size():
            self.listbox.selection_set(self._index)
        self._sync_trip()
        self._sync_mode()

    def _reset_form(self):
        self._index = None
        self.vars["from"].set("")
        self.vars["from_name"].set("")
        self.vars["to"].set("")
        self.vars["to_name"].set("")
        self.vars["alert_threshold"].set("0")
        self.vars["trip"].set("one_way")
        self.vars["monitor_mode"].set("lowest")
        for box in self.texts.values():
            box.delete("1.0", "end")
        for picker in self.date_pickers.values():
            picker.clear()
        self._clear_errors()
        self._sync_trip()
        self._sync_mode()

    def _on_select(self, _evt=None):
        sel = self.listbox.curselection()
        if sel:
            self._load(sel[0])

    def _load(self, idx: int):
        routes = self.app.runtime.cfg.get("routes") or []
        if idx < 0 or idx >= len(routes):
            return
        self._index = idx
        self._clear_errors()
        r = routes[idx]
        self.vars["from"].set(str(r.get("from", "")))
        self.vars["from_name"].set(str(r.get("from_name", "")))
        self.vars["to"].set(str(r.get("to", "")))
        self.vars["to_name"].set(str(r.get("to_name", "")))
        self.vars["alert_threshold"].set(str(r.get("alert_threshold", 0) or 0))
        trip = str(r.get("trip", "one_way") or "one_way")
        self.vars["trip"].set("round" if trip in ("round", "往返") else "one_way")
        mm = str(r.get("monitor_mode", "lowest") or "lowest")
        self.vars["monitor_mode"].set("fare_plan" if mm == "fare_plan" else "lowest")
        mapping = {
            "flight_nos": r.get("flight_nos", r.get("flight_no")),
            "return_flight_nos": r.get("return_flight_nos", r.get("return_flight_no")),
        }
        for key, val in mapping.items():
            box = self.texts[key]
            box.delete("1.0", "end")
            box.insert("1.0", _join_list(val))
        self.date_pickers["dates"].set(r.get("dates") or [])
        self.date_pickers["return_dates"].set(r.get("return_dates") or r.get("return_date") or [])
        self._sync_trip()
        self._sync_mode()

    def _collect(self) -> dict:
        thr = self.vars["alert_threshold"].get().strip() or "0"
        try:
            threshold = float(thr)
        except ValueError:
            threshold = 0
        from_name = self.vars["from_name"].get().strip()
        to_name = self.vars["to_name"].get().strip()
        for ph in ("输入中文，如 香港", "输入中文，如 香格里拉", "输入中文，如 首尔"):
            if from_name == ph:
                from_name = ""
            if to_name == ph:
                to_name = ""
        d = {
            "from": self.vars["from"].get().strip().upper(),
            "from_name": from_name,
            "to": self.vars["to"].get().strip().upper(),
            "to_name": to_name,
            "trip": self.vars["trip"].get().strip() or "one_way",
            "monitor_mode": self.vars["monitor_mode"].get().strip() or "lowest",
            "dates": self.date_pickers["dates"].get(),
            "alert_threshold": threshold,
        }
        if d["trip"] == "round":
            d["dates"] = d["dates"][:1]
            backs = self.date_pickers["return_dates"].get()[:1]
            if backs:
                d["return_dates"] = backs
            rfns = _split_lines(self.texts["return_flight_nos"].get("1.0", "end"))
            if d["monitor_mode"] == "fare_plan" and rfns:
                d["return_flight_nos"] = rfns
        fns = _split_lines(self.texts["flight_nos"].get("1.0", "end"))
        if d["monitor_mode"] == "fare_plan" and fns:
            d["flight_nos"] = fns
        return d

    def add_route(self):
        self._reset_form()
        self.listbox.selection_clear(0, "end")
        self.form_error.configure(text="正在新增航线，填好转保存", fg=t.BLUE)
        self.app.flash("正在新增航线", "ok")

    def delete_route(self):
        sel = self.listbox.curselection()
        routes = self.app.runtime.cfg.setdefault("routes", [])
        if not sel:
            if not routes:
                self._fail("没有可删的航线")
            else:
                self._fail("请先在左侧选中要删除的航线")
            return
        idx = sel[0]
        if 0 <= idx < len(routes):
            routes.pop(idx)
            self._index = None
            self.app.save_config()
            self.reload()
            self.app.flash("已删除航线", "ok")
            self.form_error.configure(text="已删除", fg=t.OK)

    def save_route(self):
        self._clear_errors()
        routes = self.app.runtime.cfg.setdefault("routes", [])
        data = self._collect()
        if not data["from_name"]:
            self._fail("请填写出发城市", "from_name")
            return
        if not data["to_name"]:
            self._fail("请填写到达城市", "to_name")
            return
        if not data["from"]:
            self._fail("出发地没有三字码，请从联想列表选择或手动填写", "from")
            return
        if not data["to"]:
            self._fail("到达地没有三字码，请从联想列表选择或手动填写", "to")
            return
        if not data["dates"]:
            self._fail("请选择出发日期", "dates")
            return
        if data["trip"] == "round" and not data.get("return_dates"):
            self._fail("往返请选择返程日期", "return_dates")
            return
        if data["trip"] == "round":
            go = (data.get("dates") or [""])[0]
            back = (data.get("return_dates") or [""])[0]
            if go and back and back < go:
                self._fail("返程不能早于出发", "return_dates")
                return
        if data.get("monitor_mode") == "fare_plan" and not data.get("flight_nos"):
            self._fail("盯指定航班需要填写去程航班号（中转用 + 连接）", "flight_nos")
            return
        if self._index is not None and 0 <= self._index < len(routes):
            routes[self._index] = data
        else:
            routes.append(data)
            self._index = len(routes) - 1
        self.app.save_config()
        self.reload()
        if 0 <= self._index < self.listbox.size():
            self.listbox.selection_set(self._index)
        self.form_error.configure(text="已保存这条航线", fg=t.OK)
        self.app.flash("航线已保存", "ok")
