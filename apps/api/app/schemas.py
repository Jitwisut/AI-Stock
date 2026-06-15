from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field


class ScoreBreakdownOut(BaseModel):
    momentum: float
    volume: float
    catalyst: float
    premarket_gap: float
    breakout: float
    risk: float


class NewsOut(BaseModel):
    symbol: str
    title: str
    url: str
    source: str
    published_at: str
    summary: Optional[str] = None
    sentiment_score: Optional[float] = None


class RecommendationOut(BaseModel):
    label: str
    short_label: str
    reason: str
    level: str


class SurgeGradeOut(BaseModel):
    grade: str
    label: str
    tone: str
    is_up: bool = False
    explosive_volume: bool = False
    has_breakout: bool = False
    high_risk_mover: bool = False
    extreme_move: bool = False
    missing_s_conditions: List[str] = []
    risk_note: str = ""


class SurgeTargetOut(BaseModel):
    label: str
    price: float


class SurgePlanOut(BaseModel):
    bias: str
    data_basis: str = "daily"
    note: str
    entry_zone: Optional[float] = None
    key_high: Optional[float] = None
    stop: Optional[float] = None
    stop_pct: Optional[float] = None
    targets: List[SurgeTargetOut] = []
    risk_reward: Optional[str] = None
    entry_note: Optional[str] = None
    style: str


class SignalOut(BaseModel):
    id: Optional[int] = None
    symbol: str
    score: float = Field(ge=0, le=100)
    premarket_gap_percent: float
    relative_volume: float
    latest_price: float
    previous_close: float = 0
    day_high: float = 0
    prior_high: float = 0
    market_cap: Optional[float] = None
    provider_updated_at: Optional[str] = None
    data_source: str = "unknown"
    catalyst: str
    reason: str
    risks: List[str]
    watch_plan: List[str]
    score_breakdown: ScoreBreakdownOut
    score_breakdown_max: Optional[dict] = None
    surge_score: float = 0
    surge_grade: Optional[SurgeGradeOut] = None
    surge_plan: Optional[SurgePlanOut] = None
    recommendation: Optional[RecommendationOut] = None
    news: List[NewsOut] = []
    created_at: Optional[str] = None


class ScanRunOut(BaseModel):
    scan_run_id: Optional[int] = None
    status: str = "completed"
    signals: List[SignalOut]
    summary: str
    telegram_sent: bool
    telegram_status: str
    saved_signal_count: int
    scanned_symbol_count: int = 0
    universe_symbol_count: int = 0
    failed_symbol_count: int = 0
    candidate_symbol_count: int = 0


class ScanRunStatusOut(BaseModel):
    id: int
    kind: str
    status: str
    provider: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    universe_count: int = 0
    scanned_count: int = 0
    failed_count: int = 0
    error: Optional[str] = None


class LongTermOut(BaseModel):
    symbol: str
    name: str = ""
    sector: Optional[str] = None
    price: float
    score: float = Field(ge=0, le=100)
    grade: str
    label: str
    tone: str
    breakdown: dict = {}
    reasons: List[str] = []
    risks: List[str] = []
    sma50: float = 0
    sma200: float = 0
    above_sma200: bool = False
    week52_high: float = 0
    week52_low: float = 0
    pct_from_52w_high: float = 0
    return_3m_percent: float = 0
    return_6m_percent: float = 0
    volatility_percent: float = 0
    history_days: int = 0


class StockOut(BaseModel):
    symbol: str
    name: str
    exchange: str
    sector: Optional[str] = None
    market_cap: Optional[float] = None
    float_shares: Optional[float] = None
    is_active: bool


class StockAnalysisOut(BaseModel):
    query: str
    analysis_source: str
    stock: StockOut
    signal: SignalOut
    analysis: str
    disclaimer: str


class SettingsStatusOut(BaseModel):
    app_env: str
    market_data_provider: str = "yahoo"
    ai_provider: str = "gemini"
    use_live_data: bool
    allow_mock_data: bool = True
    save_signals: bool
    send_telegram: bool
    telegram_configured: bool
    openai_configured: bool
    gemini_configured: bool = False
    ai_configured: bool = False
    finnhub_configured: bool
    polygon_configured: bool
    admin_api_key_configured: bool = False
    database_configured: bool
    database_reachable: bool
    redis_configured: bool = False
    redis_reachable: bool = False
    top_signal_limit: int
    universe_mode: str = "default"
    scan_universe_limit: int = 0
    scan_workers: int = 1
    ai_candidate_limit: int = 50


class ErrorOut(BaseModel):
    detail: Any
