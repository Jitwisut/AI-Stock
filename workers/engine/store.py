"""Persistence ของ engine (Postgres) — ถ้า DB ใช้ไม่ได้ ระบบยังทำงานต่อด้วยหน่วยความจำ."""
from __future__ import annotations

import json
import logging
from datetime import date

from workers.config import get_settings

logger = logging.getLogger(__name__)


def _engine():
    from workers.repository import _engine as repo_engine

    return repo_engine(get_settings().database_url)


def load_bar_cache(symbol: str) -> dict | None:
    try:
        from sqlalchemy import text

        with _engine().connect() as conn:
            row = conn.execute(
                text("SELECT symbol, last_bar_date, bars, source, fetched_at FROM bar_cache WHERE symbol = :s"),
                {"s": symbol.upper()},
            ).mappings().first()
        return dict(row) if row else None
    except Exception:
        logger.debug("bar_cache read failed", exc_info=True)
        return None


def save_bar_cache(symbol: str, last_bar_date: date, bars: list, source: str) -> None:
    try:
        from sqlalchemy import text

        with _engine().begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO bar_cache (symbol, last_bar_date, bars, source, fetched_at)
                    VALUES (:s, :d, CAST(:b AS JSONB), :src, NOW())
                    ON CONFLICT (symbol) DO UPDATE
                    SET last_bar_date = EXCLUDED.last_bar_date, bars = EXCLUDED.bars,
                        source = EXCLUDED.source, fetched_at = EXCLUDED.fetched_at
                    """
                ),
                {"s": symbol.upper(), "d": last_bar_date, "b": json.dumps(bars), "src": source},
            )
    except Exception:
        logger.debug("bar_cache write failed", exc_info=True)


def insert_run(table: str, values: dict) -> int | None:
    try:
        from sqlalchemy import text

        columns = ", ".join(values)
        params = ", ".join(
            f"CAST(:{key} AS JSONB)" if isinstance(values[key], (dict, list)) else f":{key}" for key in values
        )
        bound = {k: json.dumps(v, default=str) if isinstance(v, (dict, list)) else v for k, v in values.items()}
        with _engine().begin() as conn:
            row = conn.execute(text(f"INSERT INTO {table} ({columns}) VALUES ({params}) RETURNING id"), bound).first()
        return int(row[0]) if row else None
    except Exception:
        logger.warning("run insert failed", extra={"event": "db_error", "table": table}, exc_info=True)
        return None


def update_run(table: str, run_id: int | None, values: dict) -> None:
    if run_id is None:
        return
    try:
        from sqlalchemy import text

        sets = ", ".join(
            f"{key} = CAST(:{key} AS JSONB)" if isinstance(values[key], (dict, list)) else f"{key} = :{key}"
            for key in values
        )
        bound = {k: json.dumps(v, default=str) if isinstance(v, (dict, list)) else v for k, v in values.items()}
        bound["id"] = run_id
        with _engine().begin() as conn:
            conn.execute(text(f"UPDATE {table} SET {sets} WHERE id = :id"), bound)
    except Exception:
        logger.warning("run update failed", extra={"event": "db_error", "table": table}, exc_info=True)


def fetch_run(table: str, *, run_id: int | None = None, where: dict | None = None) -> dict | None:
    try:
        from sqlalchemy import text

        clauses, params = [], {}
        if run_id is not None:
            clauses.append("id = :id")
            params["id"] = run_id
        for key, value in (where or {}).items():
            clauses.append(f"{key} = :{key}")
            params[key] = value
        condition = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with _engine().connect() as conn:
            row = conn.execute(
                text(f"SELECT * FROM {table} {condition} ORDER BY started_at DESC, id DESC LIMIT 1"), params
            ).mappings().first()
        return _jsonable(dict(row)) if row else None
    except Exception:
        logger.warning("run read failed", extra={"event": "db_error", "table": table}, exc_info=True)
        return None


def _jsonable(row: dict) -> dict:
    out = {}
    for key, value in row.items():
        if hasattr(value, "isoformat"):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out
