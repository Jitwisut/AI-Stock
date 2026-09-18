from workers.data_models import MarketSnapshot


def is_breakout(snapshot: MarketSnapshot) -> bool:
    candidate_price = snapshot.premarket_price or snapshot.latest_price
    # prior_high = 0 แปลว่าไม่ทราบ — ห้ามนับเป็น breakout
    return snapshot.prior_high > 0 and candidate_price > snapshot.prior_high


def breakout_distance_percent(snapshot: MarketSnapshot) -> float:
    candidate_price = snapshot.premarket_price or snapshot.latest_price
    if snapshot.prior_high <= 0:
        return 0.0
    return ((candidate_price - snapshot.prior_high) / snapshot.prior_high) * 100
