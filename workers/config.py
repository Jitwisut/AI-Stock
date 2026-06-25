from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _normalize_database_url(raw: str) -> str:
    """แก้ไข DATABASE_URL ให้ SQLAlchemy + psycopg อ่านได้.

    Render inject ``postgres://user:pass@host/db`` แต่ psycopg ต้องการ
    ``postgresql+psycopg://...``.
    """
    if raw.startswith("postgres://"):
        raw = "postgresql+psycopg" + raw[len("postgres"):]
    elif raw.startswith("postgresql://"):
        raw = "postgresql+psycopg" + raw[len("postgresql"):]
    return raw


def _normalize_redis_url(raw: str) -> str:
    """Render inject ``rediss://...`` (TLS) แต่ Celery + redis-py
    ต้องการ ``redis://...`` โดย TLS เปิดผ่าน query param ``?ssl_cert_reqs=none``
    แทน (Render internal network ไม่ต้อง verify cert).
    """
    if raw.startswith("rediss://"):
        raw = "redis://" + raw[len("rediss://"):]
        if "?" in raw:
            raw += "&ssl_cert_reqs=none"
        else:
            raw += "?ssl_cert_reqs=none"
    return raw


def _load_dotenv(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _env_csv(name: str, default: str = "") -> list[str]:
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    openai_api_key: str | None
    gemini_api_key: str | None
    ai_provider: str
    openai_model: str
    gemini_model: str
    finnhub_api_key: str | None
    polygon_api_key: str | None
    admin_api_key: str | None
    database_url: str
    redis_url: str
    app_env: str
    log_level: str
    market_data_provider: str
    use_live_data: bool
    allow_mock_data: bool
    send_telegram: bool
    save_signals: bool
    backend_cors_origins: list[str]
    top_signal_limit: int
    universe_mode: str
    scan_universe_limit: int
    scan_workers: int
    ai_candidate_limit: int
    longterm_scan_limit: int
    surge_extra_symbols: list[str]
    surge_universe_mode: str
    surge_universe_limit: int
    surge_candidate_limit: int
    surge_min_change_percent: float
    surge_backfill_min_change_percent: float
    surge_min_results: int


@lru_cache
def get_settings() -> Settings:
    _load_dotenv()
    return Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        ai_provider=os.getenv("AI_PROVIDER", "gemini").lower(),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        finnhub_api_key=os.getenv("FINNHUB_API_KEY") or None,
        polygon_api_key=os.getenv("POLYGON_API_KEY") or None,
        admin_api_key=os.getenv("ADMIN_API_KEY") or None,
        database_url=_normalize_database_url(os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg://stock_radar:stock_radar@localhost:5432/stock_radar",
        )),
        redis_url=_normalize_redis_url(os.getenv("REDIS_URL", "redis://localhost:6379/0")),
        app_env=os.getenv("APP_ENV", "development"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        market_data_provider=os.getenv("MARKET_DATA_PROVIDER", "yahoo").lower(),
        use_live_data=_env_bool("USE_LIVE_DATA"),
        allow_mock_data=_env_bool("ALLOW_MOCK_DATA", default=False),
        send_telegram=_env_bool("SEND_TELEGRAM"),
        save_signals=_env_bool("SAVE_SIGNALS"),
        backend_cors_origins=_env_csv("BACKEND_CORS_ORIGINS", "*"),
        top_signal_limit=_env_int("TOP_SIGNAL_LIMIT", default=10, minimum=1, maximum=100),
        universe_mode=os.getenv("UNIVERSE_MODE", "all").lower(),
        scan_universe_limit=_env_int("SCAN_UNIVERSE_LIMIT", default=0, minimum=0, maximum=10000),
        scan_workers=_env_int("SCAN_WORKERS", default=12, minimum=1, maximum=48),
        ai_candidate_limit=_env_int("AI_CANDIDATE_LIMIT", default=50, minimum=5, maximum=500),
        longterm_scan_limit=_env_int("LONGTERM_SCAN_LIMIT", default=80, minimum=5, maximum=500),
        surge_extra_symbols=_env_csv("SURGE_EXTRA_SYMBOLS"),
        surge_universe_mode=os.getenv("SURGE_UNIVERSE_MODE", "all").lower(),
        surge_universe_limit=_env_int("SURGE_UNIVERSE_LIMIT", default=0, minimum=0, maximum=10000),
        surge_candidate_limit=_env_int("SURGE_CANDIDATE_LIMIT", default=120, minimum=10, maximum=500),
        surge_min_change_percent=_env_float(
            "SURGE_MIN_CHANGE_PERCENT",
            default=50.0,
            minimum=0.0,
            maximum=500.0,
        ),
        surge_backfill_min_change_percent=_env_float(
            "SURGE_BACKFILL_MIN_CHANGE_PERCENT",
            default=10.0,
            minimum=0.0,
            maximum=500.0,
        ),
        surge_min_results=_env_int("SURGE_MIN_RESULTS", default=50, minimum=1, maximum=100),
    )
