from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from workers.data_models import NewsItem


class NewsProvider:
    def get_company_news(self, symbol: str) -> list[NewsItem]:
        raise NotImplementedError


class MockNewsProvider(NewsProvider):
    def get_company_news(self, symbol: str) -> list[NewsItem]:
        now = datetime.now(timezone.utc)
        titles = {
            "AMD": "Analysts highlight AI chip demand and raise near-term estimates",
            "NVDA": "AI infrastructure demand remains strong after new product commentary",
            "TSLA": "Delivery data beats expectations and lifts premarket sentiment",
            "PLTR": "New enterprise AI contract expands government and commercial pipeline",
            "AVGO": "Semiconductor demand outlook improves after analyst upgrade",
        }
        title = titles.get(symbol, f"{symbol} shows unusual market activity in premarket trading")
        return [
            NewsItem(
                symbol=symbol,
                title=title,
                url=f"https://example.com/news/{symbol.lower()}",
                source="MockNews",
                published_at=now - timedelta(hours=4),
                summary=title,
                sentiment_score=0.65,
            )
        ]


class YahooNewsProvider(NewsProvider):
    search_url = "https://query1.finance.yahoo.com/v1/finance/search"

    def get_company_news(self, symbol: str) -> list[NewsItem]:
        params = urlencode({"q": symbol, "quotesCount": 0, "newsCount": 3})
        request = Request(
            f"{self.search_url}?{params}",
            headers={"User-Agent": "stock-radar-ai/0.1"},
        )
        with urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))

        items: list[NewsItem] = []
        for item in payload.get("news", [])[:3]:
            published_at = datetime.fromtimestamp(
                item.get("providerPublishTime", 0),
                tz=timezone.utc,
            )
            title = item.get("title") or f"{symbol} latest market news"
            items.append(
                NewsItem(
                    symbol=symbol,
                    title=title,
                    url=item.get("link") or "",
                    source=item.get("publisher") or "Yahoo Finance",
                    published_at=published_at,
                    summary=title,
                    sentiment_score=None,
                )
            )

        return items


class GoogleNewsRssProvider(NewsProvider):
    rss_url = "https://news.google.com/rss/search"

    def get_company_news(self, symbol: str) -> list[NewsItem]:
        params = urlencode({"q": f"{symbol} stock", "hl": "en-US", "gl": "US", "ceid": "US:en"})
        request = Request(
            f"{self.rss_url}?{params}",
            headers={"User-Agent": "stock-radar-ai/0.1"},
        )
        with urlopen(request, timeout=15) as response:
            content = response.read()

        root = ET.fromstring(content)
        items: list[NewsItem] = []
        for item in root.findall("./channel/item")[:3]:
            title = item.findtext("title") or f"{symbol} latest market news"
            link = item.findtext("link") or ""
            pub_date = item.findtext("pubDate")
            published_at = _parse_rss_date(pub_date)
            source = item.findtext("source") or "Google News"
            items.append(
                NewsItem(
                    symbol=symbol,
                    title=title,
                    url=link,
                    source=source,
                    published_at=published_at,
                    summary=title,
                    sentiment_score=None,
                )
            )

        return items


class FallbackNewsProvider(NewsProvider):
    def __init__(self, primary: NewsProvider, fallback: NewsProvider) -> None:
        self.primary = primary
        self.fallback = fallback

    def get_company_news(self, symbol: str) -> list[NewsItem]:
        try:
            items = self.primary.get_company_news(symbol)
            return items or self.fallback.get_company_news(symbol)
        except Exception:
            return self.fallback.get_company_news(symbol)


class EmptyNewsProvider(NewsProvider):
    """ใช้เมื่อแหล่งข่าวจริงล่ม — คืนรายการว่าง ห้ามสร้างข่าวแทน."""

    def get_company_news(self, symbol: str) -> list[NewsItem]:
        return []


def get_news_provider(use_live_data: bool = False) -> NewsProvider:
    if use_live_data:
        # เดิม fallback เป็น MockNewsProvider ซึ่งสร้างหัวข่าวปลอม + sentiment 0.65
        # แล้วไปดันคะแนน catalyst ทุกครั้งที่ Google News ล่ม — ห้ามใช้ข้อมูลปลอมใน live mode
        return FallbackNewsProvider(GoogleNewsRssProvider(), EmptyNewsProvider())
    return MockNewsProvider()


def _parse_rss_date(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        from email.utils import parsedate_to_datetime

        parsed = parsedate_to_datetime(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)
