"""วัดผล "หุ้นน่าสนใจวันนี้" จากของจริง — คะแนน AI ช่วยเลือกหุ้นได้ดีกว่ากฎไหม.

ทุกรอบที่สร้าง picks จะบันทึกหุ้นทุกตัวที่ผ่านด่านแรก (ไม่ใช่แค่ตัวที่แสดง) พร้อม
คะแนนกฎและคะแนน AI ลง pick_snapshots แล้ว evaluate_picks จะดูผลตอบแทนจริง
1/3/5 วันทำการถัดไป เทียบ:
- AI top N  vs  กฎ top N   (เลือกจาก shortlist ชุดเดียวกัน → วัดว่าการจัดอันดับของ AI ดีกว่าไหม)
- ค่าเฉลี่ยทั้ง shortlist / ทั้งชุดที่ผ่านด่านแรก / SPY

ราคาเข้าและราคาออกใช้ราคาปิดจากแท่งรายวันเท่านั้น (ไม่ใช้ราคา quote ระหว่างวัน)
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

from workers.config import get_settings
from workers.pick_scoring import parse_bar_date

logger = logging.getLogger(__name__)

HORIZONS = (1, 3, 5)
_EVAL_CACHE: dict = {"ts": 0.0, "key": None, "data": None}
_EVAL_TTL_SECONDS = 30 * 60.0


# ── บันทึก ───────────────────────────────────────────────────────────────────


def snapshot_rows(collected: list[dict], picks: list[dict], ai_info: dict, *, market_open: bool) -> list[dict]:
    """แปลงผลหนึ่งรอบเป็นแถวของ pick_snapshots (แยกออกมาเพื่อทดสอบได้โดยไม่ต้องมี DB)."""
    final_rank = {pick["symbol"]: index for index, pick in enumerate(picks, 1)}
    by_symbol = {pick["symbol"]: pick for pick in picks}

    rows = []
    for candidate in collected:
        symbol = candidate["stock"].symbol
        session = parse_bar_date(candidate["features"].get("as_of"))
        if session is None:
            continue
        pick = by_symbol.get(symbol)
        ai_score = pick.get("ai_score") if pick else None
        rows.append(
            {
                "session_date": session,
                "symbol": symbol,
                "market_open": market_open,
                # ต่อแถว ไม่ใช่ต่อรอบ: รอบที่ AI ให้คะแนนไม่ครบ ตัวที่ขาดต้องถูกเติมได้ในรอบถัดไป
                "quality": (0 if market_open else 2) + (1 if ai_score is not None else 0),
                "prescreen_rank": candidate.get("prescreen_rank"),
                "in_shortlist": pick is not None,
                "rule_score": candidate["rule_score"],
                "ai_score": ai_score,
                "final_score": pick.get("score") if pick else None,
                "final_rank": final_rank.get(symbol),
                "ai_model": ai_info.get("model") if pick and pick.get("ai_score") is not None else None,
                "score_breakdown": json.dumps(pick.get("score_breakdown"), ensure_ascii=False)
                if pick and pick.get("score_breakdown")
                else None,
                "ai_notes": json.dumps(
                    {key: pick.get(key) for key in ("reason", "catalyst", "risk")}, ensure_ascii=False
                )
                if pick and pick.get("ai_score") is not None
                else None,
            }
        )
    return rows


def save_pick_snapshots(collected: list[dict], picks: list[dict], ai_info: dict) -> int:
    """บันทึกแบบ best-effort — DB ล่มต้องไม่ทำให้หน้า picks พัง.

    ไม่ผูกกับ SAVE_SIGNALS (production ตั้ง false) ไม่งั้นจะไม่มีข้อมูลให้วัดผลเลย
    """
    from workers.engine.market_calendar import market_session

    settings = get_settings()
    if not settings.database_url:
        return 0
    market_open = market_session()["status"] == "open"
    rows = snapshot_rows(collected, picks, ai_info, market_open=market_open)
    if not rows:
        return 0
    try:
        from sqlalchemy import text

        from workers.repository import _engine

        with _engine(settings.database_url).begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO pick_snapshots (
                        session_date, symbol, market_open, quality, prescreen_rank, in_shortlist,
                        rule_score, ai_score, final_score, final_rank, ai_model, score_breakdown, ai_notes
                    ) VALUES (
                        :session_date, :symbol, :market_open, :quality, :prescreen_rank, :in_shortlist,
                        :rule_score, :ai_score, :final_score, :final_rank, :ai_model,
                        CAST(:score_breakdown AS JSONB), CAST(:ai_notes AS JSONB)
                    )
                    ON CONFLICT (session_date, symbol) DO UPDATE SET
                        generated_at = NOW(),
                        market_open = EXCLUDED.market_open,
                        quality = EXCLUDED.quality,
                        prescreen_rank = EXCLUDED.prescreen_rank,
                        in_shortlist = EXCLUDED.in_shortlist,
                        rule_score = EXCLUDED.rule_score,
                        ai_score = EXCLUDED.ai_score,
                        final_score = EXCLUDED.final_score,
                        final_rank = EXCLUDED.final_rank,
                        ai_model = EXCLUDED.ai_model,
                        score_breakdown = EXCLUDED.score_breakdown,
                        ai_notes = EXCLUDED.ai_notes
                    WHERE EXCLUDED.quality > pick_snapshots.quality
                    """
                ),
                rows,
            )
        return len(rows)
    except Exception as exc:
        logger.warning("pick snapshots not saved: %s", exc)
        return 0


