#!/usr/bin/env python3
"""
Crash monitor bot for BTC/ETH/QQQ with Telegram alerts.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import requests
from zoneinfo import ZoneInfo


UTC = timezone.utc


@dataclass
class Candle:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class Config:
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    poll_interval_seconds: int = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))
    cooldown_hours: int = int(os.getenv("COOLDOWN_HOURS", "24"))
    drop_threshold_pct: float = float(os.getenv("DROP_THRESHOLD_PCT", "10"))
    wick_threshold_pct: float = float(os.getenv("WICK_THRESHOLD_PCT", "8"))
    wick_volume_spike_ratio: float = float(os.getenv("WICK_VOLUME_SPIKE_RATIO", "1.8"))
    request_timeout_seconds: int = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "10"))
    state_file: str = os.getenv("STATE_FILE", "alert_state.json")
    twelvedata_api_key: str = os.getenv("TWELVEDATA_API_KEY", "demo").strip()
    run_once: bool = os.getenv("RUN_ONCE", "false").lower() in {"1", "true", "yes"}


class MarketDataClient:
    def __init__(self, timeout_seconds: int = 10, twelvedata_api_key: str = "demo") -> None:
        self.session = requests.Session()
        self.timeout_seconds = timeout_seconds
        self.twelvedata_api_key = twelvedata_api_key
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )
            }
        )

    def _get_json(self, url: str, params: Optional[Dict[str, str]] = None) -> dict:
        resp = self.session.get(url, params=params, timeout=self.timeout_seconds)
        resp.raise_for_status()
        return resp.json()

    def fetch_binance_price(self, symbol: str) -> float:
        data = self._get_json(
            "https://api.binance.com/api/v3/ticker/price", params={"symbol": symbol}
        )
        return float(data["price"])

    def fetch_binance_klines(self, symbol: str, interval: str, limit: int = 200) -> List[Candle]:
        data = self._get_json(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "limit": str(limit)},
        )
        candles: List[Candle] = []
        for row in data:
            candles.append(
                Candle(
                    ts=datetime.fromtimestamp(row[0] / 1000.0, tz=UTC),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
            )
        return candles

    def fetch_yahoo_chart(
        self, symbol: str, interval: str, range_value: str
    ) -> List[Candle]:
        # Yahoo interval examples: 5m, 15m, 60m, 1d
        data = self._get_json(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"interval": interval, "range": range_value},
        )
        result = data.get("chart", {}).get("result", [])
        if not result:
            return []
        chart = result[0]
        timestamps = chart.get("timestamp", [])
        quote = chart.get("indicators", {}).get("quote", [{}])[0]
        opens = quote.get("open", [])
        highs = quote.get("high", [])
        lows = quote.get("low", [])
        closes = quote.get("close", [])
        volumes = quote.get("volume", [])

        candles: List[Candle] = []
        for i, ts in enumerate(timestamps):
            try:
                o = opens[i]
                h = highs[i]
                l = lows[i]
                c = closes[i]
                v = volumes[i] if volumes and i < len(volumes) and volumes[i] is not None else 0.0
                if None in (o, h, l, c):
                    continue
                candles.append(
                    Candle(
                        ts=datetime.fromtimestamp(ts, tz=UTC),
                        open=float(o),
                        high=float(h),
                        low=float(l),
                        close=float(c),
                        volume=float(v),
                    )
                )
            except (IndexError, TypeError, ValueError):
                continue
        return candles

    def fetch_twelvedata_chart(self, symbol: str, interval: str, outputsize: int) -> List[Candle]:
        if not self.twelvedata_api_key:
            return []

        data = self._get_json(
            "https://api.twelvedata.com/time_series",
            params={
                "symbol": symbol,
                "interval": interval,
                "outputsize": str(outputsize),
                "apikey": self.twelvedata_api_key,
            },
        )
        values = data.get("values", [])
        if not values:
            return []

        tz_name = data.get("meta", {}).get("exchange_timezone", "UTC")
        try:
            local_tz = ZoneInfo(tz_name)
        except Exception:
            local_tz = UTC

        candles: List[Candle] = []
        for row in values:
            dt_str = row.get("datetime")
            if not dt_str:
                continue
            try:
                if len(dt_str) == 10:
                    dt_local = datetime.strptime(dt_str, "%Y-%m-%d").replace(
                        hour=0, minute=0, second=0, tzinfo=local_tz
                    )
                else:
                    dt_local = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(
                        tzinfo=local_tz
                    )
                ts_utc = dt_local.astimezone(UTC)

                candles.append(
                    Candle(
                        ts=ts_utc,
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row.get("volume") or 0.0),
                    )
                )
            except (ValueError, TypeError, KeyError):
                continue

        candles.sort(key=lambda x: x.ts)
        return candles

    def fetch_equity_chart(
        self, symbol: str, interval: str, range_value: str, outputsize: int
    ) -> List[Candle]:
        try:
            candles = self.fetch_yahoo_chart(symbol, interval, range_value)
            if candles:
                return candles
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status is not None:
                logging.warning(
                    "Yahoo chart rate/HTTP error for %s %s %s: %s. Falling back to TwelveData.",
                    symbol,
                    interval,
                    range_value,
                    status,
                )
        except Exception as exc:
            logging.warning(
                "Yahoo chart failed for %s %s %s: %s. Falling back to TwelveData.",
                symbol,
                interval,
                range_value,
                exc,
            )

        td_interval_map = {"5m": "5min", "15m": "15min", "60m": "1h", "1d": "1day"}
        td_interval = td_interval_map.get(interval)
        if not td_interval:
            return []
        return self.fetch_twelvedata_chart(symbol, td_interval, outputsize)

    def fetch_fear_greed(self) -> Optional[Tuple[int, str]]:
        try:
            data = self._get_json("https://api.alternative.me/fng/", params={"limit": "1"})
            values = data.get("data", [])
            if not values:
                return None
            value = int(values[0]["value"])
            label = values[0]["value_classification"]
            return value, label
        except Exception:
            return None


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str, timeout_seconds: int = 10) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.session = requests.Session()
        self.timeout_seconds = timeout_seconds

    def send_message(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text}
        resp = self.session.post(url, data=payload, timeout=self.timeout_seconds)
        resp.raise_for_status()


class CooldownStore:
    def __init__(self, path: str) -> None:
        self.path = path
        self.state: Dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            self.state = {}
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.state = json.load(f)
        except Exception:
            self.state = {}

    def _save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)

    def is_in_cooldown(self, key: str, hours: int) -> bool:
        raw = self.state.get(key)
        if not raw:
            return False
        try:
            last = datetime.fromisoformat(raw)
        except ValueError:
            return False
        return datetime.now(tz=UTC) - last < timedelta(hours=hours)

    def mark_sent(self, key: str) -> None:
        self.state[key] = datetime.now(tz=UTC).isoformat()
        self._save()


ASSETS = [
    {"id": "BTC", "source": "binance", "symbol": "BTCUSDT", "label": "BTC"},
    {"id": "ETH", "source": "binance", "symbol": "ETHUSDT", "label": "ETH"},
    # QQQ as practical Nasdaq 100 proxy for real-time tradable market behavior.
    {"id": "NASDAQ100", "source": "yahoo", "symbol": "QQQ", "label": "Nasdaq100(QQQ)"},
]

WICK_TIMEFRAMES = [
    {"name": "5m", "lookback": 12},
    {"name": "15m", "lookback": 8},
    {"name": "1h", "lookback": 6},
]

TF_TO_MINUTES = {"5m": 5, "15m": 15, "1h": 60}


def pct_change(current: float, reference: float) -> float:
    if reference == 0:
        return 0.0
    return (current - reference) / reference * 100.0


def format_price(value: float) -> str:
    if value >= 1000:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:,.4f}"
    return f"{value:,.6f}"


def get_reference_72h(candles: List[Candle], now_utc: datetime) -> Optional[float]:
    target_time = now_utc - timedelta(hours=72)
    candidates = [c for c in candles if c.ts <= target_time]
    if candidates:
        return candidates[-1].close
    return None


def detect_three_day_drop(
    data_client: MarketDataClient, asset: dict, drop_threshold_pct: float
) -> Optional[dict]:
    now_utc = datetime.now(tz=UTC)

    if asset["source"] == "binance":
        hourly = data_client.fetch_binance_klines(asset["symbol"], "1h", limit=200)
        daily = data_client.fetch_binance_klines(asset["symbol"], "1d", limit=10)
        current_price = data_client.fetch_binance_price(asset["symbol"])
    else:
        hourly = data_client.fetch_equity_chart(asset["symbol"], "60m", "10d", outputsize=200)
        daily = data_client.fetch_equity_chart(asset["symbol"], "1d", "1mo", outputsize=30)
        current_price = hourly[-1].close if hourly else (daily[-1].close if daily else 0.0)

    if current_price <= 0:
        return None

    reference = get_reference_72h(hourly, now_utc)
    window_label = "最近72小时"
    if reference is None and len(daily) >= 4:
        reference = daily[-4].close
        window_label = "最近3个交易日"
    if reference is None:
        return None

    drop_pct = pct_change(current_price, reference)
    if drop_pct <= -abs(drop_threshold_pct):
        support = None
        if len(hourly) >= 24:
            support = min(c.low for c in hourly[-24:])
        elif daily:
            support = min(c.low for c in daily[-5:])
        return {
            "type": "three_day_drop",
            "drop_pct": drop_pct,
            "window": window_label,
            "current_price": current_price,
            "reference_price": reference,
            "support": support,
        }
    return None


def _detect_recent_wick_in_candles(
    candles: List[Candle],
    lookback: int,
    tf_name: str,
    wick_threshold_pct: float,
    volume_spike_ratio: float,
    now_utc: datetime,
) -> Optional[dict]:
    if len(candles) < lookback + 2:
        return None

    tf_minutes = TF_TO_MINUTES[tf_name]
    recent_cutoff = now_utc - timedelta(minutes=tf_minutes * 3)

    best: Optional[dict] = None
    start_idx = max(lookback, len(candles) - 5)
    for i in range(start_idx, len(candles)):
        c = candles[i]
        if c.ts < recent_cutoff:
            continue

        prev_segment = candles[i - lookback : i]
        prev_high = max(x.high for x in prev_segment)
        drop_pct = pct_change(c.low, prev_high)

        if drop_pct <= -abs(wick_threshold_pct):
            avg_vol = sum(x.volume for x in prev_segment) / max(len(prev_segment), 1)
            volume_spike = c.volume >= avg_vol * volume_spike_ratio if avg_vol > 0 else False
            candidate = {
                "type": "flash_wick",
                "drop_pct": drop_pct,
                "timeframe": tf_name,
                "candle_time": c.ts,
                "current_price": c.close,
                "pre_high": prev_high,
                "wick_low": c.low,
                "support": c.low,
                "volume_spike": volume_spike,
            }
            if best is None or candidate["drop_pct"] < best["drop_pct"]:
                best = candidate
    return best


def detect_flash_wick(
    data_client: MarketDataClient, asset: dict, wick_threshold_pct: float, volume_spike_ratio: float
) -> Optional[dict]:
    now_utc = datetime.now(tz=UTC)
    best_signal: Optional[dict] = None

    for tf in WICK_TIMEFRAMES:
        tf_name = tf["name"]
        lookback = tf["lookback"]

        if asset["source"] == "binance":
            candles = data_client.fetch_binance_klines(asset["symbol"], tf_name, limit=120)
        else:
            yahoo_interval = "60m" if tf_name == "1h" else tf_name
            yahoo_range = "5d" if tf_name in {"5m", "15m"} else "1mo"
            outputsize = 200 if tf_name in {"5m", "15m"} else 120
            candles = data_client.fetch_equity_chart(
                asset["symbol"], yahoo_interval, yahoo_range, outputsize=outputsize
            )

        signal = _detect_recent_wick_in_candles(
            candles=candles,
            lookback=lookback,
            tf_name=tf_name,
            wick_threshold_pct=wick_threshold_pct,
            volume_spike_ratio=volume_spike_ratio,
            now_utc=now_utc,
        )
        if signal and (best_signal is None or signal["drop_pct"] < best_signal["drop_pct"]):
            best_signal = signal
    return best_signal


def build_alert_message(
    asset_label: str,
    signal: dict,
    fear_greed: Optional[Tuple[int, str]],
    drop_threshold_pct: float,
    wick_threshold_pct: float,
) -> str:
    fng_text = "未知"
    if fear_greed:
        fng_text = f"{fear_greed[0]} ({fear_greed[1]})"

    if signal["type"] == "three_day_drop":
        support_text = (
            f"${format_price(signal['support'])}" if signal.get("support") is not None else "N/A"
        )
        return (
            f"🚨⚠️ {asset_label} 触发风控信号\n"
            f"📉 条件：连续3日累计暴跌（>{abs(drop_threshold_pct):.0f}%）\n"
            f"📊 跌幅：{signal['drop_pct']:.2f}%（{signal['window']}）\n"
            f"💵 当前价格：${format_price(signal['current_price'])}\n"
            f"🧱 关键支撑参考：{support_text}\n"
            f"📝 建议：可考虑立即分批买入（做多/抄底），并在买入后24小时内分批卖出（短线反弹策略）。\n"
            f"⚠️ 风险提示：高风险短线操作，仅供参考；务必控制仓位并设置止损。\n"
            f"🧪 辅助确认：恐慌指数={fng_text}\n"
            f"🔎 数据源：Binance / Yahoo Finance / Alternative.me"
        )

    volume_text = "是" if signal.get("volume_spike") else "否"
    return (
        f"🚨⚠️ {asset_label} 触发风控信号\n"
        f"📉 条件：突然插针（单根{signal['timeframe']} K线回撤>{abs(wick_threshold_pct):.0f}%）\n"
        f"📊 跌幅：{signal['drop_pct']:.2f}%（前高 ${format_price(signal['pre_high'])} → 最低 ${format_price(signal['wick_low'])}）\n"
        f"💵 当前价格：${format_price(signal['current_price'])}\n"
        f"🧱 关键支撑参考：${format_price(signal['support'])}\n"
        f"📝 建议：可考虑立即分批买入（做多/抄底），并在买入后24小时内分批卖出（短线反弹策略）。\n"
        f"⚠️ 风险提示：高风险短线操作，仅供参考；务必控制仓位并设置止损。\n"
        f"🧪 辅助确认：成交量放大={volume_text}；恐慌指数={fng_text}\n"
        f"🔎 数据源：Binance / Yahoo Finance / Alternative.me"
    )


def validate_env(cfg: Config) -> None:
    missing = []
    if not cfg.telegram_bot_token:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not cfg.telegram_chat_id:
        missing.append("TELEGRAM_CHAT_ID")
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")


def run_cycle(cfg: Config, data_client: MarketDataClient, tg: TelegramClient, store: CooldownStore) -> None:
    fear_greed = data_client.fetch_fear_greed()
    for asset in ASSETS:
        asset_label = asset["label"]

        try:
            three_day_signal = detect_three_day_drop(
                data_client=data_client,
                asset=asset,
                drop_threshold_pct=cfg.drop_threshold_pct,
            )
        except Exception as exc:
            logging.exception("Three-day-drop detection failed for %s: %s", asset_label, exc)
            three_day_signal = None

        if three_day_signal:
            key = f"{asset['id']}|three_day_drop"
            if not store.is_in_cooldown(key, cfg.cooldown_hours):
                msg = build_alert_message(
                    asset_label,
                    three_day_signal,
                    fear_greed,
                    cfg.drop_threshold_pct,
                    cfg.wick_threshold_pct,
                )
                tg.send_message(msg)
                store.mark_sent(key)
                logging.warning("Alert sent: %s", key)
            else:
                logging.info("Cooldown active: %s", key)

        try:
            wick_signal = detect_flash_wick(
                data_client=data_client,
                asset=asset,
                wick_threshold_pct=cfg.wick_threshold_pct,
                volume_spike_ratio=cfg.wick_volume_spike_ratio,
            )
        except Exception as exc:
            logging.exception("Flash-wick detection failed for %s: %s", asset_label, exc)
            wick_signal = None

        if wick_signal:
            key = f"{asset['id']}|flash_wick"
            if not store.is_in_cooldown(key, cfg.cooldown_hours):
                msg = build_alert_message(
                    asset_label,
                    wick_signal,
                    fear_greed,
                    cfg.drop_threshold_pct,
                    cfg.wick_threshold_pct,
                )
                tg.send_message(msg)
                store.mark_sent(key)
                logging.warning("Alert sent: %s", key)
            else:
                logging.info("Cooldown active: %s", key)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    cfg = Config()
    validate_env(cfg)

    data_client = MarketDataClient(
        timeout_seconds=cfg.request_timeout_seconds,
        twelvedata_api_key=cfg.twelvedata_api_key,
    )
    tg = TelegramClient(
        bot_token=cfg.telegram_bot_token,
        chat_id=cfg.telegram_chat_id,
        timeout_seconds=cfg.request_timeout_seconds,
    )
    store = CooldownStore(cfg.state_file)

    logging.info("Crash monitor started for assets: %s", ", ".join(a["label"] for a in ASSETS))
    while True:
        try:
            run_cycle(cfg, data_client, tg, store)
        except Exception as exc:
            logging.exception("Run cycle failed: %s", exc)

        if cfg.run_once:
            break
        time.sleep(cfg.poll_interval_seconds)


if __name__ == "__main__":
    main()
