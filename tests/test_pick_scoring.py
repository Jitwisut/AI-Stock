import json
from datetime import date, timedelta

import pytest

from workers import pick_scoring as ps
from workers import pick_tracking as pt


def _bars(closes, volumes=None, start=date(2026, 1, 2)):
    bars, day = [], start
    for index, close in enumerate(closes):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        bars.append(
            {"date": day.strftime("%m/%d/%Y"), "close": close, "volume": (volumes or [1_000_000] * len(closes))[index]}
        )
        day += timedelta(days=1)
    return bars


# ── combine / parse ─────────────────────────────────────────────────────────


def _item(score):
    return {"score": score, "evidence": "x"}


def test_combine_weights_and_scale():
    full = {name: _item(5) for name in ps.CRITERIA}
    assert ps.combine_rubric(full) == 100.0
    assert ps.combine_rubric({name: _item(0) for name in ps.CRITERIA}) == 0.0


def test_combine_ignores_nulls_but_requires_coverage():
    only_one = {name: _item(None) for name in ps.CRITERIA}
    only_one["trend_structure"] = _item(5)
    # เกณฑ์เดียวได้ 5 ต้องไม่กลายเป็น 100 คะแนน
    assert ps.combine_rubric(only_one) is None

    three = {name: _item(None) for name in ps.CRITERIA}
    three.update(trend_structure=_item(5), relative_strength=_item(0), risk_control=_item(5))
    # (25*5 + 20*0 + 20*5) / 65 * 20
    assert ps.combine_rubric(three) == pytest.approx((25 * 5 + 20 * 5) / 65 * 20, abs=0.1)


def test_parse_clamps_drops_extras_and_reports_missing():
    payload = [
        {"symbol": "aaa", **{n: {"score": 9, "evidence": "e"} for n in ps.CRITERIA}, "reason": "r", "catalyst": "c", "risk": "k"},
        {"symbol": "ZZZ", **{n: {"score": 3, "evidence": "e"} for n in ps.CRITERIA}, "reason": "", "catalyst": "", "risk": ""},
        {"symbol": "BBB", "trend_structure": {"score": -2, "evidence": "e"}, "reason": "r", "catalyst": "c", "risk": "k"},
    ]
    result = ps.parse_rubric_response(json.dumps(payload), ["AAA", "BBB", "CCC"])
    assert set(result) == {"AAA", "BBB"}                      # ZZZ ไม่ได้ขอ, CCC ไม่ได้ตอบ
    assert result["AAA"]["breakdown"]["trend_structure"]["score"] == 5   # 9 → clamp 5
    assert result["AAA"]["ai_score"] == 100.0
    assert result["BBB"]["breakdown"]["trend_structure"]["score"] == 0   # -2 → clamp 0
    assert result["BBB"]["ai_score"] is None                  # มีแค่เกณฑ์เดียว


def test_parse_rejects_non_json():
    with pytest.raises(ValueError):
        ps.parse_rubric_response("sorry, I cannot", ["AAA"])


def test_verdict_thresholds():
    assert ps.verdict_for_score(85) == "น่าสนใจมาก"
    assert ps.verdict_for_score(72) == "น่าสนใจ"
    # ทุกเกณฑ์ 3/5 = 60 → "เฝ้าดู"
    assert ps.combine_rubric({name: _item(3) for name in ps.CRITERIA}) == 60.0
    assert ps.verdict_for_score(60) == "เฝ้าดู"
    assert ps.verdict_for_score(40) == "ยังไม่น่าเข้า"


# ── features ────────────────────────────────────────────────────────────────


def test_features_from_bars_uses_real_volume_and_spy():
    closes = [100 + i * 0.5 for i in range(260)]
    volumes = [1_000_000] * 259 + [3_000_000]
    spy = _bars([400 + i * 0.1 for i in range(260)])
    features = ps.features_from_bars(_bars(closes, volumes), spy)
    assert features["relative_volume"] == 3.0
    assert features["above_sma50"] and features["above_sma200"]
    assert features["rs_1m_vs_spy"] is not None and features["rs_1m_vs_spy"] > 0
    assert features["as_of"] == _bars(closes)[-1]["date"]


def test_features_missing_volume_is_none_not_fake():
    closes = [100 + i for i in range(60)]
    features = ps.features_from_bars(_bars(closes, [None] * 60))
    assert features["relative_volume"] is None
    assert features["rs_1m_vs_spy"] is None
    # rule_score ต้องรับ None ได้
    assert 0 <= ps.rule_score(features) <= 100


def test_features_need_enough_history():
    assert ps.features_from_bars(_bars([10.0] * 10)) is None


# ── tracking / evaluation ───────────────────────────────────────────────────


def test_forward_return_counts_trading_bars_not_calendar_days():
    series = [(date(2026, 7, 2), 100.0), (date(2026, 7, 6), 110.0), (date(2026, 7, 7), 121.0)]  # 3 ก.ค. หยุด
    assert pt.forward_return(series, date(2026, 7, 2), 1) == 10.0
    assert pt.forward_return(series, date(2026, 7, 2), 2) == 21.0
    assert pt.forward_return(series, date(2026, 7, 2), 3) is None    # ยังไม่ครบกำหนด
    assert pt.forward_return(series, date(2026, 7, 3), 1) is None    # ไม่มีแท่งวันนั้น


def test_spearman():
    assert pt.spearman([1, 2, 3, 4, 5], [10, 20, 30, 40, 50]) == 1.0
    assert pt.spearman([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]) == -1.0
    assert pt.spearman([1, 2], [1, 2]) is None