def load_session_ai_scores(session: date, symbols: list[str], *, market_open: bool) -> dict[str, dict]:
    """คะแนน AI ที่เคยให้ไว้แล้วสำหรับวันทำการนี้ — ใช้ซ้ำแทนการเรียก AI ใหม่.

    ฟีเจอร์คิดจากแท่ง EOD ซึ่งไม่เปลี่ยนจนกว่าตลาดปิดรอบถัดไป การให้คะแนนซ้ำทุก 45 นาที
    จึงเปลืองโควตาเปล่า ๆ (free tier ได้แค่ 20 ครั้ง/วัน/รุ่น) · ผลที่ได้ตอนตลาดเปิด
    จะไม่ถูกใช้ซ้ำหลังตลาดปิด (ให้คะแนนใหม่บนข้อมูลที่ปิดจริงแล้ว)
    """
    settings = get_settings()
    if not settings.database_url or not symbols:
        return {}
    try:
        from sqlalchemy import bindparam, text

        from workers.repository import _engine

        query = text(
            "SELECT symbol, ai_score, ai_model, score_breakdown, ai_notes FROM pick_snapshots "
            "WHERE session_date = :session AND symbol IN :symbols AND ai_score IS NOT NULL "
            "AND score_breakdown IS NOT NULL AND ai_notes IS NOT NULL "
            "AND (NOT market_open OR :market_open)"
        ).bindparams(bindparam("symbols", expanding=True))
        with _engine(settings.database_url).connect() as connection:
            rows = connection.execute(
                query, {"session": session, "symbols": list(symbols), "market_open": market_open}
            ).mappings().all()
    except Exception as exc:
        logger.warning("pick snapshots: cannot load cached AI scores (%s)", exc)
        return {}

    cached = {}
    for row in rows:
        breakdown = row["score_breakdown"]
        notes = row["ai_notes"]
        if isinstance(breakdown, str):
            breakdown = json.loads(breakdown)
        if isinstance(notes, str):
            notes = json.loads(notes)
        cached[row["symbol"]] = {
            "breakdown": breakdown,
            "ai_score": float(row["ai_score"]),
            "reason": notes.get("reason") or "",
            "catalyst": notes.get("catalyst") or "",
            "risk": notes.get("risk") or "",
            "model": row["ai_model"],
        }
    return cached


# ── วัดผล ────────────────────────────────────────────────────────────────────


def forward_return(bars: list[tuple[date, float]], session: date, horizon: int) -> float | None:
    """ผลตอบแทน (%) จากราคาปิดวัน session ถึงราคาปิดอีก horizon วันทำการ.

    นับตามแท่งที่มีจริง วันหยุดตลาดจึงไม่ถูกนับเป็นวันทำการ · ยังไม่ครบกำหนด → None
    """
    index = next((i for i, (day, _) in enumerate(bars) if day == session), None)
    if index is None or index + horizon >= len(bars):
        return None
    entry, exit_ = bars[index][1], bars[index + horizon][1]
    return round((exit_ / entry - 1) * 100, 3) if entry > 0 else None


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 5 or len(xs) != len(ys):
        return None

    def _ranks(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        ranks = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            for k in range(i, j + 1):
                ranks[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return ranks

    rx, ry = _ranks(xs), _ranks(ys)
    mean_x, mean_y = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mean_x) * (b - mean_y) for a, b in zip(rx, ry))
    var_x = sum((a - mean_x) ** 2 for a in rx)
    var_y = sum((b - mean_y) ** 2 for b in ry)
    if var_x == 0 or var_y == 0:
        return None
    return round(cov / (var_x * var_y) ** 0.5, 3)


def _mean(values: list[float]) -> float | None:
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 3) if values else None


