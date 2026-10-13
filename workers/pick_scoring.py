"""ให้คะแนน "หุ้นน่าสนใจวันนี้" แบบมีเกณฑ์ (rubric) — ใช้ร่วมกันทั้งระบบจริงและ replay.

ของเดิมให้ Gemini เดา "โอกาสวิ่งแรง 0-100%" ลอย ๆ: ตัวเลขไม่ได้มาจากสถิติ
รันสองรอบได้ไม่เท่ากัน และวัดผลย้อนหลังไม่ได้ ตอนนี้ AI ให้คะแนนแต่ละเกณฑ์ 0-5
พร้อมหลักฐาน (หรือ null ถ้าข้อมูลไม่พอ) แล้วโค้ดเป็นคนรวมเป็นคะแนนเอง

ฟีเจอร์ทุกตัวคำนวณจากแท่งราคารายวัน (EOD) ล้วน ๆ ผ่าน features_from_bars เพื่อให้
replay ณ วันในอดีตใช้สูตรเดียวกับของจริงได้โดยไม่มีข้อมูลอนาคตปนเข้ามา
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from statistics import pstdev

from workers.indicators.rsi import rsi

logger = logging.getLogger(__name__)

# เกณฑ์และน้ำหนัก (รวม 100) — risk_control: 5 = ความเสี่ยงต่ำสุด
CRITERIA: dict[str, int] = {
    "trend_structure": 25,
    "relative_strength": 20,
    "volume_confirmation": 20,
    "catalyst_quality": 15,
    "risk_control": 20,
}
# ต้องมีคะแนนอย่างน้อยกี่เกณฑ์ถึงจะเชื่อคะแนนรวมของ AI — ไม่งั้นเกณฑ์เดียวได้ 5
# จะกลายเป็น 100 คะแนนเต็ม
MIN_CRITERIA_SCORED = 3

# ทุกเกณฑ์ได้ 3/5 ("ปกติ/ผสม") = 60 คะแนน ต้องตกที่ "เฝ้าดู" ไม่ใช่ "น่าสนใจ"
VERDICT_THRESHOLDS = ((80, "น่าสนใจมาก"), (70, "น่าสนใจ"), (55, "เฝ้าดู"))
VERDICT_DEFAULT = "ยังไม่น่าเข้า"

_TRADING_DAYS_YEAR = 252


# ── ฟีเจอร์จากแท่งราคา ────────────────────────────────────────────────────────


def parse_bar_date(raw) -> date | None:
    if not raw:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(raw), fmt).date()
        except ValueError:
            continue
    return None


def _ret(closes: list[float], days: int) -> float | None:
    if len(closes) <= days or closes[-(days + 1)] <= 0:
        return None
    return round((closes[-1] / closes[-(days + 1)] - 1) * 100, 2)


def _sma(closes: list[float], days: int) -> float | None:
    if len(closes) < days:
        return None
    return sum(closes[-days:]) / days


def features_from_bars(bars: list[dict], spy_bars: list[dict] | None = None) -> dict | None:
    """ฟีเจอร์ "หน้าตากราฟ" ณ แท่งสุดท้ายของ bars (เรียงเก่า→ใหม่).

    ค่าไหนคำนวณไม่ได้ให้เป็น None — ห้ามเติมค่าสมมติ (เช่น relative volume 1.0x)
    เพราะ AI จะเอาไปให้คะแนนเหมือนเป็นข้อมูลจริง
    """
    bars = [bar for bar in bars if bar.get("close") and bar["close"] > 0]
    if len(bars) < 30:
        return None
    closes = [float(bar["close"]) for bar in bars]
    volumes = [bar.get("volume") for bar in bars]
    price = closes[-1]
    previous = closes[-2]

    prior_volumes = [v for v in volumes[-21:-1] if v]
    relative_volume = (
        round(volumes[-1] / (sum(prior_volumes) / len(prior_volumes)), 2)
        if volumes[-1] and len(prior_volumes) >= 10
        else None
    )
    prior_high = max(closes[-21:-1])
    window = closes[-_TRADING_DAYS_YEAR:]
    high_52w, low_52w = max(window), min(window)
    span = high_52w - low_52w
    sma20, sma50, sma200 = _sma(closes, 20), _sma(closes, 50), _sma(closes, 200)
    daily_returns = [closes[i] / closes[i - 1] - 1 for i in range(max(1, len(closes) - 60), len(closes))]
    volatility = pstdev(daily_returns) * (_TRADING_DAYS_YEAR ** 0.5) * 100 if len(daily_returns) > 1 else None

    return_1m = _ret(closes, 21)
    return_3m = _ret(closes, 63)
    spy_closes = [float(bar["close"]) for bar in (spy_bars or []) if bar.get("close")]
    spy_1m = _ret(spy_closes, 21) if spy_closes else None
    spy_3m = _ret(spy_closes, 63) if spy_closes else None

    return {
        "price": round(price, 2),
        "change_percent": round((price / previous - 1) * 100, 2) if previous > 0 else 0.0,
        "change_5d_percent": _ret(closes, 5),
        "relative_volume": relative_volume,
        "rsi14": round(rsi(closes), 1) if len(closes) > 15 else None,
        "pct_from_sma20": round((price / sma20 - 1) * 100, 2) if sma20 else None,
        "above_sma50": price > sma50 if sma50 else None,
        "above_sma200": price > sma200 if sma200 else None,
        "sma50": round(sma50, 2) if sma50 else None,
        "sma200": round(sma200, 2) if sma200 else None,
        "position_in_52w_range_percent": round((price - low_52w) / span * 100, 1) if span > 0 else None,
        "pct_from_52w_high": round((price / high_52w - 1) * 100, 2) if high_52w > 0 else None,
        "breakout_gap_percent": round((price / prior_high - 1) * 100, 2) if prior_high > 0 else None,
        "return_1m_percent": return_1m,
        "return_3m_percent": return_3m,
        "return_6m_percent": _ret(closes, 126),
        "rs_1m_vs_spy": round(return_1m - spy_1m, 2) if return_1m is not None and spy_1m is not None else None,
        "rs_3m_vs_spy": round(return_3m - spy_3m, 2) if return_3m is not None and spy_3m is not None else None,
        "volatility_percent": round(volatility, 1) if volatility is not None else None,
        "as_of": str(bars[-1].get("date")),
        "closes_20d": [round(c, 2) for c in closes[-20:]],
    }


def market_context_from_bars(spy_bars: list[dict]) -> dict:
    closes = [float(bar["close"]) for bar in spy_bars if bar.get("close")]
    if len(closes) < 30:
        return {}
    sma50, sma200 = _sma(closes, 50), _sma(closes, 200)
    return {
        "spy_price": round(closes[-1], 2),
        "spy_change_percent": round((closes[-1] / closes[-2] - 1) * 100, 2),
        "spy_return_1m_percent": _ret(closes, 21),
        "spy_above_sma50": closes[-1] > sma50 if sma50 else None,
        "spy_above_sma200": closes[-1] > sma200 if sma200 else None,
    }


def rule_score(features: dict) -> float:
    """คะแนนจากกฎล้วน ๆ — ใช้คัดตัวก่อนส่ง AI และเป็นคะแนนสำรองเมื่อ AI ใช้ไม่ได้."""
    score = 0.0
    rel_vol = features.get("relative_volume")
    if isinstance(rel_vol, (int, float)):
        score += min(30.0, max(0.0, rel_vol - 1) * 20)          # วอลุ่มผิดปกติ
    score += min(20.0, max(0.0, features.get("change_percent") or 0.0) * 3)  # โมเมนตัมวันล่าสุด
    if features.get("above_sma50"):
        score += 10
    if features.get("above_sma200"):
        score += 10
    breakout = features.get("breakout_gap_percent")
    if breakout is not None and breakout > -2:                   # จ่อ/ผ่านแนวต้านเดิม
        score += 15
    position = features.get("position_in_52w_range_percent")
    if position is not None and position > 70:
        score += 10
    rsi_value = features.get("rsi14")
    if rsi_value is not None:
        if 55 <= rsi_value <= 72:
            score += 10                                           # แข็งแต่ยังไม่ overbought จัด
        elif rsi_value > 80:
            score -= 10
    volatility = features.get("volatility_percent")
    if volatility is not None and volatility > 90:
        score -= 5                                                # ผันผวนเกินไป = เสี่ยงมากกว่าโอกาส
    return round(max(0.0, min(100.0, score)), 1)


def verdict_for_score(score: float) -> str:
    for threshold, label in VERDICT_THRESHOLDS:
        if score >= threshold:
            return label
    return VERDICT_DEFAULT


# ── prompt + schema ─────────────────────────────────────────────────────────


_CRITERION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "score": {"type": "INTEGER", "nullable": True, "minimum": 0, "maximum": 5},
        "evidence": {"type": "STRING"},
    },
    "required": ["score", "evidence"],
}

RUBRIC_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "symbol": {"type": "STRING"},
            **{name: _CRITERION_SCHEMA for name in CRITERIA},
            "reason": {"type": "STRING"},
            "catalyst": {"type": "STRING"},
            "risk": {"type": "STRING"},
        },
        "required": ["symbol", *CRITERIA, "reason", "catalyst", "risk"],
    },
}

_RUBRIC_TEXT = """เกณฑ์ (ให้คะแนนจำนวนเต็ม 0-5 ต่อเกณฑ์ หรือ null ถ้าข้อมูลที่ต้องใช้เป็น null/ไม่มี):
- trend_structure: โครงสร้างแนวโน้ม — ราคาเทียบ SMA50/SMA200, pct_from_sma20, position_in_52w_range_percent
  5 = เหนือ SMA50 และ SMA200 ชัดเจน อยู่ส่วนบนของกรอบ 52 สัปดาห์ · 3 = ผสม · 0 = ใต้ทั้งสองเส้น ขาลงชัด
