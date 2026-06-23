from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from apps.api.app.schemas import (
    LongTermOut,
    ScanRunOut,
    ScanRunStatusOut,
    SettingsStatusOut,
    SignalOut,
    StockAnalysisOut,
    StockOut,
)
from workers.config import get_settings
from workers.services import (
    analyze_stock_query,
    get_latest_scan_run_dict,
    get_latest_signal_dicts,
    get_longterm_signal_dicts,
    get_scan_run_dict,
    get_settings_status,
    get_signal_dict,
    get_stock_dicts,
    get_surge_signal_dicts,
    list_signal_dicts,
    provider_ready,
    redis_available,
    run_scan,
)
from workers.repository import database_available
from workers.scanner.fetch_prices import MarketDataUnavailableError

app = FastAPI(title="AI Momentum Stock Radar", version="0.1.0")
settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.backend_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def require_admin_api_key(x_admin_api_key: Optional[str] = Header(default=None)) -> None:
    settings = get_settings()
    if not settings.admin_api_key:
        if settings.app_env == "production":
            raise HTTPException(status_code=503, detail="ADMIN_API_KEY is not configured")
        return
    if x_admin_api_key != settings.admin_api_key:
        raise HTTPException(status_code=403, detail="Invalid admin API key")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/health/live")
def health_live() -> dict:
    return {"status": "ok"}


@app.get("/health/ready")
def health_ready() -> dict:
    settings = get_settings()
    checks = {
        "database": database_available(settings.database_url),
        "redis": redis_available(settings.redis_url),
        "provider": provider_ready()[0],
    }
    ready = all(checks.values())
    if not ready:
        raise HTTPException(status_code=503, detail={"status": "not_ready", "checks": checks})
    return {"status": "ready", "checks": checks}


@app.post("/scan/run", response_model=ScanRunStatusOut)
async def run_scan_endpoint(
    send_telegram: bool = False,
    kind: str = Query(default="momentum", pattern="^(momentum|surge|longterm|universe_refresh)$"),
    _: None = Depends(require_admin_api_key),
) -> dict:
    from workers.tasks import enqueue_scan_job

    return enqueue_scan_job(kind=kind, send_telegram=send_telegram)


@app.post("/alerts/daily-digest")
async def daily_digest_endpoint(
    surge_limit: int = Query(default=5, ge=1, le=20),
    longterm_limit: int = Query(default=5, ge=1, le=20),
    _: None = Depends(require_admin_api_key),
) -> dict:
    """Triggered by Vercel Cron Jobs — ส่งสรุปรายวันเข้า Telegram.

    Vercel Cron เรียก endpoint นี้ตอน 08:00 และ 20:00 (Asia/Bangkok)
    ดู vercel.json → crons[]
    """
    from workers.alerts.digest import send_daily_digest

    return await send_daily_digest(surge_limit=surge_limit, longterm_limit=longterm_limit)


@app.post("/alerts/deploy-notify")
async def deploy_notify_endpoint(
    _: None = Depends(require_admin_api_key),
) -> dict:
    """Vercel Deploy Hook เรียกเมื่อ deploy สำเร็จ — ส่งข้อความ "พร้อมแล้ว" เข้า Telegram.

    ตั้งค่า: Vercel → Settings → Git → Deploy Hooks → สร้าง hook ชี้มาที่
    https://ai-stock-e6ks.vercel.app/alerts/deploy-notify
    แล้วใส่ header x-admin-api-key = ADMIN_API_KEY ของคุณ
    """
    import os

    from workers.alerts.telegram import TelegramClient
    from workers.config import get_settings

    settings = get_settings()
    telegram = TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)

    # ใช้ Vercel env (VERCEL_GIT_COMMIT_SHA) ดึง commit info แทน git CLI
    commit_sha = os.environ.get("VERCEL_GIT_COMMIT_SHA", "")
    commit_msg = os.environ.get("VERCEL_GIT_COMMIT_MESSAGE", "")
    git_info = f"`{commit_sha[:7]}` {commit_msg}" if commit_sha else "(no commit info)"

    from datetime import datetime, timedelta, timezone

    try:
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo("Asia/Bangkok"))
    except Exception:
        now = datetime.now(timezone(timedelta(hours=7)))

    text = (
        "🚀 *Deploy สำเร็จ!*\n\n"
        f"⏰ {now.strftime('%d/%m/%Y %H:%M')} (Asia/Bangkok)\n"
        f"📦 Commit: {git_info}\n\n"
        "✅ บอทพร้อมส่งหุ้นซิ่ง + หุ้นระยะยาวเข้า chat นี้"
    )

    if not telegram.is_configured:
        return {"status": "telegram_not_configured"}

    sent = await telegram.send_message(text)
    return {"status": "sent" if sent else "failed", "sent": sent}


@app.get("/signals/scan", response_model=ScanRunOut)
async def scan_signals(
    send_telegram: bool = False,
    persist: Optional[bool] = None,
    _: None = Depends(require_admin_api_key),
) -> dict:
    return await run_scan(send_telegram=send_telegram, persist=persist)


@app.get("/scan/runs/latest", response_model=Optional[ScanRunStatusOut])
def latest_scan_run(
    kind: Optional[str] = Query(default=None, pattern="^(momentum|surge|longterm|universe_refresh)$"),
) -> dict | None:
    return get_latest_scan_run_dict(kind=kind)


@app.get("/scan/runs/{scan_run_id}", response_model=ScanRunStatusOut)
def scan_run_detail(scan_run_id: int) -> dict:
    scan_run = get_scan_run_dict(scan_run_id)
    if not scan_run:
        raise HTTPException(status_code=404, detail="Scan run not found")
    return scan_run


@app.get("/signals/latest", response_model=list[SignalOut])
def latest_signals(limit: int = Query(default=5, ge=1, le=20)) -> list[dict]:
    return get_latest_signal_dicts(limit=limit)


@app.get("/signals/surge", response_model=list[SignalOut])
def surge_signals(limit: int = Query(default=50, ge=1, le=100)) -> list[dict]:
    """หุ้นซิ่งสำหรับสายถือไว-ออกไว เรียงตาม surge_score (สแกนสด)."""
    return get_surge_signal_dicts(limit=limit)


@app.get("/signals/longterm", response_model=list[LongTermOut])
def longterm_signals(limit: int = Query(default=15, ge=1, le=50)) -> list[dict]:
    """หุ้นน่าสนใจสำหรับลงทุนระยะยาว จัดอันดับตามคะแนนเทรนด์ (แคช 6 ชม.)."""
    return get_longterm_signal_dicts(limit=limit)


@app.get("/signals", response_model=list[SignalOut])
def signal_history(
    limit: int = Query(default=25, ge=1, le=100),
    symbol: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> list[dict]:
    return list_signal_dicts(
        limit=limit,
        symbol=symbol,
        date_from=date_from,
        date_to=date_to,
    )


@app.get("/signals/{signal_id}", response_model=SignalOut)
def signal_detail(signal_id: int) -> dict:
    signal = get_signal_dict(signal_id)
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    return signal


@app.get("/analysis/stock", response_model=StockAnalysisOut)
async def stock_analysis(query: str = Query(min_length=1, max_length=80)) -> dict:
    try:
        return await analyze_stock_query(query)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MarketDataUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/stocks", response_model=list[StockOut])
def stocks() -> list[dict]:
    return get_stock_dicts()


@app.get("/settings/status", response_model=SettingsStatusOut)
def settings_status() -> dict:
    return get_settings_status()
