from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from functools import lru_cache

from workers.config import get_settings
from workers.data_models import SignalScore, Stock
from workers.recommendations import recommendation_for, surge_grade, surge_plan, surge_score
from workers.serializers import SCORE_BREAKDOWN_MAX


logger = logging.getLogger(__name__)


@lru_cache
def _engine(database_url: str):
    from sqlalchemy import create_engine

    return create_engine(_normalize_database_url(database_url), pool_pre_ping=True)


def _normalize_database_url(database_url: str) -> str:
    """Use psycopg v3 even when hosted providers give a plain postgres URL."""
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+psycopg://", 1)
    return database_url


def database_available(database_url: str) -> bool:
    try:
        from sqlalchemy import text

        with _engine(database_url).connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def run_migrations(database_url: str) -> None:
    """รัน schema.sql ตอน container start — idempotent (ใช้ IF NOT EXISTS).

    Render ไม่มี docker-entrypoint-initdb.d เหมือน docker-compose
    จึงต้องรัน migration จาก app code เองตอน startup.
    """
    from pathlib import Path

    try:
        from sqlalchemy import text
    except Exception:
        return

    schema_path = Path(__file__).resolve().parent.parent / "database" / "schema.sql"
    if not schema_path.exists():
        logger.warning("schema.sql not found at %s", schema_path)
        return

    raw_sql = schema_path.read_text(encoding="utf-8")
    statements = _split_sql_statements(raw_sql)

    try:
        with _engine(database_url).begin() as connection:
            for stmt in statements:
                connection.execute(text(stmt))
        logger.info("Database migrations applied successfully (%d statements)", len(statements))
    except Exception as exc:
        logger.exception("Database migration failed")
        if get_settings().app_env == "production":
            raise exc


def _split_sql_statements(raw: str) -> list[str]:
    """แยก raw SQL เป็น statement ๆ โดยใช้ `;` เป็นตัวคั่น
    โดยข้ามบรรทัดว่างและ comment และไม่แยกภายใน $$ dollar-quote block."""
    statements = []
    buf: list[str] = []
    in_dollar_quote = False
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        if stripped.count("$$") % 2 == 1:  # odd number of $$ → toggle
            in_dollar_quote = not in_dollar_quote
        buf.append(line)
        if stripped.endswith(";") and not in_dollar_quote:
            merged = "\n".join(buf).strip()
            if merged:
                statements.append(merged)
            buf.clear()
    remaining = "\n".join(buf).strip()
    if remaining:
        statements.append(remaining)
    return statements


def create_scan_run(database_url: str, kind: str, provider: str, status: str = "queued") -> int:
    try:
        from sqlalchemy import text

        with _engine(database_url).begin() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO scan_runs (kind, status, provider, started_at)
                    VALUES (:kind, :status, :provider, :started_at)
                    RETURNING id
                    """
                ),
                {
                    "kind": kind,
                    "status": status,
                    "provider": provider,
                    "started_at": datetime.now(timezone.utc),
                },
            ).first()
        return int(row.id)
    except Exception as exc:
        return _handle_repository_error("create scan run failed", exc, 0)


def update_scan_run(
    database_url: str,
    scan_run_id: int,
    *,
    status: str,
    universe_count: int | None = None,
    scanned_count: int | None = None,
    failed_count: int | None = None,
    error: str | None = None,
    finish: bool = False,
) -> None:
    try:
        from sqlalchemy import text

        with _engine(database_url).begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE scan_runs
                    SET status = :status,
                        universe_count = COALESCE(:universe_count, universe_count),
                        scanned_count = COALESCE(:scanned_count, scanned_count),
                        failed_count = COALESCE(:failed_count, failed_count),
                        error = :error,
                        finished_at = CASE WHEN :finish THEN :finished_at ELSE finished_at END
                    WHERE id = :id
                    """
                ),
                {
                    "id": scan_run_id,
                    "status": status,
                    "universe_count": universe_count,
                    "scanned_count": scanned_count,
                    "failed_count": failed_count,
                    "error": error,
                    "finish": finish,
                    "finished_at": datetime.now(timezone.utc),
                },
            )
    except Exception as exc:
        _handle_repository_error("update scan run failed", exc, None)


