"""แท่งราคารายวัน (OHLCV) จริงจาก Nasdaq พร้อมตรวจคุณภาพข้อมูล.

ข้อเท็จจริงของแหล่งข้อมูล (ต้องแสดงให้ผู้ใช้รู้):
- Nasdaq historical เป็นราคา EOD ที่ปรับ split แล้ว แต่ไม่ได้ปรับเงินปันผล (price return)
- แท่งของวันปัจจุบันจะมีหลังตลาดปิด — ระหว่างวันจึงคำนวณจากแท่งที่ปิดแล้วเท่านั้น
- สกุลเงิน USD
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from workers.engine import market_calendar as cal
from workers.engine import sources, store
from workers.engine.runtime import CACHE, ProviderUnavailableError, SymbolNotFoundError, iso, utcnow

logger = logging.getLogger(__name__)

SOURCE = "nasdaq_historical"
ADJUSTMENT_NOTE = "split-adjusted by provider; not dividend-adjusted (price return)"
DEFAULT_LOOKBACK_DAYS = 800  # ~2.2 ปีปฏิทิน ≈ 550 แท่ง: พอสำหรับ SMA200, 1Y และ backtest ย้อนหลัง


@dataclass(frozen=True)
class Bar:
    day: date
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: int | None

    def to_list(self) -> list:
        return [self.day.isoformat(), self.open, self.high, self.low, self.close, self.volume]

    @staticmethod
    def from_list(row: list) -> "Bar":
        return Bar(date.fromisoformat(row[0]), row[1], row[2], row[3], row[4], row[5])


@dataclass
class BarSeries:
    symbol: str
    bars: list[Bar]
    source: str
    fetched_at: datetime
    asset_class: str | None = None
    quality: dict = field(default_factory=dict)

    @property
    def last_bar_date(self) -> date | None:
        return self.bars[-1].day if self.bars else None

    def freshness(self, now: datetime | None = None) -> dict:
        expected = cal.last_completed_session(now)
        last = self.last_bar_date
        lag = None
        if last:
            lag = len(cal.trading_days_between(last + timedelta(days=1), expected))
        # Nasdaq ปล่อยแท่งวันนั้นหลังปิดตลาดไม่นาน — ยอมให้ช้า 1 session ในช่วงหัวค่ำ
        return {
            "source": self.source,
            "fetched_at": iso(self.fetched_at),
            "market_timestamp": iso(cal.session_close_utc(last)) if last else None,
            "last_bar_date": last.isoformat() if last else None,
            "expected_last_session": expected.isoformat(),
            "sessions_behind": lag,
            "is_stale": lag is None or lag > 1,
            "data_type": "end_of_day",
            "is_realtime": False,
            "adjustment": ADJUSTMENT_NOTE,
            "currency": "USD",
        }


def _num(value) -> float | None:
    if value is None:
        return None
    text = str(value).replace("$", "").replace(",", "").strip()
    if text in {"", "N/A", "--", "-"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number


def parse_nasdaq_rows(rows: list[dict]) -> tuple[list[Bar], dict]:
    """แปลง + ตรวจแถวจาก Nasdaq: ตัดซ้ำ, ตัดแถวเสีย, เรียงเก่า→ใหม่, หาแท่งที่ขาด/split."""
    by_day: dict[date, Bar] = {}
    invalid = duplicates = 0
    for row in rows:
        try:
            day = datetime.strptime(str(row.get("date", "")).strip(), "%m/%d/%Y").date()
        except ValueError:
            invalid += 1
            continue
        close = _num(row.get("close"))
        if close is None or close <= 0:
            invalid += 1
            continue
        open_, high, low = _num(row.get("open")), _num(row.get("high")), _num(row.get("low"))
        volume = _num(row.get("volume"))
        # OHLC ต้องสอดคล้อง: low <= open,close <= high — ถ้าไม่ ให้ทิ้ง high/low (ไม่แต่งตัวเลขเอง)
        if high is not None and low is not None:
            if low > high or close > high * 1.001 or close < low * 0.999:
                high = low = None
                invalid += 1
        if day in by_day:
            duplicates += 1
        by_day[day] = Bar(day, open_, high, low, close, int(volume) if volume is not None and volume >= 0 else None)

    bars = [by_day[day] for day in sorted(by_day)]
    missing: list[str] = []
    if len(bars) >= 2:
        present = {bar.day for bar in bars}
        for day in cal.trading_days_between(bars[0].day, bars[-1].day):
            if cal.calendar_covers(day) and day not in present:
                missing.append(day.isoformat())

    splits = []
    for previous, current in zip(bars, bars[1:]):
        ratio = current.close / previous.close
        if ratio < 0.55 or ratio > 1.8:
            factor = ratio if ratio > 1 else 1 / ratio
            nearest = min((2, 3, 4, 5, 8, 10, 15, 20, 25, 50), key=lambda k: abs(factor - k))
            if abs(factor - nearest) / nearest < 0.04:
                splits.append({"date": current.day.isoformat(), "close_ratio": round(ratio, 4), "looks_like": f"1:{nearest}" if ratio > 1 else f"{nearest}:1"})

    return bars, {
        "rows_received": len(rows),
        "bars": len(bars),
        "duplicates_removed": duplicates,
        "invalid_rows": invalid,
        "missing_sessions": len(missing),
        "missing_sessions_sample": missing[-5:],
        "possible_unadjusted_splits": splits[-3:],
    }


def _ttl_for(series: BarSeries) -> float:
    fresh = not series.freshness()["is_stale"]
    if not fresh:
        return 20 * 60.0
    # แท่งครบแล้ว → เก็บได้จนถึงหลังปิดตลาด session ถัดไป (สูงสุด 12 ชม.)
    now = utcnow()
    today_ny = now.astimezone(cal.NY).date()
    next_session = today_ny
    while not cal.is_trading_day(next_session) or cal.session_close_utc(next_session) <= now:
        next_session += timedelta(days=1)
    seconds = (cal.session_close_utc(next_session) - now).total_seconds() + 30 * 60
    return max(10 * 60.0, min(12 * 3600.0, seconds))


def _fetch(symbol: str, lookback_days: int) -> BarSeries:
    today = datetime.now(timezone.utc).date()
    payload, asset_class = sources.nasdaq_asset_json(
        "quote/{symbol}/historical",
        symbol,
        {
            "fromdate": (today - timedelta(days=lookback_days)).isoformat(),
            "todate": today.isoformat(),
            "limit": 9999,
        },
        timeout=20,
    )
    if asset_class is None:
        raise SymbolNotFoundError(f"Nasdaq has no data for symbol {symbol.upper()}")
    rows = (((payload.get("data") or {}).get("tradesTable") or {}).get("rows")) or []
    bars, quality = parse_nasdaq_rows(rows)
    if not bars:
        raise ProviderUnavailableError(sources.NASDAQ, f"no daily history for {symbol}")
    return BarSeries(symbol.upper(), bars, SOURCE, utcnow(), asset_class, quality)


def get_daily_bars(symbol: str, lookback_days: int = DEFAULT_LOOKBACK_DAYS, *, use_db_cache: bool = True) -> BarSeries:
    symbol = symbol.upper().strip()
    key = f"bars:{symbol}:{lookback_days}"
    hit, cached = CACHE.get(key)
    if hit:
        return cached

    expected = cal.last_completed_session()
    if use_db_cache and lookback_days == DEFAULT_LOOKBACK_DAYS:
        row = store.load_bar_cache(symbol)
        if row and row["last_bar_date"] >= expected:
            bars = [Bar.from_list(item) for item in row["bars"]]
            fetched_at = row["fetched_at"]
            series = BarSeries(symbol, bars, row["source"], fetched_at, None, {"bars": len(bars), "from_db_cache": True})
            CACHE.set(key, series, _ttl_for(series))
            return series

    started = time.perf_counter()
    series = _fetch(symbol, lookback_days)
    logger.info(
        "bars_fetched",
        extra={
            "event": "stock_fetch",
            "symbol": symbol,
            "bars": len(series.bars),
            "missing_sessions": series.quality.get("missing_sessions"),
            "latency_ms": round((time.perf_counter() - started) * 1000),
        },
    )
    if series.quality.get("possible_unadjusted_splits") or series.quality.get("invalid_rows"):
        logger.warning(
            "data_validation_warning",
            extra={"event": "data_validation", "symbol": symbol, "quality": series.quality},
        )
    CACHE.set(key, series, _ttl_for(series))
    if lookback_days == DEFAULT_LOOKBACK_DAYS and series.last_bar_date:
        store.save_bar_cache(symbol, series.last_bar_date, [bar.to_list() for bar in series.bars], series.source)
    return series
