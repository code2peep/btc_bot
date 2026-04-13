# Crypto + Nasdaq Flash-Crash Telegram Monitor

一个面向 Telegram 的实时监控机器人，按以下规则触发通知：

- 资产：`BTC`、`ETH`、`Nasdaq100 (QQQ 代理)`
- 条件 1：最近 `72 小时`（若不可用则退化到最近 3 个交易日）累计跌幅超过 `10%`
- 条件 2：短周期出现 `8%+` 插针（`5m/15m/1h` 任一 K 线，从前高到最低）
- 防重复：同一资产 + 同一条件 `24 小时`内只通知一次
- 只在触发时推送，不持续播报

## 数据源

- `Binance`：BTC/ETH 实时价格与 K 线
- `Yahoo Finance`：QQQ（纳斯达克100代理）K 线（首选）
- `TwelveData`：当 Yahoo 返回限流（429）时自动回退
- `Alternative.me`：Fear & Greed 指数（辅助确认）

## Telegram 消息内容

每次触发都包含：

- 触发类型（3日累计暴跌 / 突然插针 8%+）
- 跌幅与时间窗口
- 当前价格与关键支撑参考
- 交易建议：考虑立即分批买入，买入后 24 小时内分批卖出
- 风险提示：高风险短线，仅供参考，注意仓位与止损

## 快速开始

1. 安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

2. 配置环境变量：

```bash
cp .env.example .env
```

填写：

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `TWELVEDATA_API_KEY`（可先用默认 `demo`，生产建议替换成你自己的 key）

3. 运行：

```bash
source .venv/bin/activate  # 如果你使用虚拟环境
set -a; source .env; set +a
python3 monitor_bot.py
```

或直接使用统一启动脚本（会自动加载 `.env` 并尝试激活 `.venv`）：

```bash
./scripts/run_monitor.sh
```

## 验证（只跑一次）

```bash
set -a; source .env; set +a
RUN_ONCE=true python3 monitor_bot.py
```

## 关键参数

- `POLL_INTERVAL_SECONDS`：轮询频率（默认 60 秒）
- `COOLDOWN_HOURS`：去重冷却时间（默认 24 小时）
- `DROP_THRESHOLD_PCT`：3日累计跌幅触发阈值（默认 10）
- `WICK_THRESHOLD_PCT`：插针阈值（默认 8）
- `WICK_VOLUME_SPIKE_RATIO`：成交量放大辅助阈值（默认 1.8）
- `TWELVEDATA_API_KEY`：QQQ 备用数据源 key（默认 demo）

## 说明

- `alert_state.json` 会记录最近告警时间，用于去重。
- 美股非交易时段，QQQ 数据更新会较慢，策略会基于可用最新 K 线判断。

## PM2 常驻运行

1. 安装 PM2（已安装可跳过）：

```bash
npm install -g pm2
```

2. 启动：

```bash
pm2 start ecosystem.config.js
```

3. 常用命令：

```bash
pm2 status
pm2 logs btc-crash-monitor
pm2 restart btc-crash-monitor
pm2 stop btc-crash-monitor
```

4. 开机自启：

```bash
pm2 startup
pm2 save
```

## systemd 常驻运行（Linux）

项目已提供模板：`deploy/systemd/btc-crash-monitor.service`

### 方式 A：一键安装脚本（推荐）

```bash
./scripts/install_systemd.sh <linux_user>
```

例如：

```bash
./scripts/install_systemd.sh ubuntu
```

### 方式 B：手动安装

1. 修改模板中的占位符：

- `REPLACE_WITH_YOUR_USER`
- `REPLACE_WITH_PROJECT_DIR`

2. 安装并启动：

```bash
sudo cp deploy/systemd/btc-crash-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now btc-crash-monitor.service
```

3. 查看状态与日志：

```bash
systemctl status btc-crash-monitor.service
journalctl -u btc-crash-monitor.service -f
```
