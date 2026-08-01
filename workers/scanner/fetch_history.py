from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from io import StringIO
from statistics import pstdev
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from workers.config import get_settings
from workers.data_models import LongTermMetrics
from workers.scanner.fetch_prices import _money, fetch_nasdaq_json

_TRADING_DAYS_YEAR = 252
_ANNUALIZE = _TRADING_DAYS_YEAR**0.5

# Nasdaq's public API needs browser-like headers (see fetch_prices).
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


class HistoryProvider:
    def get_closes(self, symbol: str) -> list[float]:
        """ราคาปิดรายวัน เรียงเก่า→ใหม่ (ใช้ทั้งคำนวณ metrics และวาด sparkline)."""
        raise NotImplementedError

    def get_bars(self, symbol: str, days: int = 60) -> list[dict]:
        """แท่งราคารายวัน {date, close, volume} เรียงเก่า→ใหม่ สำหรับวาดกราฟบนเว็บ.

        ค่าเริ่มต้นคืนเฉพาะราคาปิด (volume = None) — provider ที่มีวอลุ่มให้
        override เอง หน้าเว็บจะซ่อนแผงวอลุ่มเมื่อไม่มีข้อมูล
        """
        closes = self.get_closes(symbol)[-days:]
        return [{"date": None, "close": close, "volume": None} for close in closes]

    def get_metrics(self, symbol: str) -> LongTermMetrics:
        return _metrics_from_closes(symbol, self.get_closes(symbol))


def _metrics_from_closes(
    symbol: str, closes: list[float], latest_price: float | None = None
) -> LongTermMetrics:
    """สร้าง LongTermMetrics จากรายการราคาปิดรายวัน (เรียงเก่า→ใหม่)."""
    closes = [float(value) for value in closes if value is not None and value > 0]
    if len(closes) < 2:
        raise ValueError(f"Not enough history for {symbol}")

    price = float(latest_price) if latest_price and latest_price > 0 else closes[-1]
    sma50 = sum(closes[-50:]) / min(len(closes), 50)
    sma200 = sum(closes[-200:]) / min(len(closes), 200)
    window = closes[-_TRADING_DAYS_YEAR:]
    week52_high = max(window)
    week52_low = min(window)

    def _return(days: int) -> float:
        if len(closes) <= days:
            return 0.0
        past = closes[-(days + 1)]
        return (price - past) / past * 100 if past > 0 else 0.0

    daily_returns = [
        (closes[i] - closes[i - 1]) / closes[i - 1]
        for i in range(1, len(closes))
        if closes[i - 1] > 0
    ]
    volatility = pstdev(daily_returns) * _ANNUALIZE * 100 if len(daily_returns) > 1 else 0.0

    return LongTermMetrics(
        symbol=symbol,
        price=round(price, 4),
        sma50=round(sma50, 4),
        sma200=round(sma200, 4),
        week52_high=round(week52_high, 4),
        week52_low=round(week52_low, 4),
        return_3m_percent=round(_return(63), 2),
        return_6m_percent=round(_return(126), 2),
        volatility_percent=round(volatility, 2),
        history_days=len(closes),
    )


class YahooHistoryProvider(HistoryProvider):
    chart_url = "https://query1.finance.yahoo.com/v8/finance/chart"

    def get_closes(self, symbol: str) -> list[float]:
        return [value for value in self._chart_closes(symbol)[0] if value is not None]

    def _chart_closes(self, symbol: str) -> tuple[list, float | None]:
        request = Request(
            f"{self.chart_url}/{symbol}?{urlencode({'range': '1y', 'interval': '1d'})}",
            headers={"User-Agent": "stock-radar-ai/0.1"},
        )
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))

        results = payload.get("chart", {}).get("result", [])
        if not results:
            raise ValueError(f"No Yahoo Finance history for {symbol}")
        chart = results[0]
        closes = chart.get("indicators", {}).get("quote", [{}])[0].get("close") or []
        # ใช้ราคาล่าสุดจาก meta หากมี เพื่อให้สดกว่าราคาปิดล่าสุด
        latest = chart.get("meta", {}).get("regularMarketPrice")
        return closes, latest

    def get_metrics(self, symbol: str) -> LongTermMetrics:
        closes, latest = self._chart_closes(symbol)
        return _metrics_from_closes(symbol, closes, latest_price=latest)

    def get_bars(self, symbol: str, days: int = 60) -> list[dict]:
        # ขอช่วงให้พอกับจำนวนแท่งที่ต้องการ — ผู้เรียกใช้ชุดเดียวกันนี้คำนวณ SMA200/52w ด้วย
        chart_range = "2y" if days > 150 else "6mo"
        request = Request(
            f"{self.chart_url}/{symbol}?{urlencode({'range': chart_range, 'interval': '1d'})}",
            headers={"User-Agent": "stock-radar-ai/0.1"},
        )
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
        results = payload.get("chart", {}).get("result", [])
        if not results:
            raise ValueError(f"No Yahoo Finance history for {symbol}")
        chart = results[0]
        quote = chart.get("indicators", {}).get("quote", [{}])[0]
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []
        stamps = chart.get("timestamp") or []
        bars = []
        for index, close in enumerate(closes):
            if close is None:
                continue
            stamp = stamps[index] if index < len(stamps) else None
            bars.append(
                {
                    "date": (
                        datetime.fromtimestamp(stamp, timezone.utc).strftime("%m/%d/%Y")
                        if stamp
                        else None
                    ),
                    "close": float(close),
                    "volume": int(volumes[index]) if index < len(volumes) and volumes[index] else None,
                }
            )
        return bars[-days:]


