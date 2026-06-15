from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from apps.api.app.main import app
from workers.config import get_settings
from workers.ai.analyze_stock import analyze_stock_with_ai
from workers.services import _select_surge_signals
from workers.scanner.fetch_prices import (
    FallbackMarketDataProvider,
    LiveFallbackMarketDataProvider,
    MockMarketDataProvider,
    PolygonMarketDataProvider,
    _nasdaq_screener_row_to_market_snapshot,
    _polygon_snapshot_to_market_snapshot,
    get_market_data_provider,
)
from workers.scanner import fetch_stocks
from workers.scanner.fetch_stocks import fetch_surge_stock_universe
from workers.data_models import SignalScore, Stock


def _reset_settings(monkeypatch, **env: str) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def _sample_signal() -> SignalScore:
    return SignalScore(
        symbol="NVDA",
        score=82,
        momentum_score=18,
        volume_score=17,
        catalyst_score=15,
        premarket_gap_score=12,
        breakout_score=14,
        risk_score=6,
        gap_percent=3.4,
        relative_volume=2.1,
        latest_price=205.1,
        catalyst="Earnings momentum",
        reason="ราคาและวอลุ่มแข็งแรง",
        risks=["ตลาดผันผวน"],
        watch_plan=["รอให้ยืนเหนือ high เดิม"],
        news=[],
        previous_close=198.3,
        day_high=206.0,
        prior_high=203.5,
        provider_updated_at=datetime.now(timezone.utc),
        data_source="yahoo_chart",
    )


def _surge_signal(symbol: str, gap_percent: float) -> SignalScore:
    return SignalScore(
        symbol=symbol,
        score=70,
        momentum_score=18,
        volume_score=20,
        catalyst_score=0,
        premarket_gap_score=6,
        breakout_score=0,
        risk_score=6,
        gap_percent=gap_percent,
        relative_volume=2.0,
        latest_price=10,
        catalyst="ยังไม่พบข่าว catalyst ชัดเจน",
        reason="ราคาและวอลุ่มวิ่งแรง",
        risks=[],
        watch_plan=[],
        news=[],
    )


def test_polygon_snapshot_mapping() -> None:
    snapshot = _polygon_snapshot_to_market_snapshot(
        {
            "ticker": "NVDA",
            "updated": 1_700_000_000_000_000_000,
            "day": {"h": 151.2, "c": 150.5, "v": 2_000_000},
            "prevDay": {"h": 149.0, "c": 145.0, "v": 1_000_000},
            "lastTrade": {"p": 150.75},
        }
    )

    assert snapshot.symbol == "NVDA"
    assert snapshot.latest_price == 150.75
    assert snapshot.previous_close == 145.0
    assert snapshot.relative_volume == 2.0
    assert snapshot.premarket_gap_percent > 0


def test_production_market_provider_never_uses_mock_fallback(monkeypatch) -> None:
    _reset_settings(
        monkeypatch,
        APP_ENV="production",
        USE_LIVE_DATA="true",
        ALLOW_MOCK_DATA="true",
        MARKET_DATA_PROVIDER="polygon",
        POLYGON_API_KEY="test-key",
    )

    provider = get_market_data_provider()

    assert isinstance(provider, LiveFallbackMarketDataProvider)
    assert any(isinstance(item, PolygonMarketDataProvider) for item in provider.providers)
    assert not isinstance(provider, FallbackMarketDataProvider)
    assert not any(isinstance(item, MockMarketDataProvider) for item in provider.providers)


def test_default_dev_market_provider_does_not_use_mock(monkeypatch) -> None:
    _reset_settings(
        monkeypatch,
        APP_ENV="development",
        USE_LIVE_DATA="true",
        MARKET_DATA_PROVIDER="yahoo",
    )
    monkeypatch.delenv("ALLOW_MOCK_DATA", raising=False)
    get_settings.cache_clear()

    provider = get_market_data_provider()

    assert isinstance(provider, LiveFallbackMarketDataProvider)
    assert not any(isinstance(item, MockMarketDataProvider) for item in provider.providers)


