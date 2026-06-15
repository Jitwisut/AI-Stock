def ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    if period <= 1:
        return values[-1]

    multiplier = 2 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = (value - result) * multiplier + result
    return result
