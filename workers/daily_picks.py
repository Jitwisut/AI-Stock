"""หุ้นน่าสนใจวันนี้ — คัดหุ้นที่โครงสร้างราคา + ข่าว บอกว่ามีโอกาสวิ่งแรงระยะสั้น.

ข้อจำกัดที่ต้องพูดตรง ๆ: ข้อมูลทั้งหมดเป็น daily EOD ไม่ใช่ intraday ดังนั้น
"โอกาสพุ่ง" ที่คำนวณได้คือโอกาสจากโครงสร้างราคา/วอลุ่ม/ข่าวล่าสุด ไม่ใช่การ
ทำนายว่าราคาจะขึ้นภายในวันนี้จริง ๆ — ทุกจุดที่แสดงผลต้องสื่อสารแบบนั้น

ทางอ่านหลักคือคำนวณสด + cache ในหน่วยความจำ (ไม่ใช่ DB) เพราะ production ตั้ง
SAVE_SIGNALS=false ทำให้ตาราง signals ไม่มีข้อมูลใหม่เลย
"""

from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from workers.config import get_settings
from workers.data_models import MarketSnapshot, NewsItem, Stock
from workers.news.fetch_news import get_news_provider
from workers.pick_scoring import (
    RUBRIC_SCHEMA,
    build_rubric_prompt,
    features_from_bars,
    market_context_from_bars,
    parse_rubric_response,
    rule_score,
    verdict_for_score,
)
from workers.scanner.fetch_history import get_history_provider
from workers.scanner.fetch_prices import get_market_data_provider
from workers.scanner.fetch_stocks import fetch_stock_universe

logger = logging.getLogger(__name__)

_PICKS_CACHE: dict = {"ts": 0.0, "data": None}
# (วันทำการ, symbol) ที่ AI ตอบกลับมาแล้ว — กันถามซ้ำตัวที่ข้อมูลไม่พอให้คะแนน
_AI_ASKED: set = set()
_BACKGROUND_TASKS: set = set()
_PICKS_TTL_SECONDS = 45 * 60.0  # 45 นาที — ข้อมูล daily ไม่เปลี่ยนถี่กว่านี้อยู่แล้ว

# จำนวนหุ้นที่ส่งให้ Gemini ตัดสิน — มากกว่านี้ prompt ยาวจนคุณภาพตก + เสี่ยง rate limit
_AI_CANDIDATE_LIMIT = 12
# จำนวนหุ้นที่ผ่านการคัดหยาบไปดึงประวัติราคา 1 ปี (ขั้นที่แพงที่สุด)
_PRESCREEN_LIMIT = 25
# ประวัติที่ดึง (วันทำการ) — 420 พอสำหรับ SMA200 + ช่วง 52 สัปดาห์
_HISTORY_DAYS = 420
# จำนวนแท่งที่ส่งให้หน้าเว็บวาดกราฟราคา+วอลุ่ม
_CHART_DAYS = 60
_SPARK_TICKS = "▁▂▃▄▅▆▇█"


