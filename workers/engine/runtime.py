"""โครงสร้างพื้นฐานที่ provider ทุกตัวใช้ร่วมกัน: TTL cache + สุขภาพของ provider.

- TTL cache ในหน่วยความจำ (thread-safe, กันยิงซ้ำพร้อมกันด้วย lock ต่อ key)
- Provider health: จำเวลาที่สำเร็จล่าสุด/ error ล่าสุด/ ถูก rate limit ถึงเมื่อไร
  เพื่อให้ API ตอบได้ว่า "Data temporarily unavailable" พร้อม provider,
  last successful update และ retry status แทนการสร้างข้อมูลทดแทน
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable


class ProviderUnavailableError(RuntimeError):
    def __init__(self, provider: str, message: str) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider


class RateLimitedError(ProviderUnavailableError):
    pass


class SymbolNotFoundError(LookupError):
    """provider ตอบปกติแต่ไม่รู้จัก symbol นี้ — ไม่ใช่ปัญหาการเชื่อมต่อ (API ตอบ 404)."""


@dataclass
class ProviderHealth:
    name: str
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    rate_limited_until: float = 0.0
    total_requests: int = 0
    total_failures: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def as_dict(self) -> dict:
        now = time.time()
        return {
            "provider": self.name,
            "status": self.status(),
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "last_error_at": self.last_error_at.isoformat() if self.last_error_at else None,
            "last_error": self.last_error,
            "consecutive_failures": self.consecutive_failures,
            "retry_after_seconds": max(0, round(self.rate_limited_until - now)),
            "total_requests": self.total_requests,
            "total_failures": self.total_failures,
        }

    def status(self) -> str:
        if self.rate_limited_until > time.time():
            return "rate_limited"
        if self.consecutive_failures >= 5:
            return "down"
        if self.consecutive_failures > 0:
            return "degraded"
        return "ok" if self.last_success_at else "unknown"


_HEALTH: dict[str, ProviderHealth] = {}
_HEALTH_LOCK = threading.Lock()


def health(name: str) -> ProviderHealth:
    with _HEALTH_LOCK:
        if name not in _HEALTH:
            _HEALTH[name] = ProviderHealth(name=name)
        return _HEALTH[name]


def all_health() -> list[dict]:
    with _HEALTH_LOCK:
        items = list(_HEALTH.values())
    return [item.as_dict() for item in items]


def record_success(name: str) -> None:
    item = health(name)
    with item.lock:
        item.total_requests += 1
        item.consecutive_failures = 0
        item.last_success_at = datetime.now(timezone.utc)


def record_failure(name: str, error: str, *, rate_limited_for: float = 0.0) -> None:
    item = health(name)
    with item.lock:
        item.total_requests += 1
        item.total_failures += 1
        item.consecutive_failures += 1
        item.last_error_at = datetime.now(timezone.utc)
        item.last_error = error[:300]
        if rate_limited_for > 0:
            item.rate_limited_until = max(item.rate_limited_until, time.time() + rate_limited_for)


def ensure_available(name: str) -> None:
    """Circuit breaker: ถ้า provider เพิ่งโดน rate limit ไม่ต้องยิงซ้ำให้โดนหนักขึ้น."""
    item = health(name)
    wait = item.rate_limited_until - time.time()
    if wait > 0:
        raise RateLimitedError(name, f"rate limited, retry in {round(wait)}s")


class TTLCache:
    def __init__(self, max_items: int = 5000) -> None:
        self._data: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self._max_items = max_items

    def get(self, key: str) -> tuple[bool, Any]:
        entry = self._data.get(key)
        if entry is None or entry[0] < time.time():
            return False, None
        return True, entry[1]

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        if len(self._data) >= self._max_items:
            self._evict()
        self._data[key] = (time.time() + ttl_seconds, value)

    def get_or_compute(self, key: str, ttl_seconds: float, compute: Callable[[], Any]) -> Any:
        hit, value = self.get(key)
        if hit:
            return value
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        # lock ต่อ key: ถ้าหลายเธรดขอ key เดียวกันพร้อมกัน ยิง provider ครั้งเดียว
        with lock:
            hit, value = self.get(key)
            if hit:
                return value
            value = compute()
            self.set(key, value, ttl_seconds)
            return value

    def _evict(self) -> None:
        now = time.time()
        expired = [key for key, (expires, _value) in self._data.items() if expires < now]
        for key in expired:
            self._data.pop(key, None)
        if len(self._data) >= self._max_items:
            # ยังเต็ม → ทิ้งตัวที่จะหมดอายุก่อนสุดครึ่งหนึ่ง
            for key, _entry in sorted(self._data.items(), key=lambda item: item[1][0])[: self._max_items // 2]:
                self._data.pop(key, None)


CACHE = TTLCache()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
