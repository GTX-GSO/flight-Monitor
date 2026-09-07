# 机票价格监控

Fork 自 [yangka1212/JiPiao](https://github.com/yangka1212/JiPiao)，在原版基础上增加了多平台并行抓取、图形化界面、指定航程买法穷举比价等能力。

多平台机票低价监控：定时查价、多平台比价、低价微信推送（Server酱）、价格历史入库。仅供个人出行参考，请控制查询频率。

## 功能

- GUI / 命令行双入口，多航线、多日期
- 单程 / 往返；`lowest` 最低价 / `fare_plan` 指定航班穷举买法
- 低价阈值提醒 + 推送去抖；SQLite 历史记录

## 安装

```powershell
pip install -r requirements.txt
python -m playwright install chromium   # 去哪儿、同程需要
Copy-Item config.example.yaml config.yaml
```

## 运行

```powershell
python main.py --gui          # 图形界面
python main.py                # 定时监控
python main.py --once         # 跑一次
python main.py --login qunar  # 登录保存会话
```

Windows 后台：`start.ps1` / `start-gui.ps1` / `status.ps1` / `stop.ps1`

### 打包 Windows 可执行文件

```powershell
.\scripts\build_exe.ps1
# 产出：dist\JiPiaoMonitor\JiPiaoMonitor.exe
```

首次使用 exe 前仍需本机执行一次 `python -m playwright install chromium`（浏览器内核体积大，未打进包内）。

## 配置要点

复制 `config.example.yaml` 为 `config.yaml`，主要字段：


| 字段                    | 说明                                                     |
| --------------------- | ------------------------------------------------------ |
| `routes`              | 航线：三字码、`from_name`/`to_name`、日期、`trip`、`monitor_mode`  |
| `monitor_mode`        | `lowest`（默认）或 `fare_plan`（须填 `flight_nos`）             |
| `platforms`           | 见下表                                                    |
| `schedule`            | `interval_minutes` + `jitter_minutes`                  |
| `crawler`             | `headless`、`delay_min/max`、`sequential_platforms`（降风控） |
| `notifier.serverchan` | Server酱 `send_key`                                     |


国际/港澳台线须填**中文**城市名（如「香港」「首尔」），不能只填三字码。

## 平台可用性


| 平台  | 国内  | 国际/港澳台 | 备注                 |
| --- | --- | ------ | ------------------ |
| 去哪儿 | ✅   | ✅      | 全场景推荐              |
| 飞猪  | ✅   | ❌      | httpx，无需浏览器        |
| 同程  | ✅   | ❌      | Playwright         |
| 途牛  | ⚠️  | ❌      | 易 `179991` 限流，建议低频 |
| 携程  | ❌   | ❌      | API 风控加密，暂不可用      |


**建议组合**：国内 `qunar` + `fliggy` + `tongcheng`；国际/港澳台**仅** `qunar`。

往返套票价目前仅去哪儿可靠；其他平台自动拆去程+返程相加。`fare_plan` 会穷举套票 / 组合票 / 拆段 / 跨平台拆段（查询次数多，间隔宜 ≥3 分钟）。

## 常见问题

- **无价格**：核对平台与航线类型；国际只开去哪儿；`debug: true` 看 `debug/` 截图
- **风控**：加大 `delay_min/max`，开 `sequential_platforms`，降低调度频率
- **推不到微信**：检查 Server酱 SendKey 与 `push_drop_min` / `push_rise_min`

测试用例：`test_specified/`（`python test_specified/run_all.py`）

## 免责

数据来自各平台公开页面，请遵守服务条款，勿用于商业用途。后果自负。

## License

MIT（沿用上游 [yangka1212/JiPiao](https://github.com/yangka1212/JiPiao)）