def evaluate_rows(rows: list[dict], bars_by_symbol: dict[str, list[tuple[date, float]]], *, top_n: int = 5) -> dict:
    """คำนวณผลจากแถว snapshot + แท่งราคา (แยกจาก I/O เพื่อทดสอบได้)."""
    sessions: dict[date, list[dict]] = {}
    for row in rows:
        if row.get("market_open"):
            continue
        sessions.setdefault(row["session_date"], []).append(row)

    spy = bars_by_symbol.get("SPY", [])
    result: dict = {"top_n": top_n, "sessions_recorded": len(sessions), "horizons": {}}
    for horizon in HORIZONS:
        groups: dict[str, list[float]] = {"ai_top": [], "rule_top": [], "shortlist": [], "prescreen": [], "spy": []}
        beat = {"ai_top": 0, "rule_top": 0}
        ai_pairs: list[tuple[float, float]] = []
        rule_pairs: list[tuple[float, float]] = []
        matured = 0
        for session, session_rows in sorted(sessions.items()):
            spy_ret = forward_return(spy, session, horizon)
            returns = {
                row["symbol"]: forward_return(bars_by_symbol.get(row["symbol"], []), session, horizon)
                for row in session_rows
            }
            shortlist = [
                row for row in session_rows
                if row.get("in_shortlist") and row.get("ai_score") is not None and returns.get(row["symbol"]) is not None
            ]
            if spy_ret is None or len(shortlist) < top_n:
                continue
            matured += 1
            ai_top = sorted(shortlist, key=lambda r: float(r["ai_score"]), reverse=True)[:top_n]
            rule_top = sorted(shortlist, key=lambda r: float(r["rule_score"]), reverse=True)[:top_n]
            session_means = {
                "ai_top": _mean([returns[r["symbol"]] for r in ai_top]),
                "rule_top": _mean([returns[r["symbol"]] for r in rule_top]),
                "shortlist": _mean([returns[r["symbol"]] for r in shortlist]),
                "prescreen": _mean([returns.get(r["symbol"]) for r in session_rows]),
                "spy": spy_ret,
            }
            for name, value in session_means.items():
                if value is not None:
                    groups[name].append(value)
            for name in beat:
                if session_means[name] is not None and session_means[name] > spy_ret:
                    beat[name] += 1
            for row in shortlist:
                ai_pairs.append((float(row["ai_score"]), returns[row["symbol"]]))
                rule_pairs.append((float(row["rule_score"]), returns[row["symbol"]]))

        result["horizons"][f"{horizon}d"] = {
            "sessions": matured,
            "avg_return_percent": {name: _mean(values) for name, values in groups.items()},
            "beat_spy_rate": {name: round(count / matured, 2) if matured else None for name, count in beat.items()},
            "spearman_ai_score": spearman([a for a, _ in ai_pairs], [b for _, b in ai_pairs]),
            "spearman_rule_score": spearman([a for a, _ in rule_pairs], [b for _, b in rule_pairs]),
            "observations": len(ai_pairs),
        }
    return result


def _load_rows() -> list[dict]:
    from sqlalchemy import text

    from workers.repository import _engine

    settings = get_settings()
    with _engine(settings.database_url).connect() as connection:
        rows = connection.execute(
            text(
                "SELECT session_date, symbol, market_open, in_shortlist, rule_score, ai_score "
                "FROM pick_snapshots WHERE session_date >= CURRENT_DATE - 180 ORDER BY session_date"
            )
        ).mappings().all()
    return [dict(row) for row in rows]


def _closes_by_date(symbol: str) -> list[tuple[date, float]]:
    from workers.scanner.fetch_history import get_history_provider

    settings = get_settings()
    try:
        bars = get_history_provider(use_live_data=settings.use_live_data).get_bars(symbol, days=200)
    except Exception:
        return []
    series = []
    for bar in bars:
        day = parse_bar_date(bar.get("date"))
        if day and bar.get("close"):
            series.append((day, float(bar["close"])))
    return series


def evaluate_picks(top_n: int = 5) -> dict:
    """ผลวัดจริงจาก pick_snapshots — แคช 30 นาทีเพราะต้องดึงแท่งราคาหลายสิบตัว."""
    now = time.time()
    if _EVAL_CACHE["data"] is not None and _EVAL_CACHE["key"] == top_n and now - _EVAL_CACHE["ts"] < _EVAL_TTL_SECONDS:
        return _EVAL_CACHE["data"]

    try:
        rows = _load_rows()
    except Exception as exc:
        logger.warning("pick evaluation: cannot read snapshots (%s)", exc)
        return {"status": "unavailable", "reason": "ฐานข้อมูลไม่พร้อม"}

    symbols = sorted({row["symbol"] for row in rows if not row.get("market_open")} | {"SPY"})
    with ThreadPoolExecutor(max_workers=8) as executor:
        bars_by_symbol = dict(zip(symbols, executor.map(_closes_by_date, symbols)))

    data = {
        "status": "ok" if rows else "empty",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **evaluate_rows(rows, bars_by_symbol, top_n=top_n),
        "note": (
            "ผลตอบแทนเฉลี่ยต่อรอบ (%), ราคาปิดถึงราคาปิด ไม่หักค่าธรรมเนียม · ai_top/rule_top เลือกจาก "
            "shortlist ชุดเดียวกัน · ต้องมีหลายสิบรอบขึ้นไปถึงจะเริ่มเชื่อถือได้"
        ),
    }
    _EVAL_CACHE.update({"ts": now, "key": top_n, "data": data})
    return data
