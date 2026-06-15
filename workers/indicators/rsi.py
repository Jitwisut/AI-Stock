def rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) <= period:
        return 50.0

    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(closes, closes[1:]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(abs(min(change, 0.0)))

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    relative_strength = avg_gain / avg_loss
    return 100 - (100 / (1 + relative_strength))
