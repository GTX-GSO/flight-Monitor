# 机票价格监控小工具

监控 **携程 / 飞猪 / 同程 / 去哪儿 / 途牛** 多家平台同一航线、同一日期的机票价格，支持：

- 多航线 + 多日期同时监控
- 单程 / 往返套票（去哪儿）
- 可指定航班号（含中转组合），只盯你要坐的那几班
- 指定航班时穷举买法：往返套票 vs 去程/返程分别买组合票 vs 各航段单买，推送写明最低方案
- 多平台价格对比，自动找出最低价来源
- 多日期对比，自动挑出最便宜的一天
- 低价阈值提醒：控制台 + 日志 + **微信推送（Server酱）**
- 推送去抖：价格波动太小不重复推送，避免消息轰炸
- 价格历史入库（SQLite），可用于后续画走势
- 定时调度带随机扰动，规避机器人定时特征

> 数据获取：携程/同程/去哪儿走 **Playwright 浏览器自动化**，飞猪/途牛走 **纯 httpx 逆向**（无需浏览器）。仅用于个人学习/出行参考，请勿高频请求。

---

## 1. 安装

```powershell
git clone https://github.com/GTX-GSO/flight-Monitor.git
cd flight-Monitor
pip install -r requirements.txt
# 携程/同程/去哪儿 需要 Playwright 浏览器内核（飞猪/途牛无需）
python -m playwright install chromium
```

依赖见 `requirements.txt`：`playwright`、`PyYAML`、`APScheduler`、`httpx`。

## 2. 配置

复制示例配置并按需修改：

```powershell
Copy-Item config.example.yaml config.yaml
```

`config.yaml` 主要字段：

```yaml
routes:
  - from: SHA           # 出发地三字码
    from_name: 上海
    to: BJS             # 目的地三字码
    to_name: 北京
    dates:
      - "2026-07-15"
      - "2026-07-16"
      - "2026-07-20"
    trip: round               # one_way（默认）| round 往返套票
    return_dates:
      - "2026-07-22"          # 与 dates 数量相同时按序配对，否则笛卡尔积
    flight_nos:               # 可选；去程。填写后只盯这些航班，并穷举最低买法
      - MU5735                # 中转组合写成 MU9658+MU5931
    return_flight_nos:        # 可选；返程
      - MU5736
    alert_threshold: 800   # 低于此价触发提醒（元，0 表示不启用；指定航班时用穷举后的最低买法）

# 监控平台，可选: ctrip, fliggy, tongcheng, qunar, tuniu
# fliggy / tuniu 为纯 httpx 逆向实现，无需浏览器；其余走 Playwright
platforms: [ctrip, fliggy, tongcheng, qunar, tuniu]

schedule:
  interval_minutes: 90       # 基础间隔
  jitter_minutes: 30          # 随机扰动，实际间隔 = [90-30, 90+30]
  run_on_start: true          # 启动时立即跑一次

crawler:
  headless: true              # 无头模式；首次建议 false 观察反爬
  timeout_seconds: 45
  delay_min: 5                # 单次请求间随机等待（秒）
  delay_max: 15
  user_agent: "..."           # 桌面 UA
  mobile_user_agent: ""       # 移动 UA，留空用内置 iPhone Safari
  debug: true                 # 解析失败时存截图/HTML 到 debug_dir
  debug_dir: debug
  user_data_dir: user_data    # 浏览器会话目录（各平台独立子目录）

output:
  db_path: data/prices.db
  log_path: logs/monitor.log

notifier:
  push_drop_min: 30           # 较上次推送价降幅 ≥ 此值才再推
  push_rise_min: 50           # 涨幅 ≥ 此值才再推（提醒涨价赶紧买）
  serverchan:
    enabled: true
    send_key: "SCTxxxxxxxxxxxxx"   # 你的 Server酱 SendKey
    channel: ""                     # 可选推送通道，多个用 | 分隔
```

> 常用城市三字码：北京 `BJS`、上海 `SHA`、广州 `CAN`、深圳 `SZX`、成都 `CTU`/`TFU`、杭州 `HGH`、西安 `SIA`、重庆 `CKG`、昆明 `KMG`、香格里拉 `DIG`、香港 `HKG`、澳门 `MAC`。

### 往返套票

`trip: round` 时必须同时填 `dates`（去程）和 `return_dates`（返程）。数量相同时按顺序配对，否则做笛卡尔积。

目前**仅去哪儿**支持往返；其它平台会跳过。未指定返程航班时，使用去程卡片上的套票起价；指定了 `return_flight_nos` 会点进去程再筛返程。

### 指定航班与穷举买法

填写 `flight_nos` / `return_flight_nos` 后，只监控这些航班。中转组合用 `+` 连接，例如 `MU9658+MU5931`。

去哪儿会在同一浏览器会话里穷举多种买法，用**最低那种**做阈值判断，并写进推送：

| 买法 | 说明 |
|------|------|
| 往返套票 | 去哪儿往返一次下单的打包价 |
| 去程组合票 + 返程组合票 | 去程、返程各买一张中转组合票再相加 |
| 去程组合票 + 返程拆段 | 去程买组合票，返程按航段分别单买 |
| 去程拆段 + 返程组合票 | 对称情况 |
| 全部拆段单买 | 每个航段单独搜、单独买再加总 |

