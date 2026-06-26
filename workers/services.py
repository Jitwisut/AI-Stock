from __future__ import annotations

import logging
import time
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager

from workers.ai.analyze_stock import analyze_stock_with_ai
from workers.ai.summarize_signal import summarize_signals
from workers.alerts.telegram import TelegramClient
from workers.config import get_settings
from workers.repository import (
    create_scan_run,
    database_available,
    get_signal,
    get_scan_run,
    latest_scan_run,
    latest_signals,
    list_signals,
    save_signals,
    save_telegram_alert,
    update_scan_run,
    upsert_stocks,
)
from workers.scanner.fetch_prices import get_market_data_provider
from workers.scanner.fetch_stocks import fetch_stock_universe, fetch_surge_stock_universe
from workers.news.fetch_news import get_news_provider
from workers.longterm import longterm_to_dict
from workers.recommendations import surge_grade, surge_plan, surge_score
from workers.scanner.fetch_history import get_history_provider
from workers.scoring import build_signal
from workers.serializers import signal_to_dict, stock_to_dict
from workers.data_models import Stock

logger = logging.getLogger(__name__)

# แคชผลสแกนสดช่วงสั้น ๆ เพื่อไม่ให้ทุก request (dashboard + หน้าซิ่ง) ยิงสแกน
# ทั้ง universe ซ้ำกัน ซึ่งช้าและเสี่ยงโดน rate-limit จาก public endpoint
_POOL_CACHE: dict = {"ts": 0.0, "data": None}
_POOL_TTL_SECONDS = 90.0
_SURGE_CACHE: dict = {"ts": 0.0, "data": None}

# หุ้นระยะยาวเปลี่ยนช้า + ต้องดึงประวัติ 1 ปีต่อ symbol จึงแคชนานกว่า (6 ชม.)
_LONGTERM_CACHE: dict = {"ts": 0.0, "data": None}
_LONGTERM_TTL_SECONDS = 6 * 60 * 60.0


def scan_top_signal_models(limit: int = 5) -> list:
    return scan_market(limit=limit)["signals"]


def _serialized_signal_pool(force: bool = False) -> list[dict]:
    """สแกนสดครั้งเดียวแล้ว serialize เก็บไว้ใน cache (TTL สั้น) ให้ทั้งสองหน้าใช้ร่วมกัน."""
    now = time.time()
    cache = _POOL_CACHE
    if not force and cache["data"] is not None and now - cache["ts"] < _POOL_TTL_SECONDS:
        return cache["data"]
    settings = get_settings()
    signals = scan_market(settings.ai_candidate_limit)["signals"]
    data = [signal_to_dict(signal) for signal in signals]
    cache["data"] = data
    cache["ts"] = now
    return data


def _surge_signal_models(force: bool = False) -> list:
    """สแกนหุ้นซิ่ง คืน SignalScore objects (ยังไม่ serialize) สำหรับ persist/AI screening."""
    settings = get_settings()
    stocks = fetch_surge_stock_universe(
        settings.surge_extra_symbols,
        mode=settings.surge_universe_mode,
        limit=settings.surge_universe_limit,
    )
    signals = scan_market(
        settings.surge_candidate_limit,
        stocks=stocks,
        sort_mode="surge",
        include_news=False,
    )["signals"]
    selected = _select_surge_signals(
        signals,
        primary_min_change=settings.surge_min_change_percent,
        backfill_min_change=settings.surge_backfill_min_change_percent,
        min_results=settings.surge_min_results,
    )
    return _filter_surge_junk(selected)


def _filter_surge_junk(signals: list) -> list:
    """กรองหุ้นขยะออก: penny ต่ำกว่า $1 และ gap เกิน 100% (เสี่ยงโดน halt/ผันผวนรุนแรง).

    ถ้ากรองแล้วเหลือน้อยเกินไป (<10) คืนชุดเดิมเพื่อไม่ให้ว่าง.
    """
    cleaned = [
        s for s in signals
        if (s.latest_price or 0) >= 1.0 and (s.gap_percent or 0) <= 100.0
    ]
    return cleaned if len(cleaned) >= 10 else signals


