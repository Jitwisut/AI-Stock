from __future__ import annotations

import hmac
from typing import Optional

__all__ = ["require_admin_api_key", "secrets_match"]

from fastapi import Header, HTTPException

from workers.config import get_settings


def secrets_match(provided: Optional[str], expected: str) -> bool:
    """เทียบความลับแบบ constant-time.

    เทียบเป็น bytes เพราะ hmac.compare_digest โยน TypeError ถ้า str มีอักขระนอก ASCII
    ซึ่งเกิดได้สองทาง: ตั้ง key เป็นภาษาไทย หรือ attacker ยัด byte แปลก ๆ มาใน header
    (Starlette decode header เป็น latin-1) ทั้งสองกรณีจะกลายเป็น 500 แทนที่จะเป็น 403
    """
    if not provided:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def require_admin_api_key(x_admin_api_key: Optional[str] = Header(default=None)) -> None:
    settings = get_settings()
    if not settings.admin_api_key:
        # หมายเหตุ: service จริงบน Render รายงาน app_env=development และยังไม่ได้ตั้ง
        # ADMIN_API_KEY จึงเท่ากับปล่อยผ่านทุก request — ตั้ง ADMIN_API_KEY เมื่อไร
        # guard ถึงจะเริ่มทำงานจริง
        if settings.app_env == "production":
            raise HTTPException(status_code=503, detail="ADMIN_API_KEY is not configured")
        return
    if not secrets_match(x_admin_api_key, settings.admin_api_key):
        raise HTTPException(status_code=403, detail="Invalid admin API key")
