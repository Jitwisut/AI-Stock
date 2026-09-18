"""ข่าวที่เกี่ยวกับหุ้นรายตัว — เก็บเป็น FACT เท่านั้น (title/source/published_at/url/symbols).

ลำดับแหล่ง: Nasdaq RSS (ติด tag symbol มาจาก Nasdaq เอง) → Google News RSS (ค้นด้วยชื่อ+symbol)
ถ้าทุกแหล่งล่ม คืนรายการว่างพร้อมสถานะ unavailable — ห้ามสร้างข่าวแทน
การตีความ (positive/negative) เป็นหน้าที่ของ AI layer และต้องแยกจาก fact เสมอ
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode

from workers.engine import sources
from workers.engine.runtime import CACHE, ProviderUnavailableError, iso, utcnow

logger = logging.getLogger(__name__)

NEWS_TTL = 10 * 60.0
MAX_AGE_DAYS = 30


def _parse_date(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _items(xml_text: str) -> list[ET.Element]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    return root.findall("./channel/item")


def _nasdaq_rss(symbol: str) -> list[dict]:
    text = sources.raw_text(
        sources.NASDAQ_RSS,
        f"https://www.nasdaq.com/feed/rssoutbound?{urlencode({'symbol': symbol.upper()})}",
        accept="application/rss+xml, application/xml, text/xml",
    )
    out = []
    for item in _items(text):
        tickers = []
        for child in item:
            if child.tag.endswith("tickers") and child.text:
                tickers = [t.strip().upper() for t in child.text.split(",") if t.strip()]
        if tickers and symbol.upper() not in tickers:
            continue  # feed ของ symbol นี้แต่ข่าวไม่ได้ tag หุ้นตัวนี้ (เช่นข่าว ETF flow) → ไม่เกี่ยวข้อง
        out.append({
            "title": (item.findtext("title") or "").strip(),
            "url": (item.findtext("link") or "").strip(),
            "source": "Nasdaq.com" + (f" / {item.findtext('{http://purl.org/dc/elements/1.1/}creator')}" if item.findtext("{http://purl.org/dc/elements/1.1/}creator") else ""),
            "published_at": _parse_date(item.findtext("pubDate")),
            "related_symbols": tickers,
            "provider": sources.NASDAQ_RSS,
        })
    return out


def _google_rss(symbol: str, company: str | None) -> list[dict]:
    query = f'"{symbol.upper()}" stock' if not company else f'"{symbol.upper()}" OR "{company}" stock'
    text = sources.raw_text(
        sources.GOOGLE_NEWS,
        f"https://news.google.com/rss/search?{urlencode({'q': query + ' when:14d', 'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en'})}",
        accept="application/rss+xml, application/xml, text/xml",
    )
    out = []
    for item in _items(text):
        out.append({
            "title": (item.findtext("title") or "").strip(),
            "url": (item.findtext("link") or "").strip(),
            "source": (item.findtext("source") or "Google News").strip(),
            "published_at": _parse_date(item.findtext("pubDate")),
            "related_symbols": [symbol.upper()],
            "provider": sources.GOOGLE_NEWS,
        })
    return out


def _clean_company(name: str | None) -> str | None:
    if not name:
        return None
    cleaned = re.sub(r"\b(Common Stock|Class [A-Z]|Ordinary Shares|Inc\.?|Corporation|Corp\.?|Holdings?|plc|Ltd\.?|N\.V\.)\b", "", name, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.")
    return cleaned or None


def get_news(symbol: str, company_name: str | None = None, limit: int = 8) -> dict:
    return CACHE.get_or_compute(
        f"news:{symbol.upper()}", NEWS_TTL, lambda: _fetch_news(symbol, _clean_company(company_name), limit)
    )


def _fetch_news(symbol: str, company: str | None, limit: int) -> dict:
    fetched = utcnow()
    collected: list[dict] = []
    errors: list[str] = []
    for name, fetcher in (("nasdaq_rss", lambda: _nasdaq_rss(symbol)), ("google_news_rss", lambda: _google_rss(symbol, company))):
        try:
            collected.extend(fetcher())
        except ProviderUnavailableError as exc:
            errors.append(f"{name}: {exc}")
        if len([item for item in collected if item.get("published_at")]) >= limit:
            break

    cutoff = fetched - timedelta(days=MAX_AGE_DAYS)
    seen: set[str] = set()
    items = []
    for item in sorted(collected, key=lambda x: x.get("published_at") or datetime.min.replace(tzinfo=timezone.utc), reverse=True):
        published = item.get("published_at")
        key = re.sub(r"\W+", "", item["title"].lower())[:80]
        if not item["title"] or key in seen:
            continue
        if published is None or published < cutoff:
            continue  # ไม่มีวันที่ หรือเก่าเกิน 30 วัน → ไม่ใช้ (กันข่าวเก่าถูกตีความเป็นข่าวใหม่)
        seen.add(key)
        age_hours = (fetched - published).total_seconds() / 3600
        items.append({**item, "published_at": iso(published), "age_hours": round(age_hours, 1)})
        if len(items) >= limit:
            break

    status = "ok" if items else ("unavailable" if errors and not collected else "no_recent_news")
    if errors:
        logger.info("news_partial_failure", extra={"event": "api_error", "symbol": symbol, "errors": errors})
    return {"items": items, "status": status, "errors": errors, "fetched_at": iso(fetched), "max_age_days": MAX_AGE_DAYS}
