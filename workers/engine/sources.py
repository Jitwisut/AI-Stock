"""HTTP client ของแหล่งข้อมูลจริง: Nasdaq public API, Nasdaq RSS, CBOE, Google News RSS.

ทุก request ผ่าน _get() ที่มี timeout, retry + exponential backoff, จัดการ 429
(พักทั้ง provider ชั่วคราวแทนการยิงซ้ำ), และบันทึกสุขภาพ provider + structured log
ห้ามใส่ API key ลงใน URL/log — แหล่งเหล่านี้ไม่ต้องใช้ key อยู่แล้ว
"""
from __future__ import annotations

import json
import logging
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from workers.engine.runtime import (
    ProviderUnavailableError,
    RateLimitedError,
    ensure_available,
    record_failure,
    record_success,
)

logger = logging.getLogger(__name__)

NASDAQ = "nasdaq"
NASDAQ_RSS = "nasdaq_rss"
CBOE = "cboe"
GOOGLE_NEWS = "google_news_rss"

# Nasdaq API ปฏิเสธ User-Agent ที่ไม่ใช่เบราว์เซอร์
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

_RATE_LIMIT_PAUSE_SECONDS = 60.0


def _get(provider: str, url: str, *, timeout: float = 15, retries: int = 3, accept: str | None = None) -> bytes:
    ensure_available(provider)
    headers = dict(_BROWSER_HEADERS)
    if accept:
        headers["Accept"] = accept
    last_error = "unknown error"
    for attempt in range(retries):
        started = time.perf_counter()
        try:
            with urlopen(Request(url, headers=headers), timeout=timeout) as response:
                body = response.read()
            record_success(provider)
            logger.debug(
                "fetch_ok",
                extra={
                    "event": "stock_fetch",
                    "provider": provider,
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                },
            )
            return body
        except HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            if exc.code == 429:
                record_failure(provider, last_error, rate_limited_for=_RATE_LIMIT_PAUSE_SECONDS)
                logger.warning("rate_limited", extra={"event": "rate_limit", "provider": provider})
                raise RateLimitedError(provider, "HTTP 429 rate limited") from exc
            if 500 <= exc.code < 600 and attempt < retries - 1:
                time.sleep(0.8 * (2**attempt))
                continue
            record_failure(provider, last_error)
            logger.warning("api_error", extra={"event": "api_error", "provider": provider, "status": exc.code})
            raise ProviderUnavailableError(provider, last_error) from exc
        except (URLError, TimeoutError, ConnectionError, OSError) as exc:
            last_error = type(exc).__name__
            if attempt < retries - 1:
                time.sleep(0.8 * (2**attempt))
                continue
            record_failure(provider, last_error)
            logger.warning("api_error", extra={"event": "api_error", "provider": provider, "error_type": last_error})
            raise ProviderUnavailableError(provider, last_error) from exc
    raise ProviderUnavailableError(provider, last_error)


def nasdaq_json(path: str, params: dict | None = None, *, timeout: float = 15) -> dict:
    query = f"?{urlencode(params)}" if params else ""
    body = _get(NASDAQ, f"https://api.nasdaq.com/api/{path}{query}", timeout=timeout)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        record_failure(NASDAQ, "invalid JSON")
        raise ProviderUnavailableError(NASDAQ, "invalid JSON response") from exc
    return payload if isinstance(payload, dict) else {}


def nasdaq_asset_json(path_template: str, symbol: str, params: dict | None = None, *, timeout: float = 15) -> tuple[dict, str | None]:
    """Nasdaq แยก endpoint ตาม assetclass (stocks/etf) และตอบ data=null ถ้าเลือกผิดชนิด."""
    payload: dict = {}
    for asset_class in ("stocks", "etf"):
        payload = nasdaq_json(
            path_template.format(symbol=symbol.upper()),
            {**(params or {}), "assetclass": asset_class},
            timeout=timeout,
        )
        if payload.get("data"):
            return payload, asset_class
    return payload, None


def raw_text(provider: str, url: str, *, timeout: float = 15, accept: str | None = None) -> str:
    return _get(provider, url, timeout=timeout, accept=accept).decode("utf-8", errors="replace")