def sparkline(values: list[float], width: int = 24) -> str:
    """กราฟราคาแบบ text ใช้ได้ทั้งใน Telegram และเว็บ (ไม่ต้องพึ่ง service ภายนอก)."""
    points = [float(v) for v in values if v is not None][-width:]
    if len(points) < 2:
        return ""
    low, high = min(points), max(points)
    span = high - low
    if span <= 0:
        return _SPARK_TICKS[len(_SPARK_TICKS) // 2] * len(points)
    return "".join(
        _SPARK_TICKS[min(len(_SPARK_TICKS) - 1, int((p - low) / span * (len(_SPARK_TICKS) - 1)))]
        for p in points
    )


def _prescreen_score(snapshot: MarketSnapshot) -> float:
    """คะแนนหยาบจาก snapshot อย่างเดียว (ไม่ต้องโหลดประวัติ 1 ปี) ใช้คัดตัวก่อน.

    ขั้นนี้มีไว้ตัดจำนวนหุ้นที่ต้องดึงประวัติ จาก ~150 ตัวเหลือ ~25 ตัว ซึ่งเป็น
    ส่วนที่ทำให้การสร้างหน้า "หุ้นน่าสนใจวันนี้" ช้าที่สุด
    """
    change = (
        (snapshot.latest_price - snapshot.previous_close) / snapshot.previous_close * 100
        if snapshot.previous_close > 0
        else 0.0
    )
    breakout_gap = (
        (snapshot.latest_price - snapshot.prior_high) / snapshot.prior_high * 100
        if snapshot.prior_high > 0
        else 0.0
    )
    # ไม่ให้คะแนนวอลุ่มในขั้นนี้: snapshot ที่ใช้มาจาก nasdaq_quote ซึ่งตั้ง
    # average_volume_20d = วอลุ่มวันนี้ → relative volume = 1.00 ทุกตัว คิดไปก็ไม่มีผล
    # วอลุ่มจริงคิดจากแท่งราคาในขั้นที่สอง
    score = min(60.0, max(0.0, change) * 8)
    if breakout_gap > -3:
        score += 20
    if snapshot.latest_price > snapshot.previous_close:
        score += 10
    return score


def _enrich_candidate(stock: Stock, spy_bars: list[dict]) -> dict | None:
    """ดึงแท่งราคา 1 ปีกว่า แล้วคำนวณฟีเจอร์ทั้งหมดจากแท่ง (ราคาปิด EOD).

    ไม่ใช้ราคา quote สด: ระหว่างตลาดเปิดราคานั้นเป็นราคาระหว่างวัน ซึ่งจะทำให้
    คะแนนวันนี้กับการวัดผลย้อนหลัง (ใช้ราคาปิด) ไม่ได้อยู่บนฐานเดียวกัน
    """
    settings = get_settings()
    history = get_history_provider(use_live_data=settings.use_live_data)
    try:
        bars = history.get_bars(stock.symbol, days=_HISTORY_DAYS)
    except Exception as exc:
        logger.debug("daily picks: skip %s (%s)", stock.symbol, exc)
        return None
    features = features_from_bars(bars, spy_bars)
    if features is None:
        return None
    return {
        "stock": stock,
        "features": features,
        "rule_score": rule_score(features),
        "closes": [bar["close"] for bar in bars if bar.get("close")],
        "bars": bars[-_CHART_DAYS:],
    }


def _days_to_earnings(symbol: str) -> int | None:
    from workers.scanner.fetch_earnings import fetch_earnings

    try:
        raw = fetch_earnings(symbol).get("earnings_date")
    except Exception:
        return None
    if not raw:
        return None
    for fmt in ("%b %d, %Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            when = datetime.strptime(str(raw).strip(), fmt).date()
        except ValueError:
            continue
        days = (when - datetime.now(timezone.utc).date()).days
        return days if days >= 0 else None
    return None


def _attach_context(candidates: list[dict]) -> None:
    """ข่าวล่าสุด (พร้อมอายุข่าว) + จำนวนวันถึงวันประกาศงบ ของตัวที่จะส่งให้ AI."""
    settings = get_settings()
    news_provider = get_news_provider(use_live_data=settings.use_live_data)
    now = datetime.now(timezone.utc)

    def _fetch(candidate: dict) -> tuple[list[NewsItem], int | None]:
        symbol = candidate["stock"].symbol
        try:
            news = news_provider.get_company_news(symbol)[:3]
        except Exception:
            news = []
        return news, _days_to_earnings(symbol)

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(candidates)))) as executor:
        for candidate, (news, earnings_days) in zip(candidates, executor.map(_fetch, candidates)):
            candidate["news"] = news
            candidate["days_to_earnings"] = earnings_days
            candidate["news_for_ai"] = [
                {
                    "title": item.title,
                    "source": item.source,
                    "age_hours": _news_age_hours(item, now),
                }
                for item in news
            ]


def _news_age_hours(item: NewsItem, now: datetime) -> float | None:
    published = item.published_at
    if not isinstance(published, datetime):
        return None
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return round((now - published).total_seconds() / 3600, 1)


