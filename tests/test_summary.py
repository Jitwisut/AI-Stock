from workers.ai.prompt_templates import FORBIDDEN_WORDS
from workers.ai.summarize_signal import render_fallback_summary
from workers.news.fetch_news import MockNewsProvider
from workers.scanner.fetch_prices import MockMarketDataProvider
from workers.scanner.fetch_stocks import DEFAULT_UNIVERSE
from workers.scoring import build_signal


def test_fallback_summary_avoids_forbidden_words() -> None:
    market_data = MockMarketDataProvider()
    news = MockNewsProvider()
    signals = [
        build_signal(stock, market_data.get_snapshot(stock.symbol), news.get_company_news(stock.symbol))
        for stock in DEFAULT_UNIVERSE[:5]
    ]
    summary = render_fallback_summary(signals)

    for word in FORBIDDEN_WORDS:
        assert word not in summary
