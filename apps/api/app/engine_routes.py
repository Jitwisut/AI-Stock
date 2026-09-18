"""API ของ Discovery engine — ข้อมูลจริง + คะแนน deterministic + AI อธิบาย.

เมื่อ provider ใช้ไม่ได้ ตอบ 503 พร้อม provider / last successful update / retry status
แทนการสร้างข้อมูลทดแทน
"""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from apps.api.app.security import require_admin_api_key
from workers.engine import discovery, market_calendar, runtime
from workers.engine.runtime import ProviderUnavailableError, SymbolNotFoundError

router = APIRouter()

_SYMBOL = r"^[A-Za-z][A-Za-z0-9.\-]{0,9}$"


def unavailable_response(exc: ProviderUnavailableError) -> JSONResponse:
    health = runtime.health(exc.provider).as_dict()
    return JSONResponse(
        status_code=503,
        content={
            "detail": "Data temporarily unavailable",
            "provider": exc.provider,
            "error": str(exc),
            "last_successful_update": health["last_success_at"],
            "retry_status": health["status"],
            "retry_after_seconds": health["retry_after_seconds"],
        },
    )


@router.get("/market/status")
def market_status() -> dict:
    return {
        "session": market_calendar.market_session(),
        "markets": discovery.SUPPORTED_MARKETS,
        "providers": runtime.all_health(),
    }


@router.get("/market/context")
def market_context():
    from workers.engine.market_context import get_market_context

    try:
        return get_market_context()
    except ProviderUnavailableError as exc:
        return unavailable_response(exc)


@router.get("/discovery/strategies")
def discovery_strategies() -> dict:
    from workers.engine.strategies import describe_all

    return {"strategies": describe_all(), "markets": discovery.SUPPORTED_MARKETS}


@router.post("/discovery/run")
def discovery_run(
    strategy: str = Query(default="balanced", pattern="^[a-z_]{3,30}$"),
    market: str = Query(default="US", pattern="^[A-Za-z]{2,5}$"),
    limit: int = Query(default=25, ge=5, le=50),
    min_price: float = Query(default=2.0, ge=0.5, le=1000),
    min_market_cap: float = Query(default=300e6, ge=0, le=5e12),
    deep_scan_limit: Optional[int] = Query(default=None, ge=50, le=1000),
    w_trend: Optional[float] = Query(default=None, ge=0, le=100),
    w_momentum: Optional[float] = Query(default=None, ge=0, le=100),
    w_volume: Optional[float] = Query(default=None, ge=0, le=100),
    w_fundamental: Optional[float] = Query(default=None, ge=0, le=100),
    w_risk: Optional[float] = Query(default=None, ge=0, le=100),
    w_catalyst: Optional[float] = Query(default=None, ge=0, le=100),
    _: None = Depends(require_admin_api_key),
) -> dict:
    params = {"limit": limit, "min_price": min_price, "min_market_cap": min_market_cap}
    if deep_scan_limit:
        params["deep_scan_limit"] = deep_scan_limit
    weights = None
    if strategy == "custom":
        weights = {
            "trend": w_trend or 0, "momentum": w_momentum or 0, "volume": w_volume or 0,
            "fundamental": w_fundamental or 0, "risk": w_risk or 0, "catalyst": w_catalyst or 0,
        }
    try:
        return discovery.start_discovery(strategy, market, params, weights)
    except LookupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/discovery/runs/{run_id}")
def discovery_run_status(run_id: str) -> dict:
    run = discovery.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Discovery run not found")
    return run


@router.get("/discovery/latest")
def discovery_latest(
    strategy: str = Query(default="balanced", pattern="^[a-z_]{3,30}$"),
    market: str = Query(default="US"),
) -> dict:
    result = discovery.latest_result(strategy, market.upper())
    if result is None:
        return {"status": "empty", "strategy": strategy, "market": market.upper(), "results": []}
    return {"status": "ok", **result}


@router.get("/stocks/{symbol}/analysis")
def stock_analysis_v2(symbol: str, strategy: str = Query(default="balanced", pattern="^[a-z_]{3,30}$")):
    import re

    from workers.engine.stock import analyze_symbol

    if not re.match(_SYMBOL, symbol):
        raise HTTPException(status_code=400, detail="invalid symbol")
    try:
        return analyze_symbol(symbol, strategy)
    except SymbolNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProviderUnavailableError as exc:
        return unavailable_response(exc)