某一侧搜不到价时，该方案会被跳过，不会用残缺数字加总。穷举大约多搜 5–7 次，一轮会更久。未填航班号时不穷举，只盯航线最低价。

### 港澳台 / 国际

去哪儿把香港、澳门、台湾算国际航线，必须填中文 `from_name` / `to_name`（如 `香港`），不能把 `HKG` 直接当城市名，否则会提示「当前搜索无航线」。查询走 `interlist`，不是国内 `flightlist`。

## 3. 运行

### 前台运行

```powershell
python main.py                # 按 config.yaml 启动定时监控，Ctrl+C 退出
python main.py --once         # 只跑一次（调试用）
python main.py -c my.yaml      # 指定配置文件
python main.py --login qunar  # 弹出可见浏览器登录，回车后保存会话
```

### 后台运行（Windows）

仓库附带三个 PowerShell 脚本，把监控跑成后台进程：

```powershell
.\start.ps1      # 后台启动，PID 写入 .monitor.pid，输出重定向到 logs\monitor.out.log
.\status.ps1     # 查看运行状态、PID、内存占用及最近 20 行日志
.\stop.ps1       # 停止监控
```

## 4. 输出说明

- **控制台 / `logs/monitor.log`**：每轮最低价、多平台对比、多日期对比、买法比价、低价提醒
- **`data/prices.db`**：SQLite，表 `flight_prices` 存全部历史价；表 `alert_state` 存上次已推送价（用于去抖）
- **`debug/`**：开启 `crawler.debug` 后，解析失败时保存的截图与 HTML
- **微信**：触发低价阈值时由 Server酱 推送
  - `title` 必填，最长 32 字（例：`📉香港⇄香格里拉 11-27/12-06 ¥2100`）
  - `desp` 支持 Markdown，最长 32KB；指定航班时含各买法比价和推荐拆解
  - `short` 消息卡片文案，最长 64 字（例：`建议全部拆段单买¥2100，比套票省¥400`）

查询历史最低价示例：

```bash
sqlite3 data/prices.db
> SELECT depart_date, platform, MIN(price)
  FROM flight_prices
  WHERE from_city='SHA' AND to_city='BJS'
  GROUP BY depart_date, platform;
```

## 5. 项目结构

```
flight-Monitor/
├── main.py                 # 入口：调度 / --once / --login
├── config.yaml             # 真实配置（含密钥，不提交，已 gitignore）
├── config.example.yaml     # 示例配置（脱敏）
├── requirements.txt
├── start.ps1 / status.ps1 / stop.ps1   # Windows 后台运行脚本
├── core/
│   ├── models.py           # 数据模型 FlightPrice / Route
│   ├── storage.py          # SQLite 存储 + alert_state 去抖
│   ├── flights.py          # 航班号 / 航段解析
│   ├── fareplan.py         # 指定航班穷举买法 + 推送文案
│   ├── alerter.py          # 价格对比 + 低价提醒 + 推送
│   ├── notifier.py         # 消息推送（Server酱 title/desp/short）
│   ├── scheduler.py        # APScheduler 调度
│   └── logger.py           # 日志
├── crawlers/
│   ├── base.py             # Playwright 持久化上下文 + XHR 拦截
│   ├── ctrip.py            # 携程      (Playwright)
│   ├── fliggy.py           # 飞猪      (httpx 逆向)
│   ├── tongcheng.py        # 同程      (Playwright)
│   ├── qunar.py            # 去哪儿    (Playwright)
│   └── tuniu.py            # 途牛      (httpx 逆向)
├── data/                   # 运行产物：prices.db（gitignore）
├── logs/                   # 运行产物：monitor.log（gitignore）
├── debug/                  # 调试快照（gitignore）
└── user_data/              # 浏览器会话（gitignore）
```

## 6. 常见问题

- **抓不到价格 / 一直 0 条记录**：把 `crawler.headless` 改为 `false` 肉眼观察，是否触发验证码/风控；打开 `crawler.debug` 看截图。
- **香港/澳门提示无航线**：确认 `from_name`/`to_name` 填的是「香港」「澳门」等中文名，而不是三字码。
- **指定了航班但结果像整条航线最低价**：确认 `flight_nos` 已取消注释；中转写成 `MU9658+MU5931`。穷举目前仅去哪儿支持。
- **被风控 / 出现滑块**：加大 `delay_min/delay_max`，降低 `schedule.interval_minutes` 频率。指定航班穷举请求更多，间隔不要太密。
- **推不到微信**：确认 Server酱 SendKey 正确（`notifier.serverchan.send_key`），免费版每日 5 条额度；去抖阈值 `push_drop_min/push_rise_min` 过大也会抑制推送。`title` 超过 32 字会被截断。
- **想推送到钉钉/企业微信**：在 `core/notifier.py` 新增一个 Notifier 类，并在 `build_notifier` 里按配置择一构造即可。
- **想看历史走势**：基于 `data/prices.db` 写个 matplotlib 脚本，或接 Grafana。

## 7. 免责声明

本工具仅供学习交流，所有数据均来自各平台公开网页。请遵守各平台服务条款，控制请求频率，勿用于商业用途。使用本工具产生的一切后果由使用者自行承担。

## 8. License

MIT