def _serialized_surge_pool(force: bool = False) -> list[dict]:
    """สแกนหุ้นซิ่งโดยใช้ surge ranking แยกจาก dashboard momentum ranking."""
    now = time.time()
    cache = _SURGE_CACHE
    if not force and cache["data"] is not None and now - cache["ts"] < _POOL_TTL_SECONDS:
        return cache["data"]
    selected = _surge_signal_models(force=force)
    data = [signal_to_dict(signal) for signal in selected]
    cache["data"] = data
    cache["ts"] = now
    return data


def scan_market(
    limit: int = 5,
    stocks: list | None = None,
    sort_mode: str = "score",
    min_change_percent: float | None = None,
    include_news: bool = True,
) -> dict:
    settings = get_settings()
    if stocks is None:
        stocks = fetch_stock_universe(
            mode=settings.universe_mode,
            limit=settings.scan_universe_limit,
        )
    market_data = get_market_data_provider(use_live_data=settings.use_live_data)
    news_provider = get_news_provider(use_live_data=settings.use_live_data)

    price_candidates = []
    stock_by_symbol = {stock.symbol: stock for stock in stocks}
    snapshots = market_data.get_snapshots(list(stock_by_symbol))
    for symbol, snapshot in snapshots.items():
        stock = stock_by_symbol.get(symbol)
        if min_change_percent is not None and snapshot.premarket_gap_percent < min_change_percent:
            continue
        if stock:
            price_candidates.append((stock, snapshot, _price_candidate_score(snapshot)))

    failures = max(0, len(stocks) - len(price_candidates))

    price_candidates.sort(key=lambda item: item[2], reverse=True)
    selected_candidates = price_candidates[: max(limit, settings.ai_candidate_limit)]

    news_by_symbol: dict[str, list] = {}
    if include_news:
        # ดึงข่าวแบบขนาน — เดิมเรียกทีละ symbol ทำให้สแกนรอบแรกช้ามาก
        # (ผู้ให้บริการข่าวยิง HTTP ต่อ symbol และบางแหล่งโดน rate-limit/timeout)
        def _news_for(stock):
            try:
                return news_provider.get_company_news(stock.symbol)
            except Exception:
                return []

        workers = max(1, min(settings.scan_workers, len(selected_candidates) or 1))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_news_for, stock): stock
                for stock, _snapshot, _score in selected_candidates
            }
            for future in as_completed(futures):
                news_by_symbol[futures[future].symbol] = future.result()

    signals = [
        build_signal(stock, snapshot, news_by_symbol.get(stock.symbol, []))
        for stock, snapshot, _candidate_score in selected_candidates
    ]

    if sort_mode == "surge":
        grade_rank = {"S": 0, "A": 1, "B": 2, "C": 3}
        signals = sorted(
            signals,
            key=lambda signal: (
                grade_rank.get(surge_grade(signal)["grade"], 4),
                -surge_score(signal),
            ),
        )
    else:
        signals = sorted(signals, key=lambda signal: signal.score, reverse=True)
    return {
        "signals": signals[:limit],
        "scanned_symbol_count": len(price_candidates),
        "universe_symbol_count": len(stocks),
        "failed_symbol_count": failures,
        "candidate_symbol_count": len(selected_candidates),
        "provider": getattr(market_data, "name", "unknown"),
    }


def _select_surge_signals(
    signals: list,
    *,
    primary_min_change: float,
    backfill_min_change: float,
    min_results: int,
) -> list:
    """เลือกหุ้นซิ่งจริงก่อน แล้วเติมตัวแรงรองลงมาเมื่อวันนั้น 50%+ มีน้อย."""
    primary = [signal for signal in signals if signal.gap_percent >= primary_min_change]
    if len(primary) >= min_results:
        return primary

    selected = list(primary)
    seen = {signal.symbol for signal in selected}
    for signal in signals:
        if signal.symbol in seen:
            continue
        if signal.gap_percent < backfill_min_change:
            continue
        selected.append(signal)
        seen.add(signal.symbol)
        if len(selected) >= min_results:
            break
    return selected


