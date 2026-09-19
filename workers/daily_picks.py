"""หุ้นน่าสนใจวันนี้ — คัดหุ้นที่โครงสร้างราคา + ข่าว บอกว่ามีโอกาสวิ่งแรงระยะสั้น.

ข้อจำกัดที่ต้องพูดตรง ๆ: ข้อมูลทั้งหมดเป็น daily EOD ไม่ใช่ intraday ดังนั้น
"โอกาสพุ่ง" ที่คำนวณได้คือโอกาสจากโครงสร้างราคา/วอลุ่ม/ข่าวล่าสุด ไม่ใช่การ
ทำนายว่าราคาจะขึ้นภายในวันนี้จริง ๆ — ทุกจุดที่แสดงผลต้องสื่อสารแบบนั้น

ทางอ่านหลักคือคำนวณสด + cache ในหน่วยความจำ (ไม่ใช่ DB) เพราะ production ตั้ง
SAVE_SIGNALS=false ทำให้ตาราง signals ไม่มีข้อมูลใหม่เลย
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from workers.config import get_settings
from workers.data_models import LongTermMetrics, MarketSnapshot, NewsItem, Stock
from workers.indicators.rsi import rsi
from workers.news.fetch_news import get_news_provider
from workers.scanner.fetch_history import _metrics_from_closes, get_history_provider
from workers.scanner.fetch_prices import get_market_data_provider
from workers.scanner.fetch_stocks import fetch_stock_universe

logger = logging.getLogger(__name__)

_PICKS_CACHE: dict = {"ts": 0.0, "data": None}
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


def _chart_features(symbol: str, closes: list[float], metrics: LongTermMetrics, snapshot: MarketSnapshot) -> dict:
    """สรุป "หน้าตากราฟ" เป็นตัวเลขให้ Gemini อ่าน — Gemini client ส่งได้แต่ text."""
    price = snapshot.latest_price or metrics.price
    range_span = metrics.week52_high - metrics.week52_low
    position_in_range = (
        (price - metrics.week52_low) / range_span * 100 if range_span > 0 else 50.0
    )
    breakout_gap = (
        (price - snapshot.prior_high) / snapshot.prior_high * 100
        if snapshot.prior_high > 0
        else 0.0
    )
    change_percent = (
        (price - snapshot.previous_close) / snapshot.previous_close * 100
        if snapshot.previous_close > 0
        else 0.0
    )
    return {
        "symbol": symbol,
        "price": round(price, 2),
        "change_percent": round(change_percent, 2),
        "relative_volume": round(
            snapshot.volume / snapshot.average_volume_20d
            if snapshot.average_volume_20d > 0
            else 1.0,
            2,
        ),
        "rsi14": round(rsi(closes), 1) if len(closes) > 15 else None,
        "above_sma50": price > metrics.sma50,
        "above_sma200": metrics.above_sma200,
        "sma50": round(metrics.sma50, 2),
        "sma200": round(metrics.sma200, 2),
        "position_in_52w_range_percent": round(position_in_range, 1),
        "pct_from_52w_high": round(
            (price - metrics.week52_high) / metrics.week52_high * 100
            if metrics.week52_high > 0
            else 0.0,
            2,
        ),
        "breakout_gap_percent": round(breakout_gap, 2),
        "return_3m_percent": metrics.return_3m_percent,
        "return_6m_percent": metrics.return_6m_percent,
        "volatility_percent": metrics.volatility_percent,
        "closes_20d": [round(c, 2) for c in closes[-20:]],
    }


def _rule_score(features: dict) -> float:
    """คะแนนโอกาสวิ่งแรงจากกฎล้วน ๆ — ใช้คัดตัวก่อนส่ง AI และเป็น fallback เมื่อ AI ล่ม."""
    score = 0.0
    rel_vol = features["relative_volume"]
    change = features["change_percent"]

    score += min(30.0, max(0.0, rel_vol - 1) * 20)          # วอลุ่มผิดปกติ
    score += min(20.0, max(0.0, change) * 3)                 # โมเมนตัมวันล่าสุด
    if features["above_sma50"]:
        score += 10
    if features["above_sma200"]:
        score += 10
    if features["breakout_gap_percent"] > -2:                # จ่อ/ผ่านแนวต้านเดิม
        score += 15
    if features["position_in_52w_range_percent"] > 70:
        score += 10
    rsi_value = features.get("rsi14")
    if rsi_value is not None:
        if 55 <= rsi_value <= 72:
            score += 10                                       # แข็งแต่ยังไม่ overbought จัด
        elif rsi_value > 80:
            score -= 10
    if features["volatility_percent"] > 90:
        score -= 5                                            # ผันผวนเกินไป = เสี่ยงมากกว่าโอกาส
    return round(max(0.0, min(100.0, score)), 1)


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
    # วอลุ่มจริงเข้ามาตอน _with_volume_baseline ในขั้นที่สอง
    score = min(60.0, max(0.0, change) * 8)
    if breakout_gap > -3:
        score += 20
    if snapshot.latest_price > snapshot.previous_close:
        score += 10
    return score


def _with_volume_baseline(snapshot: MarketSnapshot) -> MarketSnapshot:
    """เปลี่ยนมาใช้ snapshot จาก Nasdaq historical ที่มีวอลุ่มเฉลี่ย 20 วันจริง.

    nasdaq_quote (ตัวแรกใน fallback chain) ไม่มีวอลุ่มเฉลี่ย จึงตั้ง average_volume_20d
    = วอลุ่มวันนี้ ทำให้ relative volume ออกมาเป็น 1.00 ทุกตัวและใช้คัดหุ้นไม่ได้เลย
    """
    from workers.scanner.fetch_prices import NasdaqMarketDataProvider

    try:
        return NasdaqMarketDataProvider().get_snapshot(snapshot.symbol)
    except Exception:
        return snapshot


def _enrich_candidate(item: tuple[Stock, MarketSnapshot]) -> dict | None:
    """เติมประวัติราคา 1 ปี + ตัวชี้วัดกราฟ ให้หุ้นที่ผ่านการคัดขั้นแรก."""
    stock, snapshot = item
    settings = get_settings()
    history = get_history_provider(use_live_data=settings.use_live_data)
    snapshot = _with_volume_baseline(snapshot) if settings.use_live_data else snapshot
    try:
        # ดึงชุดเดียวแล้วใช้ทั้งคำนวณ metrics และวาดกราฟ — แยกเรียกจะยิง HTTP ซ้ำต่อ symbol
        bars = history.get_bars(stock.symbol, days=_HISTORY_DAYS)
        closes = [bar["close"] for bar in bars]
        metrics = _metrics_from_closes(stock.symbol, closes, latest_price=snapshot.latest_price)
    except Exception as exc:
        logger.debug("daily picks: skip %s (%s)", stock.symbol, exc)
        return None

    features = _chart_features(stock.symbol, closes, metrics, snapshot)
    return {
        "stock": stock,
        "features": features,
        "rule_score": _rule_score(features),
        "closes": closes,
        "bars": bars[-_CHART_DAYS:],
        "snapshot": snapshot,
    }


def _attach_news(candidates: list[dict]) -> None:
    settings = get_settings()
    news_provider = get_news_provider(use_live_data=settings.use_live_data)

    def _fetch(candidate: dict) -> list[NewsItem]:
        try:
            return news_provider.get_company_news(candidate["stock"].symbol)[:3]
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(candidates)))) as executor:
        for candidate, items in zip(candidates, executor.map(_fetch, candidates)):
            candidate["news"] = items


def _build_prompt(candidates: list[dict]) -> str:
    blocks = []
    for candidate in candidates:
        features = dict(candidate["features"])
        closes = features.pop("closes_20d")
        news_lines = "\n".join(
            f"    - {item.title} ({item.source})" for item in candidate.get("news", [])
        ) or "    - ไม่พบข่าวล่าสุด"
        blocks.append(
            f"{candidate['stock'].symbol} ({candidate['stock'].name}):\n"
            f"  ตัวเลขกราฟ: {json.dumps(features, ensure_ascii=False)}\n"
            f"  ราคาปิด 20 วันล่าสุด: {closes}\n"
            f"  ข่าว:\n{news_lines}"
        )

    return (
        "คุณเป็นนักวิเคราะห์เทคนิคหุ้นสหรัฐ ประเมินหุ้นต่อไปนี้จาก 'โครงสร้างราคา "
        "(กราฟ) + วอลุ่ม + ข่าวล่าสุด' ว่าตัวไหนมีโอกาสวิ่งแรงในระยะสั้น (1-5 วันทำการ)\n\n"
        "ข้อมูลทั้งหมดเป็นราคาปิดรายวัน (EOD) ไม่ใช่ราคาระหว่างวัน — ห้ามอ้างว่ารู้ราคาปัจจุบัน "
        "หรือรับประกันว่าจะขึ้นวันนี้ ให้ประเมินเป็น 'โอกาส' เท่านั้น\n\n"
        f"{chr(10).join(blocks)}\n\n"
        "ตอบกลับเป็น JSON array เท่านั้น ห้ามมีข้อความอื่นนอก JSON แต่ละ element มี key:\n"
        '  symbol (string), pop_probability (int 0-100 โอกาสวิ่งแรงระยะสั้น),\n'
        '  verdict (string สั้น ๆ: "น่าสนใจมาก" | "น่าสนใจ" | "เฝ้าดู" | "ยังไม่น่าเข้า"),\n'
        '  reason (string ภาษาไทย 1-2 ประโยค อ้างอิงตัวเลขกราฟหรือข่าวที่เห็นจริง),\n'
        '  catalyst (string ภาษาไทย ตัวกระตุ้นหลัก ถ้าไม่มีให้ใส่ "ไม่พบ catalyst ชัดเจน"),\n'
        '  risk (string ภาษาไทย ความเสี่ยงหลัก 1 ข้อ)\n'
        "เรียงจาก pop_probability มากไปน้อย"
    )


async def _ai_verdicts(candidates: list[dict]) -> tuple[dict[str, dict], str]:
    """ถาม Gemini ครั้งเดียวสำหรับทุกตัว (ยิงถี่ ๆ จะโดน free-tier rate limit)."""
    settings = get_settings()
    if settings.ai_provider != "gemini" or not settings.gemini_api_key:
        return {}, "rules"

    from workers.ai.gemini_client import generate_gemini

    try:
        result = await generate_gemini(
            _build_prompt(candidates),
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            temperature=0.3,
        )
        text = result.text
        logger.info("daily picks: answered by %s", result.model)
    except Exception as exc:
        logger.warning("daily picks: gemini failed (%s)", exc)
        return {}, "rules"

    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1] if "```" in cleaned[3:] else cleaned[3:]
        cleaned = cleaned.removeprefix("json").strip()
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start < 0 or end <= start:
        logger.warning("daily picks: gemini returned non-JSON")
        return {}, "rules"

    try:
        parsed = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        logger.warning("daily picks: gemini JSON parse failed (%s)", exc)
        return {}, "rules"

    verdicts: dict[str, dict] = {}
    for item in parsed:
        symbol = str(item.get("symbol", "")).upper()
        if symbol:
            verdicts[symbol] = item
    return verdicts, ("gemini" if verdicts else "rules")


def _to_pick(candidate: dict, verdict: dict | None) -> dict:
    features = candidate["features"]
    ai_probability = None
    if verdict:
        try:
            ai_probability = max(0, min(100, int(float(verdict.get("pop_probability", 0)))))
        except (TypeError, ValueError):
            ai_probability = None

    # ไม่มีคำตอบ AI → ใช้คะแนนกฎแทน เพื่อให้หน้าเว็บยังมีข้อมูลเสมอ
    probability = ai_probability if ai_probability is not None else candidate["rule_score"]
    return {
        "symbol": candidate["stock"].symbol,
        "name": candidate["stock"].name,
        "sector": candidate["stock"].sector,
        "price": features["price"],
        "change_percent": features["change_percent"],
        "relative_volume": features["relative_volume"],
        "pop_probability": probability,
        "rule_score": candidate["rule_score"],
        "verdict": (verdict or {}).get("verdict") or _rule_verdict(candidate["rule_score"]),
        "reason": (verdict or {}).get("reason") or _rule_reason(features),
        "catalyst": (verdict or {}).get("catalyst") or "ไม่พบ catalyst ชัดเจน",
        "risk": (verdict or {}).get("risk") or "ราคาอาจย่อแรงถ้าวอลุ่มไม่ตามต่อ",
        "source": "gemini" if verdict else "rules",
        "rsi14": features["rsi14"],
        "above_sma50": features["above_sma50"],
        "above_sma200": features["above_sma200"],
        "position_in_52w_range_percent": features["position_in_52w_range_percent"],
        "breakout_gap_percent": features["breakout_gap_percent"],
        "return_3m_percent": features["return_3m_percent"],
        "volatility_percent": features["volatility_percent"],
        "closes_20d": features["closes_20d"],
        # แท่งรายวันสำหรับกราฟบนเว็บ (ราคา + วอลุ่มใช้แกนเวลาเดียวกัน)
        "bars": candidate.get("bars") or [],
        "sparkline": sparkline(candidate["closes"]),
        "news": [
            {"title": item.title, "url": item.url, "source": item.source}
            for item in candidate.get("news", [])
        ],
    }


def _rule_verdict(score: float) -> str:
    if score >= 70:
        return "น่าสนใจมาก"
    if score >= 50:
        return "น่าสนใจ"
    if score >= 30:
        return "เฝ้าดู"
    return "ยังไม่น่าเข้า"


def _rule_reason(features: dict) -> str:
    bits = []
    if features["relative_volume"] >= 1.5:
        bits.append(f"วอลุ่ม {features['relative_volume']:.1f} เท่าของค่าเฉลี่ย")
    if features["change_percent"] > 0:
        bits.append(f"ราคาปิดบวก {features['change_percent']:.1f}%")
    if features["above_sma200"]:
        bits.append("ยืนเหนือ SMA200")
    if features["breakout_gap_percent"] > -2:
        bits.append("จ่อแนวต้านเดิม")
    return " · ".join(bits) if bits else "โครงสร้างราคายังไม่มีสัญญาณเด่น"


async def build_daily_picks(limit: int = 10) -> dict:
    """คำนวณรายการหุ้นน่าสนใจวันนี้ (สด ไม่พึ่ง DB) — สองขั้น: คัดหยาบ แล้วค่อยเจาะลึก."""
    settings = get_settings()
    stocks = fetch_stock_universe(mode="default")
    market_data = get_market_data_provider(use_live_data=settings.use_live_data)

    # ขั้น 1: ราคาล่าสุดของทั้ง universe (ดึงขนานใน get_snapshots)
    snapshots = market_data.get_snapshots([stock.symbol for stock in stocks])
    ranked = sorted(
        (
            (stock, snapshots[stock.symbol.upper()])
            for stock in stocks
            if stock.symbol.upper() in snapshots
        ),
        key=lambda pair: _prescreen_score(pair[1]),
        reverse=True,
    )[:_PRESCREEN_LIMIT]

    # ขั้น 2: ดึงประวัติ 1 ปีเฉพาะตัวที่ผ่านการคัด
    workers_count = max(1, min(settings.scan_workers, len(ranked) or 1))
    with ThreadPoolExecutor(max_workers=workers_count) as executor:
        collected = [item for item in executor.map(_enrich_candidate, ranked) if item]

    if not collected:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "analysis_source": "none",
            "picks": [],
            "universe_count": len(stocks),
            "disclaimer": _DISCLAIMER,
        }

    collected.sort(key=lambda item: item["rule_score"], reverse=True)
    shortlist = collected[: max(limit, _AI_CANDIDATE_LIMIT)][:_AI_CANDIDATE_LIMIT]
    _attach_news(shortlist)

    verdicts, source = await _ai_verdicts(shortlist)
    picks = [_to_pick(candidate, verdicts.get(candidate["stock"].symbol)) for candidate in shortlist]
    picks.sort(key=lambda item: item["pop_probability"], reverse=True)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "analysis_source": source,
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
