from workers.data_models import MarketSnapshot, NewsItem, SignalScore, Stock
from workers.indicators.breakout import is_breakout
from workers.indicators.momentum import momentum_score, volume_score
from workers.news.catalyst_score import catalyst_score


def premarket_gap_score(snapshot: MarketSnapshot) -> float:
    gap = snapshot.premarket_gap_percent
    if 5 <= gap <= 15:
        return 10.0
    if 2 <= gap < 5:
        return 5.0 + ((gap - 2) / 3) * 4
    if gap > 15:
        return 6.0
    return max(0.0, gap / 2 * 5)


def breakout_score(snapshot: MarketSnapshot) -> float:
    return 10.0 if is_breakout(snapshot) else 0.0


def risk_score(stock: Stock, snapshot: MarketSnapshot) -> tuple[float, list[str]]:
    score = 10.0
    risks: list[str] = []

    if snapshot.relative_volume < 1.5:
        score -= 4
        risks.append("relative volume ยังไม่สูงพอ")
    if snapshot.premarket_gap_percent > 15:
        score -= 4
        risks.append("gap สูงเกินไป อาจผันผวนแรงหลังเปิดตลาด")
    if snapshot.premarket_gap_percent >= 100:
        risks.append("ราคาวิ่งเกิน 100% เสี่ยงย่อแรงหรือโดน halt ต้องรอ confirmation")
    elif snapshot.premarket_gap_percent >= 50:
        risks.append("ราคาวิ่งเกิน 50% เป็น High Risk Mover ไม่ควรไล่ราคาโดยไม่มีแผน")
    if stock.market_cap is not None and stock.market_cap < 2_000_000_000:
        score -= 3
        risks.append("market cap เล็ก มีความเสี่ยงด้านสภาพคล่อง")

    if not risks:
        risks.append("ยังต้องรอ volume และ price action หลังตลาดเปิดเพื่อยืนยัน")

    return max(0.0, score), risks


def build_signal(stock: Stock, snapshot: MarketSnapshot, news_items: list[NewsItem]) -> SignalScore:
    momentum = momentum_score(snapshot)
    volume = volume_score(snapshot)
    catalyst = catalyst_score(news_items)
    gap = premarket_gap_score(snapshot)
    breakout = breakout_score(snapshot)
    risk, risks = risk_score(stock, snapshot)
    total = round(momentum + volume + catalyst + gap + breakout + risk, 2)

    catalyst_text = news_items[0].title if news_items else "ยังไม่พบข่าว catalyst ชัดเจน"
    reason_parts = [
        f"ราคาเปลี่ยนแปลงล่าสุด {snapshot.premarket_gap_percent:.1f}%",
        f"relative volume {snapshot.relative_volume:.1f}x",
    ]
    if breakout:
        reason_parts.append("ราคาทะลุแนวต้านระยะสั้น")
    if catalyst:
        reason_parts.append("มีข่าวหรือประเด็นหนุนในรอบล่าสุด")

    return SignalScore(
        symbol=stock.symbol,
        score=min(100.0, total),
        momentum_score=round(momentum, 2),
        volume_score=round(volume, 2),
        catalyst_score=round(catalyst, 2),
        premarket_gap_score=round(gap, 2),
        breakout_score=round(breakout, 2),
        risk_score=round(risk, 2),
        gap_percent=round(snapshot.premarket_gap_percent, 2),
        relative_volume=round(snapshot.relative_volume, 2),
        latest_price=snapshot.premarket_price or snapshot.latest_price,
        previous_close=round(snapshot.previous_close, 2),
        day_high=round(snapshot.day_high, 2),
        prior_high=round(snapshot.prior_high, 2),
        market_cap=stock.market_cap,
        provider_updated_at=snapshot.updated_at,
        data_source=snapshot.data_source,
        catalyst=catalyst_text,
        reason="; ".join(reason_parts),
        risks=risks,
        watch_plan=[
            "รอดูว่ายืนเหนือแนวสูงล่าสุดได้หรือไม่",
            "หลีกเลี่ยงการไล่ราคาเมื่อแท่งแรกยืดแรงเกินไป",
            "ให้ความสำคัญกับ confirmation จาก volume หลังตลาดเปิด",
        ],
        news=news_items,
    )
