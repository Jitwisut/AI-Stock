from workers.data_models import MarketSnapshot


def has_unusual_volume(snapshot: MarketSnapshot, min_relative_volume: float = 1.5) -> bool:
    return snapshot.relative_volume >= min_relative_volume
