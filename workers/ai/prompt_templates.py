from __future__ import annotations

from workers.data_models import SignalScore

FORBIDDEN_WORDS = ["ซื้อตัวนี้", "การันตีขึ้น", "เข้าเลย", "รวยแน่"]


def build_signal_prompt(signals: list[SignalScore]) -> str:
    rows = []
    for signal in signals:
        rows.append(
            {
                "symbol": signal.symbol,
                "score": signal.score,
                "premarket_gap_percent": signal.gap_percent,
                "relative_volume": signal.relative_volume,
                "catalyst": signal.catalyst,
                "reason": signal.reason,
                "risks": signal.risks,
            }
        )

    return (
        "สรุปหุ้น US momentum Top 5 เป็นภาษาไทยสำหรับ Telegram "
        "โดยห้ามใช้คำว่า ซื้อตัวนี้, การันตีขึ้น, เข้าเลย, รวยแน่ "
        "ให้ย้ำว่าเป็นข้อมูลช่วยคัดกรอง ไม่ใช่คำแนะนำทางการเงิน\n\n"
        f"signals={rows}"
    )
