from __future__ import annotations

import asyncio
import logging
import math
import os
import re
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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

logger = logging.getLogger(__name__)

# เมนูคำสั่งที่ส่งให้ Telegram ผ่าน setMyCommands (โชว์ autocomplete ในแชท)
_BOT_COMMAND_MENU = [
    {"command": "today", "description": "หุ้นน่าสนใจวันนี้ (AI อ่านกราฟ+ข่าว)"},
    {"command": "analyze", "description": "วิเคราะห์หุ้นรายตัว เช่น /analyze AAPL"},
    {"command": "compare", "description": "เทียบหุ้น เช่น /compare AAPL MSFT"},
    {"command": "chart", "description": "กราฟราคา 60 วัน เช่น /chart NVDA"},
    {"command": "news", "description": "ข่าวล่าสุดของหุ้น"},
    {"command": "earnings", "description": "วันประกาศงบ + EPS"},
    {"command": "market", "description": "ภาพรวมตลาดสหรัฐ"},
    {"command": "watch", "description": "เตือนเมื่อราคาถึงเป้า เช่น /watch AAPL 250"},
    {"command": "watchlist", "description": "ดูรายการเฝ้าดู"},
    {"command": "unwatch", "description": "ลบหุ้นออกจากรายการเฝ้าดู"},
    {"command": "port", "description": "พอร์ตจำลอง กำไร/ขาดทุน"},
    {"command": "subscribe", "description": "รับสรุปรายวันอัตโนมัติ"},
    {"command": "unsubscribe", "description": "ยกเลิกสรุปรายวัน"},
    {"command": "surge", "description": "หุ้นซิ่งวันนี้ (คัดกรองโดย AI)"},
    {"command": "longterm", "description": "หุ้นน่าสนใจระยะยาว"},
    {"command": "digest", "description": "สรุปรายวัน (ซิ่ง + ระยะยาว)"},
    {"command": "scan", "description": "สแกนหุ้นใหม่ทันที"},
    {"command": "help", "description": "ดูคำสั่งทั้งหมด"},
]


# งานพื้นหลังต้องถูกอ้างอิงไว้ ไม่งั้น event loop เก็บแค่ weak reference แล้ว GC
# อาจเก็บ task กลางคัน — อาการคือบอทเงียบเป็นครั้งคราวโดยไม่มี error ใน log
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


def telegram_webhook_secret() -> str:
    """secret_token สำหรับ webhook — derive จาก bot token จึงไม่ต้องตั้ง env var เพิ่ม.

    Telegram จะส่งค่านี้กลับมาใน header X-Telegram-Bot-Api-Secret-Token ทุกครั้ง
    ใช้พิสูจน์ว่า update มาจาก Telegram จริง ไม่ใช่คนที่เดา URL เจอ
    """
    import hashlib

    settings = get_settings()
    if not settings.telegram_bot_token:
        return ""
    return hashlib.sha256(f"webhook:{settings.telegram_bot_token}".encode()).hexdigest()


async def _register_telegram_webhook(render_url: str) -> None:
    """ลงทะเบียน webhook + เมนูคำสั่งกับ Telegram และ log ผลจริงที่ตอบกลับมา."""
    import httpx

    settings = get_settings()
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/setWebhook",
            json={
                "url": f"{render_url}/telegram/webhook",
                "secret_token": telegram_webhook_secret(),
            },
        )
        # ของเดิมทิ้ง response ทั้งก้อน ทำให้การลงทะเบียนล้มเหลวแบบเงียบ ๆ มองไม่เห็นเลย
        body = response.json()
        if body.get("ok"):
            logger.info("Telegram webhook registered: %s/telegram/webhook", render_url)
        else:
            logger.error("Telegram setWebhook rejected: %s", body)
        await client.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/setMyCommands",
            json={"commands": _BOT_COMMAND_MENU},
        )
        me = (await client.get(f"https://api.telegram.org/bot{settings.telegram_bot_token}/getMe")).json()
        global _BOT_USERNAME
        _BOT_USERNAME = ((me.get("result") or {}).get("username") or None) if me.get("ok") else None


async def _on_startup() -> None:
    """ส่ง deploy notification + digest เข้า Telegram และลงทะเบียน webhook อัตโนมัติ."""
    await asyncio.sleep(5)  # รอให้ server boot เสร็จก่อน
    settings = get_settings()

    # --- ลงทะเบียน Telegram webhook โดยใช้ RENDER_EXTERNAL_URL ที่ Render inject ให้ ---
    # ไม่ผูกกับ APP_ENV: service จริงบน Render รายงาน app_env=development อยู่ จึงเคย
    # ข้ามขั้นนี้ทั้งหมด ทำให้ webhook ค้างชี้ service เก่าที่ตายแล้วและบอทเงียบสนิท
    render_url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    if render_url and settings.telegram_bot_token:
        try:
            await _register_telegram_webhook(render_url)
        except Exception as exc:
            logger.warning("webhook registration failed: %s", exc)

    # เช็คราคาเตือน watchlist เป็นรอบ ๆ — ปิดไว้เป็นค่าเริ่มต้น (PRICE_ALERTS_ENABLED)
    # เปิดเมื่อไม่มี Celery worker และยอมให้ web instance ทำงานนี้เอง
    if settings.price_alerts_enabled:
        async def _alert_loop() -> None:
            from workers.bot_features import send_price_alerts

            interval = settings.price_alerts_interval_minutes * 60
            while True:
                try:
                    result = await send_price_alerts()
                    if result.get("sent"):
                        logger.info("price alerts sent: %s", result)
                except Exception as exc:
                    logger.warning("price alert loop error: %s", exc)
                await asyncio.sleep(interval)

        _spawn(_alert_loop())

    # อุ่น cache "หุ้นน่าสนใจวันนี้" ไว้ล่วงหน้า — รอบแรกของวันดึงแท่งราคาหลายร้อยตัวจากทั้งตลาด
    # ถ้าไม่อุ่นไว้ ผู้เข้าชมคนแรกหลัง deploy จะต้องรอทั้งก้อน
    if settings.use_live_data:
        async def _warm_picks() -> None:
            try:
                from workers.daily_picks import get_daily_picks
                from workers.services import is_refreshing

                # เริ่มหลังสแกนซิ่ง/ระยะยาวเสร็จ — ยิง Nasdaq หลายร้อย request พร้อมกันเสี่ยงโดน rate limit
                await asyncio.sleep(50)
                for _ in range(48):
                    if not (is_refreshing("surge") or is_refreshing("longterm")):
                        break
                    await asyncio.sleep(5)
                await get_daily_picks(limit=10, wait=True)
                logger.info("daily picks cache warmed")
            except Exception as exc:
                logger.warning("daily picks warm-up failed: %s", exc)

        _spawn(_warm_picks())

        # อุ่นชุดหุ้นซิ่ง/ระยะยาวไปพร้อมกัน — ทั้งสองหน้าเคยต้องรอสแกนสด 35-66 วินาที
        # ตอนมีคนเปิดหน้าครั้งแรก
        async def _warm_pools() -> None:
            from workers.services import (
                _longterm_pool,
                _serialized_surge_pool,
                start_background_refresh,
            )

            start_background_refresh("surge", lambda: _serialized_surge_pool(force=True))
            await asyncio.sleep(45)  # เว้นระยะไม่ให้ยิง provider พร้อมกันจนโดน rate limit
            start_background_refresh("longterm", lambda: _longterm_pool(force=True))

        _spawn(_warm_pools())

    if settings.app_env != "production" or not settings.send_telegram:
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
    if not settings.background_workers_enabled:
        # สแกนรันในเธรดของ process นี้ — รีสตาร์ทแล้วงานที่ค้างอยู่ไม่มีวันจบ ปิดสถานะให้ชัด
        from workers.repository import fail_stale_scan_runs

        fail_stale_scan_runs(settings.database_url, older_than_minutes=0)
    _spawn(_on_startup())
    yield


from apps.api.app.security import require_admin_api_key, secrets_match  # noqa: E402
from workers.engine.logging_setup import configure_logging  # noqa: E402

configure_logging()

app = FastAPI(title="AI Momentum Stock Radar", version="0.2.0", lifespan=lifespan)
settings = get_settings()

from apps.api.app.engine_routes import router as engine_router  # noqa: E402

app.include_router(engine_router)