def test_mock_provider_requires_explicit_offline_flags(monkeypatch) -> None:
    _reset_settings(
        monkeypatch,
        APP_ENV="development",
        USE_LIVE_DATA="false",
        ALLOW_MOCK_DATA="true",
    )

    provider = get_market_data_provider()

    assert isinstance(provider, MockMarketDataProvider)


def test_scan_run_requires_admin_api_key(monkeypatch) -> None:
    _reset_settings(monkeypatch, APP_ENV="development", ADMIN_API_KEY="secret")

    response = TestClient(app).post("/scan/run")

    assert response.status_code == 403


def test_scan_run_enqueues_with_admin_key(monkeypatch) -> None:
    _reset_settings(monkeypatch, APP_ENV="development", ADMIN_API_KEY="secret")

    def fake_enqueue_scan_job(kind: str = "momentum", send_telegram: bool = False) -> dict:
        return {
            "id": 123,
            "kind": kind,
            "status": "queued",
            "provider": "polygon",
            "started_at": None,
            "finished_at": None,
            "universe_count": 0,
            "scanned_count": 0,
            "failed_count": 0,
            "error": None,
        }

    import workers.tasks

    monkeypatch.setattr(workers.tasks, "enqueue_scan_job", fake_enqueue_scan_job)

    response = TestClient(app).post("/scan/run", headers={"X-Admin-API-Key": "secret"})

    assert response.status_code == 200
    assert response.json()["id"] == 123
    assert response.json()["status"] == "queued"


def test_health_ready_fails_when_provider_is_not_configured(monkeypatch) -> None:
    _reset_settings(
        monkeypatch,
        APP_ENV="production",
        USE_LIVE_DATA="true",
        ALLOW_MOCK_DATA="false",
        MARKET_DATA_PROVIDER="polygon",
        POLYGON_API_KEY="",
    )

    response = TestClient(app).get("/health/ready")

    assert response.status_code == 503
    assert response.json()["detail"]["checks"]["provider"] is False


def test_stock_analysis_returns_503_when_live_price_is_unavailable(monkeypatch) -> None:
    _reset_settings(
        monkeypatch,
        APP_ENV="development",
        USE_LIVE_DATA="true",
        ALLOW_MOCK_DATA="false",
        MARKET_DATA_PROVIDER="yahoo",
    )

    import workers.services

    class BrokenProvider:
        name = "broken_live"

        def get_snapshot(self, symbol: str):
            raise RuntimeError(f"no live data for {symbol}")

    monkeypatch.setattr(workers.services, "get_market_data_provider", lambda use_live_data=None: BrokenProvider())
    monkeypatch.setattr(
        workers.services,
        "_resolve_stock",
        lambda query: Stock(symbol=query.upper(), name=query.upper(), exchange="US"),
    )

    response = TestClient(app).get("/analysis/stock?query=NVDA")

    assert response.status_code == 503
    assert "no live data" in response.json()["detail"]


def test_stock_analysis_uses_gemini_when_configured(monkeypatch) -> None:
    async def fake_generate_gemini_text(*args, **kwargs) -> str:
        return "gemini analysis"

    import workers.ai.analyze_stock

    monkeypatch.setattr(
        workers.ai.analyze_stock,
        "generate_gemini_text",
        fake_generate_gemini_text,
    )

    text, source = asyncio.run(
        analyze_stock_with_ai(
            Stock(symbol="NVDA", name="NVIDIA", exchange="NASDAQ"),
            _sample_signal(),
            [],
            ai_provider="gemini",
            gemini_api_key="test-key",
            gemini_model="gemini-2.5-flash",
        )
    )

    assert text == "gemini analysis"
    assert source == "gemini"


def test_surge_universe_includes_microcap_movers() -> None:
    symbols = {stock.symbol for stock in fetch_surge_stock_universe(["TESTX"], mode="curated")}

    assert {"MTEN", "CUPR", "HUBC", "CHAI", "AZI", "VSME", "DSY", "PPCB", "SUNE", "TESTX"}.issubset(
        symbols
    )
    assert "NVDA" not in symbols
    assert "AAPL" not in symbols


