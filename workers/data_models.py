from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class Stock:
    symbol: str
    name: str
    exchange: str = "NASDAQ"
    sector: str | None = None
    market_cap: float | None = None
    float_shares: float | None = None
    is_active: bool = True


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    previous_close: float
    latest_price: float
    premarket_price: float | None
    day_high: float
    prior_high: float
    volume: int
    average_volume_20d: int
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    data_source: str = "unknown"

    @property
    def premarket_gap_percent(self) -> float:
        price = self.premarket_price or self.latest_price
        if self.previous_close <= 0:
            return 0.0
        return ((price - self.previous_close) / self.previous_close) * 100

    @property
    def relative_volume(self) -> float:
        if self.average_volume_20d <= 0:
            return 0.0
        return self.volume / self.average_volume_20d


@dataclass(frozen=True)
class NewsItem:
    symbol: str
    title: str
    url: str
    source: str
    published_at: datetime
    summary: str | None = None
    sentiment_score: float | None = None


@dataclass(frozen=True)
class LongTermMetrics:
    """ตัวชี้วัดเชิงเทรนด์สำหรับการลงทุนระยะยาว (คำนวณจากราคา daily ~1 ปี)."""

    symbol: str
    price: float
    sma50: float
    sma200: float
    week52_high: float
    week52_low: float
    return_3m_percent: float
    return_6m_percent: float
    volatility_percent: float  # annualized volatility ของ daily returns
    history_days: int

    @property
    def above_sma200(self) -> bool:
        return self.sma200 > 0 and self.price > self.sma200

    @property
    def golden_alignment(self) -> bool:
        return self.sma200 > 0 and self.sma50 > self.sma200

    @property
    def pct_from_52w_high(self) -> float:
        if self.week52_high <= 0:
            return 0.0
        return (self.price - self.week52_high) / self.week52_high * 100

    @property
    def pct_above_52w_low(self) -> float:
        if self.week52_low <= 0:
            return 0.0
        return (self.price - self.week52_low) / self.week52_low * 100


@dataclass(frozen=True)
class SignalScore:
    symbol: str
    score: float
    momentum_score: float
    volume_score: float
    catalyst_score: float
    premarket_gap_score: float
    breakout_score: float
    risk_score: float
    gap_percent: float
    relative_volume: float
    latest_price: float
    catalyst: str
    reason: str
    risks: list[str]
    watch_plan: list[str]
    news: list[NewsItem]
    # Daily reference levels carried through from the snapshot so downstream
    # consumers (e.g. the ซิ่ง watchlist) can derive reference zones without
    # re-fetching. These are daily EOD values, not intraday ticks.
    previous_close: float = 0.0
    day_high: float = 0.0
    prior_high: float = 0.0
    market_cap: float | None = None
    provider_updated_at: datetime | None = None
    data_source: str = "unknown"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