@app.middleware("http")
async def _json_errors(request, call_next):
    """แปลง exception ที่หลุดมาเป็น JSON 500 ภายใน CORS middleware.

    ถ้าปล่อยให้ ServerErrorMiddleware ของ Starlette (ชั้นนอกสุด) ตอบ 500 เอง
    response จะไม่มี header CORS แล้วเบราว์เซอร์รายงานเป็น "Load failed" /
    "Failed to fetch" แทนข้อความ error จริง
    """
    try:
        return await call_next(request)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "unhandled_error",
            extra={"event": "api_error", "path": request.url.path, "error_type": type(exc).__name__},
        )
        return JSONResponse(
            status_code=500,
            content={"detail": f"Internal error: {type(exc).__name__}"},
        )


# add_middleware หลังสุด = ชั้นนอกสุด → CORS ครอบ _json_errors ไว้ ทุก response จึงมี CORS header
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.backend_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
def run_scan_endpoint(
    send_telegram: bool = False,
    kind: str = Query(default="momentum", pattern="^(momentum|surge|longterm|universe_refresh)$"),
    _: None = Depends(require_admin_api_key),
) -> dict:
    """เริ่มสแกนแล้วคืนสถานะ queued ทันที — ผลลัพธ์ให้ถามต่อที่ /scan/runs/{id}.

    เป็น def ธรรมดา (รันใน threadpool) เพื่อไม่ให้การต่อ DB/Redis ไปบล็อก event loop
    """
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
def surge_signals(
    response: Response,
    limit: int = Query(default=50, ge=1, le=100),
    fresh: bool = Query(default=False),
) -> list[dict]:
    """หุ้นซิ่งสำหรับสายถือไว-ออกไว เรียงตาม surge_score.

    ตอบทันทีด้วยชุดล่าสุดที่มี ถ้าข้อมูลเก่าหรือขอ fresh จะสแกนเบื้องหลังให้
    หน้าเว็บดูจาก header X-Refreshing ว่าควรถามซ้ำอีกไหม
    """
    from workers.services import is_refreshing

    rows = get_surge_signal_dicts(limit=limit, fresh=fresh)
    response.headers["X-Refreshing"] = "1" if is_refreshing("surge") else "0"
    return rows


@app.get("/signals/longterm", response_model=list[LongTermOut])
def longterm_signals(
    response: Response,
    limit: int = Query(default=15, ge=1, le=50),
) -> list[dict]:
    """หุ้นน่าสนใจสำหรับลงทุนระยะยาว จัดอันดับตามคะแนนเทรนด์ (แคช 6 ชม.)."""
    from workers.services import is_refreshing

    rows = get_longterm_signal_dicts(limit=limit)
    response.headers["X-Refreshing"] = "1" if is_refreshing("longterm") else "0"
    return rows


@app.post("/alerts/check")
async def run_price_alerts(_: None = Depends(require_admin_api_key)) -> dict:
    """เช็ค watchlist ทุกคนแล้วส่งเตือนที่ถึงเป้า.

    มีไว้ให้ cron ภายนอก (หรือ Celery beat ถ้าเปิดใช้) เรียก เพราะ worker/beat
    ยังไม่ได้รันอยู่จริงบน production
    """
    from workers.bot_features import send_price_alerts

    return await send_price_alerts()


@app.get("/picks/today")
async def daily_picks(
    limit: int = Query(default=10, ge=1, le=20),
    refresh: bool = Query(default=False),
) -> dict:
    """หุ้นน่าสนใจวันนี้ — คัดด้วยกฎ แล้วให้ Gemini ให้คะแนนตามเกณฑ์ (โค้ดรวมคะแนนเอง)."""
    from workers.daily_picks import get_daily_picks

    try:
        return await get_daily_picks(limit=limit, force=refresh)
    except MarketDataUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/picks/evaluation")
async def picks_evaluation(top_n: int = Query(default=5, ge=1, le=10)) -> dict:
    """ผลจริงของ "หุ้นน่าสนใจวันนี้" — คะแนน AI vs คะแนนกฎ vs SPY ที่ 1/3/5 วันทำการ."""
    from workers.pick_tracking import evaluate_picks

    return await asyncio.to_thread(evaluate_picks, top_n)


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


_FRESH_KEYWORDS = {"live", "fresh", "สด", "now", "ล่าสุด"}
_TELEGRAM_MAX_CHARS = 4000  # margin below Telegram's 4096 hard limit

# US ticker: AAPL, BRK.B, BF-B — ไม่รับอักขระอื่นเพื่อไม่ให้เก็บ "$NVDA" หรือข้อความมั่ว ๆ ลง DB
_SYMBOL_PATTERN = re.compile(r"^[A-Z]{1,6}(?:[.\-][A-Z]{1,2})?$")

# ชุดหุ้นซิ่ง/ระยะยาวที่เก่ากว่านี้ บอทจะรอสแกนใหม่ก่อนตอบ (หน้าเว็บตอบของเก่าไปก่อนได้
# เพราะ poll ซ้ำเองได้ แต่ข้อความ Telegram ส่งแล้วแก้ไม่ได้)
_POOL_MAX_AGE_SECONDS = 30 * 60
_POOL_WAIT_SECONDS = 150

# username ของบอทจาก getMe — ใช้แยกว่าคำสั่ง "/surge@SomeBot" ในกลุ่มเรียกบอทเราจริงไหม
_BOT_USERNAME: Optional[str] = None


def _parse_alert_arg(arg: str, *, default: int, min_count: int = 5, max_count: int = 30) -> tuple[int, bool]:
    """แปลง argument ของ /surge, /longterm เป็น (จำนวนที่ขอ, ต้องการสแกนราคาสดไหม).

    รองรับ "/surge 20" (จำนวน) และ "/surge live" (ขอราคาสดแทนแคช).
    """
    if not arg:
        return default, False
    token = arg.strip().lower()
    if token in _FRESH_KEYWORDS:
        return default, True
    try:
        count = int(token)
    except ValueError:
        return default, False
    return max(min_count, min(max_count, count)), False


def _normalize_symbol(token: str) -> Optional[str]:
    """"$nvda" / "#aapl" / " brk.b " → "NVDA" / "AAPL" / "BRK.B"; ไม่ใช่ ticker → None."""
    cleaned = (token or "").strip().lstrip("$#").upper()
    return cleaned if _SYMBOL_PATTERN.match(cleaned) else None


def _parse_positive_number(token: str) -> Optional[float]:
    """"1,000" / "$250.5" → float; ติดลบ ศูนย์ nan inf หรือไม่ใช่ตัวเลข → None."""
    try:
        value = float((token or "").replace("$", "").replace(",", ""))
    except ValueError:
        return None
    return value if math.isfinite(value) and value > 0 else None


def _looks_like_symbol_query(text: str) -> bool:
    """ข้อความที่ไม่มี '/' นำหน้าแต่หน้าตาเป็น ticker (AAPL, voo, $TSLA) → ถือเป็นคำขอวิเคราะห์."""
    token = text.strip()
    if not token or " " in token or not token.isascii() or token.isdigit():
        return False
    return _normalize_symbol(token) is not None


def _clip_telegram_text(text: str, *, limit: int = _TELEGRAM_MAX_CHARS) -> str:
    """กันข้อความเกิน Telegram hard limit (4096 ตัวอักษร) ทำให้ sendMessage ล้มเหลวทั้งข้อความ."""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n… (ตัดข้อความเพราะยาวเกินไป)"


_MD_RULE = re.compile(r"^\s*(?:[-*_]\s*){3,}$", re.M)
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_MD_BULLET = re.compile(r"^\s*[*\-+]\s+", re.M)


