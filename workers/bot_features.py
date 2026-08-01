"""ตรรกะเบื้องหลังคำสั่งใหม่ของ Telegram bot — watchlist, พอร์ต, เทียบหุ้น, ภาพรวมตลาด.

แยกจาก apps/api/app/main.py เพื่อให้ไฟล์ webhook เหลือแค่ routing + จัดรูปข้อความ
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from workers.config import get_settings
from workers.data_models import MarketSnapshot
from workers.repository import (
    add_position,
    add_watch,
    list_positions,
    list_watches,
    mark_watch_alerted,
    remove_position,
    remove_watch,
)
from workers.scanner.fetch_prices import get_market_data_provider

logger = logging.getLogger(__name__)

# ETF ที่ใช้แทน "ภาพรวมตลาด" — ทำได้เพราะแก้ให้ดึง ETF จาก Nasdaq ได้แล้ว
MARKET_OVERVIEW_SYMBOLS = ("SPY", "QQQ", "DIA", "IWM")

# กันเตือนซ้ำ ๆ ตัวเดิมภายในไม่กี่นาที
_ALERT_COOLDOWN = timedelta(hours=6)


def _snapshots(symbols: list[str]) -> dict[str, MarketSnapshot]:
    settings = get_settings()
    provider = get_market_data_provider(use_live_data=settings.use_live_data)
    return provider.get_snapshots(symbols)


def change_percent(snapshot: MarketSnapshot) -> float:
    if snapshot.previous_close <= 0:
        return 0.0
    return (snapshot.latest_price - snapshot.previous_close) / snapshot.previous_close * 100


# ── watchlist ────────────────────────────────────────────────────────────────


def watch_add(chat_id: str, symbol: str, target_price: float | None) -> bool:
    settings = get_settings()
    direction = "above"
    if target_price is not None:
        try:
            snapshot = get_market_data_provider(
                use_live_data=settings.use_live_data
            ).get_snapshot(symbol)
            # ตั้งราคาต่ำกว่าราคาปัจจุบัน = ผู้ใช้อยากรู้ตอน "ย่อลงมาถึง"
            direction = "below" if target_price < snapshot.latest_price else "above"
        except Exception:
            direction = "above"
    return add_watch(settings.database_url, chat_id, symbol, target_price, direction)


def watch_remove(chat_id: str, symbol: str) -> bool:
    return remove_watch(get_settings().database_url, chat_id, symbol)


def watch_list(chat_id: str) -> list[dict]:
    """รายการเฝ้าดูพร้อมราคาล่าสุด."""
    watches = list_watches(get_settings().database_url, chat_id)
    if not watches:
        return []
    snapshots = _snapshots([row["symbol"] for row in watches])
    enriched = []
    for row in watches:
        snapshot = snapshots.get(row["symbol"].upper())
        enriched.append(
            {
                **row,
                "price": snapshot.latest_price if snapshot else None,
                "change_percent": change_percent(snapshot) if snapshot else None,
            }
        )
    return enriched


def check_price_alerts() -> list[dict]:
    """เช็ค watchlist ทุกคนแล้วคืนรายการที่ถึงเป้า (ยังไม่ส่ง — ผู้เรียกเป็นคนส่ง).

    ยิงราคาครั้งเดียวแบบ bulk สำหรับ symbol ที่ไม่ซ้ำกัน เพื่อไม่ให้ web worker
    ตัวเดียวบนโฮสต์ล้มเพราะ job นี้
    """
    settings = get_settings()
    watches = [row for row in list_watches(settings.database_url) if row.get("target_price")]
    if not watches:
        return []

    symbols = sorted({row["symbol"].upper() for row in watches})
    snapshots = _snapshots(symbols)
    now = datetime.now(timezone.utc)
    triggered = []

    for row in watches:
        snapshot = snapshots.get(row["symbol"].upper())
        if not snapshot:
            continue
        last_alert = row.get("last_alert_at")
        if last_alert and last_alert.tzinfo is None:
            last_alert = last_alert.replace(tzinfo=timezone.utc)
        if last_alert and now - last_alert < _ALERT_COOLDOWN:
            continue

        target = float(row["target_price"])
        price = snapshot.latest_price
        hit = price >= target if row.get("direction", "above") == "above" else price <= target
        if not hit:
            continue

        triggered.append(
            {
                "chat_id": row["chat_id"],
                "symbol": row["symbol"],
                "target_price": target,
                "direction": row.get("direction", "above"),
                "price": price,
                "change_percent": change_percent(snapshot),
            }
        )
        mark_watch_alerted(settings.database_url, row["id"])

    return triggered


# ── พอร์ตจำลอง ───────────────────────────────────────────────────────────────


def portfolio_add(chat_id: str, symbol: str, quantity: float, cost_basis: float) -> bool:
    return add_position(get_settings().database_url, chat_id, symbol, quantity, cost_basis)


def portfolio_remove(chat_id: str, symbol: str) -> bool:
    return remove_position(get_settings().database_url, chat_id, symbol)


def portfolio_summary(chat_id: str) -> dict:
    positions = list_positions(get_settings().database_url, chat_id)
    if not positions:
        return {"positions": [], "total_cost": 0.0, "total_value": 0.0, "total_pl": 0.0}

    snapshots = _snapshots([row["symbol"] for row in positions])
    rows = []
    total_cost = total_value = 0.0
    for position in positions:
        snapshot = snapshots.get(position["symbol"].upper())
        quantity = float(position["quantity"])
        cost_basis = float(position["cost_basis"])
        price = snapshot.latest_price if snapshot else cost_basis
        cost = quantity * cost_basis
        value = quantity * price
        total_cost += cost
        total_value += value
        rows.append(
            {
                "symbol": position["symbol"],
                "quantity": quantity,
                "cost_basis": cost_basis,
                "price": price,
                "value": value,
                "pl": value - cost,
                "pl_percent": ((price - cost_basis) / cost_basis * 100) if cost_basis > 0 else 0.0,
                "priced": snapshot is not None,
            }
        )

    rows.sort(key=lambda item: item["pl"], reverse=True)
    return {
        "positions": rows,
        "total_cost": total_cost,
        "total_value": total_value,
        "total_pl": total_value - total_cost,
        "total_pl_percent": ((total_value - total_cost) / total_cost * 100) if total_cost > 0 else 0.0,
    }


# ── เทียบหุ้น / ภาพรวมตลาด / กราฟ ────────────────────────────────────────────


def compare_symbols(symbols: list[str]) -> list[dict]:
    """เทียบหุ้นหลายตัว: ราคา, %เปลี่ยน, วอลุ่มสัมพัทธ์, ผลตอบแทน 3 เดือน, ตำแหน่ง 52 สัปดาห์."""
    from workers.scanner.fetch_history import _metrics_from_closes, get_history_provider

    settings = get_settings()
    snapshots = _snapshots(symbols)
    history = get_history_provider(use_live_data=settings.use_live_data)

    def _row(symbol: str) -> dict | None:
        snapshot = snapshots.get(symbol.upper())
        if not snapshot:
            return None
        row = {
            "symbol": symbol.upper(),
            "price": snapshot.latest_price,
            "change_percent": change_percent(snapshot),
            "relative_volume": (
                snapshot.volume / snapshot.average_volume_20d
                if snapshot.average_volume_20d > 0
                else 1.0
            ),
            "return_3m_percent": None,
            "pct_from_52w_high": None,
            "above_sma200": None,
            "closes": [],
        }
        try:
            closes = history.get_closes(symbol)
            metrics = _metrics_from_closes(symbol, closes, latest_price=snapshot.latest_price)
            row["return_3m_percent"] = metrics.return_3m_percent
            row["pct_from_52w_high"] = (
                (metrics.price - metrics.week52_high) / metrics.week52_high * 100
                if metrics.week52_high > 0
                else None
            )
            row["above_sma200"] = metrics.above_sma200
            row["closes"] = closes
        except Exception:
            pass
        return row

    with ThreadPoolExecutor(max_workers=min(6, max(1, len(symbols)))) as executor:
        rows = [row for row in executor.map(_row, symbols) if row]
    return rows


def market_overview() -> list[dict]:
    snapshots = _snapshots(list(MARKET_OVERVIEW_SYMBOLS))
    labels = {
        "SPY": "S&P 500",
        "QQQ": "Nasdaq 100",
        "DIA": "Dow Jones",
        "IWM": "Russell 2000",
    }
    rows = []
    for symbol in MARKET_OVERVIEW_SYMBOLS:
        snapshot = snapshots.get(symbol)
        if not snapshot:
            continue
        rows.append(
            {
                "symbol": symbol,
                "label": labels[symbol],
                "price": snapshot.latest_price,
                "change_percent": change_percent(snapshot),
            }
        )
    return rows


async def send_price_alerts() -> dict:
    """เช็ค watchlist แล้วส่งข้อความเตือนเข้า Telegram — คืนสรุปว่าส่งไปกี่ราย."""
    from workers.alerts.telegram import TelegramClient

    settings = get_settings()
    telegram = TelegramClient(settings.telegram_bot_token, None)
    if not telegram.is_configured:
        return {"checked": 0, "sent": 0, "error": "telegram not configured"}

    triggered = check_price_alerts()
    sent = 0
    for alert in triggered:
        direction = "ขึ้นถึง" if alert["direction"] == "above" else "ย่อลงถึง"
        text = (
            f"🔔 {alert['symbol']} {direction}เป้าหมายแล้ว\n"
            f"ราคาล่าสุด ${alert['price']:.2f} ({alert['change_percent']:+.2f}%) "
            f"· เป้าที่ตั้งไว้ ${alert['target_price']:,.2f}\n"
            f"ดูรายละเอียดด้วย /analyze {alert['symbol']}"
        )
        try:
            await telegram.send_message(text, chat_id=alert["chat_id"])
            sent += 1
        except Exception as exc:
            logger.warning("price alert send failed for %s: %s", alert["chat_id"], exc)

    return {"checked": len(triggered), "sent": sent}


def price_history(symbol: str, days: int = 60) -> list[float]:
    from workers.scanner.fetch_history import get_history_provider

    settings = get_settings()
    history = get_history_provider(use_live_data=settings.use_live_data)
    return history.get_closes(symbol)[-days:]
