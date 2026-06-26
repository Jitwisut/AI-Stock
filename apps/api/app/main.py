from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

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
from workers.repository import database_available, run_migrations
from workers.scanner.fetch_prices import MarketDataUnavailableError

async def _on_startup() -> None:
    """ส่ง deploy notification + digest เข้า Telegram และลงทะเบียน webhook อัตโนมัติ."""
    await asyncio.sleep(5)  # รอให้ server boot เสร็จก่อน
    settings = get_settings()
    if settings.app_env != "production":
        return

    # --- ลงทะเบียน Telegram webhook โดยใช้ RENDER_EXTERNAL_URL ที่ Render inject ให้ ---
    render_url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    if render_url and settings.telegram_bot_token:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"https://api.telegram.org/bot{settings.telegram_bot_token}/setWebhook",
                    json={"url": f"{render_url}/telegram/webhook"},
                )
            logger.info("Telegram webhook registered: %s/telegram/webhook", render_url)
        except Exception as exc:
            logger.warning("webhook registration failed: %s", exc)

    if not settings.send_telegram:
        return

    from workers.alerts.digest import build_daily_digest
    from workers.alerts.telegram import TelegramClient

    telegram = TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)
    if not telegram.is_configured:
        return

    # commit info จากไฟล์ที่ Dockerfile สร้างไว้
    try:
        commit_info = Path("/app/COMMIT_INFO.txt").read_text().strip()[:120]
    except Exception:
        commit_info = "(ไม่พบข้อมูล commit)"

    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Asia/Bangkok"))
    except Exception:
        now = datetime.now(timezone(timedelta(hours=7)))

    # 1. Deploy notification
    try:
        await telegram.send_message(
            f"🚀 <b>Deploy สำเร็จ!</b>\n\n"
            f"⏰ {now.strftime('%d/%m/%Y %H:%M')} (Asia/Bangkok)\n"
            f"📦 {commit_info}\n\n"
            "✅ บอทพร้อมตอบ /surge /longterm /digest /help",
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.warning("deploy notify failed: %s", exc)

    # 2. ส่งข้อมูลหุ้นซิ่ง + ระยะยาว วันนั้นทันที
    try:
        await telegram.send_message(build_daily_digest())
    except Exception as exc:
        logger.warning("startup digest failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    run_migrations(settings.database_url)
    asyncio.create_task(_on_startup())
    yield


app = FastAPI(title="AI Momentum Stock Radar", version="0.1.0", lifespan=lifespan)
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
async def deploy_notify_endpoint() -> dict:
    """Deploy Hook — ส่งข้อความ "พร้อมแล้ว" เข้า Telegram ทุกครั้งที่ deploy สำเร็จ.

    ใช้กับ Vercel Deploy Hook (Settings → Git → Deploy Hooks) หรือเรียก
    ตรงจาก Railway post-deploy ก็ได้ ไม่ต้องใช้ admin key (เป็น internal hook)
    """
    import os

    from workers.alerts.telegram import TelegramClient
    from workers.config import get_settings

    settings = get_settings()
    telegram = TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)

    # Vercel system env vars (มีที่ runtime เฉพาะบน Vercel production)
    commit_sha = os.environ.get("VERCEL_GIT_COMMIT_SHA", "")
    commit_ref = os.environ.get("VERCEL_GIT_COMMIT_REF", "")
    commit_msg = os.environ.get("VERCEL_GIT_COMMIT_MESSAGE", "")

    if commit_sha:
        parts = [f"<code>{commit_sha[:7]}</code>"]
        if commit_ref:
            parts.append(f"({commit_ref})")
        if commit_msg:
            parts.append(commit_msg)
        git_info = " ".join(parts)
    else:
        # Fallback สำหรับ Railway — ลองอ่านจากไฟล์ที่ Dockerfile สร้างไว้
        try:
            from pathlib import Path

            commit_file = Path("/app/COMMIT_INFO.txt")
            if commit_file.exists():
                git_info = commit_file.read_text().strip()[:120]
            else:
                git_info = "(ไม่พบข้อมูล commit)"
        except Exception:
            git_info = "(ไม่พบข้อมูล commit)"

    from datetime import datetime, timedelta, timezone

    try:
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo("Asia/Bangkok"))
    except Exception:
        now = datetime.now(timezone(timedelta(hours=7)))

    text = (
        "🚀 <b>Deploy สำเร็จ!</b>\n\n"
        f"⏰ {now.strftime('%d/%m/%Y %H:%M')} (Asia/Bangkok)\n"
        f"📦 Commit: {git_info}\n\n"
        "✅ บอทพร้อมตอบ /surge /longterm /digest /help"
    )

    if not telegram.is_configured:
        return {"status": "telegram_not_configured"}

    sent = await telegram.send_message(text, parse_mode="HTML")
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


