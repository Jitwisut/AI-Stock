from __future__ import annotations

from workers.data_models import SignalScore


def surge_score(signal: SignalScore | dict) -> float:
    score = _value(signal, "score")
    gap = _value(signal, "gap_percent", "premarket_gap_percent")
    relative_volume = _value(signal, "relative_volume")
    catalyst = _score_breakdown_value(signal, "catalyst")
    return score + max(0.0, gap) * 2 + relative_volume * 4 + catalyst


def recommendation_for(signal: SignalScore | dict) -> dict:
    score = _value(signal, "score")
    gap = _value(signal, "gap_percent", "premarket_gap_percent")
    relative_volume = _value(signal, "relative_volume")
    catalyst_score = _score_breakdown_value(signal, "catalyst")

    has_strong_move = gap >= 5
    has_volume = relative_volume >= 1.5
    has_catalyst = catalyst_score >= 8
    is_high_risk_mover = gap >= 50

    if is_high_risk_mover and score >= 55:
        return {
            "label": "วิ่งแรงมาก แต่เสี่ยงสูง",
            "short_label": "เฝ้าดูพิเศษ",
            "reason": "ราคาวิ่งแรงผิดปกติและมีวอลุ่มยืนยัน เหมาะกับ watchlist หุ้นซิ่ง แต่ไม่ควรไล่ราคาโดยไม่มี confirmation",
            "level": "strong",
        }

    if score >= 80 and has_strong_move and has_volume and has_catalyst:
        return {
            "label": "น่าเฝ้าดูมาก",
            "short_label": "เด่นมาก",
            "reason": "momentum, วอลุ่ม และข่าวหนุนสอดคล้องกัน เหมาะสำหรับเฝ้าดู confirmation",
            "level": "strong",
        }

    if score >= 60 or (has_strong_move and has_catalyst):
        return {
            "label": "น่าสนใจ แต่ต้องรอยืนยัน",
            "short_label": "รอยืนยัน",
            "reason": "มีแรงเคลื่อนไหวหรือ catalyst ชัด แต่ควรดูวอลุ่มและแนวรับแนวต้านเพิ่ม",
            "level": "watch",
        }

    return {
        "label": "เฝ้าดูแบบระมัดระวัง",
        "short_label": "ระวัง",
        "reason": "ยังมีบางเงื่อนไขไม่ครบ เช่น วอลุ่มไม่เด่นหรือคะแนนรวมยังไม่สูง",
        "level": "caution",
    }


def surge_grade(signal: SignalScore | dict) -> dict:
    """จัดเกรดหุ้น "ซิ่ง" (S/A/B/C) สำหรับสายถือไว-ออกไว

    เน้นแรงเคลื่อนไหวระยะสั้น: การเปลี่ยนแปลงราคา + relative volume + breakout
    ใช้ข้อมูล daily EOD จึงเป็นการ "จัดอันดับเพื่อเฝ้าดู" ไม่ใช่สัญญาณ intraday จริง
    """
    change = _value(signal, "gap_percent", "premarket_gap_percent")
    relative_volume = _value(signal, "relative_volume")
    breakout = _score_breakdown_value(signal, "breakout")

    is_up = change > 0
    has_volume = relative_volume >= 1.5
    explosive_volume = relative_volume >= 3
    has_breakout = breakout > 0
    high_risk_mover = is_up and change >= 50
    extreme_move = is_up and change >= 100

    # ใช้เกณฑ์ที่ตีความได้ (ขนาดการเปลี่ยนแปลง + relative volume + breakout) แทน
    # surge_score ดิบ เพราะหุ้นเพนนีบนข้อมูลจริงทำ surge_score พุ่งหลักพันได้
    # ทำให้ทุกตัวกลายเป็น S — แต่หุ้นที่วิ่ง 50%-100%+ พร้อมวอลุ่มควรถูกดันขึ้น
    # เป็น High Risk Mover เพื่อไม่พลาดตัวที่ตลาดกำลังเล่นจริง
    if extreme_move or high_risk_mover or (
        is_up and change >= 15 and relative_volume >= 5 and has_breakout
    ):
        grade, label, tone = "S", "วิ่งแรงมาก - เสี่ยงสูง", "fire"
    elif is_up and (change >= 20 or (change >= 7 and explosive_volume and (has_breakout or relative_volume >= 5))):
        grade, label, tone = "A", "ซิ่งเด่น", "strong"
    elif is_up and (change >= 10 or (change >= 3 and has_volume)):
        grade, label, tone = "B", "เริ่มมีจังหวะ", "watch"
    else:
        grade, label, tone = "C", "ยังไม่เร้าใจ", "caution"

    missing_s_conditions = _missing_s_conditions(
        change=change,
        relative_volume=relative_volume,
        has_breakout=has_breakout,
        high_risk_mover=high_risk_mover,
    )
    risk_note = _surge_risk_note(change, high_risk_mover=high_risk_mover)
    return {
        "grade": grade,
        "label": label,
        "tone": tone,
        "is_up": is_up,
        "explosive_volume": explosive_volume,
        "has_breakout": has_breakout,
        "high_risk_mover": high_risk_mover,
        "extreme_move": extreme_move,
        "missing_s_conditions": [] if grade == "S" else missing_s_conditions,
        "risk_note": risk_note,
    }