def _clean_ai_text(text: str, *, limit: int = 1500) -> str:
    """แปลง Markdown ของ Gemini เป็นข้อความธรรมดา และตัดความยาวที่ท้ายบรรทัด/วลี.

    ข้อความส่งแบบไม่มี parse_mode ถ้าไม่แปลง ผู้ใช้จะเห็น ** ### --- ดิบ ๆ เต็มจอ
    และการตัดที่ตัวอักษรที่ N ตรง ๆ ทำให้ประโยคขาดกลางคำ
    """
    text = _MD_RULE.sub("", text or "")
    text = _MD_HEADING.sub("", text)
    text = _MD_BULLET.sub("• ", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    boundary = cut.rfind("\n")
    if boundary < limit // 2:
        boundary = cut.rfind(" ")
    if boundary > 0:
        cut = cut[:boundary]
    return cut.rstrip() + " …"


def _strip_source_suffix(title: str, source: str) -> str:
    """ข่าวจาก Google News มีชื่อสำนักข่าวต่อท้ายหัวข้ออยู่แล้ว ("... - Benzinga") ไม่ต้องซ้ำ."""
    title = (title or "").strip()
    suffix = f" - {source}" if source else ""
    if suffix and title.endswith(suffix):
        title = title[: -len(suffix)].rstrip()
    return title


def _analysis_error_reply(exc: Exception, label: str) -> str:
    """แปลง error ของการวิเคราะห์เป็นข้อความผู้ใช้ — ห้ามส่งข้อความ error ดิบเข้าแชท.

    ต้องเช็คชนิด error ก่อนข้อความ: MarketDataUnavailableError รวม error ของทุก
    provider ไว้ในข้อความเดียว ซึ่งมัก มี "429" ของ Yahoo ปนอยู่ ถ้าเช็ค "429" ก่อน
    หุ้นที่ไม่มีจริงจะถูกบอกว่า "rate limit" ทุกครั้ง
    """
    if isinstance(exc, (MarketDataUnavailableError, ValueError)):
        return (
            f"❓ ไม่พบข้อมูลหุ้น {label} — ตรวจสอบสัญลักษณ์อีกครั้ง "
            "(รองรับเฉพาะหุ้น/ETF ตลาดสหรัฐ)"
        )
    message = str(exc)
    if "Too Many Requests" in message or "429" in message:
        return "⏳ ระบบข้อมูลถูกจำกัดชั่วคราว (rate limit) ลองใหม่อีกครั้งใน 1-2 นาที"
    return f"⚠️ วิเคราะห์ {label} ไม่สำเร็จ ลองใหม่อีกครั้งในอีกสักครู่"


def _fmt_rel_vol(value) -> str:
    """relative volume 0/None = ไม่มีฐานวอลุ่มให้เทียบ → N/A (ไม่แสดง 0.0x หรือ 1.0x ปลอม)."""
    return f"vol {value:.1f}x" if isinstance(value, (int, float)) and value > 0 else "vol N/A"


def _num(value, default: float = 0.0) -> float:
    return float(value) if isinstance(value, (int, float)) else default


def _format_surge_line(signal: dict, index: int) -> str:
    """แปลง surge signal dict เป็นข้อความบรรทัดเดียว."""
    grade = (signal.get("surge_grade") or {}).get("grade", "-")
    change = _num(signal.get("premarket_gap_percent"))
    price = _num(signal.get("latest_price"))
    return (
        f"{index}. {signal.get('symbol', '?')} [{grade}] {change:+.1f}% · "
        f"{_fmt_rel_vol(signal.get('relative_volume'))} · ${price:.2f}"
    )


def _surge_as_of(signals: list[dict]) -> str:
    """วันที่ของราคาที่ใช้ — ผู้ใช้ต้องรู้ว่าเป็นราคาปิดวันไหน ไม่ใช่เดาว่าเป็นของวันนี้."""
    from zoneinfo import ZoneInfo

    newest = None
    for signal in signals:
        raw = signal.get("provider_updated_at")
        if not isinstance(raw, str) or not raw:
            continue
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if newest is None or parsed > newest:
            newest = parsed
    if newest is None:
        return ""
    return f"ราคาปิด {newest.astimezone(ZoneInfo('America/New_York')).strftime('%d/%m/%Y')} (ตลาดสหรัฐ)"


def _pool_age_note(kind: str) -> str:
    from workers.services import pool_age_seconds

    age = pool_age_seconds(kind)
    if age is None:
        return "⚠️ ยังสแกนชุดใหม่ไม่สำเร็จ — แสดงชุดล่าสุดที่บันทึกไว้ ซึ่งอาจเป็นข้อมูลเก่า"
    minutes = int(age // 60)
    return "อัปเดตเมื่อสักครู่" if minutes < 1 else f"อัปเดตเมื่อ {minutes} นาทีที่แล้ว"


async def _run_analysis(query: str) -> dict:
    """เรียกวิเคราะห์หุ้นรายตัว (ฟังก์ชันเดียวกับที่เว็บใช้ผ่าน /analysis/stock)."""
    from workers.services import analyze_stock_query

    return await analyze_stock_query(query)


def _format_analysis(result: dict) -> str:
    """จัดรูปผลวิเคราะห์รายตัวเป็นข้อความ Telegram: จุดเข้า/จุดขาย/ข่าว/บทวิเคราะห์ AI."""
    stock = result.get("stock") or {}
    signal = result.get("signal") or {}
    plan = signal.get("surge_plan") or {}
    grade = (signal.get("surge_grade") or {}).get("grade", "-")
    rec = signal.get("recommendation") or {}

    symbol = stock.get("symbol") or signal.get("symbol") or "?"
    name = stock.get("name") or ""
    price = _num(signal.get("latest_price"))
    change = _num(signal.get("premarket_gap_percent"))
    title = f"📊 วิเคราะห์ {symbol}"
    if name and name.upper() != symbol.upper():
        title += f" — {name}"
    lines = [
        title,
        f"ราคา ${price:.2f} · {change:+.1f}% · {_fmt_rel_vol(signal.get('relative_volume'))} · เกรด [{grade}]",
    ]
    if rec.get("label"):
        lines.append(f"คำแนะนำเชิงเฝ้าดู: {rec['label']}")

    # จุดเข้า / จุดขาย / stop จาก surge_plan
    lines.append("")
    lines.append("🎯 แผนเทรด (อ้างอิงข้อมูล daily)")
    if plan.get("entry_zone"):
        lines.append(f"• จุดเข้า: ${plan['entry_zone']}")
    if plan.get("entry_note"):
        lines.append(f"  ({plan['entry_note']})")
    if plan.get("stop"):
        stop_pct = plan.get("stop_pct")
        stop_txt = f"• Stop loss: ${plan['stop']}"
        if stop_pct:
            stop_txt += f" (-{stop_pct:.0f}%)"
        lines.append(stop_txt)
    for target in plan.get("targets") or []:
        lines.append(f"• {target.get('label', 'TP')}: ${target.get('price')}")
    if plan.get("risk_reward"):
        lines.append(f"• Risk/Reward: {plan['risk_reward']}")

    # ข่าวล่าสุด
    lines.append("")
    news = [item for item in (signal.get("news") or []) if (item.get("title") or "").strip()]
    if news:
        lines.append("📰 ข่าวล่าสุด")
        for item in news[:3]:
            source = item.get("source") or ""
            title_text = _strip_source_suffix(item.get("title") or "", source)
            lines.append(f"• {title_text}" + (f" ({source})" if source else ""))
    else:
        lines.append("📰 ยังไม่พบข่าวล่าสุดที่ชัดเจน")

    analysis = _clean_ai_text(result.get("analysis") or "", limit=1500)
    if analysis:
        lines.append("")
        lines.append("🤖 บทวิเคราะห์ AI")
        lines.append(analysis)

    lines.append("")
    lines.append("⚠️ เป็นเครื่องมือช่วยคัดกรอง ไม่ใช่คำแนะนำการลงทุน")
    return "\n".join(lines)


def _analysis_buttons(symbol: str) -> Optional[dict]:
    """ปุ่ม inline ใต้ผลวิเคราะห์ — กดแล้วไม่ต้องพิมพ์คำสั่งซ้ำ.

    ใช้ได้เฉพาะ ticker ที่ผ่านการตรวจแล้ว: callback_data ยาวเกิน 64 bytes จะทำให้
    Telegram ปฏิเสธทั้งข้อความ (ผลวิเคราะห์หายไปทั้งก้อน)
    """
    symbol = _normalize_symbol(symbol) or ""
    if not symbol:
        return None
    return {
        "inline_keyboard": [
            [
                {"text": "🔄 รีเฟรช", "callback_data": f"a:{symbol}"},
                {"text": "⭐ เฝ้าดู", "callback_data": f"w:{symbol}"},
            ],
            [
                {"text": "📰 ข่าว", "callback_data": f"n:{symbol}"},
                {"text": "📈 กราฟ", "callback_data": f"c:{symbol}"},
                {"text": "📊 งบ", "callback_data": f"e:{symbol}"},
            ],
        ]
    }


def _format_daily_picks(data: dict) -> str:
    picks = data.get("picks") or []
    source = "เกณฑ์ที่ Gemini ให้คะแนน" if data.get("analysis_source") == "gemini" else "กฎเชิงเทคนิค"
    lines = [f"✨ หุ้นน่าสนใจวันนี้ (คะแนนจาก{source})\n"]
    if not picks:
        lines.append("— ยังไม่พบหุ้นที่เข้าเกณฑ์ —")
        return "\n".join(lines)

    for index, pick in enumerate(picks, 1):
        lines.append(
            f"{index}. {pick.get('symbol', '?')} — คะแนน {round(_num(pick.get('score', pick.get('pop_probability'))))}/100 "
            f"[{pick.get('verdict', '-')}]"
        )
        lines.append(
            f"   ${_num(pick.get('price')):.2f} · {_num(pick.get('change_percent')):+.1f}% · "
            f"{_fmt_rel_vol(pick.get('relative_volume'))} {pick.get('sparkline', '')}".rstrip()
        )
        if pick.get("reason"):
            lines.append(f"   💡 {pick['reason']}")
        if pick.get("catalyst"):
            lines.append(f"   ⚡ {pick['catalyst']}")
        lines.append("")

    lines.append(data.get("disclaimer", ""))
    return "\n".join(lines)


async def _reply(telegram, chat_id: str, text: str, **kwargs) -> None:
    await telegram.send_message(_clip_telegram_text(text), chat_id=chat_id, **kwargs)


async def _wait_until_idle(kind: str, timeout: float = _POOL_WAIT_SECONDS) -> None:
    from workers.services import is_refreshing

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while is_refreshing(kind) and loop.time() < deadline:
        await asyncio.sleep(2)


async def _ensure_fresh_pool(telegram, chat_id: str, kind: str, *, force: bool = False) -> None:
    """ถ้าชุด surge/longterm ไม่มีหรือเก่าเกินไป ให้สแกนใหม่และรอจนเสร็จ (มีเพดานเวลา).

    ของเดิม: แคชว่างหลัง restart → อ่านแถวเก่าจาก DB มาแสดงเหมือนเป็นของวันนี้
    (production เคยส่งหุ้นซิ่งของเดือนก่อน) หรือสั่งสแกนซ้อนกับงานเบื้องหลังที่วิ่งอยู่แล้ว
    """
    from workers.services import is_refreshing, pool_age_seconds, refresh_pool

    age = pool_age_seconds(kind)
    if not force and age is not None and age <= _POOL_MAX_AGE_SECONDS:
        return
    refresh_pool(kind)  # ถ้ากำลังสแกนอยู่แล้วจะไม่สั่งซ้ำ แค่รอรอบเดิม
    if is_refreshing(kind):
        label = "หุ้นซิ่ง" if kind == "surge" else "หุ้นระยะยาว"
        await _reply(telegram, chat_id, f"🔄 กำลังสแกน{label}ชุดล่าสุด... รอประมาณ 1-2 นาที")
        await _wait_until_idle(kind)


async def _handle_analyze(telegram, chat_id: str, arg: str) -> None:
    query = arg.split()[0] if arg else ""
    symbol = _normalize_symbol(query)
    if not symbol:
        await _reply(
            telegram,
            chat_id,
            "พิมพ์ชื่อหุ้นต่อท้ายด้วย เช่น /analyze AAPL หรือ /a TSLA",
        )
        return

    await _reply(telegram, chat_id, f"🔍 กำลังวิเคราะห์ {symbol}... รอสักครู่")
    try:
        result = await _run_analysis(symbol)
    except Exception as exc:
        logger.warning("bot analysis failed for %s: %s", symbol, exc)
        await _reply(telegram, chat_id, _analysis_error_reply(exc, symbol))
        return
    resolved = (result.get("stock") or {}).get("symbol") or symbol
    await _reply(
        telegram,
        chat_id,
        _format_analysis(result),
        reply_markup=_analysis_buttons(resolved),
    )


async def _handle_watch_add(telegram, chat_id: str, arg: str) -> None:
    from workers.bot_features import watch_add
    from workers.config import get_settings

    parts = arg.split()
    symbol = _normalize_symbol(parts[0]) if parts else None
    if not symbol:
        await _reply(
            telegram,
            chat_id,
            "ใช้แบบนี้: /watch AAPL 250 (เตือนเมื่อถึง $250) หรือ /watch AAPL (เฝ้าดูเฉย ๆ)",
        )
        return

    target: Optional[float] = None
    if len(parts) > 1:
        target = _parse_positive_number(parts[1])
        if target is None:
            await _reply(
                telegram, chat_id, "ราคาเป้าหมายต้องเป็นตัวเลขมากกว่า 0 เช่น /watch AAPL 250"
            )
            return

    result = await asyncio.to_thread(watch_add, chat_id, symbol, target)
    if result["status"] == "not_found":
        await _reply(telegram, chat_id, f"❓ ไม่พบหุ้น {symbol} — ตรวจสอบสัญลักษณ์อีกครั้ง")
        return
    if result["status"] != "ok":
        await _reply(telegram, chat_id, "⚠️ บันทึกไม่สำเร็จ (ฐานข้อมูลไม่พร้อม) ลองใหม่อีกครั้ง")
        return

    lines = [f"⭐ เพิ่ม {symbol} เข้ารายการเฝ้าดูแล้ว (ราคาล่าสุด ${result['price']:,.2f})"]
    if target:
        direction = "ขึ้นถึง" if result["direction"] == "above" else "ย่อลงถึง"
        lines.append(f"🔔 ตั้งเตือนเมื่อราคา{direction} ${target:,.2f}")
        if not get_settings().price_alerts_enabled:
            lines.append("ℹ️ ระบบส่งเตือนราคาอัตโนมัติยังไม่ได้เปิดบนเซิร์ฟเวอร์")
    else:
        lines.append("ยังไม่ได้ตั้งราคาเตือน — ตั้งได้ด้วย /watch " + symbol + " 250")
    await _reply(telegram, chat_id, "\n".join(lines))


async def _handle_watchlist(telegram, chat_id: str) -> None:
    from workers.bot_features import watch_list

    rows = await asyncio.to_thread(watch_list, chat_id)
    if not rows:
        await _reply(telegram, chat_id, "ยังไม่มีหุ้นในรายการเฝ้าดู — เพิ่มด้วย /watch AAPL 250")
        return

    lines = ["⭐ รายการเฝ้าดูของคุณ\n"]
    for row in rows:
        price = row.get("price")
        target = row.get("target_price")
        line = f"• {row['symbol']}"
        if price:
            line += f" — ${price:,.2f} ({_num(row.get('change_percent')):+.1f}%)"
        else:
            line += " — ไม่มีราคาล่าสุด"
        if target:
            direction = "ขึ้นถึง" if row.get("direction") == "above" else "ย่อลงถึง"
            line += f" · เตือนเมื่อ{direction} ${float(target):,.2f}"
        lines.append(line)
    lines.append("\nลบด้วย /unwatch SYMBOL")
    await _reply(telegram, chat_id, "\n".join(lines))


async def _handle_unwatch(telegram, chat_id: str, arg: str) -> None:
    from workers.bot_features import watch_remove

    symbol = _normalize_symbol(arg.split()[0]) if arg else None
    if not symbol:
        await _reply(telegram, chat_id, "ระบุหุ้นด้วย เช่น /unwatch AAPL")
        return
    removed = await asyncio.to_thread(watch_remove, chat_id, symbol)
    await _reply(
        telegram,
        chat_id,
        f"🗑️ เอา {symbol} ออกจากรายการเฝ้าดูแล้ว" if removed else f"ไม่พบ {symbol} ในรายการเฝ้าดู",
    )


async def _handle_compare(telegram, chat_id: str, arg: str) -> None:
    from workers.bot_features import compare_symbols

    tokens = [token for token in arg.replace(",", " ").split() if token]
    symbols: list[str] = []
    invalid: list[str] = []
    for token in tokens:
        symbol = _normalize_symbol(token)
        if symbol is None:
            invalid.append(token)
        elif symbol not in symbols:
            symbols.append(symbol)
    symbols = symbols[:5]
    if invalid:
        await _reply(telegram, chat_id, f"❓ ไม่ใช่สัญลักษณ์หุ้น: {', '.join(invalid[:5])}")
        return
    if len(symbols) < 2:
        await _reply(telegram, chat_id, "ใส่อย่างน้อย 2 ตัว (สูงสุด 5) เช่น /compare AAPL MSFT NVDA")
        return

    await _reply(telegram, chat_id, f"⚖️ กำลังเทียบ {', '.join(symbols)}...")
    rows = await asyncio.to_thread(compare_symbols, symbols)
    if not rows:
        await _reply(telegram, chat_id, "⚠️ ดึงข้อมูลไม่สำเร็จ ลองใหม่อีกครั้ง")
        return

    rows.sort(key=lambda item: item["change_percent"], reverse=True)
    lines = ["⚖️ เทียบหุ้น (ราคาปิดล่าสุด)\n"]
    for row in rows:
        lines.append(f"{row['symbol']} — ${row['price']:,.2f} · {row['change_percent']:+.2f}%")
        details = []
        if row.get("relative_volume"):
            details.append(_fmt_rel_vol(row["relative_volume"]))
        if row.get("return_3m_percent") is not None:
            details.append(f"3 ด. {row['return_3m_percent']:+.1f}%")
        from_high = row.get("pct_from_52w_high")
        if from_high is not None:
            details.append(
                "อยู่ที่จุดสูงสุด 52w" if from_high > -0.05 else f"ต่ำกว่าจุดสูงสุด 52w {abs(from_high):.1f}%"
            )
        if row.get("above_sma200") is not None:
            details.append("เหนือ MA200" if row["above_sma200"] else "ใต้ MA200")
        if details:
            lines.append(f"   {' · '.join(details)}")
    missing = [symbol for symbol in symbols if symbol not in {row["symbol"] for row in rows}]
    if missing:
        lines.append(f"\n❓ ไม่พบข้อมูล: {', '.join(missing)}")
    await _reply(telegram, chat_id, "\n".join(lines))


async def _handle_news(telegram, chat_id: str, arg: str) -> None:
    import html

    symbol = _normalize_symbol(arg.split()[0]) if arg else None
    if not symbol:
        await _reply(telegram, chat_id, "ระบุหุ้นด้วย เช่น /news TSLA")
        return

    from workers.config import get_settings
    from workers.news.fetch_news import get_news_provider

    settings = get_settings()
    provider = get_news_provider(use_live_data=settings.use_live_data)
    items = await asyncio.to_thread(provider.get_company_news, symbol)
    if not items:
        await _reply(telegram, chat_id, f"📰 ยังไม่พบข่าวล่าสุดของ {symbol}")
        return

    # ลิงก์ของ Google News ยาวหลายร้อยตัวอักษร — ฝังไว้ในหัวข้อแทนการพิมพ์ URL ดิบ
    lines = [f"📰 ข่าวล่าสุด {symbol}\n"]
    plain = [f"📰 ข่าวล่าสุด {symbol}\n"]
    for item in items[:5]:
        title = _strip_source_suffix(item.title, item.source or "")
        source = f" ({item.source})" if item.source else ""
        if item.url:
            lines.append(
                f'• <a href="{html.escape(item.url, quote=True)}">{html.escape(title)}</a>{html.escape(source)}'
            )
        else:
            lines.append(f"• {html.escape(title)}{html.escape(source)}")
        plain.append(f"• {title}{source}")
    try:
        await telegram.send_message("\n".join(lines), chat_id=chat_id, parse_mode="HTML")
    except Exception as exc:
        # Telegram ปฏิเสธ HTML (เช่นลิงก์แปลก ๆ) → ส่งหัวข้อข่าวแบบข้อความธรรมดาแทน
        logger.warning("news HTML send failed, falling back to plain text: %s", exc)
        await _reply(telegram, chat_id, "\n".join(plain))


async def _handle_chart(telegram, chat_id: str, arg: str) -> None:
    symbol = _normalize_symbol(arg.split()[0]) if arg else None
    if not symbol:
        await _reply(telegram, chat_id, "ระบุหุ้นด้วย เช่น /chart NVDA")
        return

    from workers.bot_features import price_history
    from workers.daily_picks import sparkline

    try:
        closes = await asyncio.to_thread(price_history, symbol, 60)
    except Exception:
        closes = []
    if len(closes) < 2:
        await _reply(telegram, chat_id, f"❓ ไม่พบประวัติราคาของ {symbol}")
        return

    change = (closes[-1] - closes[0]) / closes[0] * 100 if closes[0] else 0.0
    lines = [
        f"📈 {symbol} — {len(closes)} วันทำการล่าสุด",
        "",
        sparkline(closes, width=32),
        "",
        f"ล่าสุด ${closes[-1]:,.2f} · ช่วง ${min(closes):,.2f}-${max(closes):,.2f}",
        f"เปลี่ยนแปลงช่วงนี้ {change:+.1f}%",
    ]
    await _reply(telegram, chat_id, "\n".join(lines))


_PORT_USAGE = (
    "💼 คำสั่งพอร์ต\n"
    "/port — ดูพอร์ต\n"
    "/port add AAPL 10 200.5 — ซื้อเพิ่ม (หุ้น จำนวน ต้นทุนต่อหุ้น)\n"
    "/port del AAPL — ลบออกจากพอร์ต"
)


async def _handle_portfolio(telegram, chat_id: str, arg: str) -> None:
    from workers.bot_features import portfolio_add, portfolio_remove, portfolio_summary

    parts = arg.split()
    action = parts[0].lower() if parts else ""

    if action in ("add", "เพิ่ม", "buy"):
        if len(parts) < 4:
            await _reply(telegram, chat_id, "ใช้แบบนี้: /port add AAPL 10 200.5  (หุ้น จำนวน ต้นทุนต่อหุ้น)")
            return
        symbol = _normalize_symbol(parts[1])
        quantity = _parse_positive_number(parts[2])
        cost = _parse_positive_number(parts[3])
        if not symbol:
            await _reply(telegram, chat_id, f"❓ '{parts[1]}' ไม่ใช่สัญลักษณ์หุ้น เช่น AAPL")
            return
        if quantity is None or cost is None:
            await _reply(telegram, chat_id, "จำนวนและต้นทุนต้องเป็นตัวเลขมากกว่า 0 เช่น /port add AAPL 10 200.5")
            return
        result = await asyncio.to_thread(portfolio_add, chat_id, symbol, quantity, cost)
        if result["status"] == "not_found":
            await _reply(telegram, chat_id, f"❓ ไม่พบหุ้น {symbol} — ตรวจสอบสัญลักษณ์อีกครั้ง")
            return
        if result["status"] != "ok":
            await _reply(telegram, chat_id, "⚠️ บันทึกไม่สำเร็จ (ฐานข้อมูลไม่พร้อม) ลองใหม่อีกครั้ง")
            return
        position = result["position"]
        text = f"✅ บันทึก {symbol} {quantity:g} หุ้น @ ${cost:,.2f}"
        if abs(position["quantity"] - quantity) > 1e-9:
            # มีตัวนี้อยู่แล้ว → รวมจำนวน + เฉลี่ยต้นทุน
            text += (
                f"\nรวมในพอร์ตตอนนี้ {position['quantity']:g} หุ้น "
                f"ต้นทุนเฉลี่ย ${position['cost_basis']:,.2f}"
            )
        await _reply(telegram, chat_id, text)
        return

    if action in ("del", "remove", "delete", "ลบ"):
        symbol = _normalize_symbol(parts[1]) if len(parts) > 1 else None
        if not symbol:
            await _reply(telegram, chat_id, "ระบุหุ้นด้วย เช่น /port del AAPL")
            return
        removed = await asyncio.to_thread(portfolio_remove, chat_id, symbol)
        await _reply(
            telegram,
            chat_id,
            f"🗑️ ลบ {symbol} ออกจากพอร์ตแล้ว" if removed else f"ไม่พบ {symbol} ในพอร์ต",
        )
        return

    if action:
        await _reply(telegram, chat_id, _PORT_USAGE)
        return

    summary = await asyncio.to_thread(portfolio_summary, chat_id)
    if not summary["positions"]:
        await _reply(telegram, chat_id, "พอร์ตยังว่าง — เพิ่มด้วย /port add AAPL 10 200.5")
        return

    lines = ["💼 พอร์ตของคุณ (ราคาปิดล่าสุด)\n"]
    unpriced = False
    for row in summary["positions"]:
        if not row["priced"]:
            unpriced = True
            lines.append(
                f"⚪ {row['symbol']}: {row['quantity']:g} หุ้น @ ${row['cost_basis']:,.2f} → ไม่มีราคาล่าสุด"
            )
            continue
        mark = "🟢" if row["pl"] >= 0 else "🔴"
        lines.append(
            f"{mark} {row['symbol']}: {row['quantity']:g} หุ้น @ ${row['cost_basis']:,.2f} "
            f"→ ${row['price']:,.2f}"
        )
        lines.append(f"   กำไร/ขาดทุน {_fmt_money(row['pl'])} ({row['pl_percent']:+.1f}%)")
    if summary["total_cost"] > 0:
        lines.append("")
        lines.append(
            f"รวมต้นทุน ${summary['total_cost']:,.2f} · มูลค่าปัจจุบัน ${summary['total_value']:,.2f}"
        )
        lines.append(
            f"รวมกำไร/ขาดทุน {_fmt_money(summary['total_pl'])} "
            f"({summary.get('total_pl_percent', 0):+.1f}%)"
        )
    if unpriced:
        lines.append("(หุ้นที่ไม่มีราคาล่าสุดไม่ถูกนับในยอดรวม)")
    lines.append("\nลบด้วย /port del SYMBOL")
    await _reply(telegram, chat_id, "\n".join(lines))


def _fmt_money(value: float) -> str:
    """+$1,234.50 / -$1,234.50 (ของเดิมได้ "$-1,704.85" อ่านยาก)."""
    sign = "+" if value >= 0 else "-"
    return f"{sign}${abs(value):,.2f}"


async def _handle_earnings(telegram, chat_id: str, arg: str) -> None:
    symbol = _normalize_symbol(arg.split()[0]) if arg else None
    if not symbol:
        await _reply(telegram, chat_id, "ระบุหุ้นด้วย เช่น /earnings NVDA")
        return

    from workers.scanner.fetch_earnings import fetch_earnings

    data = await asyncio.to_thread(fetch_earnings, symbol)
    lines = [f"📊 งบการเงิน {symbol}\n"]

    upcoming = data.get("upcoming") or []
    previous = data.get("previous") or []
    if not (data.get("earnings_date") or upcoming or previous):
        await _reply(telegram, chat_id, f"❓ ไม่พบข้อมูลงบของ {symbol}")
        return

    if data.get("earnings_date"):
        lines.append(f"🗓️ วันประกาศงบถัดไป: {data['earnings_date']}")
    else:
        # Nasdaq/Zacks ไม่ได้ให้วันประกาศทุกตัว — บอกตรง ๆ ดีกว่าเดา
        lines.append("🗓️ ยังไม่มีข้อมูลวันประกาศงบถัดไปจากผู้ให้บริการ")

    if upcoming:
        lines.append("")
        lines.append("ตลาดคาดการณ์ (EPS):")
        for row in upcoming[:2]:
            lines.append(f"• {row['period']}: ${row['consensus']}")

    if previous:
        lines.append("")
        lines.append("ไตรมาสที่ผ่านมา (คาด → จริง):")
        for row in previous[-4:]:
            actual = row.get("actual")
            consensus = row.get("consensus")
            beat = ""
            if isinstance(actual, (int, float)) and isinstance(consensus, (int, float)):
                beat = " ✅ เกินคาด" if actual > consensus else (" ❌ ต่ำกว่าคาด" if actual < consensus else " ➖ ตามคาด")
            lines.append(f"• {row['period']}: ${consensus} → ${actual}{beat}")

    await _reply(telegram, chat_id, "\n".join(lines))


async def _handle_market(telegram, chat_id: str) -> None:
    from workers.bot_features import market_overview

    rows = await asyncio.to_thread(market_overview)
    if not rows:
        await _reply(telegram, chat_id, "⚠️ ดึงข้อมูลตลาดไม่สำเร็จ ลองใหม่อีกครั้ง")
        return
    lines = ["🌎 ภาพรวมตลาดสหรัฐ (ราคาปิดล่าสุด)\n"]
    for row in rows:
        arrow = "🟢" if row["change_percent"] >= 0 else "🔴"
        lines.append(
            f"{arrow} {row['label']} ({row['symbol']}): ${row['price']:,.2f} "
            f"{row['change_percent']:+.2f}%"
        )
    await _reply(telegram, chat_id, "\n".join(lines))


async def _handle_surge(telegram, chat_id: str, arg: str) -> None:
    from workers.config import get_settings
    from workers.services import get_surge_signal_dicts

    count, want_fresh = _parse_alert_arg(arg, default=get_settings().surge_alert_limit, min_count=1)
    await _ensure_fresh_pool(telegram, chat_id, "surge", force=want_fresh)
    signals = await asyncio.to_thread(get_surge_signal_dicts, count)

    lines = [f"🚀 หุ้นซิ่ง Top {len(signals) or count}"]
    as_of = _surge_as_of(signals)
    lines.append(" · ".join(part for part in (as_of, _pool_age_note("surge")) if part))
    lines.append("")
    if signals:
        for index, signal in enumerate(signals, 1):
            lines.append(_format_surge_line(signal, index))
            reason = (signal.get("reason") or "").strip()
            if reason:
                lines.append(f"   💡 {reason[:110]}")
    else:
        lines.append("— ยังไม่พบหุ้นที่เข้าเกณฑ์ —")
    lines.append("\nพิมพ์ /surge 30 เพื่อดูมากขึ้น หรือ /surge live เพื่อสแกนใหม่")
    await _reply(telegram, chat_id, "\n".join(lines))


async def _handle_longterm(telegram, chat_id: str, arg: str) -> None:
    from workers.config import get_settings
    from workers.services import get_longterm_signal_dicts

    count, want_fresh = _parse_alert_arg(arg, default=get_settings().longterm_alert_limit, min_count=1)
    await _ensure_fresh_pool(telegram, chat_id, "longterm", force=want_fresh)
    picks = await asyncio.to_thread(get_longterm_signal_dicts, count)

    lines = [f"📈 หุ้นน่าสนใจระยะยาว Top {len(picks) or count}", _pool_age_note("longterm"), ""]
    if picks:
        for index, pick in enumerate(picks, 1):
            return_6m = pick.get("return_6m_percent")
            return_text = f"6 ด. {return_6m:+.0f}%" if isinstance(return_6m, (int, float)) else "6 ด. N/A"
            lines.append(
                f"{index}. {pick.get('symbol', '?')} [{pick.get('grade', '-')}] "
                f"คะแนน {_num(pick.get('score')):.0f} · {return_text} · ${_num(pick.get('price')):,.2f}"
            )
            reasons = pick.get("reasons") or []
            if reasons:
                lines.append(f"   • {reasons[0]}")
    else:
        lines.append("— ยังไม่พบหุ้นที่เข้าเกณฑ์ —")
    lines.append("\nพิมพ์ /longterm 30 เพื่อดูมากขึ้น")
    await _reply(telegram, chat_id, "\n".join(lines))


async def _handle_scan(telegram, chat_id: str) -> None:
    from workers.services import pool_age_seconds, refresh_pool

    await _reply(telegram, chat_id, "🔄 เริ่มสแกนหุ้นซิ่งและหุ้นระยะยาวใหม่... รอประมาณ 1-3 นาที")
    # ทีละชุด — ยิงพร้อมกันเสี่ยงโดน rate limit จากผู้ให้บริการข้อมูล
    results = []
    for kind in ("surge", "longterm"):
        refresh_pool(kind)
        await _wait_until_idle(kind)
        age = pool_age_seconds(kind)
        results.append(age is not None and age < _POOL_WAIT_SECONDS * 2)
    if all(results):
        await _reply(telegram, chat_id, "✅ สแกนเสร็จแล้ว! ลอง /surge หรือ /longterm ได้เลย")
    else:
        await _reply(telegram, chat_id, "⚠️ สแกนไม่สำเร็จบางส่วน (ผู้ให้บริการข้อมูลอาจจำกัดการใช้งาน) ลองใหม่อีกครั้งในอีกสักครู่")


async def _handle_digest(telegram, chat_id: str) -> None:
    from workers.alerts.digest import build_daily_digest

    for kind in ("surge", "longterm"):
        await _ensure_fresh_pool(telegram, chat_id, kind)
    await _reply(telegram, chat_id, await asyncio.to_thread(build_daily_digest))


async def _handle_subscription(telegram, chat_id: str, active: bool) -> None:
    from workers.config import get_settings
    from workers.repository import set_subscriber

    ok = await asyncio.to_thread(set_subscriber, get_settings().database_url, chat_id, active)
    if active:
        text = (
            "🔔 สมัครรับสรุปรายวันแล้ว — ส่งให้ตอน 08:00 และ 20:00 (เวลาไทย)\nยกเลิกด้วย /unsubscribe"
            if ok
            else "⚠️ สมัครไม่สำเร็จ (ฐานข้อมูลไม่พร้อม) ลองใหม่อีกครั้ง"
        )
    else:
        text = "🔕 ยกเลิกรับสรุปรายวันแล้ว" if ok else "⚠️ ยกเลิกไม่สำเร็จ ลองใหม่อีกครั้ง"
    await _reply(telegram, chat_id, text)


async def _handle_today(telegram, chat_id: str, arg: str) -> None:
    from workers.daily_picks import get_daily_picks

    count, _fresh = _parse_alert_arg(arg, default=5, min_count=3, max_count=10)
    await _reply(telegram, chat_id, "✨ กำลังคัดหุ้นน่าสนใจวันนี้จากทั้งตลาด... รอบแรกของวันใช้เวลาราว 2-3 นาที")
    data = await get_daily_picks(limit=count, wait=True)
    await _reply(telegram, chat_id, _format_daily_picks(data))


_SYMBOL_IN_TEXT = re.compile(r"(?<![A-Za-z])\$?([A-Z]{1,5}(?:\.[A-Z])?)(?![A-Za-z])")
_NOT_SYMBOLS = {"AI", "USD", "IPO", "ETF", "CEO", "EPS", "US", "PE", "OK", "MA", "RSI", "I", "A"}


async def _handle_free_question(telegram, chat_id: str, question: str) -> None:
    """ตอบคำถามภาษาไทยแบบอิสระ — ถ้าพบ ticker ในข้อความจะแนบราคาจริงให้ AI ด้วย.

    ที่ต้องแนบราคาจริงเพราะ Gemini ไม่รู้ราคาปัจจุบัน ถ้าปล่อยให้ตอบลอย ๆ
    มันจะเดาตัวเลขออกมาเหมือนเป็นข้อมูลจริง
    """
    from workers.config import get_settings

    settings = get_settings()
    if not settings.gemini_api_key:
        await _reply(telegram, chat_id, "❓ ไม่รู้จักคำสั่งนี้ ลอง /help ดูคำสั่งที่ใช้ได้")
        return

    # จับเฉพาะ token ที่ผู้ใช้พิมพ์เป็นตัวพิมพ์ใหญ่จริง ๆ — ถ้า .upper() ทั้งประโยคก่อน
    # คำอังกฤษธรรมดา ("is", "good") จะกลายเป็น ticker ไปด้วย
    symbols: list[str] = []
    for token in _SYMBOL_IN_TEXT.findall(question):
        if token not in _NOT_SYMBOLS and token not in symbols:
            symbols.append(token)
    symbols = symbols[:3]

    await _reply(telegram, chat_id, "🤔 กำลังคิด...")

    # ดึงแค่ราคา ไม่เรียก _run_analysis เพราะนั่นเรียก Gemini อีกรอบต่อหุ้นหนึ่งตัว
    # (โควตา free tier จำกัด — คำถามเดียวจะกินไป 2-4 ครั้ง)
    def _price_context(symbol: str) -> Optional[str]:
        from workers.scanner.fetch_prices import get_market_data_provider

        try:
            snapshot = get_market_data_provider(use_live_data=settings.use_live_data).get_snapshot(symbol)
        except Exception:
            return None
        if not snapshot or snapshot.latest_price <= 0:
            return None
        change = (
            (snapshot.latest_price - snapshot.previous_close) / snapshot.previous_close * 100
            if snapshot.previous_close > 0
            else 0.0
        )
        line = f"{symbol}: ราคาปิดล่าสุด ${snapshot.latest_price:.2f} ({change:+.2f}%)"
        # ไม่มีฐานวอลุ่มเฉลี่ย = ไม่รู้ ห้ามใส่ 1.0x ให้ AI เอาไปพูดเหมือนเป็นข้อมูลจริง
        if snapshot.average_volume_20d > 0:
            line += f", วอลุ่มเทียบค่าเฉลี่ย 20 วัน {snapshot.volume / snapshot.average_volume_20d:.1f}x"
        return line

    contexts = await asyncio.gather(*(asyncio.to_thread(_price_context, symbol) for symbol in symbols))
    context_lines = [line for line in contexts if line]
    priced_symbols = [symbol for symbol, line in zip(symbols, contexts) if line]

    context_block = (
        "ข้อมูลราคาล่าสุด (ราคาปิดรายวัน):\n" + "\n".join(context_lines)
        if context_lines
        else "ไม่มีข้อมูลราคาประกอบ — อย่าเดาตัวเลขราคา"
    )
    prompt = (
        "คุณเป็นผู้ช่วยวิเคราะห์หุ้นสหรัฐ ตอบเป็นภาษาไทย กระชับไม่เกิน 6 บรรทัด ไม่ใช้ Markdown\n"
        "ห้ามแต่งตัวเลขที่ไม่ได้ให้มา และต้องบอกเสมอว่านี่ไม่ใช่คำแนะนำการลงทุน\n\n"
        f"{context_block}\n\nคำถาม: {question}"
    )

    from workers.ai.gemini_client import generate_gemini_text

    try:
        answer = await generate_gemini_text(
            prompt,
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            temperature=0.4,
        )
    except Exception as exc:
        logger.warning("free question AI failed: %s", exc)
        await _reply(
            telegram,
            chat_id,
            "⚠️ ตอนนี้ AI ไม่พร้อมใช้งาน (โควตาเต็มหรือถูกจำกัด) ลองใช้ /analyze SYMBOL แทนได้",
        )
        return

    await _reply(
        telegram,
        chat_id,
        f"🤖 {_clean_ai_text(answer, limit=3500)}",
        reply_markup=_analysis_buttons(priced_symbols[0]) if priced_symbols else None,
    )


async def _handle_callback(callback: dict) -> None:
    """จัดการปุ่ม inline ใต้ผลวิเคราะห์ (callback_data = "<action>:<symbol>")."""
    from workers.alerts.telegram import TelegramClient
    from workers.config import get_settings

    settings = get_settings()
    telegram = TelegramClient(settings.telegram_bot_token, None)
    if not telegram.is_configured:
        return

    data = str(callback.get("data") or "")
    chat = (callback.get("message") or {}).get("chat") or {}
    chat_id = str(chat.get("id", ""))
    query_id = str(callback.get("id") or "")
    action, _, raw_symbol = data.partition(":")
    symbol = _normalize_symbol(raw_symbol)
    commands = {
        "a": ("/analyze", "วิเคราะห์"),
        "w": ("/watch", "เพิ่มเฝ้าดู"),
        "n": ("/news", "ข่าว"),
        "c": ("/chart", "กราฟ"),
        "e": ("/earnings", "งบ"),
    }
    # ตอบ callback ทุกครั้ง แม้ข้อมูลผิด — ไม่งั้นปุ่มจะหมุนค้าง
    try:
        label = commands.get(action, ("", ""))[1]
        await telegram.answer_callback_query(query_id, f"{label} {symbol or ''}".strip())
    except Exception as exc:
        logger.warning("answerCallbackQuery failed: %s", exc)
    if not chat_id or not symbol or action not in commands:
        return
    await _handle_bot_command(
        f"{commands[action][0]} {symbol}", chat_id, str(chat.get("type") or "private")
    )


def _help_text(settings) -> str:
    return (
        "🤖 คำสั่งที่ใช้ได้:\n\n"
        "📊 วิเคราะห์\n"
        "/analyze SYMBOL — วิเคราะห์รายตัว (จุดเข้า/จุดขาย/ข่าว/AI)\n"
        "   หรือพิมพ์ชื่อหุ้นเปล่า ๆ ก็ได้ เช่น VOO\n"
        "/today — หุ้นน่าสนใจวันนี้ (AI อ่านกราฟ+ข่าว)\n"
        "/compare AAPL MSFT — เทียบหุ้นสูงสุด 5 ตัว\n"
        "/chart SYMBOL — กราฟราคา 60 วัน\n"
        "/news SYMBOL — ข่าวล่าสุด\n"
        "/earnings SYMBOL — วันประกาศงบ + EPS\n"
        "/market — ภาพรวมตลาด (S&P/Nasdaq/Dow/Russell)\n\n"
        "⭐ เฝ้าดู & พอร์ต\n"
        "/watch AAPL 250 — เตือนเมื่อราคาถึงเป้า\n"
        "/watchlist — ดูรายการเฝ้าดู · /unwatch SYMBOL — ลบ\n"
        "/port — ดูพอร์ต · /port add AAPL 10 200.5 · /port del AAPL\n\n"
        "🔔 สรุปรายวัน\n"
        "/subscribe — รับสรุป 08:00 และ 20:00 · /unsubscribe — ยกเลิก\n"
        "/digest — ขอสรุปทันที\n\n"
        "🚀 สแกนตลาด\n"
        f"/surge — หุ้นซิ่ง Top {settings.surge_alert_limit} (/surge 30, /surge live)\n"
        f"/longterm — ระยะยาว Top {settings.longterm_alert_limit}\n"
        "/scan — สแกนใหม่ทันที\n\n"
        "💬 ถามเป็นภาษาไทยได้เลย เช่น \"AAPL น่าซื้อไหม\""
    )


async def _handle_bot_command(command: str, chat_id: str, chat_type: str = "private") -> None:
    """ตอบกลับคำสั่งจากผู้ใช้ใน Telegram."""
    from workers.alerts.telegram import TelegramClient
    from workers.config import get_settings

    settings = get_settings()
    telegram = TelegramClient(settings.telegram_bot_token, None)
    if not telegram.is_configured:
        return

    # แยก command กับ argument (เช่น "/analyze AAPL" -> cmd_word="/analyze", arg="AAPL")
    parts = command.strip().split(maxsplit=1)
    cmd_word = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""
    is_private = chat_type == "private"
    # ในกลุ่ม Telegram ส่งคำสั่งมาเป็น "/surge@MyBot" — ตัด @botname ทิ้ง และถ้าระบุ
    # บอทตัวอื่นก็ไม่ใช่คำสั่งของเรา (กลุ่มที่มีหลายบอทจะได้ไม่ตอบแทนกัน)
    if cmd_word.startswith("/") and "@" in cmd_word:
        cmd_word, _, addressed_to = cmd_word.partition("@")
        if _BOT_USERNAME and addressed_to != _BOT_USERNAME.lower():
            return
    # ในกลุ่ม: เงียบไว้ถ้าไม่ใช่คำสั่ง ไม่งั้นบอทจะตอบใส่บทสนทนาปกติ
    if not is_private and not cmd_word.startswith("/"):
        return
    # พิมพ์ชื่อหุ้นเปล่า ๆ ("AAPL", "voo") ให้ถือว่าเป็นการขอวิเคราะห์รายตัว — เฉพาะแชทส่วนตัว
    if is_private and cmd_word and not cmd_word.startswith("/") and _looks_like_symbol_query(command):
        arg = command.strip()
        cmd_word = "/analyze"

    handlers = {
        ("/analyze", "/a", "/วิเคราะห์", "/stock"): lambda: _handle_analyze(telegram, chat_id, arg),
        ("/today", "/picks", "/วันนี้"): lambda: _handle_today(telegram, chat_id, arg),
        ("/watch", "/เฝ้าดู"): lambda: _handle_watch_add(telegram, chat_id, arg),
        ("/watchlist", "/watches", "/รายการเฝ้าดู"): lambda: _handle_watchlist(telegram, chat_id),
        ("/unwatch", "/เลิกเฝ้าดู"): lambda: _handle_unwatch(telegram, chat_id, arg),
        ("/subscribe", "/สมัคร"): lambda: _handle_subscription(telegram, chat_id, True),
        ("/unsubscribe", "/ยกเลิก"): lambda: _handle_subscription(telegram, chat_id, False),
        ("/compare", "/เทียบ"): lambda: _handle_compare(telegram, chat_id, arg),
        ("/news", "/ข่าว"): lambda: _handle_news(telegram, chat_id, arg),
        ("/market", "/ตลาด"): lambda: _handle_market(telegram, chat_id),
        ("/chart", "/กราฟ"): lambda: _handle_chart(telegram, chat_id, arg),
        ("/port", "/portfolio", "/พอร์ต"): lambda: _handle_portfolio(telegram, chat_id, arg),
        ("/earnings", "/งบ"): lambda: _handle_earnings(telegram, chat_id, arg),
        ("/surge", "/surges", "/ziing", "/ซิ่ง"): lambda: _handle_surge(telegram, chat_id, arg),
        ("/longterm", "/long", "/lt", "/ระยะยาว"): lambda: _handle_longterm(telegram, chat_id, arg),
        ("/scan", "/สแกน", "/update"): lambda: _handle_scan(telegram, chat_id),
        ("/digest", "/summary", "/สรุป"): lambda: _handle_digest(telegram, chat_id),
        ("/start", "/help", "/ช่วยเหลือ"): lambda: _reply(telegram, chat_id, _help_text(settings)),
    }
    handler = next((make for names, make in handlers.items() if cmd_word in names), None)

    try:
        if handler is not None:
            await handler()
        elif is_private and not cmd_word.startswith("/") and len(command.strip()) > 3:
            # ข้อความอิสระในแชทส่วนตัว → ให้ AI ตอบ พร้อมราคาจริงของหุ้นที่เอ่ยถึง
            await _handle_free_question(telegram, chat_id, command.strip())
        elif is_private:
            await _reply(telegram, chat_id, "❓ ไม่รู้จักคำสั่งนี้ ลอง /help ดูคำสั่งที่ใช้ได้")
        # ในกลุ่ม: คำสั่งที่ไม่รู้จักอาจเป็นของบอทอื่น — เงียบไว้
    except Exception as exc:
        # ห้ามส่ง str(exc) เข้าแชท — error ของ psycopg/httpx มักมี connection string
        # หรือ URL ที่ฝังรหัสผ่าน/โทเคนไว้ ผู้ใช้ปลายทางไม่ควรเห็นและไม่ได้ช่วยอะไร
        logger.exception("bot command failed: %s", exc)
        try:
            await _reply(
                telegram,
                chat_id,
                "⚠️ เกิดข้อผิดพลาดระหว่างประมวลผล ลองใหม่อีกครั้ง หรือพิมพ์ /help",
            )
        except Exception:
            pass


# update_id ที่ประมวลผลไปแล้ว — Telegram ส่ง update เดิมซ้ำถ้าเคยตอบช้า/ไม่ได้ 200
# ถ้าไม่กันไว้คำสั่งอย่าง /port add จะถูกบันทึกซ้ำ
_SEEN_UPDATE_IDS: "OrderedDict[int, None]" = OrderedDict()
_SEEN_UPDATE_LIMIT = 1000


def _is_duplicate_update(update_id) -> bool:
    if not isinstance(update_id, int):
        return False
    if update_id in _SEEN_UPDATE_IDS:
        return True
    _SEEN_UPDATE_IDS[update_id] = None
    while len(_SEEN_UPDATE_IDS) > _SEEN_UPDATE_LIMIT:
        _SEEN_UPDATE_IDS.popitem(last=False)
    return False


@app.post("/telegram/webhook")
async def telegram_webhook(
    update: dict,
    x_telegram_bot_api_secret_token: Optional[str] = Header(default=None),
) -> dict:
    """Telegram Bot API webhook — รับ message จากผู้ใช้แล้วตอบกลับ.

    URL นี้เปิดสาธารณะ จึงต้องพิสูจน์ว่า update มาจาก Telegram จริงด้วย secret token
    ที่ลงทะเบียนไว้ตอน setWebhook ไม่งั้นใครเดา URL เจอก็สั่งบอทส่งข้อความหา chat ใดก็ได้
    """
    expected_secret = telegram_webhook_secret()
    if expected_secret and not secrets_match(x_telegram_bot_api_secret_token, expected_secret):
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    if _is_duplicate_update(update.get("update_id")):
        return {"ok": True}

    callback = update.get("callback_query")
    if callback:
        _spawn(_handle_callback(callback))
        return {"ok": True}

    # ไม่รับ edited_message: แก้ข้อความเก่า (เช่น /port add) แล้วคำสั่งจะถูกรันซ้ำ
    message = update.get("message")
    if not message:
        return {"ok": True}

    text = (message.get("text") or "").strip()
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id", ""))

    if not text or not chat_id:
        return {"ok": True}

    # fire-and-forget: ตอบ Telegram 200 ทันที ไม่ให้ webhook timeout แล้ว retry
    # (การ scan ใช้เวลานาน บอทจะส่งข้อความกลับเองผ่าน chat_id เมื่อเสร็จ)
    _spawn(_handle_bot_command(text, chat_id, str(chat.get("type") or "private")))
    return {"ok": True}


@app.post("/telegram/set-webhook")
async def telegram_set_webhook(_: None = Depends(require_admin_api_key)) -> dict:
    """ตั้ง webhook ให้บอทชี้กลับมาที่ service นี้ (เรียกเองเมื่อ auto-register ล้มเหลว).

    ไม่รับ URL จากผู้เรียกอีกต่อไป — ของเดิมเป็น GET ที่ใครก็ได้ยิง
    ?url=https://evil.example/hook เพื่อเปลี่ยนปลายทาง webhook แล้วดูดข้อความผู้ใช้ทั้งหมด
    """
    settings = get_settings()
    if not settings.telegram_bot_token:
        return {"status": "no_token"}

    render_url = os.getenv("RENDER_EXTERNAL_URL", "https://ai-stock-01or.onrender.com").rstrip("/")
    await _register_telegram_webhook(render_url)
    return {"status": "set", "url": f"{render_url}/telegram/webhook"}
