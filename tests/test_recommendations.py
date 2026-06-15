from __future__ import annotations

from workers.data_models import SignalScore
from workers.recommendations import recommendation_for, surge_grade


def _signal(
    *,
    gap_percent: float,
    relative_volume: float,
    breakout_score: float = 0,
    score: float = 70,
) -> SignalScore:
    return SignalScore(
        symbol="RUNR",
        score=score,
        momentum_score=18,
        volume_score=20,
        catalyst_score=0,
        premarket_gap_score=6,
        breakout_score=breakout_score,
        risk_score=6,
        gap_percent=gap_percent,
        relative_volume=relative_volume,
        latest_price=12.0,
        catalyst="ยังไม่พบข่าว catalyst ชัดเจน",
        reason="ราคาและวอลุ่มวิ่งแรง",
        risks=[],
        watch_plan=[],
        news=[],
    )


def test_extreme_mover_gets_s_grade_with_high_risk_warning() -> None:
    grade = surge_grade(_signal(gap_percent=120, relative_volume=1.8))

    assert grade["grade"] == "S"
    assert grade["high_risk_mover"] is True
    assert grade["extreme_move"] is True
    assert "100%" in grade["risk_note"]


def test_fifty_percent_mover_gets_s_grade_even_without_relative_volume() -> None:
    grade = surge_grade(_signal(gap_percent=81, relative_volume=1.0))

    assert grade["grade"] == "S"
    assert grade["high_risk_mover"] is True
    assert grade["missing_s_conditions"] == []


def test_non_s_mover_explains_missing_conditions() -> None:
    grade = surge_grade(_signal(gap_percent=8, relative_volume=2.0))

    assert grade["grade"] == "B"
    assert grade["missing_s_conditions"]
    assert any("15%" in item for item in grade["missing_s_conditions"])


def test_high_risk_mover_is_watchlisted_even_without_news_catalyst() -> None:
    recommendation = recommendation_for(_signal(gap_percent=80, relative_volume=2.2, score=62))

    assert recommendation["level"] == "strong"
    assert recommendation["short_label"] == "เฝ้าดูพิเศษ"
