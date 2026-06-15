from __future__ import annotations

from workers.data_models import LongTermMetrics, Stock


def longterm_score(metrics: LongTermMetrics, market_cap: float | None = None) -> dict:
    """ให้คะแนนความน่าสนใจสำหรับ "ลงทุนระยะยาว" จากเทรนด์ราคา (0-100).

    เน้นคุณภาพเทรนด์ที่ยั่งยืน ไม่ใช่การวิ่งระยะสั้น:
      - เทรนด์ขาขึ้น (ราคาเหนือ SMA200, SMA50 > SMA200)
      - โมเมนตัม 6 เดือนเป็นบวกแบบมีเสถียรภาพ (ไม่ร้อนแรงเกิน)
      - ราคายังอยู่ใกล้จุดสูงรอบปี (เทรนด์ยังไม่เสีย)
      - ความผันผวนต่ำ และขนาดบริษัทใหญ่ = มั่นคงกว่า
    เป็นการคัดกรองเชิงเทรนด์จากข้อมูลราคา ไม่ใช่การวิเคราะห์ปัจจัยพื้นฐาน
    """
    reasons: list[str] = []
    risks: list[str] = []

    # --- เทรนด์ (35) ---
    trend = 0.0
    if metrics.above_sma200:
        trend += 20
        reasons.append("ราคายืนเหนือเส้นค่าเฉลี่ย 200 วัน (เทรนด์ขาขึ้นระยะยาว)")
    else:
        risks.append("ราคายังต่ำกว่า SMA200 — เทรนด์ระยะยาวยังไม่เป็นขาขึ้น")
    if metrics.golden_alignment:
        trend += 15
        reasons.append("เส้นค่าเฉลี่ย 50 วันอยู่เหนือ 200 วัน (โครงสร้างขาขึ้น)")

    # --- โมเมนตัม 6 เดือน (20) ---
    ret6 = metrics.return_6m_percent
    if 5 <= ret6 <= 60:
        momentum = 20.0
        reasons.append(f"ผลตอบแทน 6 เดือน +{ret6:.0f}% เติบโตสม่ำเสมอ")
    elif 0 <= ret6 < 5:
        momentum = 10.0
    elif ret6 > 60:
        momentum = 12.0
        risks.append(f"ราคาขึ้นแรงมากในรอบ 6 เดือน (+{ret6:.0f}%) อาจร้อนแรงเกินสำหรับเข้าใหม่")
    else:
        momentum = 0.0
        risks.append(f"ผลตอบแทน 6 เดือนติดลบ ({ret6:.0f}%)")

    # --- ตำแหน่งเทียบจุดสูงรอบปี (20) ---
    from_high = metrics.pct_from_52w_high  # ค่าติดลบ = ต่ำกว่าจุดสูง
    if from_high >= -8:
        position = 20.0
        reasons.append("ราคาเกาะใกล้จุดสูงรอบ 52 สัปดาห์ (เทรนด์แข็งแรง)")
    elif from_high >= -20:
        position = 14.0
    elif from_high >= -35:
        position = 7.0
        risks.append("ราคาห่างจากจุดสูงรอบปีพอสมควร")
    else:
        position = 2.0
        risks.append("ราคาห่างจากจุดสูงรอบปีมาก เทรนด์อาจอ่อนแรง")

    # --- เสถียรภาพ / ความผันผวน (15) ---
    vol = metrics.volatility_percent
    if vol <= 25:
        stability = 15.0
    elif vol <= 45:
        stability = 10.0
    elif vol <= 70:
        stability = 5.0
        risks.append(f"ความผันผวนสูง (~{vol:.0f}%/ปี)")
    else:
        stability = 0.0
        risks.append(f"ความผันผวนสูงมาก (~{vol:.0f}%/ปี) เสี่ยงต่อการถือยาว")

    # --- ขนาดบริษัท (10) ---
    if market_cap is None:
        size = 5.0
    elif market_cap >= 50_000_000_000:
        size = 10.0
        reasons.append("เป็นบริษัทขนาดใหญ่ (large cap) สภาพคล่องและความมั่นคงสูง")
    elif market_cap >= 10_000_000_000:
        size = 7.0
    elif market_cap >= 2_000_000_000:
        size = 4.0
    else:
        size = 1.0
        risks.append("market cap เล็ก ผันผวนและเสี่ยงสภาพคล่อง")

    total = round(trend + momentum + position + stability + size, 1)

    if total >= 75 and metrics.above_sma200:
        grade, label, tone = "A", "น่าสะสมระยะยาว", "strong"
    elif total >= 55:
        grade, label, tone = "B", "น่าสนใจ ทยอยสะสมได้", "watch"
    else:
        grade, label, tone = "C", "ยังไม่เข้าเงื่อนไข เฝ้าดู", "caution"

    if not reasons:
        reasons.append("ยังไม่มีจุดเด่นด้านเทรนด์ที่ชัดเจน")

    return {
        "score": total,
        "grade": grade,
        "label": label,
        "tone": tone,
        "breakdown": {
            "trend": round(trend, 1),
            "momentum": round(momentum, 1),
            "position": round(position, 1),
            "stability": round(stability, 1),
            "size": round(size, 1),
        },
        "reasons": reasons,
        "risks": risks,
    }


def longterm_to_dict(metrics: LongTermMetrics, stock: Stock) -> dict:
    scored = longterm_score(metrics, market_cap=stock.market_cap)
    return {
        "symbol": metrics.symbol,
        "name": stock.name,
        "sector": stock.sector,
        "price": round(metrics.price, 2),
        "score": scored["score"],
        "grade": scored["grade"],
        "label": scored["label"],
        "tone": scored["tone"],
        "breakdown": scored["breakdown"],
        "reasons": scored["reasons"],
        "risks": scored["risks"],
        "sma50": round(metrics.sma50, 2),
        "sma200": round(metrics.sma200, 2),
        "above_sma200": metrics.above_sma200,
        "week52_high": round(metrics.week52_high, 2),
        "week52_low": round(metrics.week52_low, 2),
        "pct_from_52w_high": round(metrics.pct_from_52w_high, 1),
        "return_3m_percent": metrics.return_3m_percent,
        "return_6m_percent": metrics.return_6m_percent,
        "volatility_percent": metrics.volatility_percent,
        "history_days": metrics.history_days,
    }