def surge_plan(signal: SignalScore | dict) -> dict:
    """โซนอ้างอิงแบบ "ถือไว-ออกไว" คำนวณจากข้อมูล daily เท่านั้น

    ระดับราคาเป็น "โซนอ้างอิง" จาก previous_close / day_high / prior_high
    ไม่ใช่ entry/exit แบบ intraday จริง ต้องรอ confirmation จากราคา-วอลุ่มจริง
    หากต้องการระดับ intraday แม่นยำต้องต่อ data source แบบ realtime (Polygon/Alpaca)
    """
    price = _value(signal, "latest_price")
    previous_close = _value(signal, "previous_close")
    day_high = _value(signal, "day_high")
    prior_high = _value(signal, "prior_high")
    change = _value(signal, "gap_percent", "premarket_gap_percent")
    data_source = _text_value(signal, "data_source").lower()
    data_basis = (
        "live quote + daily ref"
        if "yahoo_quote" in data_source or "polygon" in data_source
        else "daily"
    )
    basis_note = (
        "ราคาอ้างอิงจาก quote สด/extended-hours แต่โซนเทรดยังใช้ daily reference ต้องรอ tape จริงยืนยัน"
        if data_basis != "daily"
        else "โซนอ้างอิงจากข้อมูล daily — ไม่ใช่ระดับ intraday จริง ต้องรอ confirmation"
    )

    if price <= 0:
        return {
            "bias": "ไม่ชัดเจน",
            "data_basis": data_basis,
            "note": "ข้อมูลราคาไม่พอสำหรับประเมินโซนอ้างอิง",
            "entry_zone": None,
            "key_high": None,
            "stop": None,
            "stop_pct": None,
            "targets": [],
            "risk_reward": None,
            "entry_note": "รอข้อมูลราคาที่ครบก่อน",
            "style": "รอข้อมูลราคาที่ครบก่อน",
        }

    # volatility proxy จากช่วงราคาวันล่าสุด (clamp 3%–9%)
    day_range_pct = 0.0
    if previous_close > 0 and day_high > 0:
        day_range_pct = max(0.0, (day_high - previous_close) / previous_close * 100)
    stop_pct = min(9.0, max(3.0, day_range_pct * 0.5 or abs(change) * 0.5 or 4.0))

    is_up = change >= 0
    bias = "ฝั่งขึ้น (long)" if is_up else "อ่อนแรง (รอ reversal)"

    # โซนเข้าอ้างอิงคิดจากราคาล่าสุด (ตัวที่กำลังวิ่ง) ให้ stop อยู่ใต้, target อยู่บน
    stop_level = round(price * (1 - stop_pct / 100), 2)
    risk_per_share = max(0.01, price - stop_level)
    target1 = round(price + risk_per_share * 1.5, 2)
    target2 = round(price + risk_per_share * 3.0, 2)

    # ไฮอ้างอิง (วันนี้/ก่อนหน้า) ใช้เป็นแนวต้าน/จุดสังเกต breakout
    key_high = round(max(day_high, prior_high), 2) if max(day_high, prior_high) > 0 else None
    broke_out = key_high is not None and price >= key_high
    if not is_up:
        entry_note = "ราคายังไม่ขึ้น — รอ reversal และวอลุ่มยืนยันก่อน ไม่สวนแรง"
    elif broke_out:
        entry_note = "ราคาทะลุไฮอ้างอิงแล้ว — รอย่อแตะโซนแล้วค่อยตามแรง อย่าไล่สูงเกิน"
    else:
        entry_note = f"รอยืนเหนือ {key_high} พร้อมวอลุ่มเพื่อยืนยัน momentum"

    return {
        "bias": bias,
        "data_basis": data_basis,
        "note": basis_note,
        "entry_zone": round(price, 2),
        "key_high": key_high,
        "stop": stop_level,
        "stop_pct": round(stop_pct, 1),
        "targets": [
            {"label": "TP1 (1.5R)", "price": target1},
            {"label": "TP2 (3R)", "price": target2},
        ],
        "risk_reward": "1 : 1.5 / 1 : 3",
        "entry_note": entry_note,
        "style": "เข้าเร็ว-ออกเร็ว ตัดขาดทุนทันทีถ้าหลุดโซน stop ไม่ถือข้ามแรงเหวี่ยง",
    }


def _value(signal: SignalScore | dict, *keys: str) -> float:
    for key in keys:
        if isinstance(signal, dict):
            value = signal.get(key)
        else:
            value = getattr(signal, key, None)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _text_value(signal: SignalScore | dict, key: str) -> str:
    if isinstance(signal, dict):
        value = signal.get(key)
    else:
        value = getattr(signal, key, "")
    return value if isinstance(value, str) else ""


def _score_breakdown_value(signal: SignalScore | dict, key: str) -> float:
    if isinstance(signal, dict):
        breakdown = signal.get("score_breakdown") or {}
        value = breakdown.get(key)
        return float(value) if isinstance(value, (int, float)) else 0.0
    return float(getattr(signal, f"{key}_score", 0.0))


def _missing_s_conditions(
    *,
    change: float,
    relative_volume: float,
    has_breakout: bool,
    high_risk_mover: bool,
) -> list[str]:
    if high_risk_mover:
        return []

    missing: list[str] = []
    if change < 15:
        missing.append("ราคาเปลี่ยนแปลงยังไม่ถึง 15%")
    if relative_volume < 5:
        missing.append("relative volume ยังไม่ถึง 5x")
    if not has_breakout:
        missing.append("ยังไม่ทะลุไฮอ้างอิง")
    return missing


def _surge_risk_note(change: float, *, high_risk_mover: bool) -> str:
    if change >= 100:
        return "ราคาวิ่งเกิน 100% เป็นโซนเสี่ยงสูงมาก อาจย่อแรงหรือโดน halt ต้องรอ confirmation"
    if high_risk_mover:
        return "ราคาวิ่งเกิน 50% เป็น High Risk Mover ควรเฝ้าดูมากกว่าไล่ราคา"
    if change >= 15:
        return "ราคาวิ่งแรงกว่าปกติ ต้องระวังแรงขายหลังเปิดตลาด"
    return ""
