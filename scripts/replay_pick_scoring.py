"""Replay การให้คะแนน "หุ้นน่าสนใจวันนี้" ณ วันในอดีต — ดูเบื้องต้นว่าคะแนน AI ช่วยไหม.

    python -m scripts.replay_pick_scoring --dates 6 --gap 10

กันข้อมูลอนาคตรั่ว (look-ahead):
- ฟีเจอร์ทุกตัวคำนวณจากแท่งราคาที่ตัดถึงวันนั้นเท่านั้น (รวม SPY)
- ซ่อนชื่อหุ้นเป็น S01..S12 และตัดระดับราคาจริงออก (โมเดลจำ NVDA/PLTR ได้จากชื่อ/ราคา)
- ไม่ส่งข่าว (ดึงข่าวย้อนหลังไม่ได้) → เกณฑ์ catalyst เป็น null ทุกตัว

ข้อจำกัด: ไม่กี่วัน × 12 ตัว และช่วง 5 วันซ้อนกัน → เป็นแค่ "สัญญาณเบื้องต้น" ไม่ใช่หลักฐาน
ผลจริงต้องดูจาก GET /picks/evaluation ที่สะสมทุกวันไปเรื่อย ๆ
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.getcwd())

from workers.ai.gemini_client import generate_gemini  # noqa: E402
from workers.config import get_settings  # noqa: E402
from workers.pick_scoring import (  # noqa: E402
    RUBRIC_SCHEMA,
    build_rubric_prompt,
    features_from_bars,
    market_context_from_bars,
    parse_bar_date,
    parse_rubric_response,
    rule_score,
)
from workers.pick_tracking import HORIZONS, _mean, forward_return, spearman  # noqa: E402
from workers.scanner.fetch_history import get_history_provider  # noqa: E402
from workers.scanner.fetch_stocks import fetch_stock_universe  # noqa: E402

_LEVEL_FIELDS = ("price", "sma50", "sma200")


def _anonymize(features: dict) -> dict:
    """ตัดระดับราคาจริง (ใช้เดาชื่อหุ้นได้) และ rebase กราฟ 20 วันให้เริ่มที่ 100."""
    clean = {k: v for k, v in features.items() if k not in _LEVEL_FIELDS}
    closes = features.get("closes_20d") or []
    if closes and closes[0]:
        clean["closes_20d"] = [round(c / closes[0] * 100, 2) for c in closes]
    return clean


def _fetch_all(symbols: list[str]) -> dict[str, list[dict]]:
    history = get_history_provider(use_live_data=True)

    def _one(symbol: str) -> list[dict]:
        try:
            return history.get_bars(symbol, days=420)
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=8) as executor:
        return dict(zip(symbols, executor.map(_one, symbols)))


async def main(dates: int, gap: int, shortlist_size: int, top_n: int, pause: float) -> None:
    settings = get_settings()
    symbols = [stock.symbol for stock in fetch_stock_universe(mode="default")]
    print(f"fetching bars for {len(symbols)} symbols + SPY ...")
    bars = _fetch_all(symbols + ["SPY"])
    spy = bars["SPY"]
    spy_dates = [parse_bar_date(bar["date"]) for bar in spy]
    series = {
        symbol: [(parse_bar_date(bar["date"]), float(bar["close"])) for bar in rows if bar.get("close")]
        for symbol, rows in bars.items()
    }

    # วันที่ replay: ถอยจากแท่งล่าสุดทีละ gap วัน (ต้องเหลือแท่งหลังจากนั้น ≥ 5 วัน)
    replay_days = [spy_dates[-(gap * (i + 1)) - 1] for i in range(dates)]
    calls = 0
    per_horizon: dict[int, dict[str, list]] = {
        h: {"ai_top": [], "rule_top": [], "shortlist": [], "universe": [], "spy": [], "ai_pairs": [], "rule_pairs": []}
        for h in HORIZONS
    }

    for index, day in enumerate(sorted(replay_days)):
        if index:
            # free tier จำกัดจำนวนครั้งต่อนาที — เว้นระยะไม่ให้โดน 429 กลางทาง
            await asyncio.sleep(pause)
        spy_cut = [bar for bar in spy if parse_bar_date(bar["date"]) <= day]
        candidates = []
        for symbol in symbols:
            cut = [bar for bar in bars.get(symbol, []) if parse_bar_date(bar["date"]) <= day]
            if not cut or parse_bar_date(cut[-1]["date"]) != day:
                continue
            features = features_from_bars(cut, spy_cut)
            if features:
                candidates.append({"symbol": symbol, "features": features, "rule_score": rule_score(features)})
        candidates.sort(key=lambda c: c["rule_score"], reverse=True)
        shortlist = candidates[:shortlist_size]
        alias = {f"S{i + 1:02d}": c for i, c in enumerate(shortlist)}

        prompt = build_rubric_prompt(
            [{"symbol": label, "features": _anonymize(c["features"]), "news": None} for label, c in alias.items()],
            market_context_from_bars(spy_cut),
        )
        try:
            result = await generate_gemini(
                prompt,
                api_key=settings.gemini_api_key,
                model=settings.gemini_model,
                temperature=0.0,
                response_schema=RUBRIC_SCHEMA,
                thinking_level="low",
                timeout=120.0,
            )
            calls += 1
            scored = parse_rubric_response(result.text, list(alias))
        except Exception as exc:
            print(f"{day}: AI failed ({str(exc)[:120]}) — skip")
            continue
        for label, candidate in alias.items():
            candidate["ai_score"] = (scored.get(label) or {}).get("ai_score")
        usable = [c for c in shortlist if c.get("ai_score") is not None]
        if len(usable) < top_n:
            print(f"{day}: AI scored only {len(usable)} — skip")
            continue

        ai_top = sorted(usable, key=lambda c: c["ai_score"], reverse=True)[:top_n]
        rule_top = sorted(usable, key=lambda c: c["rule_score"], reverse=True)[:top_n]
        line = [f"{day} ({result.model})"]
        for horizon in HORIZONS:
            fwd = {c["symbol"]: forward_return(series[c["symbol"]], day, horizon) for c in candidates}
            spy_ret = forward_return(series["SPY"], day, horizon)
            if spy_ret is None:
                continue
            bucket = per_horizon[horizon]
            means = {
                "ai_top": _mean([fwd[c["symbol"]] for c in ai_top]),
                "rule_top": _mean([fwd[c["symbol"]] for c in rule_top]),
                "shortlist": _mean([fwd[c["symbol"]] for c in usable]),
                "universe": _mean(list(fwd.values())),
                "spy": spy_ret,
            }
            for name, value in means.items():
                bucket[name].append(value)
            for c in usable:
                if fwd[c["symbol"]] is not None:
                    bucket["ai_pairs"].append((c["ai_score"], fwd[c["symbol"]]))
                    bucket["rule_pairs"].append((c["rule_score"], fwd[c["symbol"]]))
            line.append(f"{horizon}d AI {means['ai_top']:+.2f}% / rules {means['rule_top']:+.2f}% / SPY {spy_ret:+.2f}%")
        print(" | ".join(line))

    print(f"\nGemini calls used: {calls}")
    print(f"{'horizon':8} {'AI top':>8} {'rules top':>10} {'shortlist':>10} {'universe':>9} {'SPY':>7}  spearman(AI / rules)  n")
    for horizon, bucket in per_horizon.items():
        if not bucket["spy"]:
            continue
        ai_r = spearman([a for a, _ in bucket["ai_pairs"]], [b for _, b in bucket["ai_pairs"]])
        rule_r = spearman([a for a, _ in bucket["rule_pairs"]], [b for _, b in bucket["rule_pairs"]])
        print(
            f"{horizon}d{'':6} {_mean(bucket['ai_top']):>+7.2f}% {_mean(bucket['rule_top']):>+9.2f}% "
            f"{_mean(bucket['shortlist']):>+9.2f}% {_mean(bucket['universe']):>+8.2f}% {_mean(bucket['spy']):>+6.2f}%  "
            f"{ai_r!s:>6} / {rule_r!s:<6}  {len(bucket['spy'])} dates"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dates", type=int, default=6)
    parser.add_argument("--gap", type=int, default=10, help="trading days between replay dates")
    parser.add_argument("--shortlist", type=int, default=12)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--pause", type=float, default=25.0, help="seconds between Gemini calls")
    args = parser.parse_args()
    asyncio.run(main(args.dates, args.gap, args.shortlist, args.top, args.pause))