async def _ai_rubric(candidates: list[dict], market_context: dict) -> tuple[dict[str, dict], dict]:
    """ให้ Gemini ให้คะแนนตามเกณฑ์ — ครั้งเดียวสำหรับทุกตัวที่ยังไม่มีคะแนนของวันทำการนี้.

    คืน (ผลต่อ symbol, สถานะ AI) — สถานะบอกตรง ๆ ว่าใช้ AI ได้ไหม รุ่นไหนตอบ
    และพังเพราะอะไร แทนที่จะตกไปใช้กฎแบบเงียบ ๆ เหมือนเดิม
    """
    settings = get_settings()
    if settings.ai_provider != "gemini" or not settings.gemini_api_key:
        return {}, {"status": "disabled", "model": None, "error": None}

    from workers.ai.gemini_client import generate_gemini
    from workers.engine.market_calendar import market_session
    from workers.pick_scoring import parse_bar_date
    from workers.pick_tracking import load_session_ai_scores

    symbols = [candidate["stock"].symbol for candidate in candidates]
    session = parse_bar_date(candidates[0]["features"].get("as_of")) if candidates else None
    market_open = market_session()["status"] == "open"
    cached = (
        await asyncio.to_thread(load_session_ai_scores, session, symbols, market_open=market_open)
        if session
        else {}
    )
    # ตัวที่ AI เคยตอบแล้วแต่ข้อมูลไม่พอ (ได้คะแนน < 3 เกณฑ์) จะได้ผลเดิมทุกครั้ง — ไม่ถามซ้ำในวันเดียวกัน
    pending = [
        candidate
        for candidate in candidates
        if candidate["stock"].symbol not in cached
        and (session, candidate["stock"].symbol) not in _AI_ASKED
    ]

    scored: dict[str, dict] = dict(cached)
    model = next((item.get("model") for item in cached.values() if item.get("model")), None)
    error = None
    if pending:
        prompt_candidates = [
            {
                "symbol": candidate["stock"].symbol,
                "name": candidate["stock"].name,
                "features": candidate["features"],
                "days_to_earnings": candidate.get("days_to_earnings"),
                "news": candidate.get("news_for_ai") or [],
            }
            for candidate in pending
        ]
        try:
            result = await generate_gemini(
                build_rubric_prompt(prompt_candidates, market_context),
                api_key=settings.gemini_api_key,
                model=settings.gemini_model,
                temperature=0.0,
                response_schema=RUBRIC_SCHEMA,
                thinking_level="low",
                timeout=120.0,
            )
            model = result.model
            fresh = parse_rubric_response(result.text, [c["symbol"] for c in prompt_candidates])
            _AI_ASKED.update((session, symbol) for symbol in fresh)
            scored.update({symbol: {**item, "model": result.model} for symbol, item in fresh.items()})
        except Exception as exc:
            logger.warning("daily picks: AI rubric failed (%s)", exc)
            error = str(exc)[:160]
    else:
        logger.info("daily picks: reused AI scores for session %s (no AI call)", session)

    usable = {symbol: item for symbol, item in scored.items() if item.get("ai_score") is not None}
    status = "ok" if len(usable) == len(symbols) else ("partial" if usable else "failed")
    if status != "ok" and error is None:
        missing = sorted(set(symbols) - set(usable))
        error = f"AI ให้คะแนนไม่ครบ: {', '.join(missing)}"
        logger.warning("daily picks: %s", error)
    return usable, {
        "status": status,
        "model": model if usable else None,
        "error": error,
        "reused": len(cached),
    }


def _rule_reason(features: dict) -> str:
    bits = []
    rel_vol = features.get("relative_volume")
    if isinstance(rel_vol, (int, float)) and rel_vol >= 1.5:
        bits.append(f"วอลุ่ม {rel_vol:.1f} เท่าของค่าเฉลี่ย")
    if (features.get("change_percent") or 0) > 0:
        bits.append(f"ราคาปิดบวก {features['change_percent']:.1f}%")
    if features.get("above_sma200"):
        bits.append("ยืนเหนือ SMA200")
    breakout = features.get("breakout_gap_percent")
    if breakout is not None and breakout > -2:
        bits.append("จ่อแนวต้านเดิม")
    return " · ".join(bits) if bits else "โครงสร้างราคายังไม่มีสัญญาณเด่น"


