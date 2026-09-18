"""Technical indicators แบบ series — ค่า ณ index t ใช้ข้อมูลถึง t เท่านั้น (causal).

ออกแบบให้ backtest เรียกซ้ำได้โดยไม่มี look-ahead bias: คำนวณทั้ง series ครั้งเดียว
แล้วอ่านค่า ณ วันที่ต้องการ ค่าที่ข้อมูลยังไม่พอคำนวณคือ None (ไม่เติมค่าเดา)
สูตร RSI/ATR ใช้ Wilder smoothing ตามนิยามต้นฉบับ
"""
from __future__ import annotations

import math
from typing import List, Optional

Series = List[Optional[float]]


def sma(values: list[float], period: int) -> Series:
    out: Series = [None] * len(values)
    running = 0.0
    for i, value in enumerate(values):
        running += value
        if i >= period:
            running -= values[i - period]
        if i >= period - 1:
            out[i] = running / period
    return out


def ema(values: list[float], period: int) -> Series:
    """EMA seed ด้วย SMA ของ period แรก (มาตรฐานเดียวกับแพลตฟอร์มกราฟทั่วไป)."""
    out: Series = [None] * len(values)
    if len(values) < period:
        return out
    k = 2 / (period + 1)
    current = sum(values[:period]) / period
    out[period - 1] = current
    for i in range(period, len(values)):
        current = (values[i] - current) * k + current
        out[i] = current
    return out


def _ema_of_optional(values: Series, period: int) -> Series:
    out: Series = [None] * len(values)
    start = next((i for i, v in enumerate(values) if v is not None), None)
    if start is None:
        return out
    tail = [v for v in values[start:]]
    if any(v is None for v in tail):
        return out
    sub = ema(tail, period)  # type: ignore[arg-type]
    out[start:] = sub
    return out


def rsi(closes: list[float], period: int = 14) -> Series:
    out: Series = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain, avg_loss = gains / period, losses / period
    out[period] = _rsi_value(avg_gain, avg_loss)
    for i in range(period + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        out[i] = _rsi_value(avg_gain, avg_loss)
    return out


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[Series, Series, Series]:
    fast_ema, slow_ema = ema(closes, fast), ema(closes, slow)
    line: Series = [
        (f - s) if f is not None and s is not None else None for f, s in zip(fast_ema, slow_ema)
    ]
    signal_line = _ema_of_optional(line, signal)
    hist: Series = [
        (m - s) if m is not None and s is not None else None for m, s in zip(line, signal_line)
    ]
    return line, signal_line, hist


def true_range(highs: list[float | None], lows: list[float | None], closes: list[float]) -> Series:
    out: Series = [None] * len(closes)
    for i in range(len(closes)):
        high, low = highs[i], lows[i]
        if high is None or low is None:
            continue
        if i == 0:
            out[i] = high - low
        else:
            prev = closes[i - 1]
            out[i] = max(high - low, abs(high - prev), abs(low - prev))
    return out


def atr(highs: list[float | None], lows: list[float | None], closes: list[float], period: int = 14) -> Series:
    tr = true_range(highs, lows, closes)
    out: Series = [None] * len(closes)
    window: list[float] = []
    current: float | None = None
    for i, value in enumerate(tr):
        if value is None:
            # แท่งที่ไม่มี high/low → ATR ต่อไม่ได้อย่างซื่อตรง เริ่มนับใหม่
            window, current = [], None
            continue
        if current is None:
            window.append(value)
            if len(window) == period:
                current = sum(window) / period
                out[i] = current
        else:
            current = (current * (period - 1) + value) / period
            out[i] = current
    return out


def bollinger(closes: list[float], period: int = 20, width: float = 2.0) -> tuple[Series, Series, Series]:
    middle = sma(closes, period)
    upper: Series = [None] * len(closes)
    lower: Series = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        mean = middle[i]
        std = math.sqrt(sum((x - mean) ** 2 for x in window) / period)  # type: ignore[operator]
        upper[i] = mean + width * std  # type: ignore[operator]
        lower[i] = mean - width * std  # type: ignore[operator]
    return upper, middle, lower


def pct_return(closes: list[float], index: int, lookback: int) -> float | None:
    if index - lookback < 0:
        return None
    past = closes[index - lookback]
    return (closes[index] - past) / past * 100 if past > 0 else None


def rolling_max(values: list[float | None], end_exclusive: int, window: int) -> float | None:
    start = max(0, end_exclusive - window)
    chunk = [v for v in values[start:end_exclusive] if v is not None]
    return max(chunk) if chunk else None


def rolling_min(values: list[float | None], end_exclusive: int, window: int) -> float | None:
    start = max(0, end_exclusive - window)
    chunk = [v for v in values[start:end_exclusive] if v is not None]
    return min(chunk) if chunk else None


def stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def daily_returns(closes: list[float], end_inclusive: int, window: int) -> list[float]:
    start = max(1, end_inclusive - window + 1)
    return [
        closes[i] / closes[i - 1] - 1
        for i in range(start, end_inclusive + 1)
        if closes[i - 1] > 0
    ]


def max_drawdown(closes: list[float], end_inclusive: int, window: int) -> float | None:
    start = max(0, end_inclusive - window + 1)
    chunk = closes[start : end_inclusive + 1]
    if len(chunk) < 2:
        return None
    peak, worst = chunk[0], 0.0
    for value in chunk:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return worst * 100
