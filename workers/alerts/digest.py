from __future__ import annotations

from datetime import datetime, timedelta, timezone

from workers.alerts.telegram import TelegramClient
from workers.config import get_settings
from workers.services import get_longterm_signal_dicts, get_surge_signal_dicts


def _bangkok_tz():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("Asia/Bangkok")
    except Exception:
        # เผื่อระบบไม่มี tz database — Asia/Bangkok = UTC+7 คงที่ ไม่มี DST
        return timezone(timedelta(hours=7))


_TZ = _bangkok_tz()


def _session_label(now: datetime) -> str:
    return "เช้า" if now.hour < 12 else "ค่ำ"


def build_daily_digest(surge_limit: int = 5, longterm_limit: int = 5) -> str:
    """สร้างข้อความสรุปรายวันภาษาไทย: หุ้นซิ่ง Top + หุ้นน่าสะสมระยะยาว Top."""
    now = datetime.now(_TZ)
    date_text = now.strftime("%d/%m/%Y %H:%M")
    lines: list[str] = [f"🔔 AI Stock Radar — สรุป{_session_label(now)} {date_text}", ""]

    surge = get_surge_signal_dicts(limit=surge_limit)
    lines.append("🚀 หุ้นซิ่ง (ระยะสั้น ถือไว-ออกไว)")
    if surge:
        for index, signal in enumerate(surge, start=1):
            grade = (signal.get("surge_grade") or {}).get("grade", "-")
            change = signal.get("premarket_gap_percent", 0.0)
            rel_vol = signal.get("relative_volume", 0.0)
            price = signal.get("latest_price", 0.0)
            lines.append(
                f"{index}. {signal['symbol']} [{grade}] "
                f"{change:+.1f}% · vol {rel_vol:.1f}x · ${price:.2f}"
            )
            catalyst = (signal.get("catalyst") or "").strip()
            if catalyst and "ยังไม่พบข่าว" not in catalyst:
                lines.append(f"   📰 {catalyst[:90]}")
    else:
        lines.append("— ยังไม่พบหุ้นที่เข้าเกณฑ์ —")

    lines.append("")
    longterm = get_longterm_signal_dicts(limit=longterm_limit)
    lines.append("📈 น่าสะสมระยะยาว (เชิงเทรนด์)")
    if longterm:
        for index, pick in enumerate(longterm, start=1):
            lines.append(
                f"{index}. {pick['symbol']} [{pick['grade']}] "
                f"คะแนน {pick['score']:.0f} · 6 ด. {pick['return_6m_percent']:+.0f}% · "
                f"${pick['price']:.2f}"
            )
            reasons = pick.get("reasons") or []
            if reasons:
                lines.append(f"   • {reasons[0]}")
    else:
        lines.append("— ยังไม่พบหุ้นที่เข้าเกณฑ์ —")

    lines.append("")
    lines.append("⚠️ เป็นเครื่องมือช่วยคัดกรอง ไม่ใช่คำแนะนำการลงทุน")
    return "\n".join(lines)


async def send_daily_digest() -> dict:
    """สร้าง digest แล้วส่งเข้า Telegram."""
    settings = get_settings()
    message = build_daily_digest()

    telegram = TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)
    if not telegram.is_configured:
        return {"status": "not_configured", "sent": False, "message": message}

    try:
        sent = await telegram.send_message(message)
    except Exception:
        return {"status": "failed", "sent": False, "message": message}
    return {"status": "sent" if sent else "failed", "sent": sent, "message": message}
