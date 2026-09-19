from __future__ import annotations

import hmac
import os
from typing import Optional

from fastapi import Header, HTTPException

from workers.config import get_settings


def is_publicly_hosted() -> bool:
    """จริงเมื่อ process นี้เข้าถึงได้จากอินเทอร์เน็ต.

    เช็ค RENDER_EXTERNAL_URL ด้วย ไม่พึ่ง app_env อย่างเดียว เพราะ service จริงบน Render
    รายงาน app_env=development อยู่ ทำให้ guard กลายเป็นโมฆะทั้งที่เปิดสู่สาธารณะ
    """
    settings = get_settings()
    return settings.app_env == "production" or bool(os.getenv("RENDER_EXTERNAL_URL"))


def require_admin_api_key(x_admin_api_key: Optional[str] = Header(default=None)) -> None:
    settings = get_settings()
    if not settings.admin_api_key:
        if is_publicly_hosted():
            raise HTTPException(status_code=503, detail="ADMIN_API_KEY is not configured")
        return
    if not x_admin_api_key or not hmac.compare_digest(x_admin_api_key, settings.admin_api_key):
        raise HTTPException(status_code=403, detail="Invalid admin API key")
