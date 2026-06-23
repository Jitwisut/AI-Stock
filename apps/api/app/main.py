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


# ----------------------------------------------------------------------
# Telegram Bot Webhook — รับ message จากผู้ใช้แล้วตอบกลับ
# ----------------------------------------------------------------------

import logging

logger = logging.getLogger(__name__)


def _format_surge_line(signal: dict, index: int) -> str:
    """แปลง surge signal dict เป็นข้อความบรรทัดเดียว."""
    grade = (signal.get("surge_grade") or {}).get("grade", "-")
    change = signal.get("premarket_gap_percent", 0.0)
    rel_vol = signal.get("relative_volume", 0.0)
    price = signal.get("latest_price", 0.0)
    return f"{index}. {signal.get('symbol', '?')} [{grade}] {change:+.1f}% · vol {rel_vol:.1f}x · ${price:.2f}"


async def _handle_bot_command(command: str, chat_id: str) -> None:
    """ตอบกลับคำสั่งจากผู้ใช้ใน Telegram."""
    from workers.alerts.telegram import TelegramClient
    from workers.config import get_settings
    from workers.services import get_longterm_signal_dicts, get_surge_signal_dicts

    settings = get_settings()
    telegram = TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)
    if not telegram.is_configured:
        return

    cmd = command.strip().lower()
    try:
        if cmd in ("/surge", "/surges", "/ziing", "/ซิ่ง"):
            signals = get_surge_signal_dicts(limit=10)
            lines = ["🚀 หุ้นซิ่ง Top 10\n"]
            if signals:
                for i, s in enumerate(signals, 1):
                    lines.append(_format_surge_line(s, i))
            else:
                lines.append("— ยังไม่พบหุ้นที่เข้าเกณฑ์ —")
            await telegram.send_message("\n".join(lines))

        elif cmd in ("/longterm", "/long", "/lt", "/ระยะยาว"):
            picks = get_longterm_signal_dicts(limit=10)
            lines = ["📈 หุ้นน่าสนใจระยะยาว Top 10\n"]
            if picks:
                for i, p in enumerate(picks, 1):
                    lines.append(
                        f"{i}. {p['symbol']} [{p['grade']}] คะแนน {p['score']:.0f} · 6 ด. {p['return_6m_percent']:+.0f}% · ${p['price']:.2f}"
                    )
                    reasons = p.get("reasons") or []
                    if reasons:
                        lines.append(f"   • {reasons[0]}")
            else:
                lines.append("— ยังไม่พบหุ้นที่เข้าเกณฑ์ —")
            await telegram.send_message("\n".join(lines))

        elif cmd in ("/digest", "/summary", "/สรุป"):
            from workers.alerts.digest import build_daily_digest

            await telegram.send_message(build_daily_digest())

        elif cmd in ("/start", "/help"):
            help_text = (
                "🤖 คำสั่งที่ใช้ได้:\n\n"
                "/surge — หุ้นซิ่ง Top 10\n"
                "/longterm — หุ้นน่าสนใจระยะยาว Top 10\n"
                "/digest — สรุปรายวัน (ซิ่ง + ระยะยาว)\n"
                "/help — ข้อความนี้"
            )
            await telegram.send_message(help_text)

        else:
            await telegram.send_message(
                "❓ ไม่รู้จักคำสั่งนี้ ลอง /help ดูคำสั่งที่ใช้ได้"
            )
    except Exception as exc:
        logger.exception("bot command failed: %s", exc)
        try:
            await telegram.send_message(f"⚠️ เกิดข้อผิดพลาด: {exc}")
        except Exception:
            pass


@app.post("/telegram/webhook")
async def telegram_webhook(update: dict) -> dict:
    """Telegram Bot API webhook — รับ message จากผู้ใช้แล้วตอบกลับ.

    ตั้งค่า:
      curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" \\
           -d "url=https://ai-stock-e6ks.vercel.app/telegram/webhook"
    """
    message = update.get("message") or update.get("edited_message")
    if not message:
        return {"ok": True}

    text = (message.get("text") or "").strip()
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id", ""))

    if not text or not chat_id:
        return {"ok": True}

    await _handle_bot_command(text, chat_id)
    return {"ok": True}


@app.get("/telegram/set-webhook")
async def telegram_set_webhook(url: Optional[str] = None) -> dict:
    """ตั้ง webhook ให้บอท (เรียกครั้งเดียวหลัง deploy)."""
    import httpx

    from workers.config import get_settings

    settings = get_settings()
    if not settings.telegram_bot_token:
        return {"status": "no_token"}

    target_url = url or "https://ai-stock-e6ks.vercel.app/telegram/webhook"
    api_url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/setWebhook"
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(api_url, json={"url": target_url})
        return {"status": "set", "url": target_url, "telegram_response": resp.json()}
