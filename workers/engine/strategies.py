"""กลยุทธ์ของหน้า AI Stock Discovery — น้ำหนักคะแนน + เงื่อนไขกรองที่ตรวจสอบได้.

"Top" ของทุกกลยุทธ์มาจากคะแนนตามน้ำหนักด้านล่าง ไม่ใช่ให้ LLM เลือก
เงื่อนไข filter ใช้ค่าจาก technical snapshot / fundamentals เท่านั้น
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from workers.engine.analysis import COMPONENT_MAX


def _fv(f, key):
    return ((f or {}).get(key) or {}).get("value")


@dataclass(frozen=True)
class Strategy:
    id: str
    name: str
    description: str
    weights: dict
    filter_description: str
    technical_filter: Callable[[dict, list], bool] = lambda t, s: True
    fundamental_filter: Optional[Callable[[dict], bool]] = None
    needs_fundamentals_first: bool = False
    risk_levels: tuple = ("Low", "Medium", "High", "Very High")
    extra: dict = field(default_factory=dict)


def _ids(signals):
    return {s["id"] for s in signals}


STRATEGIES: dict[str, Strategy] = {
    "balanced": Strategy(
        "balanced", "Balanced (Opportunity Score)", "น้ำหนักตั้งต้น: Trend 25 / Momentum 20 / Volume 15 / Fundamental 20 / Risk 10 / Catalyst 10",
        dict(COMPONENT_MAX), "ไม่มีเงื่อนไขเพิ่มนอกจากสภาพคล่องขั้นต่ำ",
    ),
    "momentum": Strategy(
        "momentum", "Momentum", "หุ้นที่ราคาวิ่งแรงต่อเนื่องและแรงกว่าตลาด",
        {"trend": 25, "momentum": 30, "volume": 15, "fundamental": 10, "risk": 10, "catalyst": 10},
        "ผลตอบแทน 3M > 0 และราคา > SMA50",
        technical_filter=lambda t, s: (t["returns"].get("3m") or -1) > 0 and bool(t.get("sma50")) and t["price"] > t["sma50"],
    ),
    "growth": Strategy(
        "growth", "Growth", "รายได้และกำไรโตสูง โดยเทรนด์ราคายังไม่เสีย",
        {"trend": 15, "momentum": 15, "volume": 10, "fundamental": 40, "risk": 10, "catalyst": 10},
        "Revenue growth ≥ 10% YoY และราคา > SMA200",
        technical_filter=lambda t, s: bool(t.get("sma200")) and t["price"] > t["sma200"],
        fundamental_filter=lambda f: (_fv(f, "revenue_growth_percent") or -1) >= 10,
        needs_fundamentals_first=True,
    ),
    "value": Strategy(
        "value", "Value", "มูลค่าไม่แพงเมื่อเทียบกำไร และมีกำไรจริง (ไม่ตัดสินจาก P/E อย่างเดียว)",
        {"trend": 15, "momentum": 5, "volume": 5, "fundamental": 45, "risk": 20, "catalyst": 10},
        "0 < P/E ≤ 20, profit margin > 0 และ free cash flow > 0",
        fundamental_filter=lambda f: 0 < (_fv(f, "pe_ratio") or 0) <= 20 and (_fv(f, "profit_margin_percent") or 0) > 0 and (_fv(f, "free_cash_flow") or 0) > 0,
        needs_fundamentals_first=True,
    ),
    "breakout": Strategy(
        "breakout", "Breakout", "ราคาทะลุจุดสูงสุด 20 วันพร้อมวอลุ่มยืนยัน",
        {"trend": 20, "momentum": 20, "volume": 25, "fundamental": 5, "risk": 10, "catalyst": 20},
        "ราคาปิด > high 20 วันก่อนหน้า และ volume ratio ≥ 1.3",
        technical_filter=lambda t, s: "breakout_20d" in _ids(s) and (t.get("volume_ratio") or 0) >= 1.3,
    ),
    "oversold_recovery": Strategy(
        "oversold_recovery", "Oversold Recovery", "เคย oversold แล้วเริ่มฟื้น",
        {"trend": 10, "momentum": 15, "volume": 20, "fundamental": 20, "risk": 20, "catalyst": 15},
        "RSI14 ต่ำกว่า 30 ภายใน 10 วัน และปัจจุบันฟื้นขึ้น > 35",
        technical_filter=lambda t, s: "oversold_recovery" in _ids(s),
    ),
    "high_volume": Strategy(
        "high_volume", "High Volume", "วอลุ่มผิดปกติ (มีคนสนใจมากกว่าปกติ)",
        {"trend": 15, "momentum": 15, "volume": 35, "fundamental": 10, "risk": 10, "catalyst": 15},
        "Volume ratio ≥ 2x ค่าเฉลี่ย 20 วัน",
        technical_filter=lambda t, s: (t.get("volume_ratio") or 0) >= 2,
    ),
    "trending": Strategy(
        "trending", "Trending", "เทรนด์ขาขึ้นเรียงตัวสมบูรณ์ทุกเส้นค่าเฉลี่ย",
        {"trend": 40, "momentum": 20, "volume": 10, "fundamental": 10, "risk": 15, "catalyst": 5},
        "Price > SMA20 > SMA50 > SMA200",
        technical_filter=lambda t, s: all(t.get(k) for k in ("sma20", "sma50", "sma200")) and t["price"] > t["sma20"] > t["sma50"] > t["sma200"],
    ),
    "low_risk": Strategy(
        "low_risk", "Low Risk", "ความเสี่ยงต่ำ เทรนด์ดี พื้นฐานแข็ง",
        {"trend": 25, "momentum": 10, "volume": 5, "fundamental": 25, "risk": 35, "catalyst": 0},
        "Risk level = Low และราคา > SMA200",
        technical_filter=lambda t, s: bool(t.get("sma200")) and t["price"] > t["sma200"],
        risk_levels=("Low",),
    ),
}


def custom_strategy(weights: dict[str, float]) -> Strategy:
    clean = {k: max(0.0, float(weights.get(k, 0))) for k in COMPONENT_MAX}
    total = sum(clean.values())
    if total <= 0:
        clean = dict(COMPONENT_MAX)
        total = 100.0
    normalized = {k: round(v / total * 100, 2) for k, v in clean.items()}
    return Strategy("custom", "Custom", "น้ำหนักกำหนดเองโดยผู้ใช้ (ปรับให้รวมเป็น 100)", normalized, "ไม่มีเงื่อนไขเพิ่ม")


def get_strategy(strategy_id: str, weights: dict | None = None) -> Strategy:
    if strategy_id == "custom":
        return custom_strategy(weights or {})
    if strategy_id not in STRATEGIES:
        raise ValueError(f"unknown strategy: {strategy_id}")
    return STRATEGIES[strategy_id]


def describe_all() -> list[dict]:
    return [
        {"id": s.id, "name": s.name, "description": s.description, "weights": s.weights, "filter": s.filter_description}
        for s in STRATEGIES.values()
    ] + [{"id": "custom", "name": "Custom", "description": "กำหนดน้ำหนักเอง", "weights": dict(COMPONENT_MAX), "filter": "-"}]
