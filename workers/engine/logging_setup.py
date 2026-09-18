"""Structured (JSON) logging พร้อมปิดบัง secret.

ใช้ ``logger.info("event_name", extra={"event": "...", ...})`` แล้วทุก field ใน extra
จะออกมาเป็น key ของ JSON หนึ่งบรรทัด — ค้นใน log ของ Render ได้ง่าย
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone

# ชื่อ attribute มาตรฐานของ LogRecord — ที่เหลือคือ field ที่ส่งมาทาง extra
_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}

# ปิดบังค่าที่ดูเหมือน secret ถ้าเผลอหลุดเข้ามาในข้อความ log
_SECRET_PATTERNS = [
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password|authorization)[\"']?\s*[:=]\s*[\"']?)([^\s\"'&,]+)"),
    re.compile(r"(?i)(bot)(\d{6,}:[A-Za-z0-9_-]{20,})"),  # Telegram bot token ใน URL
    re.compile(r"(AIza[0-9A-Za-z_-]{20,})"),  # Google API key
    re.compile(r"(postgres(?:ql)?(?:\+psycopg)?://[^:\s]+:)([^@\s]+)(@)"),
]


def redact(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 3:
            text = pattern.sub(lambda m: f"{m.group(1)}***{m.group(3)}", text)
        elif pattern.groups == 2:
            text = pattern.sub(lambda m: f"{m.group(1)}***", text)
        else:
            text = pattern.sub("***", text)
    return text


_SECRET_KEYS = {"api_key", "apikey", "token", "secret", "password", "authorization", "key"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        for key, value in vars(record).items():
            if key in _STANDARD_ATTRS or key.startswith("_"):
                continue
            if key.lower() in _SECRET_KEYS:
                value = "***"
            elif isinstance(value, str):
                value = redact(value)
            payload[key] = value
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


_configured = False


def configure_logging() -> None:
    global _configured
    if _configured:
        return
    _configured = True
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    handler = logging.StreamHandler()
    if os.getenv("LOG_FORMAT", "json").lower() == "json":
        handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # httpx log URL ทุก request ที่ระดับ INFO — URL บางอันมี token (Telegram) จึงลดระดับลง
    logging.getLogger("httpx").setLevel(logging.WARNING)
