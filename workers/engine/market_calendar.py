"""ปฏิทินตลาดหุ้นสหรัฐ (NYSE/Nasdaq) — เวลาตลาดคิดเป็น America/New_York เสมอ.

ใช้ตัดสินว่า "ข้อมูลล่าสุดควรเป็นของวันไหน" เพื่อบอกได้ตรง ๆ ว่าข้อมูล STALE หรือไม่
วันหยุดอ้างอิงประกาศของ NYSE (https://www.nyse.com/markets/hours-calendars)
ปีที่ไม่มีในตารางจะรู้แค่เสาร์-อาทิตย์ จึงรายงาน holiday_calendar_covered=False
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

NYSE_HOLIDAYS: set[date] = {
    date(2024, 1, 1), date(2024, 1, 15), date(2024, 2, 19), date(2024, 3, 29),
    date(2024, 5, 27), date(2024, 6, 19), date(2024, 7, 4), date(2024, 9, 2),
    date(2024, 11, 28), date(2024, 12, 25),
    date(2025, 1, 1), date(2025, 1, 9), date(2025, 1, 20), date(2025, 2, 17),
    date(2025, 4, 18), date(2025, 5, 26), date(2025, 6, 19), date(2025, 7, 4),
    date(2025, 9, 1), date(2025, 11, 27), date(2025, 12, 25),
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
    date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
    date(2027, 11, 25), date(2027, 12, 24),
}
# วันที่ปิดเร็ว 13:00 ET
NYSE_EARLY_CLOSES: set[date] = {
    date(2024, 7, 3), date(2024, 11, 29), date(2024, 12, 24),
    date(2025, 7, 3), date(2025, 11, 28), date(2025, 12, 24),
    date(2026, 11, 27), date(2026, 12, 24),
    date(2027, 11, 26),
}
_COVERED_YEARS = {2024, 2025, 2026, 2027}

REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)
PRE_MARKET_OPEN = time(4, 0)
AFTER_HOURS_CLOSE = time(20, 0)


def is_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in NYSE_HOLIDAYS


def close_time(day: date) -> time:
    return EARLY_CLOSE if day in NYSE_EARLY_CLOSES else REGULAR_CLOSE


def previous_trading_day(day: date) -> date:
    candidate = day - timedelta(days=1)
    while not is_trading_day(candidate):
        candidate -= timedelta(days=1)
    return candidate


def trading_days_between(start: date, end: date) -> list[date]:
    """วันทำการตั้งแต่ start ถึง end (รวมทั้งสองฝั่ง)."""
    days = []
    current = start
    while current <= end:
        if is_trading_day(current):
            days.append(current)
        current += timedelta(days=1)
    return days


def last_completed_session(now: datetime | None = None) -> date:
    """วันทำการล่าสุดที่ปิดตลาดไปแล้ว — แท่ง daily ที่ "ควร" มีอยู่แล้ว."""
    now_ny = (now or datetime.now(timezone.utc)).astimezone(NY)
    today = now_ny.date()
    if is_trading_day(today) and now_ny.time() >= close_time(today):
        return today
    return previous_trading_day(today)


def market_session(now: datetime | None = None) -> dict:
    now_utc = now or datetime.now(timezone.utc)
    now_ny = now_utc.astimezone(NY)
    today = now_ny.date()
    current = now_ny.time()
    if not is_trading_day(today):
        status, label = "closed", "ตลาดปิด (วันหยุด/เสาร์-อาทิตย์)"
    elif current < PRE_MARKET_OPEN:
        status, label = "closed", "ตลาดปิด"
    elif current < REGULAR_OPEN:
        status, label = "pre_market", "ช่วง Pre-market"
    elif current < close_time(today):
        status, label = "open", "ตลาดเปิด (Regular session)"
    elif current < AFTER_HOURS_CLOSE:
        status, label = "after_hours", "ช่วง After-hours"
    else:
        status, label = "closed", "ตลาดปิด"
    return {
        "market": "US",
        "timezone": "America/New_York",
        "status": status,
        "label": label,
        "now_market_time": now_ny.isoformat(),
        "last_completed_session": last_completed_session(now_utc).isoformat(),
        "is_early_close": today in NYSE_EARLY_CLOSES,
        "holiday_calendar_covered": today.year in _COVERED_YEARS,
    }


def session_close_utc(day: date) -> datetime:
    """เวลาปิดตลาดของวันนั้นเป็น UTC — ใช้เป็น market timestamp ของแท่ง daily."""
    return datetime.combine(day, close_time(day), tzinfo=NY).astimezone(timezone.utc)


def calendar_covers(day: date) -> bool:
    return day.year in _COVERED_YEARS