class StooqHistoryProvider(HistoryProvider):
    daily_url = "https://stooq.com/q/d/l/"

    def get_closes(self, symbol: str) -> list[float]:
        params = urlencode({"s": f"{symbol.lower()}.us", "i": "d"})
        request = Request(
            f"{self.daily_url}?{params}",
            headers={"User-Agent": "stock-radar-ai/0.1"},
        )
        with urlopen(request, timeout=15) as response:
            content = response.read().decode("utf-8")
        return [
            float(row["Close"])
            for row in csv.DictReader(StringIO(content))
            if row.get("Close") not in {None, "", "N/D"}
        ]


class NasdaqHistoryProvider(HistoryProvider):
    """ประวัติราคาปิดรายวันจริงจาก Nasdaq public API (ฟรี ไม่ต้องใช้ key)."""

    historical_url = "https://api.nasdaq.com/api/quote/{symbol}/historical"

    def _rows(self, symbol: str, lookback_days: int) -> list[dict]:
        today = datetime.now(timezone.utc).date()
        # ไล่ assetclass (stocks → etf) เพราะ ETF ตอบ data=null เมื่อใช้ชนิดผิด
        payload = fetch_nasdaq_json(
            self.historical_url.format(symbol=symbol.upper()),
            symbol,
            {
                "fromdate": (today - timedelta(days=lookback_days)).isoformat(),
                "todate": today.isoformat(),
                "limit": 9999,
            },
            timeout=20,
        )
        rows = (((payload.get("data") or {}).get("tradesTable") or {}).get("rows")) or []
        # Nasdaq เรียงใหม่→เก่า; ผู้ใช้ต่อ (metrics/กราฟ) ต้องการเก่า→ใหม่
        return list(reversed(rows))

    def get_closes(self, symbol: str) -> list[float]:
        return [
            _money(row["close"])
            for row in self._rows(symbol, 420)
            if row.get("close")
        ]

    def get_bars(self, symbol: str, days: int = 60) -> list[dict]:
        bars = []
        # days = จำนวนวันทำการ ต้องเผื่อเป็นวันปฏิทิน (~1.6 เท่า) ตอนขอจาก API
        for row in self._rows(symbol, max(int(days * 1.6), 120)):
            if not row.get("close"):
                continue
            bars.append(
                {
                    "date": row.get("date"),
                    "close": _money(row["close"]),
                    "volume": int(_money(row.get("volume") or 0)) or None,
                }
            )
        return bars[-days:]


class MockHistoryProvider(HistoryProvider):
    """สร้างประวัติราคาแบบ deterministic สำหรับ dev/offline."""

    _profiles = {
        "AAPL": (180.0, 0.0007),
        "MSFT": (380.0, 0.0008),
        "NVDA": (95.0, 0.0018),
        "GOOGL": (150.0, 0.0006),
        "META": (480.0, 0.0011),
        "AVGO": (1400.0, 0.0012),
        "LLY": (700.0, 0.0009),
        "JPM": (190.0, 0.0004),
    }

    def get_closes(self, symbol: str) -> list[float]:
        start, drift = self._profiles.get(symbol, (100.0, 0.0002))
        # เดินราคาแบบ deterministic (sine + drift) ให้ได้เทรนด์ที่ต่างกันต่อ symbol
        import math

        closes = []
        seed = sum(ord(ch) for ch in symbol)
        for day in range(_TRADING_DAYS_YEAR):
            wave = math.sin((day + seed) / 18.0) * 0.04
            closes.append(start * (1 + drift * day + wave))
        return closes


class FallbackHistoryProvider(HistoryProvider):
    def __init__(self, primary: HistoryProvider, fallback: HistoryProvider) -> None:
        self.primary = primary
        self.fallback = fallback

    def get_closes(self, symbol: str) -> list[float]:
        try:
            return self.primary.get_closes(symbol)
        except Exception:
            return self.fallback.get_closes(symbol)

    def get_bars(self, symbol: str, days: int = 60) -> list[dict]:
        try:
            return self.primary.get_bars(symbol, days)
        except Exception:
            return self.fallback.get_bars(symbol, days)

    def get_metrics(self, symbol: str) -> LongTermMetrics:
        try:
            return self.primary.get_metrics(symbol)
        except Exception:
            return self.fallback.get_metrics(symbol)


def get_history_provider(use_live_data: bool = False) -> HistoryProvider:
    settings = get_settings()
    allow_mock = settings.allow_mock_data and settings.app_env != "production"
    if use_live_data:
        # Nasdaq เป็นแหล่งหลัก (เสถียร, ฟรี) แล้ว fallback ไป Yahoo/Stooq
        return FallbackHistoryProvider(
            NasdaqHistoryProvider(),
            FallbackHistoryProvider(YahooHistoryProvider(), StooqHistoryProvider()),
        )
    if allow_mock:
        return MockHistoryProvider()
    raise RuntimeError("Mock history data is disabled")
