"""ประกอบการวิเคราะห์หุ้นรายตัวครบชุด (ใช้ทั้งหน้า Stock Detail และรอบสุดท้ายของ Discovery)."""
from __future__ import annotations

import logging
from datetime import date

from workers.engine import analysis as an
from workers.engine import market_calendar as cal
from workers.engine.bars import BarSeries, get_daily_bars
from workers.engine.fundamentals import get_fundamentals, get_quote
from workers.engine.market_context import get_market_context, sector_context
from workers.engine.news import get_news
from workers.engine.runtime import ProviderUnavailableError, iso, utcnow
from workers.engine.strategies import STRATEGIES, Strategy

logger = logging.getLogger(__name__)


def _optional(future, part: str, unavailable: list):
    try:
        return future.result()
    except ProviderUnavailableError as exc:
        unavailable.append({"part": part, "provider": exc.provider, "error": str(exc)})
        return None


def benchmark_closes() -> dict:
    try:
        spy = get_daily_bars("SPY")
    except ProviderUnavailableError:
        return {}
    return {bar.day: bar.close for bar in spy.bars}


def technical_for(series: BarSeries, bench: dict | None = None) -> dict:
    ts = an.TechnicalSeries.build(series.bars)
    return ts.snapshot_at(len(series.bars) - 1, bench)


def days_until(day_iso: str | None) -> int | None:
    if not day_iso:
        return None
    try:
        target = date.fromisoformat(day_iso)
    except ValueError:
        return None
    today = utcnow().astimezone(cal.NY).date()
    return (target - today).days


def score_all(t: dict, f: dict | None, news: dict | None, market_condition: str | None, strategy: Strategy) -> dict:
    signals = an.detect_signals(t, f, news)
    days_to_earn = days_until(((f or {}).get("next_earnings_date") or {}).get("value"))
    risk = an.assess_risk(t, f, market_condition, days_to_earn)
    components = {
        "trend": an.score_trend(t),
        "momentum": an.score_momentum(t),
        "volume": an.score_volume(t),
        "fundamental": an.score_fundamental(f),
        "risk": {"score": risk["score"], "max": risk["max"], "rules": [
            {"rule": item["factor"], "points": -item["penalty"], "max": 0, "value": item["value"]} for item in risk["factors"]
        ]},
        "catalyst": an.score_catalyst(t, f, news, signals),
    }
    return {
        "signals": signals,
        "risk": risk,
        "components": components,
        "score": an.weighted_score(components, strategy.weights),
        "days_to_earnings": days_to_earn,
    }


def analyze_symbol(symbol: str, strategy_id: str = "balanced") -> dict:
    """วิเคราะห์เต็มรูปแบบหนึ่งหุ้น — ส่วนไหนดึงไม่ได้จะระบุสถานะ unavailable ไม่เดาค่า."""
    symbol = symbol.upper().strip()
    strategy = STRATEGIES.get(strategy_id, STRATEGIES["balanced"])
    generated = utcnow()
    unavailable: list[dict] = []

    from concurrent.futures import ThreadPoolExecutor

    # แหล่งข้อมูลแต่ละส่วนอิสระต่อกัน → ดึงพร้อมกัน (เดิมเรียงทีละส่วน ~40 วินาที)
    with ThreadPoolExecutor(max_workers=6) as pool:
        f_series = pool.submit(get_daily_bars, symbol)
        f_bench = pool.submit(benchmark_closes)
        f_quote = pool.submit(get_quote, symbol)
        f_fund = pool.submit(get_fundamentals, symbol, None)  # ดึงข้อมูลดิบเข้าแคชก่อน
        f_market = pool.submit(get_market_context)
        series = f_series.result()  # ไม่มีราคาเลย → ปล่อย error ขึ้นไป (API ตอบ 503 พร้อมสถานะ provider)
        quote = _optional(f_quote, "quote", unavailable)
        f_news = pool.submit(get_news, symbol, (quote or {}).get("company_name"))
        bench = f_bench.result()
        _optional(f_fund, "fundamentals", unavailable)
        market = _optional(f_market, "market_context", unavailable)
        news = f_news.result()

    t = technical_for(series, bench)
    freshness = series.freshness()
    price_for_valuation = (quote or {}).get("price", {}).get("value") or t["price"]
    fundamentals = None
    if not any(item["part"] == "fundamentals" for item in unavailable):
        fundamentals = get_fundamentals(symbol, price_for_valuation)  # จากแคช + คำนวณ P/E ด้วยราคาล่าสุด
    if news["status"] == "unavailable":
        unavailable.append({"part": "news", "provider": "nasdaq_rss/google_news_rss", "error": "; ".join(news["errors"])})

    sector = None
    if fundamentals:
        sector = sector_context(
            (fundamentals.get("sector") or {}).get("value"),
            (fundamentals.get("industry") or {}).get("value"),
            t["returns"].get("1m"),
        )

    scored = score_all(t, fundamentals, news, (market or {}).get("condition"), strategy)
    mtf = an.multi_timeframe(t)
    confidence = an.assess_confidence(t, fundamentals, news, freshness, scored["signals"], mtf)

    financial_as_of = ((fundamentals or {}).get("fiscal_period_end") or {}).get("value")
    fin_age = ((fundamentals or {}).get("financials_age_days") or {}).get("value")
    news_fetched = news.get("fetched_at")
    data_freshness = {
        "price_eod": freshness,
        "quote": {
            "market_timestamp": (quote or {}).get("market_timestamp"),
            "fetched_at": (quote or {}).get("fetched_at"),
            "is_realtime": (quote or {}).get("is_realtime"),
            "delay_note": (quote or {}).get("delay_note"),
            "status": "ok" if quote else "unavailable",
        },
        "financials": {
            "period_end": financial_as_of,
            "fetched_at": (fundamentals or {}).get("market_cap", {}).get("fetched_at"),
            # งบปีเก่ากว่า 15 เดือน = ยังไม่มีงบปีใหม่ (ปกติ) แต่ต้องบอกผู้ใช้
            "is_stale": fin_age is not None and fin_age > 456,
            "status": "ok" if fundamentals else "unavailable",
        },
        "news": {"fetched_at": news_fetched, "status": news["status"]},
        "market_session": cal.market_session(),
    }

    return {
        "symbol": symbol,
        "company_name": (quote or {}).get("company_name"),
        "exchange": (quote or {}).get("exchange"),
        "currency": "USD",
        "strategy": {"id": strategy.id, "name": strategy.name, "weights": strategy.weights},
        "quote": quote,
        "technical": t,
        "data_quality": series.quality,
        "fundamentals": fundamentals,
        "news": news,
        "market_context": market,
        "sector_context": sector,
        "signals": scored["signals"],
        "components": scored["components"],
        "opportunity_score": scored["score"],
        "risk": scored["risk"],
        "confidence": confidence,
        "multi_timeframe": mtf,
        "key_levels": an.key_levels(t),
        "days_to_earnings": scored["days_to_earnings"],
        "data_freshness": data_freshness,
        "unavailable": unavailable,
        "generated_at": iso(generated),
    }