@router.get("/stocks/{symbol}/ai")
async def stock_ai(symbol: str, strategy: str = Query(default="balanced", pattern="^[a-z_]{3,30}$"), refresh: bool = False):
    """AI อธิบายผลวิเคราะห์ (แคชตาม snapshot ข้อมูล) — แยก endpoint เพราะใช้เวลานานและกินโควตา."""
    import re

    from workers.engine.ai_explain import explain
    from workers.engine.stock import analyze_symbol

    if not re.match(_SYMBOL, symbol):
        raise HTTPException(status_code=400, detail="invalid symbol")
    try:
        analysis = await asyncio.to_thread(analyze_symbol, symbol, strategy)
    except SymbolNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProviderUnavailableError as exc:
        return unavailable_response(exc)
    return await explain(analysis, force=refresh)


_RANGES = {"1D": None, "5D": 5, "1M": 21, "3M": 63, "6M": 126, "1Y": 252, "5Y": 1260}


@router.get("/stocks/{symbol}/chart")
def stock_chart(symbol: str, range: str = Query(default="6M", pattern="^(1D|5D|1M|3M|6M|1Y|5Y)$")):  # noqa: A002
    import re

    from workers.engine import indicators as ind
    from workers.engine import sources
    from workers.engine.bars import get_daily_bars

    if not re.match(_SYMBOL, symbol):
        raise HTTPException(status_code=400, detail="invalid symbol")
    try:
        if range == "1D":
            payload, _asset = sources.nasdaq_asset_json("quote/{symbol}/chart", symbol)
            data = payload.get("data") or {}
            points = [
                {"t": point.get("x"), "time_et": (point.get("z") or {}).get("dateTime"), "price": point.get("y")}
                for point in data.get("chart") or []
                if point.get("y") is not None
            ]
            return {
                "symbol": symbol.upper(),
                "range": range,
                "interval": "1m",
                "points": points,
                "source": "nasdaq_intraday_chart",
                "market_timestamp": data.get("timeAsOf"),
                "note": "ราคา intraday รายนาทีจาก Nasdaq (รวมช่วง pre-market ตั้งแต่ 4:00 ET) ไม่มีข้อมูลวอลุ่มรายนาที",
            }
        lookback = 1900 if range == "5Y" else 800
        series = get_daily_bars(symbol, lookback_days=lookback)
    except SymbolNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProviderUnavailableError as exc:
        return unavailable_response(exc)

    closes = [bar.close for bar in series.bars]
    sma20, sma50, sma200 = ind.sma(closes, 20), ind.sma(closes, 50), ind.sma(closes, 200)
    count = _RANGES[range]
    start = max(0, len(series.bars) - count)
    bars = [
        {
            "date": bar.day.isoformat(), "open": bar.open, "high": bar.high, "low": bar.low,
            "close": bar.close, "volume": bar.volume,
            "sma20": _round(sma20[i]), "sma50": _round(sma50[i]), "sma200": _round(sma200[i]),
        }
        for i, bar in enumerate(series.bars)
        if i >= start
    ]
    return {
        "symbol": symbol.upper(),
        "range": range,
        "interval": "1d",
        "bars": bars,
        "freshness": series.freshness(),
        "note": "5D ใช้แท่งรายวัน 5 แท่ง (ไม่มีแหล่ง intraday หลายวันที่ฟรีและเชื่อถือได้)" if range == "5D" else None,
    }


def _round(value):
    return round(value, 4) if isinstance(value, (int, float)) else None


@router.get("/data/status")
def data_status() -> dict:
    return {"providers": runtime.all_health(), "session": market_calendar.market_session()}


@router.post("/backtest/run")
def backtest_run(
    sample: int = Query(default=150, ge=20, le=400),
    step: int = Query(default=5, ge=1, le=20),
    _: None = Depends(require_admin_api_key),
) -> dict:
    from workers.engine.backtest import start_backtest

    return start_backtest({"sample": sample, "step": step})


@router.get("/backtest/latest")
def backtest_latest() -> dict:
    from workers.engine.backtest import latest_backtest

    return latest_backtest() or {"status": "empty"}


@router.get("/backtest/runs/{run_id}")
def backtest_status(run_id: str) -> dict:
    from workers.engine.backtest import get_backtest

    run = get_backtest(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Backtest run not found")
    return run