- relative_strength: ความแข็งเทียบตลาด — rs_1m_vs_spy, rs_3m_vs_spy (บวก = ชนะ SPY)
  5 = ชนะ SPY ชัดทั้ง 1M และ 3M · 3 = ใกล้เคียงตลาด · 0 = แพ้ตลาดชัดทั้งสองช่วง
- volume_confirmation: วอลุ่มยืนยันการเคลื่อนไหว — relative_volume ประกอบกับทิศทางราคาวันล่าสุด
  5 = วอลุ่มสูงกว่าค่าเฉลี่ยมาก (≥2x) ในวันที่ราคาขึ้น · 3 = ปกติ · 0 = วอลุ่มสูงในวันที่ราคาลงแรง
- catalyst_quality: ตัวกระตุ้นเฉพาะบริษัท — ข่าวที่ age_hours < 72 และเป็นเรื่องของบริษัทนี้จริง
  5 = ข่าวใหม่ที่มีนัยสำคัญต่อบริษัทโดยตรง · 2 = มีแต่ข่าวทั่วไป/ข่าวเก่า · 0 = ข่าวลบชัด · null = ไม่มีข่าวให้ดู
- risk_control: ความเสี่ยงต่ำแค่ไหน (5 = เสี่ยงต่ำที่สุด, 0 = เสี่ยงสูงที่สุด)
  ดู rsi14 (>80 = ร้อนเกิน), pct_from_sma20 (ห่างมาก = ยืด), volatility_percent, days_to_earnings (≤5 วัน = เสี่ยงงบ)

