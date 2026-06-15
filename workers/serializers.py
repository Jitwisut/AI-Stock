from __future__ import annotations

from workers.data_models import SignalScore, Stock
from workers.recommendations import (
    recommendation_for,
    surge_grade,
    surge_plan,
    surge_score,
)

# คะแนนเต็มของแต่ละองค์ประกอบ (ใช้ให้ frontend วาด progress bar ได้ถูกสัดส่วน)
SCORE_BREAKDOWN_MAX = {
    "momentum": 25,
    "volume": 25,
    "catalyst": 20,
    "premarket_gap": 10,
    "breakout": 10,
    "risk": 10,
}


def stock_to_dict(stock: Stock) -> dict:
    return {
        "symbol": stock.symbol,
        "name": stock.name,
        "exchange": stock.exchange,
        "sector": stock.sector,
        "market_cap": stock.market_cap,
        "float_shares": stock.float_shares,
        "is_active": stock.is_active,
    }


def signal_to_dict(signal: SignalScore, signal_id: int | None = None) -> dict:
    data = {
        "symbol": signal.symbol,
        "score": signal.score,
        "premarket_gap_percent": signal.gap_percent,
        "relative_volume": signal.relative_volume,
        "latest_price": signal.latest_price,
        "previous_close": signal.previous_close,
        "day_high": signal.day_high,
        "prior_high": signal.prior_high,
        "market_cap": signal.market_cap,
        "provider_updated_at": (
            signal.provider_updated_at.isoformat() if signal.provider_updated_at else None
        ),
        "data_source": signal.data_source,
        "catalyst": signal.catalyst,
        "reason": signal.reason,
        "risks": signal.risks,
        "watch_plan": signal.watch_plan,
        "score_breakdown": {
            "momentum": signal.momentum_score,
            "volume": signal.volume_score,
            "catalyst": signal.catalyst_score,
            "premarket_gap": signal.premarket_gap_score,
            "breakout": signal.breakout_score,
            "risk": signal.risk_score,
        },
        "score_breakdown_max": SCORE_BREAKDOWN_MAX,
        "surge_score": round(surge_score(signal), 2),
        "surge_grade": surge_grade(signal),
        "surge_plan": surge_plan(signal),
        "recommendation": recommendation_for(signal),
        "news": [
            {
                "symbol": item.symbol,
                "title": item.title,
                "url": item.url,
                "source": item.source,
                "published_at": item.published_at.isoformat(),
                "summary": item.summary,
                "sentiment_score": item.sentiment_score,
            }
            for item in signal.news
        ],
        "created_at": signal.created_at.isoformat(),
    }
    if signal_id is not None:
        data["id"] = signal_id
    return data