def _format_surge_line(signal: dict, index: int) -> str:
    """แปลง surge signal dict เป็นข้อความบรรทัดเดียว."""
    grade = (signal.get("surge_grade") or {}).get("grade", "-")
    change = signal.get("premarket_gap_percent", 0.0)
    rel_vol = signal.get("relative_volume", 0.0)
    price = signal.get("latest_price", 0.0)
    return f"{index}. {signal.get('symbol', '?')} [{grade}] {change:+.1f}% · vol {rel_vol:.1f}x · ${price:.2f}"


async def _run_scan_and_get(kind: str, limit: int = 10) -> list[dict]:
    """รัน scan แล้วคืนผลลัพธ์ทันที (ใช้ตอนไม่มีข้อมูลใน DB)."""
    import asyncio, concurrent.futures
    from workers.services import (
        create_queued_scan, execute_scan_job,
        get_surge_signal_dicts, get_longterm_signal_dicts,
    )

    def _scan():
        run = create_queued_scan(kind=kind)
        execute_scan_job(run["id"], kind=kind, send_telegram=False)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(concurrent.futures.ThreadPoolExecutor(max_workers=1), _scan)
    return get_surge_signal_dicts(limit) if kind == "surge" else get_longterm_signal_dicts(limit)


async def _handle_bot_command(command: str, chat_id: str) -> None:
    """ตอบกลับคำสั่งจากผู้ใช้ใน Telegram."""
    from workers.alerts.telegram import TelegramClient
    from workers.config import get_settings
    from workers.services import get_longterm_signal_dicts, get_surge_signal_dicts

    settings = get_settings()
    telegram = TelegramClient(settings.telegram_bot_token, None)
    if not telegram.is_configured:
        return

    cmd = command.strip().lower()
    try:
        if cmd in ("/surge", "/surges", "/ziing", "/ซิ่ง"):
            signals = get_surge_signal_dicts(limit=10)
            if not signals:
                await telegram.send_message("🔄 ยังไม่มีข้อมูล กำลังสแกนหุ้น... รอสักครู่", chat_id=chat_id)
                signals = await _run_scan_and_get("surge", limit=10)
            lines = ["🚀 หุ้นซิ่ง Top 10\n"]
            if signals:
                for i, s in enumerate(signals, 1):
                    lines.append(_format_surge_line(s, i))
            else:
                lines.append("— ยังไม่พบหุ้นที่เข้าเกณฑ์ —")
            await telegram.send_message("\n".join(lines), chat_id=chat_id)

        elif cmd in ("/longterm", "/long", "/lt", "/ระยะยาว"):
            picks = get_longterm_signal_dicts(limit=10)
            if not picks:
                await telegram.send_message("🔄 ยังไม่มีข้อมูล กำลังสแกนหุ้น... รอสักครู่", chat_id=chat_id)
                picks = await _run_scan_and_get("longterm", limit=10)
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
            await telegram.send_message("\n".join(lines), chat_id=chat_id)

        elif cmd in ("/scan", "/สแกน", "/update"):
            await telegram.send_message("🔄 เริ่ม scan หุ้น... รอประมาณ 1-2 นาที", chat_id=chat_id)
            from workers.services import create_queued_scan, execute_scan_job
            import asyncio, concurrent.futures

            def _run_scans():
                for kind in ("surge", "longterm"):
                    try:
                        run = create_queued_scan(kind=kind)
                        execute_scan_job(run["id"], kind=kind, send_telegram=False)
                    except Exception as e:
                        logger.warning("scan %s failed: %s", kind, e)

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(concurrent.futures.ThreadPoolExecutor(), _run_scans)
            await telegram.send_message("✅ Scan เสร็จแล้ว! ลอง /surge หรือ /longterm ได้เลย", chat_id=chat_id)

        elif cmd in ("/digest", "/summary", "/สรุป"):
            from workers.alerts.digest import build_daily_digest

            await telegram.send_message(build_daily_digest(), chat_id=chat_id)

        elif cmd in ("/start", "/help"):
            help_text = (
                "🤖 คำสั่งที่ใช้ได้:\n\n"
                "/scan — สแกนหุ้นใหม่ (ใช้ครั้งแรกหรือเมื่อข้อมูลหมดอายุ)\n"
                "/surge — หุ้นซิ่ง Top 10\n"
                "/longterm — หุ้นน่าสนใจระยะยาว Top 10\n"
                "/digest — สรุปรายวัน (ซิ่ง + ระยะยาว)\n"
                "/help — ข้อความนี้"
            )
            await telegram.send_message(help_text, chat_id=chat_id)

        else:
            await telegram.send_message(
                "❓ ไม่รู้จักคำสั่งนี้ ลอง /help ดูคำสั่งที่ใช้ได้",
                chat_id=chat_id,
            )
    except Exception as exc:
        logger.exception("bot command failed: %s", exc)
        try:
            await telegram.send_message(f"⚠️ เกิดข้อผิดพลาด: {exc}", chat_id=chat_id)
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