กฎ:
1. ใช้เฉพาะตัวเลขใน INPUT ห้ามแต่งตัวเลขหรือข่าวเพิ่ม evidence ต้องอ้างชื่อ field และค่าจริง
2. ค่าที่เป็น null คือไม่มีข้อมูล ห้ามเดา — ถ้าเกณฑ์ไหนข้อมูลไม่พอ ให้ score เป็น null
3. ให้คะแนนแต่ละตัวตามเกณฑ์เดียวกันทุกตัว ไม่ต้องเรียงลำดับ ไม่ต้องคิดคะแนนรวม
4. reason = สรุปภาษาไทย 1-2 ประโยคจากหลักฐาน · catalyst = ตัวกระตุ้นหลักภาษาไทย
   (ไม่มีให้ใส่ "ไม่พบ catalyst ชัดเจน") · risk = ความเสี่ยงหลัก 1 ข้อภาษาไทย
5. ตอบครบทุก symbol ใน INPUT"""


def build_rubric_prompt(candidates: list[dict], market_context: dict) -> str:
    """candidates: [{"symbol", "features", "news": [{"title","source","age_hours"}], "days_to_earnings"}]."""
    payload = []
    for candidate in candidates:
        features = {k: v for k, v in candidate["features"].items() if k not in ("closes_20d", "as_of")}
        payload.append(
            {
                "symbol": candidate["symbol"],
                **({"name": candidate["name"]} if candidate.get("name") else {}),
                "features": features,
                "closes_20d": candidate["features"].get("closes_20d"),
                "days_to_earnings": candidate.get("days_to_earnings"),
                "news": candidate.get("news"),
            }
        )
    return (
        "คุณเป็นนักวิเคราะห์เทคนิคหุ้นสหรัฐ ให้คะแนนหุ้นแต่ละตัวตามเกณฑ์ด้านล่าง "
        "สำหรับมุมมองระยะสั้น 1-5 วันทำการ ข้อมูลทั้งหมดเป็นราคาปิดรายวัน (EOD)\n\n"
        f"{_RUBRIC_TEXT}\n\n"
        f"ภาวะตลาด (SPY): {json.dumps(market_context, ensure_ascii=False)}\n\n"
        f"INPUT:\n{json.dumps(payload, ensure_ascii=False, default=str)}"
    )


# ── แปลงคำตอบ ────────────────────────────────────────────────────────────────


def _clamp_score(raw) -> int | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = int(round(float(raw)))
    except (TypeError, ValueError):
        return None
    return max(0, min(5, value))


def combine_rubric(breakdown: dict) -> float | None:
    """รวมคะแนนเกณฑ์ (0-5) เป็น 0-100 ด้วยน้ำหนักใน CRITERIA — เกณฑ์ที่ null ไม่นับ.

    ถ้ามีคะแนนน้อยกว่า MIN_CRITERIA_SCORED เกณฑ์ คืน None (ใช้คะแนนกฎแทน)
    """
    scored = {
        name: item["score"]
        for name, item in breakdown.items()
        if name in CRITERIA and isinstance(item, dict) and item.get("score") is not None
    }
    if len(scored) < MIN_CRITERIA_SCORED:
        return None
    weight = sum(CRITERIA[name] for name in scored)
    return round(sum(CRITERIA[name] * value for name, value in scored.items()) / weight * 20, 1)


def parse_rubric_response(text: str, symbols: list[str]) -> dict[str, dict]:
    """คืน {symbol: {"breakdown", "ai_score", "reason", "catalyst", "risk"}} เฉพาะ symbol ที่ขอ.

    symbol ที่ AI ไม่ได้ตอบ/ตอบไม่ครบเกณฑ์จะไม่อยู่ในผลลัพธ์ (ผู้เรียกใช้คะแนนกฎแทน)
    """
    cleaned = (text or "").strip()
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start < 0 or end <= start:
        raise ValueError("rubric response is not a JSON array")
    items = json.loads(cleaned[start : end + 1])

    wanted = {symbol.upper() for symbol in symbols}
    results: dict[str, dict] = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", "")).upper().strip()
        if symbol not in wanted or symbol in results:
            continue
        breakdown = {}
        for name in CRITERIA:
            raw = item.get(name) if isinstance(item.get(name), dict) else {}
            breakdown[name] = {
                "score": _clamp_score(raw.get("score")),
                "evidence": str(raw.get("evidence") or "")[:300],
            }
        results[symbol] = {
            "breakdown": breakdown,
            "ai_score": combine_rubric(breakdown),
            "reason": str(item.get("reason") or "").strip(),
            "catalyst": str(item.get("catalyst") or "").strip(),
            "risk": str(item.get("risk") or "").strip(),
        }

    missing = wanted - set(results)
    if missing:
        logger.warning("rubric: AI did not score %s", sorted(missing))
    return results