async def run_scan(
    send_telegram: bool = False,
    persist: bool | None = None,
    scan_run_id: int | None = None,
    kind: str = "momentum",
) -> dict:
    settings = get_settings()
    stocks = fetch_stock_universe(
        mode=settings.universe_mode,
        limit=settings.scan_universe_limit,
    )
    scan_result = scan_market(settings.top_signal_limit, stocks=stocks)
    signals = scan_result["signals"]
    should_persist = settings.save_signals if persist is None else persist

    saved_ids: list[int] = []
    if should_persist:
        upsert_stocks(stocks, settings.database_url)
        saved_ids = save_signals(signals, settings.database_url, scan_run_id=scan_run_id)

    summary = await summarize_signals(
        signals,
        ai_provider=settings.ai_provider,
        openai_api_key=settings.openai_api_key,
        gemini_api_key=settings.gemini_api_key,
        openai_model=settings.openai_model,
        gemini_model=settings.gemini_model,
    )

    telegram_sent = False
    telegram_status = "skipped"
    should_send = send_telegram or settings.send_telegram
    if should_send:
        telegram = TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)
        if telegram.is_configured:
            telegram_sent = await telegram.send_message(summary)
            telegram_status = "sent" if telegram_sent else "failed"
        else:
            telegram_status = "not_configured"

    if should_persist and should_send:
        for signal_id in saved_ids or [None]:
            save_telegram_alert(settings.database_url, signal_id, telegram_status)

    serialized = [
        signal_to_dict(signal, saved_ids[index] if index < len(saved_ids) else None)
        for index, signal in enumerate(signals)
    ]
    return {
        "scan_run_id": scan_run_id,
        "status": "completed",
        "signals": serialized,
        "summary": summary,
        "telegram_sent": telegram_sent,
        "telegram_status": telegram_status,
        "saved_signal_count": len(saved_ids),
        "scanned_symbol_count": scan_result["scanned_symbol_count"],
        "universe_symbol_count": scan_result["universe_symbol_count"],
        "failed_symbol_count": scan_result["failed_symbol_count"],
        "candidate_symbol_count": scan_result["candidate_symbol_count"],
    }


def create_queued_scan(kind: str = "momentum") -> dict:
    settings = get_settings()
    provider = settings.market_data_provider if settings.use_live_data else "mock"
    scan_run_id = create_scan_run(settings.database_url, kind=kind, provider=provider, status="queued")
    return get_scan_run(settings.database_url, scan_run_id) or {
        "id": scan_run_id,
        "kind": kind,
        "status": "queued",
        "provider": provider,
        "universe_count": 0,
        "scanned_count": 0,
        "failed_count": 0,
        "error": None,
    }


def execute_scan_job(
    scan_run_id: int,
    *,
    kind: str = "momentum",
    send_telegram: bool = False,
) -> dict:
    settings = get_settings()
    lock_name = f"stock-radar:scan:{kind}"
    with _redis_lock(lock_name, timeout_seconds=60 * 30):
        update_scan_run(settings.database_url, scan_run_id, status="running")
        try:
            if kind == "longterm":
                results = _longterm_pool(force=True)
                _redis_set_json("stock-radar:longterm:latest", results, _LONGTERM_TTL_SECONDS)
                _save_longterm_to_signals(results, settings.database_url, scan_run_id)
                update_scan_run(
                    settings.database_url,
                    scan_run_id,
                    status="completed",
                    universe_count=settings.longterm_scan_limit,
                    scanned_count=len(results),
                    failed_count=max(0, settings.longterm_scan_limit - len(results)),
                    error=None,
                    finish=True,
                )
                return {"scan_run_id": scan_run_id, "status": "completed", "longterm": results}

            if kind == "surge":
                models = _surge_signal_models(force=True)
                selected = _run_async_scan(_gemini_screen_surge(models, limit=10))
                saved_ids = save_signals(
                    selected, settings.database_url, scan_run_id=scan_run_id
                )
                _SURGE_CACHE["data"] = [signal_to_dict(s) for s in selected]
                _SURGE_CACHE["ts"] = time.time()
                update_scan_run(
                    settings.database_url,
                    scan_run_id,
                    status="completed",
                    universe_count=len(models),
                    scanned_count=len(selected),
                    failed_count=0,
                    error=None,
                    finish=True,
                )
                return {
                    "scan_run_id": scan_run_id,
                    "status": "completed",
                    "surge": len(saved_ids),
                }

            if kind == "universe_refresh":
                stocks = fetch_stock_universe(
                    mode=settings.universe_mode,
                    limit=settings.scan_universe_limit,
                )
                upsert_stocks(stocks, settings.database_url)
                update_scan_run(
                    settings.database_url,
                    scan_run_id,
                    status="completed",
                    universe_count=len(stocks),
                    scanned_count=len(stocks),
                    failed_count=0,
                    error=None,
                    finish=True,
                )
                return {"scan_run_id": scan_run_id, "status": "completed", "stocks": len(stocks)}

            result = _run_async_scan(
                run_scan(
                    send_telegram=send_telegram,
                    persist=True,
                    scan_run_id=scan_run_id,
                    kind=kind,
                )
            )
            update_scan_run(
                settings.database_url,
                scan_run_id,
                status="completed",
                universe_count=result["universe_symbol_count"],
                scanned_count=result["scanned_symbol_count"],
                failed_count=result["failed_symbol_count"],
                error=None,
                finish=True,
            )
            return result
        except Exception as exc:
            update_scan_run(
                settings.database_url,
                scan_run_id,
                status="failed",
                error=str(exc),
                finish=True,
            )
            raise


