from __future__ import annotations

import asyncio
import logging
import os
import re
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
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"https://api.telegram.org/bot{settings.telegram_bot_token}/setWebhook",
                    json={"url": f"{render_url}/telegram/webhook"},
                )
                # ลงทะเบียนเมนูคำสั่งให้ Telegram ขึ้น autocomplete ให้ผู้ใช้
                await client.post(
                    f"https://api.telegram.org/bot{settings.telegram_bot_token}/setMyCommands",
                    json={"commands": _BOT_COMMAND_MENU},
                )
            logger.info("Telegram webhook registered: %s/telegram/webhook", render_url)
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

        asyncio.create_task(_alert_loop())

    # อุ่น cache "หุ้นน่าสนใจวันนี้" ไว้ล่วงหน้า — คำนวณสดใช้เวลา ~1 นาที
    # ถ้าไม่อุ่นไว้ ผู้เข้าชมคนแรกหลัง deploy จะต้องรอทั้งก้อน
    if settings.use_live_data:
        async def _warm_picks() -> None:
            try:
                from workers.daily_picks import get_daily_picks

                await get_daily_picks(limit=10, wait=True)
                logger.info("daily picks cache warmed")
            except Exception as exc:
                logger.warning("daily picks warm-up failed: %s", exc)

        asyncio.create_task(_warm_picks())

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
def surge_signals(
    limit: int = Query(default=50, ge=1, le=100),
    fresh: bool = Query(default=False),
) -> list[dict]:
    """หุ้นซิ่งสำหรับสายถือไว-ออกไว เรียงตาม surge_score (สแกนสด)."""
    return get_surge_signal_dicts(limit=limit, fresh=fresh)


@app.get("/signals/longterm", response_model=list[LongTermOut])
def longterm_signals(limit: int = Query(default=15, ge=1, le=50)) -> list[dict]:
    """หุ้นน่าสนใจสำหรับลงทุนระยะยาว จัดอันดับตามคะแนนเทรนด์ (แคช 6 ชม.)."""
    return get_longterm_signal_dicts(limit=limit)


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
    """หุ้นน่าสนใจวันนี้ — คัดจากโครงสร้างราคา+วอลุ่ม แล้วให้ Gemini อ่านกราฟ+ข่าวจัดอันดับ."""
    from workers.daily_picks import get_daily_picks

    try:
        return await get_daily_picks(limit=limit, force=refresh)
    except MarketDataUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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


