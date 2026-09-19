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


def _num(value) -> float:
    """แปลงเป็น float เสมอ — ค่าที่วิ่งผ่าน JSONB อาจกลับมาเป็น str หรือ None
    ซึ่งจะทำให้ format spec อย่าง :.1f โยน error แล้วล้ม digest ทั้งรอบ."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def build_daily_digest(surge_limit: int = 5, longterm_limit: int = 5) -> str:
    """สร้างข้อความสรุปรายวันภาษาไทย: หุ้นซิ่ง Top + หุ้นน่าสะสมระยะยาว Top."""
    now = datetime.now(_TZ)
    date_text = now.strftime("%d/%m/%Y %H:%M")
    lines: list[str] = [f"🔔 AI Stock Radar — สรุป{_session_label(now)} {date_text}", ""]

    # ใช้ .get() ทุกช่อง: ของเดิม index ตรง ๆ (pick['return_6m_percent']) แถวเดียวที่
    # ข้อมูลไม่ครบจะโยน KeyError ออกมาจาก build_daily_digest ที่ไม่มี try ครอบ
    # ทำให้สรุปรอบ 08:00/20:00 ล่มทั้งรอบสำหรับสมาชิกทุกคน
    surge = get_surge_signal_dicts(limit=surge_limit)
    lines.append("🚀 หุ้นซิ่ง (ระยะสั้น ถือไว-ออกไว)")
    if surge:
        for index, signal in enumerate(surge, start=1):
            grade = (signal.get("surge_grade") or {}).get("grade", "-")
            change = _num(signal.get("premarket_gap_percent"))
            rel_vol = _num(signal.get("relative_volume"))
            price = _num(signal.get("latest_price"))
            lines.append(
                f"{index}. {signal.get('symbol', '?')} [{grade}] "
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
                f"{index}. {pick.get('symbol', '?')} [{pick.get('grade', '-')}] "
                f"คะแนน {_num(pick.get('score')):.0f} · "
                f"6 ด. {_num(pick.get('return_6m_percent')):+.0f}% · "
                f"${_num(pick.get('price')):.2f}"
            )
            reasons = pick.get("reasons") or []
            if reasons:
                lines.append(f"   • {reasons[0]}")
    else:
        lines.append("— ยังไม่พบหุ้นที่เข้าเกณฑ์ —")

    lines.append("")
    lines.append("⚠️ เป็นเครื่องมือช่วยคัดกรอง ไม่ใช่คำแนะนำการลงทุน")
    return "\n".join(lines)


async def send_daily_digest(surge_limit: int = 5, longterm_limit: int = 5) -> dict:
    """สร้าง digest แล้วส่งเข้า Telegram — ทั้ง chat หลักและผู้ที่กด /subscribe.

    รับ limit ทั้งสองตัวเพราะ /alerts/daily-digest ส่งมาเป็น kwargs อยู่แล้ว
    ของเดิมไม่มีพารามิเตอร์ ทำให้ endpoint นั้นโยน TypeError ทุกครั้งที่ถูกเรียก
    """
    settings = get_settings()
    message = build_daily_digest(surge_limit=surge_limit, longterm_limit=longterm_limit)

    telegram = TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)
    if not telegram.is_configured:
        return {"status": "not_configured", "sent": False, "message": message}

    from workers.repository import list_subscribers

    # chat หลักจาก env + ผู้ที่สมัครผ่านบอท (ไม่ให้ซ้ำถ้าเป็นคนเดียวกัน)
    targets: list[str] = []
    if settings.telegram_chat_id:
        targets.append(str(settings.telegram_chat_id))
    for chat_id in list_subscribers(settings.database_url):
        if chat_id not in targets:
            targets.append(chat_id)

    if not targets:
        return {"status": "no_recipients", "sent": False, "message": message}

    sent_count = 0
    for chat_id in targets:
        try:
            if await telegram.send_message(message, chat_id=chat_id):
                sent_count += 1
        except Exception:
            continue

    return {
        "status": "sent" if sent_count else "failed",
        "sent": sent_count > 0,
        "recipients": len(targets),
        "delivered": sent_count,
        "message": message,
    }
