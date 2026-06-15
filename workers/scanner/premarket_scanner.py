from workers.data_models import MarketSnapshot


def is_premarket_mover(snapshot: MarketSnapshot, min_gap_percent: float = 2.0) -> bool:
    return snapshot.premarket_gap_percent >= min_gap_percent