def _to_pick(candidate: dict, ai: dict | None) -> dict:
    features = candidate["features"]
    ai_score = ai["ai_score"] if ai else None
    score = ai_score if ai_score is not None else candidate["rule_score"]
    return {
        "symbol": candidate["stock"].symbol,
        "name": candidate["stock"].name,
        "sector": candidate["stock"].sector,
        "price": features["price"],
        "change_percent": features["change_percent"],
        "relative_volume": features.get("relative_volume"),
        # คะแนน 0-100 จากเกณฑ์ (AI ให้คะแนนรายเกณฑ์ โค้ดรวมเอง) — ไม่ใช่ความน่าจะเป็น
        "score": score,
        # ชื่อเดิม คงไว้หนึ่งรุ่นให้หน้าเว็บที่ยังไม่อัปเดตไม่พัง — ค่าเดียวกับ score
        "pop_probability": score,
        "ai_score": ai_score,
        "rule_score": candidate["rule_score"],
        "score_breakdown": ai["breakdown"] if ai else None,
        "verdict": verdict_for_score(score),
        "reason": (ai or {}).get("reason") or _rule_reason(features),
        "catalyst": (ai or {}).get("catalyst") or "ไม่พบ catalyst ชัดเจน",
        "risk": (ai or {}).get("risk") or "ราคาอาจย่อแรงถ้าวอลุ่มไม่ตามต่อ",
        "source": "gemini" if ai_score is not None else "rules",
        "days_to_earnings": candidate.get("days_to_earnings"),
        "rsi14": features.get("rsi14"),
        "above_sma50": features.get("above_sma50"),
        "above_sma200": features.get("above_sma200"),
        "position_in_52w_range_percent": features.get("position_in_52w_range_percent"),
        "breakout_gap_percent": features.get("breakout_gap_percent"),
        "return_1m_percent": features.get("return_1m_percent"),
        "return_3m_percent": features.get("return_3m_percent"),
        "rs_1m_vs_spy": features.get("rs_1m_vs_spy"),
        "volatility_percent": features.get("volatility_percent"),
        "as_of": features.get("as_of"),
        "closes_20d": features["closes_20d"],
        # แท่งรายวันสำหรับกราฟบนเว็บ (ราคา + วอลุ่มใช้แกนเวลาเดียวกัน)
        "bars": candidate.get("bars") or [],
        "sparkline": sparkline(candidate["closes"]),
        "news": [
            {"title": item.title, "url": item.url, "source": item.source}
            for item in candidate.get("news", [])
        ],
    }


def _collect_candidates(stocks: list) -> tuple[list[dict], list[dict]]:
    """ขั้น 1: คัดหยาบจากราคาล่าสุดทั้ง universe → ขั้น 2: ดึงแท่งราคาเฉพาะตัวที่ผ่าน.

    คืน (candidates, แท่งราคาของ SPY ใช้เป็นภาวะตลาด)
    """
    settings = get_settings()
    market_data = get_market_data_provider(use_live_data=settings.use_live_data)
    history = get_history_provider(use_live_data=settings.use_live_data)
    try:
        spy_bars = history.get_bars("SPY", days=_HISTORY_DAYS)
    except Exception as exc:
        logger.warning("daily picks: SPY history unavailable (%s)", exc)
        spy_bars = []

    snapshots = market_data.get_snapshots([stock.symbol for stock in stocks])
    ranked = sorted(
        (stock for stock in stocks if stock.symbol.upper() in snapshots),
        key=lambda stock: _prescreen_score(snapshots[stock.symbol.upper()]),
        reverse=True,
    )[:_PRESCREEN_LIMIT]

    workers_count = max(1, min(settings.scan_workers, len(ranked) or 1))
    with ThreadPoolExecutor(max_workers=workers_count) as executor:
        candidates = [
            item for item in executor.map(lambda stock: _enrich_candidate(stock, spy_bars), ranked) if item
        ]
    for rank, candidate in enumerate(candidates, 1):
        candidate["prescreen_rank"] = rank
    return candidates, spy_bars


async def build_daily_picks(limit: int = 10) -> dict:
    """คำนวณรายการหุ้นน่าสนใจวันนี้ (สด ไม่พึ่ง DB) — สองขั้น: คัดหยาบ แล้วค่อยเจาะลึก."""
    stocks = fetch_stock_universe(mode="default")
    # ดึงราคาทั้ง universe + ประวัติ 1 ปี + ข่าว เป็น I/O แบบ blocking ~50 วินาที
    # ต้องทำในเธรดแยก ไม่งั้นทั้ง server (webhook บอท + หน้าเว็บ) ค้างระหว่างนั้น
    collected, spy_bars = await asyncio.to_thread(_collect_candidates, stocks)

    if not collected:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "analysis_source": "none",
            "ai_status": "skipped",
            "picks": [],
            "universe_count": len(stocks),
            "disclaimer": _DISCLAIMER,
        }

    collected.sort(key=lambda item: item["rule_score"], reverse=True)
    shortlist = collected[:_AI_CANDIDATE_LIMIT]
    await asyncio.to_thread(_attach_context, shortlist)

    market_context = market_context_from_bars(spy_bars)
    scored, ai_info = await _ai_rubric(shortlist, market_context)
    picks = [_to_pick(candidate, scored.get(candidate["stock"].symbol)) for candidate in shortlist]
    picks.sort(key=lambda item: item["score"], reverse=True)

    # เก็บผลทุกรอบ (ทั้ง 25 ตัวที่ผ่านด่านแรก ไม่ใช่แค่ตัวที่แสดง) ไว้วัดผลย้อนหลัง
    from workers.pick_tracking import save_pick_snapshots

    await asyncio.to_thread(save_pick_snapshots, collected, picks, ai_info)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "analysis_source": "gemini" if ai_info["status"] in ("ok", "partial") else "rules",
        "ai_status": ai_info["status"],
        "ai_model": ai_info["model"],
        "ai_error": ai_info["error"],
        "market_context": market_context,
        "picks": picks[:limit],
        "universe_count": len(stocks),
        "disclaimer": _DISCLAIMER,
    }