def latest_scan_run(database_url: str, kind: str | None = None) -> dict | None:
    try:
        from sqlalchemy import text

        where = "WHERE kind = :kind" if kind else ""
        params = {"kind": kind} if kind else {}
        with _engine(database_url).connect() as connection:
            row = connection.execute(
                text(
                    f"""
                    SELECT *
                    FROM scan_runs
                    {where}
                    ORDER BY started_at DESC, id DESC
                    LIMIT 1
                    """
                ),
                params,
            ).mappings().first()
        return _normalize_scan_run_row(row) if row else None
    except Exception as exc:
        return _handle_repository_error("latest scan run query failed", exc, None)


def get_scan_run(database_url: str, scan_run_id: int) -> dict | None:
    try:
        from sqlalchemy import text

        with _engine(database_url).connect() as connection:
            row = connection.execute(
                text("SELECT * FROM scan_runs WHERE id = :id"),
                {"id": scan_run_id},
            ).mappings().first()
        return _normalize_scan_run_row(row) if row else None
    except Exception as exc:
        return _handle_repository_error("get scan run query failed", exc, None)


def upsert_stocks(stocks: list[Stock], database_url: str) -> int:
    try:
        from sqlalchemy import text
    except Exception:
        return 0

    sql = text(
        """
        INSERT INTO stocks (symbol, name, exchange, sector, market_cap, float_shares, is_active)
        VALUES (:symbol, :name, :exchange, :sector, :market_cap, :float_shares, :is_active)
        ON CONFLICT (symbol) DO UPDATE SET
          name = EXCLUDED.name,
          exchange = EXCLUDED.exchange,
          sector = EXCLUDED.sector,
          market_cap = EXCLUDED.market_cap,
          float_shares = EXCLUDED.float_shares,
          is_active = EXCLUDED.is_active
        """
    )
    rows = [
        {
            "symbol": stock.symbol,
            "name": stock.name,
            "exchange": stock.exchange,
            "sector": stock.sector,
            "market_cap": stock.market_cap,
            "float_shares": stock.float_shares,
            "is_active": stock.is_active,
        }
        for stock in stocks
    ]

    try:
        with _engine(database_url).begin() as connection:
            connection.execute(sql, rows)
        return len(rows)
    except Exception as exc:
        return _handle_repository_error("upsert stocks failed", exc, 0)


