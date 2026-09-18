"""AI Stock Discovery — คัดหุ้นทั้งตลาดแบบ funnel แล้วจัดอันดับด้วยคะแนน deterministic.

Stage 1  Nasdaq screener: ทุกหุ้นที่จดทะเบียน (~7,000 ตัว) ใน request เดียว
Stage 2  ตัวกรองสภาพคล่อง/ประเภทหลักทรัพย์ → เลือกชุด deep scan (dollar volume, movers, market cap)
Stage 3  ดึงแท่งราคาจริง 2 ปี → indicator ครบ → signal → กรองตามกลยุทธ์ → คะแนนเทคนิคเบื้องต้น
Stage 4  finalists: ดึงข้อมูลพื้นฐาน + ข่าว → คะแนนเต็ม → ตัดตามระดับความเสี่ยง → จัดอันดับ

LLM ไม่มีส่วนในการเลือกหรือเรียงหุ้น — ทุกขั้นบันทึกจำนวนไว้ใน funnel ให้ตรวจสอบได้
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from workers.engine import analysis as an
from workers.engine import market_calendar as cal
from workers.engine import sources, store
from workers.engine.bars import get_daily_bars
from workers.engine.fundamentals import get_fundamentals
from workers.engine.market_context import get_market_context
from workers.engine.news import get_news
from workers.engine.runtime import CACHE, ProviderUnavailableError, RateLimitedError, iso, utcnow
from workers.engine.stock import benchmark_closes, score_all
from workers.engine.strategies import Strategy, get_strategy

logger = logging.getLogger(__name__)

SUPPORTED_MARKETS = {
    "US": {"status": "supported", "provider": "Nasdaq public API (NASDAQ, NYSE, NYSE American)"},
    "SET": {"status": "unavailable", "reason": "ยังไม่มี data provider ของตลาด SET ที่เชื่อถือได้ในระบบ — ไม่แสดงตัวเลขที่ตรวจสอบไม่ได้"},
}

_BLOCKED_NAME_TERMS = ("warrant", " right", " unit", "preferred", "depositary share representing", "notes due", "debenture", "subordinated")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


DEEP_SCAN_LIMIT = _env_int("DISCOVERY_DEEP_SCAN_LIMIT", 500)
FETCH_WORKERS = _env_int("DISCOVERY_FETCH_WORKERS", 8)
FINALIST_LIMIT = _env_int("DISCOVERY_FINALISTS", 40)
RESULT_LIMIT = _env_int("DISCOVERY_RESULTS", 25)


# ---------------------------------------------------------------------------
# Stage 1-2: universe
# ---------------------------------------------------------------------------
def _num(text) -> float | None:
    if text is None:
        return None
    cleaned = str(text).replace("$", "").replace(",", "").replace("%", "").strip()
    if cleaned in {"", "NA", "N/A", "--"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def screener_rows() -> dict:
    def fetch():
        payload = sources.nasdaq_json(
            "screener/stocks", {"tableonly": "true", "limit": 10000, "offset": 0, "download": "true"}, timeout=40
        )
        rows = ((payload.get("data") or {}).get("rows")) or []
        if not rows:
            raise ProviderUnavailableError(sources.NASDAQ, "screener returned no rows")
        return {"rows": rows, "fetched_at": iso(utcnow())}

    return CACHE.get_or_compute("screener", 5 * 60.0, fetch)


def build_universe(params: dict) -> tuple[list[dict], dict]:
    snap = screener_rows()
    rows = snap["rows"]
    min_price = float(params.get("min_price", 2.0))
    min_cap = float(params.get("min_market_cap", 300e6))
    stats = {"listed": len(rows), "not_common_stock": 0, "below_min_price": 0, "below_min_market_cap": 0}
    passed: list[dict] = []
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        name = str(row.get("name") or "")
        if not symbol or any(ch in symbol for ch in "^/.$ ") or any(term in name.lower() for term in _BLOCKED_NAME_TERMS):
            stats["not_common_stock"] += 1
            continue
        price = _num(row.get("lastsale"))
        cap = _num(row.get("marketCap"))
        if price is None or price < min_price:
            stats["below_min_price"] += 1
            continue
        if cap is None or cap < min_cap:
            stats["below_min_market_cap"] += 1
            continue
        volume = _num(row.get("volume")) or 0
        passed.append({
            "symbol": symbol,
            "name": name.strip(),
            "sector": row.get("sector") or None,
            "industry": row.get("industry") or None,
            "country": row.get("country") or None,
            "market_cap": cap,
            "screener_price": price,
            "screener_change_percent": _num(row.get("pctchange")),
            "screener_dollar_volume": price * volume,
        })
    stats["passed_filters"] = len(passed)
    stats["screener_fetched_at"] = snap["fetched_at"]
    return passed, stats


def select_deep_scan(universe: list[dict], limit: int) -> list[dict]:
    """เลือกหุ้นที่จะดึงประวัติราคาเต็ม: ครอบคลุมทั้งตัวที่เทรดหนา ตัวที่ขยับแรง และตัวใหญ่."""
    have_volume = sum(1 for item in universe if item["screener_dollar_volume"] > 0) > len(universe) * 0.5
    by_dollar = sorted(universe, key=lambda x: x["screener_dollar_volume"] if have_volume else x["market_cap"], reverse=True)
    liquid = [x for x in universe if x["screener_dollar_volume"] >= 5e6] if have_volume else universe
    movers = sorted(liquid, key=lambda x: abs(x["screener_change_percent"] or 0), reverse=True)
    by_cap = sorted(universe, key=lambda x: x["market_cap"], reverse=True)

    chosen: dict[str, dict] = {}
    quotas = ((by_dollar, int(limit * 0.55)), (movers, int(limit * 0.2)), (by_cap, limit))
    for ranked, quota in quotas:
        added = 0
        for item in ranked:
            if len(chosen) >= limit or added >= quota:
                break
            if item["symbol"] not in chosen:
                chosen[item["symbol"]] = item
                added += 1
    return list(chosen.values())


# ---------------------------------------------------------------------------
# Stage 3-4
# ---------------------------------------------------------------------------
def _fetch_series(items: list[dict], progress) -> tuple[dict, list[str], int]:
    series_by_symbol, failures, rate_limited = {}, [], 0
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        futures = {pool.submit(get_daily_bars, item["symbol"]): item["symbol"] for item in items}
        for n, future in enumerate(as_completed(futures), start=1):
            symbol = futures[future]
            try:
                series_by_symbol[symbol] = future.result()
            except RateLimitedError:
                rate_limited += 1
                failures.append(symbol)
            except Exception:  # noqa: BLE001 — หุ้นตัวเดียวพังต้องไม่ล้มทั้งการสแกน
                failures.append(symbol)
            if n % 25 == 0:
                progress(f"ดึงแท่งราคาแล้ว {n}/{len(items)}")
    return series_by_symbol, failures, rate_limited


def _compact(item: dict, t: dict, scored: dict, fundamentals: dict | None, news: dict | None, freshness: dict, confidence: dict, mtf: dict) -> dict:
    f = fundamentals or {}

    def fv(key):
        return (f.get(key) or {}).get("value")

    return {
        "symbol": item["symbol"],
        "name": item["name"],
        "sector": item.get("sector"),
        "industry": item.get("industry"),
        "market_cap": item.get("market_cap"),
        "price": t["price"],
        "price_date": t["date"],
        "returns": t["returns"],
        "opportunity_score": scored["score"],
        "components": {k: {"score": v["score"], "max": v["max"]} for k, v in scored["components"].items()},
        "risk": {"level": scored["risk"]["level"], "penalty_points": scored["risk"]["penalty_points"],
                 "factors": [x["explanation"] for x in scored["risk"]["factors"]]},
        "confidence": {"level": confidence["level"], "points": confidence["points"], "max_points": confidence["max_points"]},
        "signals": [{"id": s["id"], "name": s["name"], "direction": s["direction"], "evidence": s["evidence"]} for s in scored["signals"]],
        "trend": {k: mtf[k]["trend"] for k in ("short_term", "medium_term", "long_term")},
        "metrics": {
            "rsi14": t.get("rsi14"),
            "volume_ratio": t.get("volume_ratio"),
            "atr_percent": t.get("atr_percent"),
            "distance_sma200_percent": t.get("distance_sma200_percent"),
            "pct_from_52w_high": t.get("pct_from_52w_high"),
            "avg_dollar_volume_20d": t.get("avg_dollar_volume_20d"),
        },
        "fundamentals": {
            "pe_ratio": fv("pe_ratio"),
            "revenue_growth_percent": fv("revenue_growth_percent"),
            "earnings_growth_percent": fv("earnings_growth_percent"),
            "profit_margin_percent": fv("profit_margin_percent"),
            "coverage": (f.get("_coverage") or {}).get("ratio"),
        } if fundamentals else None,
        "news_count_72h": len([x for x in (news or {}).get("items", []) if (x.get("age_hours") or 1e9) <= 72]) if news else None,
        "days_to_earnings": scored.get("days_to_earnings"),
        "freshness": {"last_bar_date": freshness["last_bar_date"], "is_stale": freshness["is_stale"], "sessions_behind": freshness["sessions_behind"]},
    }


def run_discovery(strategy: Strategy, params: dict, progress=lambda message: None) -> dict:
    started = time.perf_counter()
    funnel: dict = {}
    progress("ดึงรายชื่อหุ้นทั้งตลาดจาก Nasdaq screener")
    universe, stats = build_universe(params)
    funnel.update(stats)

    deep_limit = int(params.get("deep_scan_limit", DEEP_SCAN_LIMIT))
    selected = select_deep_scan(universe, deep_limit)
    funnel["deep_scan_selected"] = len(selected)
    progress(f"เลือก {len(selected)} ตัวสำหรับ deep scan")

    market = None
    try:
        market = get_market_context()
    except ProviderUnavailableError:
        pass
    bench = benchmark_closes()

    series_by_symbol, failures, rate_limited = _fetch_series(selected, progress)
    funnel["history_fetched"] = len(series_by_symbol)
    funnel["history_failed"] = len(failures)
    funnel["rate_limited"] = rate_limited
    if not series_by_symbol:
        raise ProviderUnavailableError(sources.NASDAQ, "could not fetch price history for any symbol")

    progress("คำนวณ indicator และสัญญาณ")
    expected = cal.last_completed_session()
    stage3 = []
    stale = insufficient = filtered_out = 0
    item_by_symbol = {item["symbol"]: item for item in selected}
    for symbol, series in series_by_symbol.items():
        if len(series.bars) < 60:
            insufficient += 1
            continue
        freshness = series.freshness()
        if freshness["is_stale"]:
            stale += 1  # ข้อมูลเก่า (หยุดซื้อขาย/ถูกถอน) ไม่นำมาจัดอันดับ
            continue
        ts = an.TechnicalSeries.build(series.bars)
        t = ts.snapshot_at(len(series.bars) - 1, bench)
        if (t.get("avg_dollar_volume_20d") or 0) < float(params.get("min_avg_dollar_volume", 1e6)):
            filtered_out += 1
            continue
        signals = an.detect_signals(t)
        if not strategy.technical_filter(t, signals):
            filtered_out += 1
            continue
        pre = score_all(t, None, None, (market or {}).get("condition"), strategy)
        prelim = an.weighted_score(pre["components"], strategy.weights, technical_only=True)
        stage3.append({"item": item_by_symbol[symbol], "t": t, "freshness": freshness, "prelim": prelim["total"]})
    funnel.update({
        "insufficient_history": insufficient,
        "stale_data_excluded": stale,
        "failed_strategy_or_liquidity_filter": filtered_out,
        "passed_technical_filter": len(stage3),
        "expected_last_session": expected.isoformat(),
    })

    if strategy.needs_fundamentals_first:
        stage3.sort(key=lambda x: x["item"]["market_cap"], reverse=True)
        finalists = stage3[: FINALIST_LIMIT * 2]
    else:
        stage3.sort(key=lambda x: x["prelim"], reverse=True)
        finalists = stage3[:FINALIST_LIMIT]
    funnel["finalists_for_fundamentals"] = len(finalists)

    progress(f"ดึงข้อมูลพื้นฐานของ finalists {len(finalists)} ตัว")
    fundamentals_by_symbol: dict[str, dict | None] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(get_fundamentals, x["item"]["symbol"], x["t"]["price"]): x["item"]["symbol"] for x in finalists}
        for future in as_completed(futures):
            try:
                fundamentals_by_symbol[futures[future]] = future.result()
            except Exception:  # noqa: BLE001
                fundamentals_by_symbol[futures[future]] = None
    funnel["fundamentals_available"] = sum(1 for v in fundamentals_by_symbol.values() if v)

    ranked = []
    fund_filtered = risk_filtered = 0
    for x in finalists:
        f = fundamentals_by_symbol.get(x["item"]["symbol"])
        if strategy.fundamental_filter is not None and (f is None or not strategy.fundamental_filter(f)):
            fund_filtered += 1
            continue
        scored = score_all(x["t"], f, None, (market or {}).get("condition"), strategy)
        if scored["risk"]["level"] not in strategy.risk_levels:
            risk_filtered += 1
            continue
        ranked.append({**x, "f": f, "scored": scored})
    ranked.sort(key=lambda x: x["scored"]["score"]["total"], reverse=True)
    funnel["failed_fundamental_filter"] = fund_filtered
    funnel["failed_risk_filter"] = risk_filtered

    limit = int(params.get("limit", RESULT_LIMIT))
    top = ranked[:limit]
    progress(f"ดึงข่าวของ {len(top)} อันดับแรก")
    with ThreadPoolExecutor(max_workers=6) as pool:
        news_futures = {x["item"]["symbol"]: pool.submit(get_news, x["item"]["symbol"], x["item"]["name"]) for x in top}
    results = []
    for x in top:
        news = news_futures[x["item"]["symbol"]].result()
        # รวมข่าวเข้าคะแนน catalyst แล้วคำนวณคะแนนใหม่ — ทุกตัวใน top ใช้กติกาเดียวกัน
        scored = score_all(x["t"], x["f"], news, (market or {}).get("condition"), strategy)
        mtf = an.multi_timeframe(x["t"])
        confidence = an.assess_confidence(x["t"], x["f"], news, x["freshness"], scored["signals"], mtf)
        results.append(_compact(x["item"], x["t"], scored, x["f"], news, x["freshness"], confidence, mtf))
    results.sort(key=lambda r: r["opportunity_score"]["total"], reverse=True)
    for rank, row in enumerate(results, start=1):
        row["rank"] = rank
    funnel["ranked"] = len(ranked)
    funnel["returned"] = len(results)
    funnel["duration_seconds"] = round(time.perf_counter() - started, 1)
    logger.info("screening_done", extra={"event": "screening", "strategy": strategy.id, "funnel": funnel})

    return {
        "market": "US",
        "strategy": {"id": strategy.id, "name": strategy.name, "description": strategy.description,
                     "weights": strategy.weights, "filter": strategy.filter_description},
        "as_of_session": expected.isoformat(),
        "funnel": funnel,
        "market_context": {"condition": (market or {}).get("condition"), "evidence": (market or {}).get("evidence")},
        "results": results,
        "ranking_method": "deterministic weighted score (no LLM in selection/ranking)",
        "notes": [
            "คะแนนคำนวณจากแท่งราคาปิดรายวัน (EOD) ของ session ล่าสุดที่ปิดแล้ว ไม่ใช่ราคา realtime",
            "ข้อมูลพื้นฐานดึงเฉพาะ finalists เพื่อลดจำนวน request — หุ้นที่ตกรอบเทคนิคจะไม่ถูกประเมินพื้นฐาน",
            "Stage 1 ใช้ราคา/มูลค่าตลาดจาก Nasdaq screener ณ เวลาที่ดึง เพื่อคัดกรองเบื้องต้นเท่านั้น",
        ],
    }


# ---------------------------------------------------------------------------
# Job runner — คิวเดียว รันทีละงาน (แชร์แคชแท่งราคา ไม่แย่งโควตา provider กันเอง)
# ---------------------------------------------------------------------------
_JOBS: "queue.Queue[tuple[int | str, Strategy, dict]]" = queue.Queue()
_STATUS: dict = {}
_LATEST: dict[str, dict] = {}
_WORKER_STARTED = False
_WORKER_LOCK = threading.Lock()


def _key(market: str, strategy_id: str) -> str:
    return f"{market}:{strategy_id}"


def _ensure_worker() -> None:
    global _WORKER_STARTED
    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return
        _WORKER_STARTED = True
        threading.Thread(target=_worker_loop, name="discovery-worker", daemon=True).start()


def _worker_loop() -> None:
    while True:
        run_id, strategy, params = _JOBS.get()
        status = _STATUS[run_id]
        status.update({"status": "running", "progress": "เริ่มสแกน"})
        store.update_run("discovery_runs", run_id if isinstance(run_id, int) else None, {"status": "running"})

        def progress(message: str, _status=status) -> None:
            _status["progress"] = message

        try:
            result = run_discovery(strategy, params, progress)
            status.update({"status": "completed", "progress": "เสร็จ", "finished_at": iso(utcnow()), "result": result})
            _LATEST[_key("US", strategy.id)] = {**result, "run_id": run_id, "finished_at": status["finished_at"]}
            store.update_run("discovery_runs", run_id if isinstance(run_id, int) else None, {
                "status": "completed", "finished_at": utcnow(), "funnel": result["funnel"],
                "market_context": result["market_context"], "results": result, "as_of_session": result["as_of_session"],
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("discovery_failed", extra={"event": "screening_failed", "strategy": strategy.id}, exc_info=True)
            status.update({"status": "failed", "error": str(exc)[:300], "finished_at": iso(utcnow())})
            store.update_run("discovery_runs", run_id if isinstance(run_id, int) else None,
                             {"status": "failed", "error": str(exc)[:300], "finished_at": utcnow()})


def start_discovery(strategy_id: str, market: str = "US", params: dict | None = None, weights: dict | None = None) -> dict:
    market = market.upper()
    if market not in SUPPORTED_MARKETS:
        raise ValueError(f"unknown market: {market}")
    if SUPPORTED_MARKETS[market]["status"] != "supported":
        raise LookupError(SUPPORTED_MARKETS[market]["reason"])
    strategy = get_strategy(strategy_id, weights)
    params = dict(params or {})
    if weights:
        params["weights"] = strategy.weights

    # งานเดิมของกลยุทธ์เดียวกันยังรันอยู่ → คืนงานนั้นแทนการสแกนซ้อน
    for run_id, status in _STATUS.items():
        if status["strategy"] == strategy.id and status["status"] in {"queued", "running"} and status.get("params") == params:
            return _public_status(run_id)

    run_id = store.insert_run("discovery_runs", {"market": market, "strategy": strategy.id, "params": params, "status": "queued"})
    if run_id is None:
        run_id = f"mem-{int(time.time() * 1000)}"
    _STATUS[run_id] = {"id": run_id, "market": market, "strategy": strategy.id, "params": params, "status": "queued",
                       "progress": f"รอคิว ({_JOBS.qsize()} งานก่อนหน้า)", "started_at": iso(utcnow())}
    _JOBS.put((run_id, strategy, params))
    _ensure_worker()
    return _public_status(run_id)


def _public_status(run_id) -> dict:
    status = _STATUS[run_id]
    return {k: v for k, v in status.items() if k != "result"}


def get_run(run_id) -> dict | None:
    key = run_id
    if isinstance(run_id, str) and run_id.isdigit():
        key = int(run_id)
    if key in _STATUS:
        status = _STATUS[key]
        return {**_public_status(key), "result": status.get("result")}
    if isinstance(key, int):
        row = store.fetch_run("discovery_runs", run_id=key)
        if row:
            status = row["status"]
            if status in {"queued", "running"}:
                # อยู่ใน DB แต่ไม่อยู่ในหน่วยความจำ = เซิร์ฟเวอร์รีสตาร์ทระหว่างรัน
                status = "failed"
                row["error"] = row.get("error") or "interrupted: server restarted during the scan"
            return {"id": row["id"], "market": row["market"], "strategy": row["strategy"], "status": status,
                    "error": row.get("error"), "started_at": row["started_at"], "finished_at": row.get("finished_at"),
                    "result": row.get("results")}
    return None


def latest_result(strategy_id: str, market: str = "US") -> dict | None:
    cached = _LATEST.get(_key(market, strategy_id))
    if cached is None:
        row = store.fetch_run("discovery_runs", where={"market": market, "strategy": strategy_id, "status": "completed"})
        if row and row.get("results"):
            cached = {**row["results"], "run_id": row["id"], "finished_at": row.get("finished_at")}
            _LATEST[_key(market, strategy_id)] = cached
    if cached is None:
        return None
    expected = cal.last_completed_session().isoformat()
    return {**cached, "is_stale": cached.get("as_of_session") != expected, "expected_last_session": expected}