def get_latest_signal_dicts(limit: int = 5) -> list[dict]:
    settings = get_settings()
    rows = latest_signals(settings.database_url, limit=limit)
    if rows:
        return rows
    if settings.app_env == "production":
        return []
    pool = _serialized_signal_pool()
    return sorted(pool, key=lambda item: item.get("score", 0), reverse=True)[:limit]


async def _gemini_screen_surge(models: list, limit: int = 10) -> list:
    """ใช้ Gemini คัดเลือกหุ้นซิ่งที่ดีที่สุด `limit` ตัว + เขียนเหตุผลสั้นๆ ลง signal.reason.

    Fallback: ถ้า Gemini ใช้ไม่ได้/ผิดพลาด → เลือก top `limit` ตาม surge_score เดิม.
    """
    settings = get_settings()
    # เรียงตาม surge_score ก่อนเสมอ เผื่อ fallback
    ranked = sorted(models, key=lambda s: surge_score(s), reverse=True)

    if not settings.gemini_api_key or settings.ai_provider != "gemini" or len(ranked) <= limit:
        return ranked[:limit]

    # ส่งเฉพาะ candidate ชั้นนำ (สูงสุด 30 ตัว) ให้ Gemini จัดอันดับ
    candidates = ranked[: min(30, len(ranked))]
    payload = [
        {
            "symbol": s.symbol,
            "gap_percent": round(s.gap_percent or 0, 1),
            "relative_volume": round(s.relative_volume or 0, 1),
            "price": round(s.latest_price or 0, 2),
            "surge_score": round(surge_score(s), 1),
            "grade": surge_grade(s).get("grade"),
        }
        for s in candidates
    ]
    prompt = (
        "คุณเป็นนักวิเคราะห์หุ้นซิ่ง (momentum day-trade) ของตลาดหุ้น US\n"
        f"จากรายการหุ้นด้านล่าง ให้เลือกหุ้นที่น่าสนใจที่สุด {limit} ตัว "
        "โดยพิจารณาจาก momentum, volume, ความเสี่ยง และคุณภาพโดยรวม "
        "(หลีกเลี่ยงหุ้นที่เสี่ยงเกินไปหรือ gap สูงผิดปกติจนน่าจะโดน halt)\n"
        "ตอบกลับเป็น JSON array เท่านั้น แต่ละ element มี key: symbol, reason "
        '(reason = เหตุผลภาษาไทยสั้นๆ ไม่เกิน 60 ตัวอักษร ไม่ชี้นำให้ซื้อ)\n'
        'ตัวอย่าง: [{"symbol":"ABC","reason":"วอลุ่มหนุนชัด ทะลุแนวต้าน"}]\n\n'
        f"หุ้น: {payload}"
    )
    try:
        from workers.ai.gemini_client import generate_gemini_text

        text = await generate_gemini_text(
            prompt, api_key=settings.gemini_api_key, model=settings.gemini_model, temperature=0.2
        )
        # ตัด markdown code fence ถ้ามี
        cleaned = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        picks = json.loads(cleaned)
        from dataclasses import replace

        by_symbol = {s.symbol: s for s in candidates}
        result = []
        for pick in picks:
            sym = pick.get("symbol")
            signal = by_symbol.get(sym)
            if signal is None:
                continue
            reason = (pick.get("reason") or "").strip()
            # SignalScore เป็น frozen dataclass — ใช้ replace แทนการ set field
            result.append(replace(signal, reason=reason) if reason else signal)
            if len(result) >= limit:
                break
        if result:
            return result
    except Exception:
        logger.warning("Gemini surge screening failed, falling back to surge_score", exc_info=True)
    return ranked[:limit]