def save_signals(
    signals: list[SignalScore],
    database_url: str,
    scan_run_id: int | None = None,
) -> list[int]:
    try:
        from sqlalchemy import text
    except Exception:
        return []

    sql = text(
        """
        INSERT INTO signals (
          scan_run_id,
          symbol,
          score,
          momentum_score,
          volume_score,
          catalyst_score,
          premarket_gap_score,
          breakout_score,
          risk_score,
          gap_percent,
          relative_volume,
          latest_price,
          previous_close,
          day_high,
          prior_high,
          market_cap,
          surge_score,
          surge_grade,
          surge_plan,
          recommendation,
          provider_updated_at,
          data_source,
          catalyst,
          reason,
          risks,
          watch_plan,
          news,
          created_at
        )
        VALUES (
          :scan_run_id,
          :symbol,
          :score,
          :momentum_score,
          :volume_score,
          :catalyst_score,
          :premarket_gap_score,
          :breakout_score,
          :risk_score,
          :gap_percent,
          :relative_volume,
          :latest_price,
          :previous_close,
          :day_high,
          :prior_high,
          :market_cap,
          :surge_score,
          CAST(:surge_grade AS JSONB),
          CAST(:surge_plan AS JSONB),
          CAST(:recommendation AS JSONB),
          :provider_updated_at,
          :data_source,
          :catalyst,
          :reason,
          CAST(:risks AS JSONB),
          CAST(:watch_plan AS JSONB),
          CAST(:news AS JSONB),
          :created_at
        )
        RETURNING id
        """
    )

    inserted_ids: list[int] = []
    try:
        with _engine(database_url).begin() as connection:
            for signal in signals:
                news = [
                    {
                        "symbol": item.symbol,
                        "title": item.title,
                        "url": item.url,
                        "source": item.source,
                        "published_at": item.published_at.isoformat(),
                        "summary": item.summary,
                        "sentiment_score": item.sentiment_score,
                    }
                    for item in signal.news
                ]
                row = connection.execute(
                    sql,
                    {
                        "scan_run_id": scan_run_id,
                        "symbol": signal.symbol,
                        "score": signal.score,
                        "momentum_score": signal.momentum_score,
                        "volume_score": signal.volume_score,
                        "catalyst_score": signal.catalyst_score,
                        "premarket_gap_score": signal.premarket_gap_score,
                        "breakout_score": signal.breakout_score,
                        "risk_score": signal.risk_score,
                        "gap_percent": signal.gap_percent,
                        "relative_volume": signal.relative_volume,
                        "latest_price": signal.latest_price,
                        "previous_close": signal.previous_close,
                        "day_high": signal.day_high,
                        "prior_high": signal.prior_high,
                        "market_cap": signal.market_cap,
                        "surge_score": round(surge_score(signal), 2),
                        "surge_grade": json.dumps(surge_grade(signal), ensure_ascii=False),
                        "surge_plan": json.dumps(surge_plan(signal), ensure_ascii=False),
                        "recommendation": json.dumps(
                            recommendation_for(signal),
                            ensure_ascii=False,
                        ),
                        "provider_updated_at": signal.provider_updated_at,
                        "data_source": signal.data_source,
                        "catalyst": signal.catalyst,
                        "reason": signal.reason,
                        "risks": json.dumps(signal.risks, ensure_ascii=False),
                        "watch_plan": json.dumps(signal.watch_plan, ensure_ascii=False),
                        "news": json.dumps(news, ensure_ascii=False),
                        "created_at": signal.created_at,
                    },
                ).first()
                if row:
                    inserted_ids.append(int(row.id))
        return inserted_ids
    except Exception as exc:
        return _handle_repository_error("save signals failed", exc, [])


