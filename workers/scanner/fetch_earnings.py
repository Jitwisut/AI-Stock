"""ข้อมูลงบ/EPS จาก Nasdaq public API (ฟรี ไม่ต้องใช้ key).

ข้อจำกัดที่ต้องบอกผู้ใช้ตรง ๆ: วันประกาศงบมาจาก Zacks ผ่าน Nasdaq ซึ่ง "ไม่มีให้
ทุกตัว" — หุ้นใหญ่อย่าง MSFT/TSLA มักได้ค่าว่าง ส่วน EPS ย้อนหลัง/คาดการณ์มีเกือบ
ทุกตัว จึงคืน earnings_date = None แทนการเดา
"""

from __future__ import annotations

import re
from urllib.parse import urlencode
from urllib.request import Request

from workers.scanner.fetch_prices import _BROWSER_HEADERS, _http_get_with_retry

_EPS_URL = "https://api.nasdaq.com/api/quote/{symbol}/eps"
_EARNINGS_DATE_URL = "https://api.nasdaq.com/api/analyst/{symbol}/earnings-date"
_ANNOUNCEMENT_RE = re.compile(r":\s*([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})")


def _get_json(url: str, *, timeout: int = 15) -> dict:
    import json

    request = Request(f"{url}?{urlencode({})}".rstrip("?"), headers=_BROWSER_HEADERS)
    return json.loads(_http_get_with_retry(request, timeout=timeout).decode("utf-8"))


def fetch_earnings(symbol: str) -> dict:
    """คืนวันประกาศงบ (ถ้ามี) + EPS ไตรมาสที่ผ่านมาและที่ตลาดคาด."""
    normalized = symbol.upper()
    result: dict = {
        "symbol": normalized,
        "earnings_date": None,
        "report_text": None,
        "upcoming": [],
        "previous": [],
    }

    try:
        payload = _get_json(_EARNINGS_DATE_URL.format(symbol=normalized))
        data = payload.get("data") or {}
        announcement = str(data.get("announcement") or "")
        match = _ANNOUNCEMENT_RE.search(announcement)
        if match:
            result["earnings_date"] = match.group(1)
            result["report_text"] = data.get("reportText")
    except Exception:
        pass

    try:
        payload = _get_json(_EPS_URL.format(symbol=normalized))
        rows = ((payload.get("data") or {}).get("earningsPerShare")) or []
        for row in rows:
            entry = {
                "period": row.get("period"),
                "consensus": row.get("consensus"),
                "actual": row.get("earnings"),
            }
            if str(row.get("type", "")).startswith("Upcoming"):
                result["upcoming"].append(entry)
            else:
                result["previous"].append(entry)
    except Exception:
        pass

    return result