def get_surge_signal_dicts(limit: int = 10) -> list[dict]:
    """หุ้น "ซิ่ง" สำหรับสายถือไว-ออกไว — DB-first (เร็ว <1 วิ) fallback สแกนสดเฉพาะ dev.

    บน production (Vercel + Neon) อ่านจากฐานข้อมูลที่ Celery beat บน Railway
    สแกนเก็บไว้ตอน 21:45 TH ทุกวัน — ไม่ต้องสแกนสดซึ่งใช้เวลา 30-60 วิ
    """
    settings = get_settings()
    from workers.repository import latest_signals_by_kind

    rows = latest_signals_by_kind(settings.database_url, kind="surge", limit=limit)
    if rows:
        return rows[:limit]

    # Fallback: สแกนสด (เฉพาะ dev / staging)
    if settings.app_env != "production":
        return _surge_pool_fallback(limit)
    return []


def _surge_pool_fallback(limit: int) -> list[dict]:
    """สแกนสด surge pool (ใช้ตอน dev หรือไม่มี DB)."""
    pool = _serialized_surge_pool()

    enriched: list[dict] = []
    for row in pool:
        item = dict(row)
        item.setdefault("surge_score", round(surge_score(item), 2))
        if not item.get("surge_grade"):
            item["surge_grade"] = surge_grade(item)
        if not item.get("surge_plan"):
            item["surge_plan"] = surge_plan(item)
        enriched.append(item)

    grade_rank = {"S": 0, "A": 1, "B": 2, "C": 3}
    enriched.sort(
        key=lambda item: (
            grade_rank.get((item.get("surge_grade") or {}).get("grade"), 4),
            -item.get("surge_score", 0),
        )
    )
    return enriched[:limit]


def _longterm_pool(force: bool = False) -> list[dict]:
    """วิเคราะห์เทรนด์ระยะยาวของหุ้นชุดที่จำกัด (ดึงประวัติ 1 ปี) แล้วแคชไว้นาน."""
    now = time.time()
    cache = _LONGTERM_CACHE
    if not force and cache["data"] is not None and now - cache["ts"] < _LONGTERM_TTL_SECONDS:
        return cache["data"]

    settings = get_settings()
    # ลงทุนระยะยาวเน้นหุ้นคุณภาพ จึงใช้รายชื่อ curated (large/mid cap) เสมอ
    # แทนการไล่ทั้งตลาดตามตัวอักษร ซึ่งจะได้หุ้นเล็ก/เพนนีที่ไม่เหมาะถือยาว
    stocks = fetch_stock_universe(mode="default")[: settings.longterm_scan_limit]
    history = get_history_provider(use_live_data=settings.use_live_data)

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=settings.scan_workers) as executor:
        futures = {executor.submit(history.get_metrics, stock.symbol): stock for stock in stocks}
        for future in as_completed(futures):
            stock = futures[future]
            try:
                metrics = future.result()
            except Exception:
                continue
            results.append(longterm_to_dict(metrics, stock))

    results.sort(key=lambda item: item.get("score", 0), reverse=True)
    cache["data"] = results
    cache["ts"] = now
    return results


def get_longterm_signal_dicts(limit: int = 10) -> list[dict]:
    """หุ้นน่าสนใจสำหรับลงทุนระยะยาว — DB-first (เร็ว <1 วิ) fallback Redis/dev."""
    settings = get_settings()
    from workers.repository import latest_signals_by_kind

    # DB-first: Celery beat scan ตอน 07:30 TH เก็บใน signals table
    rows = latest_signals_by_kind(settings.database_url, kind="longterm", limit=limit)
    if rows:
        # recommendation JSONB holds the full longterm dict from _save_longterm_to_signals.
        # เก็บเฉพาะ row ที่เป็น longterm-shaped จริง — กัน momentum signal ที่หลุดมาจาก
        # fallback query ทำให้ LongTermOut serialize พัง (500)
        result = [
            rec for row in rows
            if isinstance((rec := row.get("recommendation")), dict)
            and "grade" in rec and "return_6m_percent" in rec
        ]
        if result:
            return result[:limit]
        # ถ้า DB ไม่มี longterm-shaped row → ตกไปใช้ redis/scan สดด้านล่าง

    # Fallback: Redis cache (Railway worker writes here)
    cached = _redis_get_json("stock-radar:longterm:latest")
    if cached:
        return cached[:limit]

    # Last resort: scan สด (เฉพาะ dev/staging)
    if settings.app_env != "production":
        return _longterm_pool()[:limit]
    return []