def list_signals(
    database_url: str,
    limit: int = 25,
    symbol: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[dict]:
    try:
        from sqlalchemy import text
    except Exception as exc:
        return _handle_repository_error("list signals query failed", exc, [])

    filters = []
    params = {"limit": max(1, min(100, limit))}
    if symbol:
        filters.append("symbol = :symbol")
        params["symbol"] = symbol.upper()
    if date_from:
        filters.append("created_at >= :date_from")
        params["date_from"] = date_from
    if date_to:
        filters.append("created_at <= :date_to")
        params["date_to"] = date_to

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    sql = text(
        f"""
        SELECT *
        FROM signals
        {where_clause}
        ORDER BY created_at DESC, score DESC
        LIMIT :limit
        """
    )

    try:
        with _engine(database_url).connect() as connection:
            rows = connection.execute(sql, params).mappings().all()
        return [_normalize_signal_row(row) for row in rows]
    except Exception:
        return []


def latest_signals(database_url: str, limit: int = 5) -> list[dict]:
    try:
        from sqlalchemy import text
    except Exception:
        return []

    sql = text(
        """
        WITH latest_completed_run AS (
          SELECT id
          FROM scan_runs
          WHERE status = 'completed'
          ORDER BY finished_at DESC NULLS LAST, started_at DESC, id DESC
          LIMIT 1
        )
        SELECT s.*
        FROM signals s, latest_completed_run
        WHERE s.scan_run_id = latest_completed_run.id
        ORDER BY s.score DESC
        LIMIT :limit
        """
    )
    try:
        with _engine(database_url).connect() as connection:
            rows = connection.execute(sql, {"limit": max(1, min(20, limit))}).mappings().all()
        return [_normalize_signal_row(row) for row in rows]
    except Exception as exc:
        return _handle_repository_error("latest signals query failed", exc, [])


def latest_signals_by_kind(database_url: str, kind: str, limit: int = 10) -> list[dict]:
    """อ่านสัญญาณล่าสุดจาก DB กรองตามชนิด (surge / longterm / momentum) — เร็ว <1 วิ."""
    try:
        from sqlalchemy import text
    except Exception:
        return []

    _limit = max(1, min(100, limit))
    sql = text(
        """\
        WITH latest_completed_run AS (
          SELECT id
          FROM scan_runs
          WHERE status = 'completed' AND kind = :kind
          ORDER BY finished_at DESC NULLS LAST, started_at DESC, id DESC
          LIMIT 1
        )
        SELECT s.*
        FROM signals s, latest_completed_run
        WHERE s.scan_run_id = latest_completed_run.id
        ORDER BY s.surge_score DESC, s.score DESC
        LIMIT :limit
        """
    )
    # Fallback: signals เก่าที่ยังไม่ผูก scan_run (ก่อน migration)
    order_col = "surge_score" if kind == "surge" else "score"
    sql_fallback = text(
        f"SELECT * FROM signals ORDER BY {order_col} DESC NULLS LAST, created_at DESC LIMIT :limit"
    )
    try:
        with _engine(database_url).connect() as connection:
            rows = (
                connection.execute(sql, {"kind": kind, "limit": _limit})
                .mappings()
                .all()
            )
            if not rows and kind != "surge":
                rows = connection.execute(sql_fallback, {"limit": _limit}).mappings().all()
        return [_normalize_signal_row(row) for row in rows]
    except Exception as exc:
        return _handle_repository_error(f"latest signals by kind ({kind}) query failed", exc, [])


def get_signal(database_url: str, signal_id: int) -> dict | None:
    try:
        from sqlalchemy import text
    except Exception as exc:
        return _handle_repository_error("get signal query failed", exc, None)

    try:
        with _engine(database_url).connect() as connection:
            row = connection.execute(
                text("SELECT * FROM signals WHERE id = :id"),
                {"id": signal_id},
            ).mappings().first()
        return _normalize_signal_row(row) if row else None
    except Exception:
        return None


def save_telegram_alert(database_url: str, signal_id: int | None, status: str) -> None:
    try:
        from sqlalchemy import text

        with _engine(database_url).begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO telegram_alerts (signal_id, sent_at, status)
                    VALUES (:signal_id, :sent_at, :status)
                    """
                ),
                {
                    "signal_id": signal_id,
                    "sent_at": datetime.now(timezone.utc),
                    "status": status,
                },
            )
    except Exception as exc:
        _handle_repository_error("save telegram alert failed", exc, None)


# ── Telegram bot storage: watchlist / subscribers / portfolio ────────────────
# ทุกฟังก์ชันคืนค่า default เมื่อ DB ล่ม แทนที่จะโยน error ขึ้นไปถึงบอท
# (บอทควรตอบผู้ใช้ว่าทำไม่สำเร็จ ไม่ใช่เงียบไปเฉย ๆ จึงคืน False/[] ให้ caller ตัดสินใจ)


def bot_tables_ready(database_url: str) -> bool:
    """ตารางของบอท (watchlist/subscribers/portfolio) ถูกสร้างครบหรือยัง."""
    try:
        from sqlalchemy import text

        with _engine(database_url).connect() as connection:
            for table in ("bot_watchlist", "bot_subscribers", "bot_portfolio"):
                connection.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
        return True
    except Exception:
        return False


def add_watch(
    database_url: str,
    chat_id: str,
    symbol: str,
    target_price: float | None,
    direction: str = "above",
) -> bool:
    try:
        from sqlalchemy import text

        with _engine(database_url).begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO bot_watchlist (chat_id, symbol, target_price, direction)
                    VALUES (:chat_id, :symbol, :target_price, :direction)
                    ON CONFLICT (chat_id, symbol) DO UPDATE
                    SET target_price = EXCLUDED.target_price,
                        direction = EXCLUDED.direction,
                        last_alert_at = NULL
                    """
                ),
                {
                    "chat_id": str(chat_id),
                    "symbol": symbol.upper(),
                    "target_price": target_price,
                    "direction": direction,
                },
            )
        return True
    except Exception as exc:
        return _handle_repository_error("add watch failed", exc, False)