def _parse_alert_arg(arg: str, *, default: int, min_count: int = 5, max_count: int = 30) -> tuple[int, bool]:
    """แปลง argument ของ /surge, /longterm เป็น (จำนวนที่ขอ, ต้องการสแกนราคาสดไหม).

    รองรับ "/surge 20" (จำนวน) และ "/surge live" (ขอราคาสดแทนแคช DB).
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


def _looks_like_symbol_query(text: str) -> bool:
    """ข้อความที่ไม่มี '/' นำหน้าแต่หน้าตาเป็น ticker (AAPL, voo, BRK.B) → ถือเป็นคำขอวิเคราะห์."""
    token = text.strip()
    if not token or " " in token or len(token) > 12 or not token.isascii():
        return False
    return token.replace(".", "").replace("-", "").isalnum() and not token.isdigit()


def _clip_telegram_text(text: str, *, limit: int = _TELEGRAM_MAX_CHARS) -> str:
    """กันข้อความเกิน Telegram hard limit (4096 ตัวอักษร) ทำให้ sendMessage ล้มเหลวทั้งข้อความ."""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n… (ตัดข้อความเพราะยาวเกินไป)"


def _format_surge_line(signal: dict, index: int) -> str:
    """แปลง surge signal dict เป็นข้อความบรรทัดเดียว."""
    grade = (signal.get("surge_grade") or {}).get("grade", "-")
    change = signal.get("premarket_gap_percent", 0.0)
    rel_vol = signal.get("relative_volume", 0.0)
    price = signal.get("latest_price", 0.0)
    return f"{index}. {signal.get('symbol', '?')} [{grade}] {change:+.1f}% · vol {rel_vol:.1f}x · ${price:.2f}"


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
    price = signal.get("latest_price", 0.0)
    change = signal.get("premarket_gap_percent", 0.0)
    rel_vol = signal.get("relative_volume", 0.0)

    lines = [
        f"📊 วิเคราะห์ {symbol}" + (f" — {name}" if name else ""),
        f"ราคา ${price:.2f} · {change:+.1f}% · vol {rel_vol:.1f}x · เกรด [{grade}]",
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
    targets = plan.get("targets") or []
    for t in targets:
        lines.append(f"• {t.get('label', 'TP')}: ${t.get('price')}")
    if plan.get("risk_reward"):
        lines.append(f"• Risk/Reward: {plan['risk_reward']}")

    # ข่าวล่าสุด
    news = signal.get("news") or []
    if news:
        lines.append("")
        lines.append("📰 ข่าวล่าสุด")
        for item in news[:3]:
            title = (item.get("title") or "").strip()
            source = item.get("source") or ""
            if title:
                lines.append(f"• {title}" + (f" ({source})" if source else ""))
    else:
        lines.append("")
        lines.append("📰 ยังไม่พบข่าวล่าสุดที่ชัดเจน")

    # บทวิเคราะห์ AI (ตัดความยาวกัน message เกิน limit)
    analysis = (result.get("analysis") or "").strip()
    if analysis:
        lines.append("")
        lines.append("🤖 บทวิเคราะห์ AI")
        lines.append(analysis[:1500])

    lines.append("")
    lines.append("⚠️ เป็นเครื่องมือช่วยคัดกรอง ไม่ใช่คำแนะนำการลงทุน")
    return "\n".join(lines)


def _analysis_buttons(symbol: str) -> dict:
    """ปุ่ม inline ใต้ผลวิเคราะห์ — กดแล้วไม่ต้องพิมพ์คำสั่งซ้ำ."""
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
    source = "Gemini" if data.get("analysis_source") == "gemini" else "กฎเชิงเทคนิค"
    lines = [f"✨ หุ้นน่าสนใจวันนี้ (จัดอันดับโดย {source})\n"]
    if not picks:
        lines.append("— ยังไม่พบหุ้นที่เข้าเกณฑ์ —")
        return "\n".join(lines)

    for index, pick in enumerate(picks, 1):
        lines.append(
            f"{index}. {pick['symbol']} — โอกาสวิ่งแรง {round(pick['pop_probability'])}% "
            f"[{pick['verdict']}]"
        )
        lines.append(
            f"   ${pick['price']:.2f} · {pick['change_percent']:+.1f}% · "
            f"vol {pick['relative_volume']:.1f}x {pick.get('sparkline', '')}"
        )
        lines.append(f"   💡 {pick['reason']}")
        if pick.get("catalyst"):
            lines.append(f"   ⚡ {pick['catalyst']}")
        lines.append("")

    lines.append(data.get("disclaimer", ""))
    return "\n".join(lines)


async def _handle_watch_add(telegram, chat_id: str, arg: str) -> None:
    from workers.bot_features import watch_add

    parts = arg.split()
    if not parts:
        await telegram.send_message(
            "ใช้แบบนี้: /watch AAPL 250 (เตือนเมื่อถึง $250) หรือ /watch AAPL (เฝ้าดูเฉย ๆ)",
            chat_id=chat_id,
        )
        return

    symbol = parts[0].upper()
    target: float | None = None
    if len(parts) > 1:
        try:
            target = float(parts[1].replace("$", "").replace(",", ""))
        except ValueError:
            await telegram.send_message(
                "ราคาเป้าหมายต้องเป็นตัวเลข เช่น /watch AAPL 250", chat_id=chat_id
            )
            return

    if not watch_add(chat_id, symbol, target):
        await telegram.send_message("⚠️ บันทึกไม่สำเร็จ (ฐานข้อมูลไม่พร้อม)", chat_id=chat_id)
        return

    await telegram.send_message(
        f"⭐ เพิ่ม {symbol} เข้ารายการเฝ้าดูแล้ว"
        + (f" · จะเตือนเมื่อราคาถึง ${target:,.2f}" if target else " (ยังไม่ตั้งราคาเตือน)"),
        chat_id=chat_id,
    )


async def _handle_watchlist(telegram, chat_id: str) -> None:
    from workers.bot_features import watch_list

    rows = watch_list(chat_id)
    if not rows:
        await telegram.send_message(
            "ยังไม่มีหุ้นในรายการเฝ้าดู — เพิ่มด้วย /watch AAPL 250", chat_id=chat_id
        )
        return

    lines = ["⭐ รายการเฝ้าดูของคุณ\n"]
    for row in rows:
        price = row.get("price")
        target = row.get("target_price")
        line = f"• {row['symbol']}"
        if price:
            line += f" — ${price:.2f} ({row.get('change_percent') or 0:+.1f}%)"
        if target:
            direction = "ถึง" if row.get("direction") == "above" else "ย่อถึง"
            line += f" · เตือนเมื่อ{direction} ${float(target):,.2f}"
        lines.append(line)
    lines.append("\nลบด้วย /unwatch SYMBOL")
    await telegram.send_message(_clip_telegram_text("\n".join(lines)), chat_id=chat_id)


async def _handle_compare(telegram, chat_id: str, arg: str) -> None:
    from workers.bot_features import compare_symbols

    symbols = [token.upper() for token in arg.replace(",", " ").split() if token][:5]
    if len(symbols) < 2:
        await telegram.send_message(
            "ใส่อย่างน้อย 2 ตัว เช่น /compare AAPL MSFT NVDA", chat_id=chat_id
        )
        return

    await telegram.send_message(f"⚖️ กำลังเทียบ {', '.join(symbols)}...", chat_id=chat_id)
    rows = compare_symbols(symbols)
    if not rows:
        await telegram.send_message("⚠️ ดึงข้อมูลไม่สำเร็จ ลองใหม่อีกครั้ง", chat_id=chat_id)
        return

    rows.sort(key=lambda item: item["change_percent"], reverse=True)
    lines = ["⚖️ เทียบหุ้น (ราคาปิดล่าสุด)\n"]
    for row in rows:
        lines.append(f"{row['symbol']} — ${row['price']:.2f} · {row['change_percent']:+.2f}%")
        details = [f"vol {row['relative_volume']:.1f}x"]
        if row.get("return_3m_percent") is not None:
            details.append(f"3 ด. {row['return_3m_percent']:+.1f}%")
        if row.get("pct_from_52w_high") is not None:
            details.append(f"ห่างจุดสูงสุด 52w {row['pct_from_52w_high']:.1f}%")
        if row.get("above_sma200") is not None:
            details.append("เหนือ MA200" if row["above_sma200"] else "ใต้ MA200")
        lines.append(f"   {' · '.join(details)}")
    await telegram.send_message(_clip_telegram_text("\n".join(lines)), chat_id=chat_id)


async def _handle_news(telegram, chat_id: str, arg: str) -> None:
    if not arg:
        await telegram.send_message("ระบุหุ้นด้วย เช่น /news TSLA", chat_id=chat_id)
        return

    from workers.config import get_settings
    from workers.news.fetch_news import get_news_provider

    symbol = arg.split()[0].upper()
    settings = get_settings()
    items = get_news_provider(use_live_data=settings.use_live_data).get_company_news(symbol)
    if not items:
        await telegram.send_message(f"📰 ยังไม่พบข่าวล่าสุดของ {symbol}", chat_id=chat_id)
        return

    lines = [f"📰 ข่าวล่าสุด {symbol}\n"]
    for item in items[:5]:
        lines.append(f"• {item.title}" + (f" ({item.source})" if item.source else ""))
        if item.url:
            lines.append(f"  {item.url}")
    await telegram.send_message(_clip_telegram_text("\n".join(lines)), chat_id=chat_id)


async def _handle_chart(telegram, chat_id: str, arg: str) -> None:
    if not arg:
        await telegram.send_message("ระบุหุ้นด้วย เช่น /chart NVDA", chat_id=chat_id)
        return

    from workers.bot_features import price_history
    from workers.daily_picks import sparkline

    symbol = arg.split()[0].upper()
    try:
        closes = price_history(symbol, days=60)
    except Exception:
        closes = []
    if len(closes) < 2:
        await telegram.send_message(f"⚠️ ไม่พบประวัติราคาของ {symbol}", chat_id=chat_id)
        return

    change = (closes[-1] - closes[0]) / closes[0] * 100 if closes[0] else 0.0
    lines = [
        f"📈 {symbol} — 60 วันทำการล่าสุด",
        "",
        sparkline(closes, width=32),
        "",
        f"ล่าสุด ${closes[-1]:.2f} · ช่วง ${min(closes):.2f}-${max(closes):.2f}",
        f"เปลี่ยนแปลงช่วงนี้ {change:+.1f}%",
    ]
    await telegram.send_message("\n".join(lines), chat_id=chat_id)


async def _handle_portfolio(telegram, chat_id: str, arg: str) -> None:
    from workers.bot_features import portfolio_add, portfolio_remove, portfolio_summary

    parts = arg.split()
    action = parts[0].lower() if parts else ""

    if action in ("add", "เพิ่ม"):
        if len(parts) < 4:
            await telegram.send_message(
                "ใช้แบบนี้: /port add AAPL 10 200.5  (หุ้น จำนวน ต้นทุนต่อหุ้น)",
                chat_id=chat_id,
            )
            return
        try:
            quantity = float(parts[2])
            cost = float(parts[3].replace("$", "").replace(",", ""))
        except ValueError:
            await telegram.send_message("จำนวนและต้นทุนต้องเป็นตัวเลข", chat_id=chat_id)
            return
        ok = portfolio_add(chat_id, parts[1], quantity, cost)
        await telegram.send_message(
            f"✅ บันทึก {parts[1].upper()} {quantity:g} หุ้น @ ${cost:,.2f}"
            if ok
            else "⚠️ บันทึกไม่สำเร็จ (ฐานข้อมูลไม่พร้อม)",
            chat_id=chat_id,
        )
        return

    if action in ("del", "remove", "ลบ"):
        if len(parts) < 2:
            await telegram.send_message("ระบุหุ้นด้วย เช่น /port del AAPL", chat_id=chat_id)
            return
        removed = portfolio_remove(chat_id, parts[1])
        await telegram.send_message(
            f"🗑️ ลบ {parts[1].upper()} ออกจากพอร์ตแล้ว" if removed else "ไม่พบหุ้นตัวนี้ในพอร์ต",
            chat_id=chat_id,
        )
        return

    summary = portfolio_summary(chat_id)
    if not summary["positions"]:
        await telegram.send_message(
            "พอร์ตยังว่าง — เพิ่มด้วย /port add AAPL 10 200.5", chat_id=chat_id
        )
        return

    lines = ["💼 พอร์ตของคุณ (ราคาปิดล่าสุด)\n"]
    for row in summary["positions"]:
        mark = "🟢" if row["pl"] >= 0 else "🔴"
        lines.append(
            f"{mark} {row['symbol']}: {row['quantity']:g} หุ้น @ ${row['cost_basis']:,.2f} "
            f"→ ${row['price']:,.2f}"
        )
        lines.append(f"   กำไร/ขาดทุน ${row['pl']:+,.2f} ({row['pl_percent']:+.1f}%)")
    lines.append("")
    lines.append(
        f"รวมต้นทุน ${summary['total_cost']:,.2f} · มูลค่าปัจจุบัน ${summary['total_value']:,.2f}"
    )
    lines.append(
        f"รวมกำไร/ขาดทุน ${summary['total_pl']:+,.2f} ({summary.get('total_pl_percent', 0):+.1f}%)"
    )
    lines.append("\nลบด้วย /port del SYMBOL")
    await telegram.send_message(_clip_telegram_text("\n".join(lines)), chat_id=chat_id)


async def _handle_earnings(telegram, chat_id: str, arg: str) -> None:
    if not arg:
        await telegram.send_message("ระบุหุ้นด้วย เช่น /earnings NVDA", chat_id=chat_id)
        return

    from workers.scanner.fetch_earnings import fetch_earnings

    symbol = arg.split()[0].upper()
    data = fetch_earnings(symbol)
    lines = [f"📊 งบการเงิน {symbol}\n"]

    if data.get("earnings_date"):
        lines.append(f"🗓️ วันประกาศงบถัดไป: {data['earnings_date']}")
    else:
        # Nasdaq/Zacks ไม่ได้ให้วันประกาศทุกตัว — บอกตรง ๆ ดีกว่าเดา
        lines.append("🗓️ ยังไม่มีข้อมูลวันประกาศงบถัดไปจากผู้ให้บริการ")

    upcoming = data.get("upcoming") or []
    if upcoming:
        lines.append("")
        lines.append("ตลาดคาดการณ์ (EPS):")
        for row in upcoming[:2]:
            lines.append(f"• {row['period']}: ${row['consensus']}")

    previous = data.get("previous") or []
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

    if len(lines) <= 2:
        lines.append("\nไม่พบข้อมูลงบของหุ้นตัวนี้")
    await telegram.send_message(_clip_telegram_text("\n".join(lines)), chat_id=chat_id)


_SYMBOL_IN_TEXT = re.compile(r"\b[A-Z]{1,5}(?:\.[A-Z])?\b")
_NOT_SYMBOLS = {"AI", "USD", "IPO", "ETF", "CEO", "EPS", "US", "PE", "OK", "MA", "RSI"}


async def _handle_free_question(telegram, chat_id: str, question: str) -> None:
    """ตอบคำถามภาษาไทยแบบอิสระ — ถ้าพบ ticker ในข้อความจะแนบราคาจริงให้ AI ด้วย.

    ที่ต้องแนบราคาจริงเพราะ Gemini ไม่รู้ราคาปัจจุบัน ถ้าปล่อยให้ตอบลอย ๆ
    มันจะเดาตัวเลขออกมาเหมือนเป็นข้อมูลจริง
    """
    from workers.config import get_settings

    settings = get_settings()
    if not settings.gemini_api_key:
        await telegram.send_message(
            "❓ ไม่รู้จักคำสั่งนี้ ลอง /help ดูคำสั่งที่ใช้ได้", chat_id=chat_id
        )
        return

    # จับเฉพาะ token ที่ผู้ใช้พิมพ์เป็นตัวพิมพ์ใหญ่จริง ๆ — ถ้า .upper() ทั้งประโยคก่อน
    # คำอังกฤษธรรมดา ("is", "good") จะกลายเป็น ticker ไปด้วย
    symbols = [
        token for token in _SYMBOL_IN_TEXT.findall(question) if token not in _NOT_SYMBOLS
    ][:3]

    # ดึงแค่ราคา ไม่เรียก _run_analysis เพราะนั่นเรียก Gemini อีกรอบต่อหุ้นหนึ่งตัว
    # (โควตา free tier มีแค่ 20 ครั้ง/วัน — คำถามเดียวจะกินไป 2-4 ครั้ง)
    context_lines = []
    if symbols:
        from workers.scanner.fetch_prices import get_market_data_provider

        provider = get_market_data_provider(use_live_data=settings.use_live_data)
        for symbol in symbols:
            try:
                snapshot = provider.get_snapshot(symbol)
            except Exception:
                continue
            change = (
                (snapshot.latest_price - snapshot.previous_close) / snapshot.previous_close * 100
                if snapshot.previous_close > 0
                else 0.0
            )
            rel_vol = (
                snapshot.volume / snapshot.average_volume_20d
                if snapshot.average_volume_20d > 0
                else 1.0
            )
            context_lines.append(
                f"{symbol}: ราคา ${snapshot.latest_price:.2f} ({change:+.2f}%), "
                f"vol {rel_vol:.1f}x"
            )

    await telegram.send_message("🤔 กำลังคิด...", chat_id=chat_id)

    context_block = (
        "ข้อมูลราคาล่าสุด (ราคาปิดรายวัน):\n" + "\n".join(context_lines)
        if context_lines
        else "ไม่มีข้อมูลราคาประกอบ — อย่าเดาตัวเลขราคา"
    )
    prompt = (
        "คุณเป็นผู้ช่วยวิเคราะห์หุ้นสหรัฐ ตอบเป็นภาษาไทย กระชับไม่เกิน 6 บรรทัด\n"
        "ห้ามแต่งตัวเลขราคาที่ไม่ได้ให้มา และต้องบอกเสมอว่านี่ไม่ใช่คำแนะนำการลงทุน\n\n"
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
    except Exception:
        await telegram.send_message(
            "⚠️ ตอนนี้ AI ไม่พร้อมใช้งาน (โควตาเต็มหรือถูกจำกัด) ลองใช้ /analyze SYMBOL แทนได้",
            chat_id=chat_id,
        )
        return

    await telegram.send_message(
        _clip_telegram_text(f"🤖 {answer}"),
        chat_id=chat_id,
        reply_markup=_analysis_buttons(symbols[0]) if symbols else None,
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
    chat_id = str(((callback.get("message") or {}).get("chat") or {}).get("id", ""))
    query_id = str(callback.get("id") or "")
    if not chat_id or ":" not in data:
        return

    action, symbol = data.split(":", 1)
    labels = {"a": "วิเคราะห์", "w": "เพิ่มเฝ้าดู", "n": "ข่าว", "c": "กราฟ", "e": "งบ"}
    await telegram.answer_callback_query(query_id, f"{labels.get(action, '')} {symbol}")

    commands = {
        "a": f"/analyze {symbol}",
        "w": f"/watch {symbol}",
        "n": f"/news {symbol}",
        "c": f"/chart {symbol}",
        "e": f"/earnings {symbol}",
    }
    command = commands.get(action)
    if command:
        await _handle_bot_command(command, chat_id, "private")


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


async def _handle_bot_command(command: str, chat_id: str, chat_type: str = "private") -> None:
    """ตอบกลับคำสั่งจากผู้ใช้ใน Telegram."""
    from workers.alerts.telegram import TelegramClient
    from workers.config import get_settings
    from workers.services import get_longterm_signal_dicts, get_surge_signal_dicts

    settings = get_settings()
    telegram = TelegramClient(settings.telegram_bot_token, None)
    if not telegram.is_configured:
        return

    # แยก command กับ argument (เช่น "/analyze AAPL" -> cmd_word="/analyze", arg="AAPL")
    parts = command.strip().split(maxsplit=1)
    cmd_word = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""
    # ในกลุ่ม Telegram ส่งคำสั่งมาเป็น "/surge@MyBot" — ต้องตัด @botname ทิ้งก่อน
    if "@" in cmd_word:
        cmd_word = cmd_word.split("@", 1)[0]
    # ในกลุ่ม: เงียบไว้ถ้าไม่ใช่คำสั่ง ไม่งั้นบอทจะตอบ "ไม่รู้จักคำสั่ง" ใส่บทสนทนาปกติ
    if chat_type != "private" and not cmd_word.startswith("/"):
        return
    # พิมพ์ชื่อหุ้นเปล่า ๆ ("AAPL", "voo") ให้ถือว่าเป็นการขอวิเคราะห์รายตัว — เฉพาะแชทส่วนตัว
    # เท่านั้น ไม่งั้นในกลุ่มคำพูดทั่วไปอย่าง "ok"/"test" จะกลายเป็นคำสั่งวิเคราะห์ทุกครั้ง
    if (
        chat_type == "private"
        and cmd_word
        and not cmd_word.startswith("/")
        and _looks_like_symbol_query(command)
    ):
        arg = command.strip()
        cmd_word = "/analyze"
    try:
        if cmd_word in ("/analyze", "/a", "/วิเคราะห์", "/stock"):
            if not arg:
                await telegram.send_message(
                    "พิมพ์ชื่อหุ้นต่อท้ายด้วย เช่น /analyze AAPL หรือ /a TSLA",
                    chat_id=chat_id,
                )
                return
            await telegram.send_message(f"🔍 กำลังวิเคราะห์ {arg.upper()}... รอสักครู่", chat_id=chat_id)
            try:
                result = await _run_analysis(arg)
            except Exception as analysis_exc:
                msg = str(analysis_exc)
                if "Too Many Requests" in msg or "429" in msg:
                    reply = f"⏳ ระบบข้อมูลถูกจำกัดชั่วคราว (rate limit) ลองใหม่อีกครั้งใน 1-2 นาที"
                elif "unavailable" in msg or "No " in msg or "ไม่พบ" in msg:
                    reply = f"❓ ไม่พบข้อมูลหุ้น {arg.upper()} — อาจเป็นหุ้นเล็ก/ไม่มีในฐานข้อมูล ลองเช็คสัญลักษณ์อีกครั้ง"
                else:
                    reply = f"⚠️ วิเคราะห์ {arg.upper()} ไม่สำเร็จ: {msg[:150]}"
                await telegram.send_message(reply, chat_id=chat_id)
                return
            await telegram.send_message(
                _format_analysis(result),
                chat_id=chat_id,
                reply_markup=_analysis_buttons(arg.upper()),
            )

        elif cmd_word in ("/today", "/picks", "/วันนี้"):
            await telegram.send_message(
                "✨ กำลังคัดหุ้นน่าสนใจวันนี้... รอบแรกใช้เวลาราว 1 นาที", chat_id=chat_id
            )
            from workers.daily_picks import get_daily_picks

            count, _fresh = _parse_alert_arg(arg, default=5, min_count=3, max_count=10)
            data = await get_daily_picks(limit=count, wait=True)
            await telegram.send_message(
                _clip_telegram_text(_format_daily_picks(data)), chat_id=chat_id
            )

        elif cmd_word in ("/watch", "/เฝ้าดู"):
            await _handle_watch_add(telegram, chat_id, arg)

        elif cmd_word in ("/watchlist", "/watches", "/รายการเฝ้าดู"):
            await _handle_watchlist(telegram, chat_id)

        elif cmd_word in ("/unwatch", "/เลิกเฝ้าดู"):
            from workers.bot_features import watch_remove

            if not arg:
                await telegram.send_message("ระบุหุ้นด้วย เช่น /unwatch AAPL", chat_id=chat_id)
                return
            removed = watch_remove(chat_id, arg.split()[0])
            await telegram.send_message(
                f"🗑️ เอา {arg.split()[0].upper()} ออกจากรายการเฝ้าดูแล้ว"
                if removed
                else f"ไม่พบ {arg.split()[0].upper()} ในรายการเฝ้าดู",
                chat_id=chat_id,
            )

        elif cmd_word in ("/subscribe", "/สมัคร"):
            from workers.repository import set_subscriber

            ok = set_subscriber(settings.database_url, chat_id, True)
            await telegram.send_message(
                "🔔 สมัครรับสรุปรายวันแล้ว — จะส่งให้ตอน 08:00 และ 20:00 (เวลาไทย)\nยกเลิกด้วย /unsubscribe"
                if ok
                else "⚠️ สมัครไม่สำเร็จ (ฐานข้อมูลไม่พร้อม) ลองใหม่อีกครั้ง",
                chat_id=chat_id,
            )

        elif cmd_word in ("/unsubscribe", "/ยกเลิก"):
            from workers.repository import set_subscriber

            ok = set_subscriber(settings.database_url, chat_id, False)
            await telegram.send_message(
                "🔕 ยกเลิกรับสรุปรายวันแล้ว" if ok else "⚠️ ยกเลิกไม่สำเร็จ ลองใหม่อีกครั้ง",
                chat_id=chat_id,
            )

        elif cmd_word in ("/compare", "/เทียบ"):
            await _handle_compare(telegram, chat_id, arg)

        elif cmd_word in ("/news", "/ข่าว"):
            await _handle_news(telegram, chat_id, arg)

        elif cmd_word in ("/market", "/ตลาด"):
            from workers.bot_features import market_overview

            rows = market_overview()
            if not rows:
                await telegram.send_message("⚠️ ดึงข้อมูลตลาดไม่สำเร็จ ลองใหม่อีกครั้ง", chat_id=chat_id)
                return
            lines = ["🌎 ภาพรวมตลาดสหรัฐ (ราคาปิดล่าสุด)\n"]
            for row in rows:
                arrow = "🟢" if row["change_percent"] >= 0 else "🔴"
                lines.append(
                    f"{arrow} {row['label']} ({row['symbol']}): ${row['price']:.2f} "
                    f"{row['change_percent']:+.2f}%"
                )
            await telegram.send_message("\n".join(lines), chat_id=chat_id)

        elif cmd_word in ("/chart", "/กราฟ"):
            await _handle_chart(telegram, chat_id, arg)

        elif cmd_word in ("/port", "/portfolio", "/พอร์ต"):
            await _handle_portfolio(telegram, chat_id, arg)

        elif cmd_word in ("/earnings", "/งบ"):
            await _handle_earnings(telegram, chat_id, arg)

        elif cmd_word in ("/surge", "/surges", "/ziing", "/ซิ่ง"):
            count, want_fresh = _parse_alert_arg(arg, default=settings.surge_alert_limit)
            if want_fresh:
                await telegram.send_message("🔄 กำลังสแกนราคาสดล่าสุด... รอสักครู่", chat_id=chat_id)
            signals = get_surge_signal_dicts(limit=count, fresh=want_fresh)
            if not signals:
                await telegram.send_message("🔄 ยังไม่มีข้อมูล กำลังสแกน+คัดกรองด้วย AI... รอ 1-2 นาที", chat_id=chat_id)
                signals = await _run_scan_and_get("surge", limit=count)
            lines = [f"🚀 หุ้นซิ่ง Top {len(signals) or count} (คัดกรองโดย AI)\n"]
            if signals:
                for i, s in enumerate(signals, 1):
                    lines.append(_format_surge_line(s, i))
                    reason = (s.get("reason") or "").strip()
                    if reason:
                        lines.append(f"   💡 {reason}")
            else:
                lines.append("— ยังไม่พบหุ้นที่เข้าเกณฑ์ —")
            lines.append("\nพิมพ์ /surge 30 เพื่อดูมากขึ้น หรือ /surge live เพื่อสแกนราคาสด")
            await telegram.send_message(_clip_telegram_text("\n".join(lines)), chat_id=chat_id)

        elif cmd_word in ("/longterm", "/long", "/lt", "/ระยะยาว"):
            count, _want_fresh = _parse_alert_arg(arg, default=settings.longterm_alert_limit)
            picks = get_longterm_signal_dicts(limit=count)
            if not picks:
                await telegram.send_message("🔄 ยังไม่มีข้อมูล กำลังสแกนหุ้น... รอสักครู่", chat_id=chat_id)
                picks = await _run_scan_and_get("longterm", limit=count)
            lines = [f"📈 หุ้นน่าสนใจระยะยาว Top {len(picks) or count}\n"]
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
            lines.append("\nพิมพ์ /longterm 30 เพื่อดูมากขึ้น")
            await telegram.send_message(_clip_telegram_text("\n".join(lines)), chat_id=chat_id)

        elif cmd_word in ("/scan", "/สแกน", "/update"):
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

        elif cmd_word in ("/digest", "/summary", "/สรุป"):
            from workers.alerts.digest import build_daily_digest

            await telegram.send_message(build_daily_digest(), chat_id=chat_id)

        elif cmd_word in ("/start", "/help"):
            help_text = (
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
            await telegram.send_message(help_text, chat_id=chat_id)

        elif chat_type == "private" and not cmd_word.startswith("/") and len(command.strip()) > 3:
            # ข้อความอิสระในแชทส่วนตัว → ให้ AI ตอบ พร้อมราคาจริงของหุ้นที่เอ่ยถึง
            await _handle_free_question(telegram, chat_id, command.strip())

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
    import asyncio

    callback = update.get("callback_query")
    if callback:
        asyncio.create_task(_handle_callback(callback))
        return {"ok": True}

    message = update.get("message") or update.get("edited_message")
    if not message:
        return {"ok": True}

    text = (message.get("text") or "").strip()
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id", ""))

    if not text or not chat_id:
        return {"ok": True}

    # fire-and-forget: ตอบ Telegram 200 ทันที ไม่ให้ webhook timeout แล้ว retry
    # (การ scan ใช้เวลานาน บอทจะส่งข้อความกลับเองผ่าน chat_id เมื่อเสร็จ)
    asyncio.create_task(
        _handle_bot_command(text, chat_id, str(chat.get("type") or "private"))
    )
    return {"ok": True}


@app.get("/telegram/set-webhook")
async def telegram_set_webhook(url: Optional[str] = None) -> dict:
    """ตั้ง webhook ให้บอท (เรียกครั้งเดียวหลัง deploy)."""
    import httpx

    from workers.config import get_settings

    settings = get_settings()
    if not settings.telegram_bot_token:
        return {"status": "no_token"}

    # default = service ที่กำลังรันอยู่จริง (Render inject RENDER_EXTERNAL_URL ให้)
    # ของเดิม hardcode URL Vercel เก่าไว้ ทำให้เผลอชี้ webhook ไป service ที่ตายแล้ว
    render_url = os.getenv("RENDER_EXTERNAL_URL", "https://ai-stock-01or.onrender.com").rstrip("/")
    target_url = url or f"{render_url}/telegram/webhook"
    api_url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/setWebhook"
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(api_url, json={"url": target_url})
        await client.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/setMyCommands",
            json={"commands": _BOT_COMMAND_MENU},
        )
        return {"status": "set", "url": target_url, "telegram_response": resp.json()}