def list_signal_dicts(
    limit: int = 25,
    symbol: str | None = None,
    date_from=None,
    date_to=None,
) -> list[dict]:
    settings = get_settings()
    return list_signals(
        settings.database_url,
        limit=limit,
        symbol=symbol,
        date_from=date_from,
        date_to=date_to,
    )


def get_scan_run_dict(scan_run_id: int) -> dict | None:
    settings = get_settings()
    return get_scan_run(settings.database_url, scan_run_id)


def get_latest_scan_run_dict(kind: str | None = None) -> dict | None:
    settings = get_settings()
    return latest_scan_run(settings.database_url, kind=kind)


def get_signal_dict(signal_id: int) -> dict | None:
    settings = get_settings()
    return get_signal(settings.database_url, signal_id)


async def analyze_stock_query(query: str) -> dict:
    settings = get_settings()
    stock = _resolve_stock(query)
    market_data = get_market_data_provider(use_live_data=settings.use_live_data)
    news_provider = get_news_provider(use_live_data=settings.use_live_data)
    snapshot = market_data.get_snapshot(stock.symbol)
    news_items = news_provider.get_company_news(stock.symbol)
    signal = build_signal(stock, snapshot, news_items)
    analysis_text, analysis_source = await analyze_stock_with_ai(
        stock,
        signal,
        news_items,
        ai_provider=settings.ai_provider,
        openai_api_key=settings.openai_api_key,
        gemini_api_key=settings.gemini_api_key,
        openai_model=settings.openai_model,
        gemini_model=settings.gemini_model,
    )
    signal_data = signal_to_dict(signal)
    return {
        "query": query,
        "analysis_source": analysis_source,
        "stock": stock_to_dict(stock),
        "signal": signal_data,
        "analysis": analysis_text,
        "disclaimer": "ข้อมูลนี้เป็นเครื่องมือช่วยคัดกรอง ไม่ใช่คำแนะนำทางการเงิน",
    }


def get_stock_dicts() -> list[dict]:
    settings = get_settings()
    return [
        stock_to_dict(stock)
        for stock in fetch_stock_universe(
            mode=settings.universe_mode,
            limit=settings.scan_universe_limit,
        )
    ]


def _resolve_stock(query: str) -> Stock:
    settings = get_settings()
    normalized = query.strip().upper()
    if not normalized:
        raise ValueError("กรุณาใส่ชื่อหุ้นหรือ symbol")

    default_by_symbol = {stock.symbol.upper(): stock for stock in fetch_stock_universe(mode="default")}
    if normalized in default_by_symbol:
        return default_by_symbol[normalized]

    # Fast path for direct symbols such as LUNG, ASTS, RKLB. Company-name
    # searches still use the configured universe below.
    if normalized.replace(".", "").replace("-", "").isalnum() and len(normalized) <= 12:
        return Stock(symbol=normalized, name=normalized, exchange="US")

    stocks = fetch_stock_universe(
        mode=settings.universe_mode,
        limit=settings.scan_universe_limit,
    )
    by_symbol = {stock.symbol.upper(): stock for stock in stocks}
    if normalized in by_symbol:
        return by_symbol[normalized]

    lowered = query.strip().lower()
    matches = [
        stock
        for stock in stocks
        if lowered in stock.name.lower() or lowered == stock.symbol.lower()
    ]
    if matches:
        return sorted(matches, key=lambda item: (len(item.name), item.symbol))[0]

    raise ValueError("ไม่พบหุ้นที่ต้องการวิเคราะห์")