def remove_watch(database_url: str, chat_id: str, symbol: str) -> bool:
    try:
        from sqlalchemy import text

        with _engine(database_url).begin() as connection:
            result = connection.execute(
                text("DELETE FROM bot_watchlist WHERE chat_id = :chat_id AND symbol = :symbol"),
                {"chat_id": str(chat_id), "symbol": symbol.upper()},
            )
        return bool(result.rowcount)
    except Exception as exc:
        return _handle_repository_error("remove watch failed", exc, False)


def list_watches(database_url: str, chat_id: str | None = None) -> list[dict]:
    """คืนรายการเฝ้าดู — ไม่ระบุ chat_id = ทุกคน (ใช้ตอน job เช็คราคา)."""
    try:
        from sqlalchemy import text

        query = "SELECT * FROM bot_watchlist"
        params: dict = {}
        if chat_id is not None:
            query += " WHERE chat_id = :chat_id"
            params["chat_id"] = str(chat_id)
        query += " ORDER BY symbol"
        with _engine(database_url).connect() as connection:
            rows = connection.execute(text(query), params).mappings().all()
        return [dict(row) for row in rows]
    except Exception as exc:
        return _handle_repository_error("list watches failed", exc, [])


def mark_watch_alerted(database_url: str, watch_id: int) -> None:
    try:
        from sqlalchemy import text

        with _engine(database_url).begin() as connection:
            connection.execute(
                text("UPDATE bot_watchlist SET last_alert_at = :now WHERE id = :id"),
                {"now": datetime.now(timezone.utc), "id": watch_id},
            )
    except Exception as exc:
        _handle_repository_error("mark watch alerted failed", exc, None)


def set_subscriber(database_url: str, chat_id: str, active: bool) -> bool:
    try:
        from sqlalchemy import text

        with _engine(database_url).begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO bot_subscribers (chat_id, active)
                    VALUES (:chat_id, :active)
                    ON CONFLICT (chat_id) DO UPDATE SET active = EXCLUDED.active
                    """
                ),
                {"chat_id": str(chat_id), "active": active},
            )
        return True
    except Exception as exc:
        return _handle_repository_error("set subscriber failed", exc, False)


def list_subscribers(database_url: str) -> list[str]:
    try:
        from sqlalchemy import text

        with _engine(database_url).connect() as connection:
            rows = connection.execute(
                text("SELECT chat_id FROM bot_subscribers WHERE active = TRUE")
            ).all()
        return [str(row[0]) for row in rows]
    except Exception as exc:
        return _handle_repository_error("list subscribers failed", exc, [])


def add_position(
    database_url: str, chat_id: str, symbol: str, quantity: float, cost_basis: float
) -> bool:
    try:
        from sqlalchemy import text

        with _engine(database_url).begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO bot_portfolio (chat_id, symbol, quantity, cost_basis)
                    VALUES (:chat_id, :symbol, :quantity, :cost_basis)
                    ON CONFLICT (chat_id, symbol) DO UPDATE
                    SET quantity = EXCLUDED.quantity, cost_basis = EXCLUDED.cost_basis
                    """
                ),
                {
                    "chat_id": str(chat_id),
                    "symbol": symbol.upper(),
                    "quantity": quantity,
                    "cost_basis": cost_basis,
                },
            )
        return True
    except Exception as exc:
        return _handle_repository_error("add position failed", exc, False)


