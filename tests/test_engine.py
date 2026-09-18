"""Engine tests: indicators ถูกสูตร, ไม่มี look-ahead, คะแนน deterministic, AI validator กันตัวเลขปลอม."""
from __future__ import annotations

from datetime import date, timedelta

from workers.engine import analysis as an
from workers.engine import indicators as ind
from workers.engine.ai_explain import validate_output
from workers.engine.bars import Bar, parse_nasdaq_rows
from workers.engine.market_context import classify_market
from workers.news.fetch_news import get_news_provider


def _bars(n: int = 300, drift: float = 0.001) -> list[Bar]:
    out, price, day = [], 100.0, date(2025, 1, 2)
    for i in range(n):
        price *= 1 + drift + (0.01 if i % 7 == 0 else -0.004)
        out.append(Bar(day, price * 0.995, price * 1.01, price * 0.99, price, 1_000_000 + (i % 5) * 100_000))
        day += timedelta(days=1)
    return out


def test_rsi_matches_wilder_reference() -> None:
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00]
    values = ind.rsi(closes, 14)
    assert values[13] is None
    assert abs(values[14] - 70.46) < 0.1


def test_sma_ema_basics() -> None:
    assert ind.sma([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]
    assert ind.ema([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]


def test_snapshot_has_no_look_ahead() -> None:
    """ค่า ณ วัน t ต้องเท่าเดิมไม่ว่าจะมีแท่งอนาคตต่อท้ายหรือไม่."""
    bars = _bars(300)
    full = an.TechnicalSeries.build(bars).snapshot_at(250)
    truncated = an.TechnicalSeries.build(bars[:251]).snapshot_at(250)
    assert full == truncated


def test_score_is_deterministic_and_bounded() -> None:
    t = an.TechnicalSeries.build(_bars()).snapshot_at(299)
    comps = {
        "trend": an.score_trend(t), "momentum": an.score_momentum(t), "volume": an.score_volume(t),
        "fundamental": an.score_fundamental(None), "risk": {"score": an.assess_risk(t)["score"], "max": 10.0},
        "catalyst": an.score_catalyst(t, None, None, an.detect_signals(t)),
    }
    first = an.weighted_score(comps, an.COMPONENT_MAX)
    second = an.weighted_score(comps, an.COMPONENT_MAX)
    assert first == second
    assert 0 <= first["total"] <= 100
    # ไม่มีข้อมูลพื้นฐาน → ส่วน fundamental ต้องได้ 0 ไม่ใช่คะแนนฟรี
    assert first["breakdown"]["fundamental"]["points"] == 0


def test_every_signal_has_evidence() -> None:
    t = an.TechnicalSeries.build(_bars()).snapshot_at(299)
    for signal in an.detect_signals(t):
        assert signal["evidence"], signal["id"]


def test_parse_rows_dedupes_and_flags_splits() -> None:
    rows = [
        {"date": "01/03/2025", "close": "$100", "open": "$99", "high": "$101", "low": "$98", "volume": "1,000"},
        {"date": "01/03/2025", "close": "$100", "open": "$99", "high": "$101", "low": "$98", "volume": "1,000"},
        {"date": "01/06/2025", "close": "$50", "open": "$50", "high": "$51", "low": "$49", "volume": "2,000"},
        {"date": "bad", "close": "$1"},
    ]
    bars, quality = parse_nasdaq_rows(rows)
    assert len(bars) == 2
    assert quality["duplicates_removed"] == 1
    assert quality["invalid_rows"] == 1
    assert quality["possible_unadjusted_splits"][0]["looks_like"] == "2:1"


def test_market_condition_requires_evidence() -> None:
    spy = {"close": 100, "sma50": 95, "sma200": 90, "last_bar_date": "2026-09-17", "above_sma50": True, "above_sma200": True}
    condition, evidence = classify_market(spy, None, {"close": 30, "avg_20d": 25, "date": "2026-09-17"})
    assert condition == "Risk-Off"
    assert any("VIX" in line for line in evidence)
    assert classify_market(None, None, None)[0] == "Unknown"


def test_ai_validator_removes_fabricated_numbers_and_news() -> None:
    ai_input = {"technical": {"rsi14": 63.2}, "news": [{"title": "Real headline", "source": "X", "published_at": "2026-09-18", "age_hours": 3}], "signals": [{"id": "breakout_20d"}]}
    output = {
        "summary": "RSI อยู่ที่ 63.2",
        "bull_case": ["RSI 63.2 ยังไม่ overbought", "ราคาเป้าหมาย 500 ดอลลาร์", "กำไรโต 87.5%"],
        "news_assessment": [
            {"title": "Real headline", "classification": "positive", "interpretation": "ok"},
            {"title": "Invented headline", "classification": "positive", "interpretation": "x"},
        ],
        "signal_explanations": [{"signal_id": "made_up", "explanation": "x"}],
    }
    clean, validation = validate_output(output, ai_input)
    assert clean["summary"] == "RSI อยู่ที่ 63.2"
    assert clean["bull_case"] == ["RSI 63.2 ยังไม่ overbought"]
    assert [n["fact"]["title"] for n in clean["news_assessment"]] == ["Real headline"]
    assert clean["signal_explanations"] == []
    assert validation["removed_count"] == 4


def test_live_news_never_falls_back_to_mock(monkeypatch) -> None:
    provider = get_news_provider(use_live_data=True)

    def boom(symbol):
        raise RuntimeError("rss down")

    monkeypatch.setattr(provider.primary, "get_company_news", boom)
    assert provider.get_company_news("AAPL") == []