_DISCLAIMER = (
    "ประเมินจากราคาปิดรายวัน (EOD) + ข่าวล่าสุด เป็นโซนอ้างอิงเชิงสถิติ "
    "ไม่ใช่การทำนายราคาระหว่างวัน และไม่ใช่คำแนะนำการลงทุน"
)


async def _refresh_cache(limit: int) -> dict:
    result = await build_daily_picks(limit=max(limit, 10))
    if result["picks"]:
        _PICKS_CACHE["data"] = result
        _PICKS_CACHE["ts"] = time.time()
    return result


def _start_background_build(limit: int) -> None:
    """เริ่มคำนวณเบื้องหลังหนึ่งงานเท่านั้น (กันยิงซ้ำเมื่อมีหลาย request พร้อมกัน)."""
    import asyncio

    if _PICKS_CACHE.get("building"):
        return
    _PICKS_CACHE["building"] = True

    async def _run() -> None:
        try:
            await _refresh_cache(limit)
        except Exception as exc:
            logger.warning("daily picks background build failed: %s", exc)
        finally:
            _PICKS_CACHE["building"] = False

    # ต้องถืออ้างอิงไว้: event loop เก็บแค่ weak reference ถ้า GC เก็บ task นี้กลางคัน
    # finally จะไม่ทำงาน ทำให้ building ค้างเป็น True แล้วแคชจะไม่ถูกสร้างใหม่อีกเลย
    task = asyncio.create_task(_run())
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


async def get_daily_picks(limit: int = 10, *, force: bool = False, wait: bool = False) -> dict:
    """เวอร์ชันมี cache — หน้าเว็บและบอทเรียกผ่านตัวนี้.

    การคำนวณสดใช้เวลา ~1 นาที (ราคาทั้ง universe + ประวัติ 1 ปีของตัวที่ผ่านคัด)
    ซึ่งนานเกินกว่าจะให้ HTTP request รอ — ก่อนหน้านี้หน้าเว็บจึงค้างยาวแล้วไม่ขึ้น
    ข้อมูล จนกว่าจะกดรีเฟรชซ้ำ (รอบสองเจอแคชอุ่นแล้ว)

    ตอนนี้จะตอบทันทีเสมอ: มีแคชก็คืนแคช (พร้อมรีเฟรชเบื้องหลังถ้าเก่า) ยังไม่มีก็
    คืนสถานะ building ให้หน้าเว็บ poll ต่อ ส่วนบอท Telegram ใช้ wait=True เพราะ
    ตอบกลับเป็นข้อความครั้งเดียว poll ไม่ได้
    """
    now = time.time()
    cached = _PICKS_CACHE["data"]
    is_fresh = bool(cached) and (now - _PICKS_CACHE["ts"]) < _PICKS_TTL_SECONDS

    if wait and (force or not cached):
        result = await _refresh_cache(limit)
        return {
            **result,
            "picks": result["picks"][:limit],
            "status": "ready" if result["picks"] else "empty",
            "cached": False,
            "stale": False,
        }

    if force or not is_fresh:
        _start_background_build(limit)

    if cached:
        return {
            **cached,
            "picks": cached["picks"][:limit],
            "status": "ready",
            "cached": True,
            "stale": not is_fresh,
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "analysis_source": "pending",
        "status": "building",
        "picks": [],
        "universe_count": 0,
        "disclaimer": _DISCLAIMER,
        "cached": False,
        "stale": False,
    }