def remove_position(database_url: str, chat_id: str, symbol: str) -> bool:
    try:
        from sqlalchemy import text

        with _engine(database_url).begin() as connection:
            result = connection.execute(
                text("DELETE FROM bot_portfolio WHERE chat_id = :chat_id AND symbol = :symbol"),
                {"chat_id": str(chat_id), "symbol": symbol.upper()},
            )
        return bool(result.rowcount)
    except Exception as exc:
        return _handle_repository_error("remove position failed", exc, False)


def list_positions(database_url: str, chat_id: str) -> list[dict]:
    try:
        from sqlalchemy import text

        with _engine(database_url).connect() as connection:
            rows = connection.execute(
                text("SELECT * FROM bot_portfolio WHERE chat_id = :chat_id ORDER BY symbol"),
                {"chat_id": str(chat_id)},
            ).mappings().all()
        return [dict(row) for row in rows]
    except Exception as exc:
        return _handle_repository_error("list positions failed", exc, [])


def _normalize_signal_row(row) -> dict:
    data = dict(row)
    risks = data.get("risks") or []
    watch_plan = data.get("watch_plan") or []
    news = data.get("news") or []

    if isinstance(risks, str):
        risks = json.loads(risks)
    if isinstance(watch_plan, str):
        watch_plan = json.loads(watch_plan)
    if isinstance(news, str):
        news = json.loads(news)

    normalized = {
        "id": data.get("id"),
        "symbol": data.get("symbol"),
        "score": float(data.get("score") or 0),
        "premarket_gap_percent": float(data.get("gap_percent") or 0),
        "relative_volume": float(data.get("relative_volume") or 0),
        "latest_price": float(data.get("latest_price") or 0),
        "previous_close": float(data.get("previous_close") or 0),
        "day_high": float(data.get("day_high") or 0),
        "prior_high": float(data.get("prior_high") or 0),
        "market_cap": float(data["market_cap"]) if data.get("market_cap") is not None else None,
        "provider_updated_at": (
            data.get("provider_updated_at").isoformat() if data.get("provider_updated_at") else None
        ),
        "data_source": data.get("data_source") or "unknown",
        "catalyst": data.get("catalyst") or "",
        "reason": data.get("reason") or "",
        "risks": risks,
        "watch_plan": watch_plan,
        "score_breakdown": {
            "momentum": float(data.get("momentum_score") or 0),
            "volume": float(data.get("volume_score") or 0),
            "catalyst": float(data.get("catalyst_score") or 0),
            "premarket_gap": float(data.get("premarket_gap_score") or 0),
            "breakout": float(data.get("breakout_score") or 0),
            "risk": float(data.get("risk_score") or 0),
        },
        "score_breakdown_max": SCORE_BREAKDOWN_MAX,
        "news": news,
        "created_at": data.get("created_at").isoformat() if data.get("created_at") else None,
    }
    normalized["surge_score"] = float(data.get("surge_score") or round(surge_score(normalized), 2))
    normalized["surge_grade"] = _json_value(data.get("surge_grade")) or surge_grade(normalized)
    normalized["surge_plan"] = _json_value(data.get("surge_plan")) or surge_plan(normalized)
    normalized["recommendation"] = _json_value(data.get("recommendation")) or recommendation_for(normalized)
    return normalized


def _normalize_scan_run_row(row) -> dict:
    data = dict(row)
    return {
        "id": data.get("id"),
        "kind": data.get("kind"),
        "status": data.get("status"),
        "provider": data.get("provider"),
        "started_at": data.get("started_at").isoformat() if data.get("started_at") else None,
        "finished_at": data.get("finished_at").isoformat() if data.get("finished_at") else None,
        "universe_count": int(data.get("universe_count") or 0),
        "scanned_count": int(data.get("scanned_count") or 0),
        "failed_count": int(data.get("failed_count") or 0),
        "error": data.get("error"),
    }


def _json_value(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


def _handle_repository_error(message: str, exc: Exception, default):
    logger.exception(message)
    if get_settings().app_env == "production":
        raise exc
    return default