def get_settings_status() -> dict:
    settings = get_settings()
    ai_configured = (
        bool(settings.gemini_api_key)
        if settings.ai_provider == "gemini"
        else bool(settings.openai_api_key)
    )
    return {
        "app_env": settings.app_env,
        "market_data_provider": settings.market_data_provider,
        "ai_provider": settings.ai_provider,
        "use_live_data": settings.use_live_data,
        "allow_mock_data": settings.allow_mock_data,
        "save_signals": settings.save_signals,
        "send_telegram": settings.send_telegram,
        "background_workers_enabled": settings.background_workers_enabled,
        "telegram_configured": bool(settings.telegram_bot_token and settings.telegram_chat_id),
        "openai_configured": bool(settings.openai_api_key),
        "gemini_configured": bool(settings.gemini_api_key),
        "ai_configured": ai_configured,
        "finnhub_configured": bool(settings.finnhub_api_key),
        "polygon_configured": bool(settings.polygon_api_key),
        "admin_api_key_configured": bool(settings.admin_api_key),
        "database_configured": bool(settings.database_url),
        "database_reachable": database_available(settings.database_url),
        "redis_configured": bool(settings.redis_url),
        "redis_reachable": redis_available(settings.redis_url),
        "top_signal_limit": settings.top_signal_limit,
        "universe_mode": settings.universe_mode,
        "scan_universe_limit": settings.scan_universe_limit,
        "scan_workers": settings.scan_workers,
        "ai_candidate_limit": settings.ai_candidate_limit,
    }


def redis_available(redis_url: str) -> bool:
    try:
        from redis import Redis

        client = Redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
        return bool(client.ping())
    except Exception:
        return False


def _redis_get_json(key: str):
    try:
        from redis import Redis

        settings = get_settings()
        client = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
        value = client.get(key)
        if not value:
            return None
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        return json.loads(value)
    except Exception:
        return None


def _redis_set_json(key: str, value, ttl_seconds: float) -> None:
    try:
        from redis import Redis

        settings = get_settings()
        client = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
        client.setex(key, int(ttl_seconds), json.dumps(value, ensure_ascii=False))
    except Exception:
        if get_settings().app_env == "production":
            raise


def provider_ready() -> tuple[bool, str | None]:
    settings = get_settings()
    if settings.app_env == "production" and not settings.use_live_data:
        return False, "USE_LIVE_DATA=true is required in production"
    if settings.market_data_provider in {"polygon", "massive"} and not settings.polygon_api_key:
        return False, "POLYGON_API_KEY is required"
    if settings.app_env == "production" and settings.allow_mock_data:
        return False, "ALLOW_MOCK_DATA=false is required in production"
    return True, None


def _save_longterm_to_signals(results: list[dict], database_url: str, scan_run_id: int) -> None:
    """Save longterm results into signals table (recommendation JSONB holds the full dict)."""
    try:
        from sqlalchemy import text
        from workers.repository import _engine

        # ใช้ CAST(...AS JSONB) ไม่ใช่ ::jsonb เพราะ SQLAlchemy text() ไม่ bind
        # :name ที่ตามด้วย :: (จะมองเป็น literal ทำให้ syntax error)
        sql = text("""
            INSERT INTO signals (scan_run_id, symbol, score, momentum_score, volume_score,
                catalyst_score, premarket_gap_score, breakout_score, risk_score, latest_price, recommendation)
            VALUES (:scan_run_id, :symbol, :score, 0, 0, 0, 0, 0, 0, :price, CAST(:rec AS JSONB))
        """)
        with _engine(database_url).begin() as conn:
            for r in results:
                conn.execute(sql, {
                    "scan_run_id": scan_run_id,
                    "symbol": r["symbol"],
                    "score": r.get("score", 0),
                    "price": r.get("price", 0),
                    "rec": json.dumps(r),
                })
    except Exception:
        logger.warning("_save_longterm_to_signals failed", exc_info=True)


@contextmanager
def _redis_lock(name: str, timeout_seconds: int):
    settings = get_settings()
    # พยายาม acquire lock — ถ้า Redis ใช้ไม่ได้ (import/connect ล้มเหลว) ก็รันต่อโดยไม่มี lock
    lock = None
    try:
        from redis import Redis

        client = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
        candidate = client.lock(name, timeout=timeout_seconds, blocking_timeout=1)
        if candidate.acquire():
            lock = candidate
    except Exception:
        logger.warning("redis lock unavailable for %s, running without lock", name)

    # yield อยู่นอก try ของ setup เพื่อไม่ให้ exception ใน body ไป trigger yield ซ้ำ
    try:
        yield
    finally:
        if lock is not None:
            try:
                lock.release()
            except Exception:
                pass


def _run_async_scan(coro):
    import asyncio

    return asyncio.run(coro)


def _price_candidate_score(snapshot) -> float:
    return (
        max(0.0, snapshot.premarket_gap_percent) * 3
        + snapshot.relative_volume * 10
        + (5 if snapshot.premarket_gap_percent >= 2 else 0)
    )
