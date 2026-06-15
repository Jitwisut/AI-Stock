from workers.data_models import MarketSnapshot


def momentum_score(snapshot: MarketSnapshot) -> float:
    gap = snapshot.premarket_gap_percent
    if gap <= 0:
        return 0.0
    if 5 <= gap <= 15:
        return 25.0
    if 2 <= gap < 5:
        return 15.0 + ((gap - 2) / 3) * 8
    if gap > 15:
        return 18.0
    return 8.0


def volume_score(snapshot: MarketSnapshot) -> float:
    rel = snapshot.relative_volume
    if rel >= 5:
        return 25.0
    if rel >= 3:
        return 20.0 + (rel - 3) * 2.5
    if rel >= 1.5:
        return 10.0 + ((rel - 1.5) / 1.5) * 10
    return max(0.0, rel / 1.5 * 10)
