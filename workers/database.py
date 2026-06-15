from __future__ import annotations

from workers.data_models import SignalScore
from workers.repository import save_signals as save_signal_rows


def save_signals(signals: list[SignalScore], database_url: str) -> int:
    return len(save_signal_rows(signals, database_url))
