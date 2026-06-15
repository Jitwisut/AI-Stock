from workers.scanner.fetch_prices import MockMarketDataProvider
from workers.scanner.fetch_stocks import fetch_stock_universe
from workers.scoring import build_signal
from workers.news.fetch_news import MockNewsProvider


def test_scoring_returns_top_candidate_shape() -> None:
    stock = next(item for item in fetch_stock_universe() if item.symbol == "NVDA")
    snapshot = MockMarketDataProvider().get_snapshot("NVDA")
    news = MockNewsProvider().get_company_news("NVDA")

    signal = build_signal(stock, snapshot, news)

    assert signal.symbol == "NVDA"
    assert 0 <= signal.score <= 100
    assert signal.relative_volume > 1
    assert signal.gap_percent > 0
    assert signal.reason
