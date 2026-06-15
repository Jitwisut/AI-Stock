from datetime import datetime, timedelta, timezone

from workers.data_models import NewsItem

CATALYST_KEYWORDS = {
    "ai",
    "upgrade",
    "contract",
    "earnings",
    "guidance",
    "approval",
    "delivery",
    "partnership",
    "demand",
}


def catalyst_score(news_items: list[NewsItem]) -> float:
    if not news_items:
        return 0.0

    now = datetime.now(timezone.utc)
    best = 0.0
    for item in news_items:
        age_bonus = 8.0 if item.published_at >= now - timedelta(hours=24) else 2.0
        keyword_hits = sum(1 for word in CATALYST_KEYWORDS if word in item.title.lower())
        sentiment_bonus = max(0.0, (item.sentiment_score or 0.0) * 6)
        best = max(best, min(20.0, age_bonus + keyword_hits * 3 + sentiment_bonus))
    return best