def test_evaluate_rows_compares_ai_and_rule_on_same_shortlist():
    session = date(2026, 9, 1)
    days = [session + timedelta(days=i) for i in range(8)]
    # AI ชอบ A,B (ขึ้น) · กฎชอบ C,D (ลง)
    moves = {"A": 1.10, "B": 1.08, "C": 0.95, "D": 0.97, "E": 1.0, "SPY": 1.01}
    bars = {sym: [(d, 100.0 * (move if i > 0 else 1.0)) for i, d in enumerate(days)] for sym, move in moves.items()}
    rows = [
        {"session_date": session, "symbol": "A", "in_shortlist": True, "ai_score": 90, "rule_score": 10},
        {"session_date": session, "symbol": "B", "in_shortlist": True, "ai_score": 80, "rule_score": 20},
        {"session_date": session, "symbol": "C", "in_shortlist": True, "ai_score": 20, "rule_score": 90},
        {"session_date": session, "symbol": "D", "in_shortlist": True, "ai_score": 10, "rule_score": 80},
        {"session_date": session, "symbol": "E", "in_shortlist": False, "ai_score": None, "rule_score": 5},
        # สร้างตอนตลาดเปิด → ต้องไม่ถูกนับ
        {"session_date": session + timedelta(days=1), "symbol": "A", "market_open": True, "in_shortlist": True, "ai_score": 1, "rule_score": 1},
    ]
    result = pt.evaluate_rows(rows, bars, top_n=2)
    one_day = result["horizons"]["1d"]
    assert result["sessions_recorded"] == 1
    assert one_day["sessions"] == 1
    assert one_day["avg_return_percent"]["ai_top"] == pytest.approx(9.0)
    assert one_day["avg_return_percent"]["rule_top"] == pytest.approx(-4.0)
    assert one_day["beat_spy_rate"] == {"ai_top": 1.0, "rule_top": 0.0}


def test_snapshot_rows_quality_and_shortlist_flags():
    class _Stock:
        def __init__(self, symbol):
            self.symbol = symbol

    collected = [
        {"stock": _Stock("A"), "features": {"as_of": "10/12/2026"}, "rule_score": 50, "prescreen_rank": 1},
        {"stock": _Stock("B"), "features": {"as_of": "10/12/2026"}, "rule_score": 40, "prescreen_rank": 2},
    ]
    picks = [{"symbol": "A", "score": 70, "ai_score": 70, "score_breakdown": {"trend_structure": {"score": 4}}}]
    rows = pt.snapshot_rows(collected, picks, {"status": "ok", "model": "m"}, market_open=False)
    assert rows[0]["session_date"] == date(2026, 10, 12)
    assert rows[0]["in_shortlist"] and rows[0]["final_rank"] == 1 and rows[0]["ai_model"] == "m"
    assert rows[0]["quality"] == 3
    assert rows[1]["quality"] == 2          # ไม่มีคะแนน AI → คุณภาพต่ำกว่า เติมทีหลังได้
    assert not rows[1]["in_shortlist"] and rows[1]["ai_score"] is None and rows[1]["ai_model"] is None
    open_rows = pt.snapshot_rows(collected, picks, {"status": "partial"}, market_open=True)
    assert open_rows[0]["quality"] == 1 and open_rows[1]["quality"] == 0


def test_ai_rubric_reuses_session_scores_and_only_asks_missing(monkeypatch):
    """รอบแรกได้ไม่ครบ → รอบสองถามเฉพาะตัวที่ขาด → รอบสามไม่เรียก AI เลย."""
    import asyncio
    import re

    from workers import daily_picks as dp
    from workers.ai import gemini_client as gc

    class _Stock:
        def __init__(self, symbol):
            self.symbol, self.name = symbol, symbol

    candidates = [
        {"stock": _Stock(sym), "features": {"as_of": "10/12/2026", "closes_20d": []}, "rule_score": 50}
        for sym in ("AAA", "BBB", "CCC")
    ]
    store: dict = {}
    asked: list[list[str]] = []
    answers = iter([["AAA", "BBB"], ["CCC"]])  # รอบแรก AI ตอบแค่ 2 ตัว

    async def fake_generate(prompt, **kwargs):
        symbols = re.findall(r'"symbol": "([A-Z]+)"', prompt)
        asked.append(symbols)
        reply = next(answers)
        body = [
            {"symbol": s, **{c: {"score": 4, "evidence": "e"} for c in ps.CRITERIA}, "reason": "r", "catalyst": "c", "risk": "k"}
            for s in reply
        ]
        return gc.GeminiResult(text=json.dumps(body), model="stub")

    def fake_load(session, symbols, *, market_open):
        return {s: store[s] for s in symbols if s in store}

    monkeypatch.setattr(gc, "generate_gemini", fake_generate)
    monkeypatch.setattr(pt, "load_session_ai_scores", fake_load)
    monkeypatch.setattr(dp, "_AI_ASKED", set())
    monkeypatch.setattr("workers.engine.market_calendar.market_session", lambda: {"status": "closed"})
    settings = dp.get_settings()
    monkeypatch.setattr(dp, "get_settings", lambda: type("S", (), {**settings.__dict__, "ai_provider": "gemini", "gemini_api_key": "k"})())

    def _run():
        scored, info = asyncio.run(dp._ai_rubric(candidates, {}))
        store.update({s: {**v, "model": "stub"} for s, v in scored.items()})  # = save_pick_snapshots
        return info

    assert _run()["status"] == "partial"
    assert _run()["status"] == "ok"
    third = _run()
    assert third["status"] == "ok" and third["reused"] == 3
    assert asked == [["AAA", "BBB", "CCC"], ["CCC"]]   # รอบสามไม่เรียก AI