def test_surge_universe_all_mode_includes_full_market(monkeypatch) -> None:
    monkeypatch.setattr(
        fetch_stocks,
        "fetch_full_us_stock_universe",
        lambda: [
            Stock("NVDA", "NVIDIA Corporation", exchange="NASDAQ"),
            Stock("ZZZ", "Tiny Runner Inc.", exchange="NASDAQ"),
        ],
    )

    symbols = {stock.symbol for stock in fetch_surge_stock_universe(["TESTX"], mode="all")}

    assert {"MTEN", "TESTX", "NVDA", "ZZZ"}.issubset(symbols)


def test_full_us_universe_keeps_partial_exchange_data(monkeypatch) -> None:
    def broken_other_listed() -> list[Stock]:
        raise RuntimeError("temporary outage")

    fetch_stocks.fetch_full_us_stock_universe.cache_clear()
    monkeypatch.setattr(
        fetch_stocks,
        "_fetch_nasdaq_listed",
        lambda: [Stock("AAA", "AAA Corp.", exchange="NASDAQ")],
    )
    monkeypatch.setattr(fetch_stocks, "_fetch_other_listed", broken_other_listed)

    try:
        stocks = fetch_stocks.fetch_full_us_stock_universe()
    finally:
        fetch_stocks.fetch_full_us_stock_universe.cache_clear()

    assert [stock.symbol for stock in stocks] == ["AAA"]


def test_nasdaq_screener_mapping_uses_percent_change() -> None:
    snapshot = _nasdaq_screener_row_to_market_snapshot(
        {
            "symbol": "VSME",
            "lastsale": "$3.06",
            "pctchange": "81.12%",
            "volume": "2,500,000",
        }
    )

    assert snapshot.symbol == "VSME"
    assert snapshot.latest_price == 3.06
    assert round(snapshot.premarket_gap_percent, 2) == 81.12
    assert snapshot.data_source == "nasdaq_screener"


def test_surge_pool_scans_dedicated_surge_universe(monkeypatch) -> None:
    _reset_settings(
        monkeypatch,
        APP_ENV="development",
        USE_LIVE_DATA="true",
        ALLOW_MOCK_DATA="false",
        MARKET_DATA_PROVIDER="nasdaq",
        SURGE_EXTRA_SYMBOLS="TESTX",
        SURGE_UNIVERSE_MODE="curated",
        SURGE_UNIVERSE_LIMIT="0",
        SURGE_CANDIDATE_LIMIT="25",
        SURGE_MIN_CHANGE_PERCENT="50",
        SURGE_BACKFILL_MIN_CHANGE_PERCENT="10",
        SURGE_MIN_RESULTS="10",
    )

    import workers.services

    captured: dict = {}

    def fake_scan_market(
        limit: int = 5,
        stocks: list | None = None,
        sort_mode: str = "score",
        include_news: bool = True,
    ) -> dict:
        captured["limit"] = limit
        captured["sort_mode"] = sort_mode
        captured["include_news"] = include_news
        captured["symbols"] = {stock.symbol for stock in stocks or []}
        return {"signals": []}

    workers.services._SURGE_CACHE["data"] = None
    monkeypatch.setattr(workers.services, "scan_market", fake_scan_market)

    workers.services._serialized_surge_pool(force=True)

    assert captured["sort_mode"] == "surge"
    assert captured["include_news"] is False
    assert captured["limit"] == 25
    assert {"MTEN", "CUPR", "HUBC", "TESTX"}.issubset(captured["symbols"])
    assert "NVDA" not in captured["symbols"]


def test_surge_selection_backfills_when_few_true_50_percent_movers() -> None:
    selected = _select_surge_signals(
        [
            _surge_signal("HOT", 60),
            _surge_signal("WARM", 25),
            _surge_signal("MOVE", 12),
            _surge_signal("FLAT", 4),
        ],
        primary_min_change=50,
        backfill_min_change=10,
        min_results=3,
    )

    assert [signal.symbol for signal in selected] == ["HOT", "WARM", "MOVE"]
