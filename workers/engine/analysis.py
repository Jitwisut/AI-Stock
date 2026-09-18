"""Deterministic analysis: technical snapshot → signals → scores → risk → confidence.

กติกาสำคัญ
- ทุกฟังก์ชันเป็น pure function ของข้อมูลที่ส่งเข้ามา (ไม่ยิง network) → ทดสอบ/backtest ได้
- TechnicalSeries คำนวณ indicator ทั้ง series ครั้งเดียว แล้ว snapshot_at(i) อ่านเฉพาะค่าที่
  index <= i เท่านั้น (ไม่มี look-ahead) — backtest ใช้ฟังก์ชันเดียวกันนี้
- คะแนนทุกข้อมี "rule" + "value" + "points" ให้ผู้ใช้เห็นว่าได้คะแนนจากอะไร
- ข้อมูลที่ขาด → ข้อนั้นได้ 0 และถูกนับใน data_quality / confidence (ไม่ให้คะแนนฟรี)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from workers.engine import indicators as ind
from workers.engine.bars import Bar

# น้ำหนักตั้งต้นของ Opportunity Score (รวม 100) — strategy แต่ละแบบปรับน้ำหนักเองได้
COMPONENT_MAX = {
    "trend": 25.0,
    "momentum": 20.0,
    "volume": 15.0,
    "fundamental": 20.0,
    "risk": 10.0,
    "catalyst": 10.0,
}
TECHNICAL_COMPONENTS = ("trend", "momentum", "volume", "risk")


def _r(value, digits: int = 2):
    return round(value, digits) if isinstance(value, (int, float)) else None


def _lerp(x: float | None, x0: float, x1: float, p0: float, p1: float) -> float:
    """map x จากช่วง [x0,x1] → คะแนน [p0,p1] แบบเส้นตรง (clamp ปลาย)."""
    if x is None:
        return 0.0
    if x1 == x0:
        return p1 if x >= x1 else p0
    t = max(0.0, min(1.0, (x - x0) / (x1 - x0)))
    return p0 + t * (p1 - p0)


# ---------------------------------------------------------------------------
# Technical series (causal)
# ---------------------------------------------------------------------------
@dataclass
class TechnicalSeries:
    bars: list[Bar]
    closes: list[float]
    highs: list[Optional[float]]
    lows: list[Optional[float]]
    opens: list[Optional[float]]
    volumes: list[Optional[int]]
    sma20: list
    sma50: list
    sma200: list
    ema20: list
    ema50: list
    rsi14: list
    macd: list
    macd_signal: list
    macd_hist: list
    atr14: list
    bb_upper: list
    bb_mid: list
    bb_lower: list

    @staticmethod
    def build(bars: list[Bar]) -> "TechnicalSeries":
        closes = [bar.close for bar in bars]
        highs = [bar.high for bar in bars]
        lows = [bar.low for bar in bars]
        macd_line, macd_sig, macd_hist = ind.macd(closes)
        bb_u, bb_m, bb_l = ind.bollinger(closes)
        return TechnicalSeries(
            bars=bars,
            closes=closes,
            highs=highs,
            lows=lows,
            opens=[bar.open for bar in bars],
            volumes=[bar.volume for bar in bars],
            sma20=ind.sma(closes, 20),
            sma50=ind.sma(closes, 50),
            sma200=ind.sma(closes, 200),
            ema20=ind.ema(closes, 20),
            ema50=ind.ema(closes, 50),
            rsi14=ind.rsi(closes, 14),
            macd=macd_line,
            macd_signal=macd_sig,
            macd_hist=macd_hist,
            atr14=ind.atr(highs, lows, closes, 14),
            bb_upper=bb_u,
            bb_mid=bb_m,
            bb_lower=bb_l,
        )

    def snapshot_at(self, i: int, benchmark_closes_by_date: dict | None = None) -> dict:
        """ค่า indicator ณ แท่ง i — ใช้เฉพาะข้อมูล index <= i."""
        c = self.closes
        price = c[i]
        vol = self.volumes[i]
        prior_vols = [v for v in self.volumes[max(0, i - 20) : i] if v is not None]
        avg_vol20 = sum(prior_vols) / len(prior_vols) if len(prior_vols) >= 15 else None
        dollar_vols = [
            v * c[j] for j, v in enumerate(self.volumes[max(0, i - 19) : i + 1], start=max(0, i - 19)) if v is not None
        ]
        avg_dollar_vol20 = sum(dollar_vols) / len(dollar_vols) if len(dollar_vols) >= 15 else None

        high20 = ind.rolling_max(self.highs, i, 20)  # 20 แท่งก่อนหน้า (ไม่รวมวันนี้)
        low20 = ind.rolling_min(self.lows, i, 20)
        high52 = ind.rolling_max(self.highs, i, 252)
        low52 = ind.rolling_min(self.lows, i, 252)
        sma20, sma50, sma200 = self.sma20[i], self.sma50[i], self.sma200[i]
        atr14 = self.atr14[i]

        rets60 = ind.daily_returns(c, i, 60)
        std60 = ind.stdev(rets60[:-1]) if len(rets60) > 20 else None  # ไม่รวมวันนี้ในฐาน
        ret1d = ind.pct_return(c, i, 1)
        zscore = (ret1d / 100) / std60 if (ret1d is not None and std60) else None

        gaps = []
        for j in range(max(1, i - 19), i + 1):
            if self.opens[j] is not None and c[j - 1] > 0:
                gaps.append(abs(self.opens[j] / c[j - 1] - 1) * 100)
        up_vol = sum(self.volumes[j] or 0 for j in range(max(1, i - 19), i + 1) if c[j] > c[j - 1])
        down_vol = sum(self.volumes[j] or 0 for j in range(max(1, i - 19), i + 1) if c[j] < c[j - 1])

        def crossed(fast: list, slow: list, within: int, up: bool) -> bool:
            for j in range(max(1, i - within + 1), i + 1):
                a0, b0, a1, b1 = fast[j - 1], slow[j - 1], fast[j], slow[j]
                if None in (a0, b0, a1, b1):
                    continue
                if up and a0 <= b0 and a1 > b1:
                    return True
                if not up and a0 >= b0 and a1 < b1:
                    return True
            return False

        rsi_recent = [v for v in self.rsi14[max(0, i - 9) : i + 1] if v is not None]

        rs_3m = None
        if benchmark_closes_by_date:
            day = self.bars[i].day
            j = i - 63
            if j >= 0:
                bench_now = benchmark_closes_by_date.get(day)
                bench_then = benchmark_closes_by_date.get(self.bars[j].day)
                stock_3m = ind.pct_return(c, i, 63)
                if bench_now and bench_then and stock_3m is not None:
                    rs_3m = stock_3m - (bench_now / bench_then - 1) * 100

        def slope(series: list, lookback: int) -> float | None:
            if i - lookback < 0 or series[i] is None or series[i - lookback] in (None, 0):
                return None
            return (series[i] / series[i - lookback] - 1) * 100

        bb_u, bb_l = self.bb_upper[i], self.bb_lower[i]
        return {
            "date": self.bars[i].day.isoformat(),
            "history_bars": i + 1,
            "price": price,
            "open": self.opens[i],
            "high": self.highs[i],
            "low": self.lows[i],
            "volume": vol,
            "returns": {
                "1d": _r(ret1d),
                "5d": _r(ind.pct_return(c, i, 5)),
                "1m": _r(ind.pct_return(c, i, 21)),
                "3m": _r(ind.pct_return(c, i, 63)),
                "6m": _r(ind.pct_return(c, i, 126)),
                "1y": _r(ind.pct_return(c, i, 252)),
            },
            "sma20": _r(sma20),
            "sma50": _r(sma50),
            "sma200": _r(sma200),
            "ema20": _r(self.ema20[i]),
            "ema50": _r(self.ema50[i]),
            "sma50_slope_10d_percent": _r(slope(self.sma50, 10)),
            "sma200_slope_20d_percent": _r(slope(self.sma200, 20)),
            "ema20_slope_5d_percent": _r(slope(self.ema20, 5)),
            "rsi14": _r(self.rsi14[i]),
            "rsi14_min_10d": _r(min(rsi_recent)) if rsi_recent else None,
            "rsi14_prev": _r(self.rsi14[i - 1]) if i > 0 else None,
            "macd": _r(self.macd[i], 4),
            "macd_signal": _r(self.macd_signal[i], 4),
            "macd_hist": _r(self.macd_hist[i], 4),
            "macd_cross_up_5d": crossed(self.macd, self.macd_signal, 5, True),
            "macd_cross_down_5d": crossed(self.macd, self.macd_signal, 5, False),
            "golden_cross_20d": crossed(self.sma50, self.sma200, 20, True),
            "death_cross_20d": crossed(self.sma50, self.sma200, 20, False),
            "atr14": _r(atr14, 4),
            "atr_percent": _r(atr14 / price * 100) if atr14 else None,
            "bb_upper": _r(bb_u),
            "bb_middle": _r(self.bb_mid[i]),
            "bb_lower": _r(bb_l),
            "bb_percent_b": _r((price - bb_l) / (bb_u - bb_l), 3) if bb_u is not None and bb_l is not None and bb_u > bb_l else None,
            "avg_volume_20d": _r(avg_vol20, 0),
            "volume_ratio": _r(vol / avg_vol20) if (vol is not None and avg_vol20) else None,
            "avg_dollar_volume_20d": _r(avg_dollar_vol20, 0),
            "up_down_volume_ratio_20d": _r(up_vol / down_vol) if down_vol > 0 else None,
            "high_20d_prior": _r(high20),
            "low_20d_prior": _r(low20),
            "high_52w_prior": _r(high52),
            "low_52w_prior": _r(low52),
            "pct_from_52w_high": _r((price / high52 - 1) * 100) if high52 else None,
            "distance_sma50_percent": _r((price / sma50 - 1) * 100) if sma50 else None,
            "distance_sma200_percent": _r((price / sma200 - 1) * 100) if sma200 else None,
            "max_drawdown_1y_percent": _r(ind.max_drawdown(c, i, 252)),
            "volatility_60d_annualized_percent": _r(ind.stdev(rets60) * (252 ** 0.5) * 100) if len(rets60) > 20 else None,
            "return_zscore_1d": _r(zscore),
            "avg_abs_gap_20d_percent": _r(sum(gaps) / len(gaps)) if gaps else None,
            "relative_strength_3m_vs_spy": _r(rs_3m),
        }


# ---------------------------------------------------------------------------
# Opportunity / warning signals — ทุก signal มีหลักฐาน
# ---------------------------------------------------------------------------
def detect_signals(t: dict, f: dict | None = None, news: dict | None = None) -> list[dict]:
    out: list[dict] = []

    def add(sid, name, direction, evidence, values):
        out.append({"id": sid, "name": name, "direction": direction, "evidence": evidence, "values": values})

    price, vr = t["price"], t.get("volume_ratio")
    h20 = t.get("high_20d_prior")
    if h20 and price > h20:
        ev = [f"ราคาปิด {price:.2f} > จุดสูงสุด 20 วันก่อนหน้า {h20:.2f}"]
        if vr is not None:
            ev.append(f"วอลุ่ม {vr:.2f}x ของค่าเฉลี่ย 20 วัน")
        add("breakout_20d", "Breakout (20-day high)", "bullish", ev, {"close": price, "high_20d_prior": h20, "volume_ratio": vr})
    l20 = t.get("low_20d_prior")
    if l20 and price < l20:
        add("breakdown_20d", "Breakdown (20-day low)", "bearish", [f"ราคาปิด {price:.2f} < จุดต่ำสุด 20 วันก่อนหน้า {l20:.2f}"], {"close": price, "low_20d_prior": l20})
    if vr is not None and vr >= 2:
        r1 = t["returns"]["1d"]
        add("volume_spike", "Volume Spike", "bullish" if (r1 or 0) >= 0 else "bearish",
            [f"วอลุ่มวันล่าสุด {vr:.2f}x ของค่าเฉลี่ย 20 วัน ({t.get('avg_volume_20d'):,.0f} หุ้น)", f"ราคาเปลี่ยน {r1:+.2f}% ในวันเดียวกัน" if r1 is not None else "ไม่มีข้อมูล % เปลี่ยนแปลง"],
            {"volume_ratio": vr, "return_1d": r1})
    r1m, r3m = t["returns"]["1m"], t["returns"]["3m"]
    if r1m is not None and r3m is not None and r1m > 3 and r1m > r3m / 3 + 3:
        add("momentum_acceleration", "Momentum Acceleration", "bullish",
            [f"ผลตอบแทน 1 เดือน {r1m:+.2f}% สูงกว่าอัตราเฉลี่ยต่อเดือนของ 3 เดือน ({r3m / 3:+.2f}%/เดือน)"], {"return_1m": r1m, "return_3m": r3m})
    rmin, rsi = t.get("rsi14_min_10d"), t.get("rsi14")
    if rmin is not None and rsi is not None and rmin < 30 and rsi > 35 and rsi > (t.get("rsi14_prev") or 0):
        add("oversold_recovery", "Oversold Recovery", "bullish",
            [f"RSI14 ต่ำสุดใน 10 วัน = {rmin:.1f} (<30 = oversold)", f"RSI14 ปัจจุบันฟื้นขึ้นมาที่ {rsi:.1f}"], {"rsi14_min_10d": rmin, "rsi14": rsi})
    if t.get("golden_cross_20d"):
        add("trend_reversal_golden_cross", "Trend Reversal (Golden Cross)", "bullish",
            [f"SMA50 ({t['sma50']}) ตัดขึ้นเหนือ SMA200 ({t['sma200']}) ภายใน 20 วัน"], {"sma50": t["sma50"], "sma200": t["sma200"]})
    if t.get("macd_cross_up_5d") and (t.get("macd") or 0) < 0:
        add("trend_reversal_macd", "Trend Reversal (MACD cross from below zero)", "bullish",
            [f"MACD ({t['macd']}) ตัดขึ้นเหนือ Signal ({t['macd_signal']}) ภายใน 5 วัน ขณะยังต่ำกว่า 0"], {"macd": t["macd"], "macd_signal": t["macd_signal"]})
    if t.get("death_cross_20d"):
        add("death_cross", "Death Cross", "bearish", [f"SMA50 ({t['sma50']}) ตัดลงใต้ SMA200 ({t['sma200']}) ภายใน 20 วัน"], {"sma50": t["sma50"], "sma200": t["sma200"]})
    if t.get("macd_cross_down_5d") and (t.get("macd") or 0) > 0:
        add("macd_bearish_cross", "MACD Bearish Cross", "bearish", [f"MACD ({t['macd']}) ตัดลงใต้ Signal ({t['macd_signal']}) ภายใน 5 วัน"], {"macd": t["macd"]})
    h52 = t.get("high_52w_prior")
    if h52 and price >= h52 and t["history_bars"] >= 200:
        add("new_52w_high", "New 52-Week High", "bullish", [f"ราคาปิด {price:.2f} ≥ จุดสูงสุด 52 สัปดาห์ก่อนหน้า {h52:.2f}"], {"close": price, "high_52w_prior": h52})
    z = t.get("return_zscore_1d")
    if z is not None and abs(z) >= 3:
        add("unusual_price_move", "Unusual Price Movement", "bullish" if z > 0 else "bearish",
            [f"ผลตอบแทนวันล่าสุด {t['returns']['1d']:+.2f}% = {z:+.1f} เท่าของส่วนเบี่ยงเบนมาตรฐาน 60 วัน"], {"zscore": z, "return_1d": t["returns"]["1d"]})
    if rsi is not None and rsi >= 80:
        add("overbought", "Overbought (RSI ≥ 80)", "bearish", [f"RSI14 = {rsi:.1f}"], {"rsi14": rsi})
    sma200 = t.get("sma200")
    if sma200 and price < sma200:
        add("below_sma200", "Below SMA200 (long-term downtrend)", "bearish", [f"ราคา {price:.2f} < SMA200 {sma200:.2f}"], {"close": price, "sma200": sma200})

    if f:
        g = _fv(f, "earnings_growth_percent")
        if g is not None and g >= 15:
            add("earnings_growth", "Earnings Growth", "bullish", [f"กำไรสุทธิปีบัญชีล่าสุดโต {g:+.1f}% YoY (งวดสิ้นสุด {f['earnings_growth_percent'].get('as_of')})"], {"earnings_growth_percent": g})
        g0, g1 = _fv(f, "revenue_growth_percent"), _fv(f, "revenue_growth_prior_year_percent")
        if g0 is not None and g1 is not None and g0 > 0 and g0 > g1 + 2:
            add("revenue_acceleration", "Revenue Acceleration", "bullish", [f"รายได้โต {g0:+.1f}% (ปีล่าสุด) เร่งขึ้นจาก {g1:+.1f}% (ปีก่อนหน้า)"], {"revenue_growth": g0, "revenue_growth_prior": g1})
        if g0 is not None and g0 < 0:
            add("revenue_decline", "Revenue Decline", "bearish", [f"รายได้ปีล่าสุดลดลง {g0:.1f}% YoY"], {"revenue_growth": g0})
    return out


def _fv(f: dict | None, key: str):
    if not f:
        return None
    item = f.get(key) or {}
    return item.get("value")


# ---------------------------------------------------------------------------
# Component scores (raw points out of COMPONENT_MAX)
# ---------------------------------------------------------------------------
def _component(rules: list[tuple[str, float, float, object]], max_points: float) -> dict:
    total = sum(points for _rule, points, _max, _value in rules)
    return {
        "score": round(min(max_points, total), 2),
        "max": max_points,
        "rules": [
            {"rule": rule, "points": round(points, 2), "max": mx, "value": value}
            for rule, points, mx, value in rules
        ],
    }


def score_trend(t: dict) -> dict:
    p = t["price"]
    sma20, sma50, sma200 = t.get("sma20"), t.get("sma50"), t.get("sma200")
    rules = [
        ("Price > SMA20", 4.0 if sma20 and p > sma20 else 0.0, 4, sma20),
        ("Price > SMA50", 5.0 if sma50 and p > sma50 else 0.0, 5, sma50),
        ("Price > SMA200", 6.0 if sma200 and p > sma200 else 0.0, 6, sma200),
        ("SMA20 > SMA50", 3.0 if sma20 and sma50 and sma20 > sma50 else 0.0, 3, f"{sma20} vs {sma50}" if sma20 and sma50 else "N/A"),
        ("SMA50 > SMA200", 4.0 if sma50 and sma200 and sma50 > sma200 else 0.0, 4, f"{sma50} vs {sma200}" if sma50 and sma200 else "N/A"),
        ("SMA50 rising (10d)", 3.0 if (t.get("sma50_slope_10d_percent") or 0) > 0 else 0.0, 3, t.get("sma50_slope_10d_percent")),
    ]
    return _component(rules, COMPONENT_MAX["trend"])


def score_momentum(t: dict) -> dict:
    r = t["returns"]
    rsi = t.get("rsi14")
    if rsi is None:
        rsi_pts = 0.0
    elif 50 <= rsi <= 70:
        rsi_pts = 4.0
    elif 40 <= rsi < 50 or 70 < rsi <= 80:
        rsi_pts = 2.0
    else:
        rsi_pts = 0.0
    rules = [
        ("1M return (-10%→0, +15%→6)", _lerp(r.get("1m"), -10, 15, 0, 6), 6, r.get("1m")),
        ("3M return (-15%→0, +30%→6)", _lerp(r.get("3m"), -15, 30, 0, 6), 6, r.get("3m")),
        ("Relative strength 3M vs SPY (-10pp→0, +10pp→4)", _lerp(t.get("relative_strength_3m_vs_spy"), -10, 10, 0, 4), 4, t.get("relative_strength_3m_vs_spy")),
        ("RSI14 healthy zone (50-70 → 4)", rsi_pts, 4, rsi),
    ]
    return _component(rules, COMPONENT_MAX["momentum"])


def score_volume(t: dict) -> dict:
    vr = t.get("volume_ratio")
    ud = t.get("up_down_volume_ratio_20d")
    dv = t.get("avg_dollar_volume_20d")
    if dv is None:
        liq = 0.0
    elif dv >= 50e6:
        liq = 4.0
    elif dv >= 10e6:
        liq = 3.0
    elif dv >= 2e6:
        liq = 2.0
    elif dv >= 0.5e6:
        liq = 1.0
    else:
        liq = 0.0
    rules = [
        ("Volume ratio vs 20D avg (0.7x→0, 2x→6)", _lerp(vr, 0.7, 2.0, 0, 6), 6, vr),
        ("Up/down volume 20D (0.67→0, 1.5→5)", _lerp(ud, 0.67, 1.5, 0, 5), 5, ud),
        ("Liquidity: avg $ volume 20D", liq, 4, dv),
    ]
    return _component(rules, COMPONENT_MAX["volume"])


def score_fundamental(f: dict | None) -> dict:
    if not f:
        return {**_component([("Fundamental data unavailable", 0.0, 20, None)], COMPONENT_MAX["fundamental"]), "coverage": 0.0}

    def tier(value, table):
        if value is None:
            return 0.0
        for threshold, points in table:
            if value >= threshold:
                return points
        return 0.0

    rev, earn = _fv(f, "revenue_growth_percent"), _fv(f, "earnings_growth_percent")
    margin, roe, de = _fv(f, "profit_margin_percent"), _fv(f, "roe_percent"), _fv(f, "debt_to_equity")
    fcf, pe = _fv(f, "free_cash_flow"), _fv(f, "pe_ratio")
    net_income = _fv(f, "net_income")
    if de is None:
        de_pts = 0.0
    elif de <= 0.5:
        de_pts = 2.0
    elif de <= 1.5:
        de_pts = 1.0
    else:
        de_pts = 0.0
    if pe is None:
        pe_pts = 0.0
    elif 0 < pe <= 25:
        pe_pts = 2.0
    elif pe <= 50:
        pe_pts = 1.0
    else:
        pe_pts = 0.0
    rules = [
        ("Revenue growth YoY (≥20%→5, ≥10→4, ≥5→3, ≥0→2)", tier(rev, [(20, 5), (10, 4), (5, 3), (0, 2)]), 5, rev),
        ("Earnings growth YoY (≥20%→4, ≥0→2; loss→0)", 0.0 if (net_income is not None and net_income <= 0) else tier(earn, [(20, 4), (0, 2)]), 4, earn),
        ("Profit margin (≥20%→3, ≥10→2, >0→1)", tier(margin, [(20, 3), (10, 2), (0.0001, 1)]), 3, margin),
        ("ROE (≥20%→3, ≥10→2, >0→1)", tier(roe, [(20, 3), (10, 2), (0.0001, 1)]), 3, roe),
        ("Debt/Equity (≤0.5→2, ≤1.5→1)", de_pts, 2, de),
        ("Free cash flow positive", 1.0 if fcf is not None and fcf > 0 else 0.0, 1, fcf),
        ("P/E sanity (0-25→2, 25-50→1; not decisive alone)", pe_pts, 2, pe),
    ]
    result = _component(rules, COMPONENT_MAX["fundamental"])
    result["coverage"] = (f.get("_coverage") or {}).get("ratio", 0.0)
    return result


def score_catalyst(t: dict, f: dict | None, news: dict | None, signals: list[dict]) -> dict:
    ids = {s["id"] for s in signals}
    recent = [item for item in (news or {}).get("items", []) if (item.get("age_hours") or 1e9) <= 72]
    news_pts = 3.0 if len(recent) >= 3 else (2.0 if recent else 0.0)
    surprise = None
    last_q = _fv(f, "last_quarter_eps") if f else None
    if isinstance(last_q, dict):
        surprise = last_q.get("surprise_percent")
    rules = [
        ("Company news in last 72h (fact: count, not sentiment)", news_pts, 3, len(recent)),
        ("20D breakout with volume ≥1.5x", 3.0 if "breakout_20d" in ids and (t.get("volume_ratio") or 0) >= 1.5 else 0.0, 3, t.get("volume_ratio")),
        ("New 52-week high", 2.0 if "new_52w_high" in ids else 0.0, 2, t.get("high_52w_prior")),
        ("Last quarter EPS beat consensus", 2.0 if surprise is not None and surprise > 0 else 0.0, 2, surprise),
    ]
    return _component(rules, COMPONENT_MAX["catalyst"])


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------
def assess_risk(t: dict, f: dict | None = None, market_condition: str | None = None, days_to_earnings: int | None = None) -> dict:
    factors: list[dict] = []

    def add(name, value, penalty, why):
        factors.append({"factor": name, "value": value, "penalty": penalty, "explanation": why})

    atrp = t.get("atr_percent")
    if atrp is not None:
        if atrp > 6:
            add("ATR %", atrp, 3, f"ATR14 = {atrp:.2f}% ของราคา — ผันผวนสูงมาก")
        elif atrp > 4:
            add("ATR %", atrp, 2, f"ATR14 = {atrp:.2f}% ของราคา — ผันผวนสูง")
        elif atrp > 2.5:
            add("ATR %", atrp, 1, f"ATR14 = {atrp:.2f}% ของราคา")
    dd = t.get("pct_from_52w_high")
    if dd is not None:
        if dd < -40:
            add("Drawdown from 52W high", dd, 2, f"ราคาต่ำกว่าจุดสูงสุด 52 สัปดาห์ {dd:.1f}%")
        elif dd < -25:
            add("Drawdown from 52W high", dd, 1, f"ราคาต่ำกว่าจุดสูงสุด 52 สัปดาห์ {dd:.1f}%")
    d200 = t.get("distance_sma200_percent")
    if d200 is not None:
        if d200 > 40:
            add("Distance from SMA200", d200, 1, f"ราคาอยู่เหนือ SMA200 ถึง {d200:.1f}% — ยืดตัวมาก เสี่ยงย่อ")
        elif d200 < 0:
            add("Distance from SMA200", d200, 1, f"ราคาอยู่ใต้ SMA200 {d200:.1f}% — เทรนด์ใหญ่ยังเป็นขาลง")
    rsi = t.get("rsi14")
    if rsi is not None and rsi > 75:
        add("Overbought RSI", rsi, 1, f"RSI14 = {rsi:.1f} (>75)")
    dv = t.get("avg_dollar_volume_20d")
    if dv is not None:
        if dv < 2e6:
            add("Low liquidity", dv, 2, f"มูลค่าซื้อขายเฉลี่ย 20 วัน ${dv / 1e6:.2f}M — สภาพคล่องต่ำ spread กว้าง")
        elif dv < 5e6:
            add("Low liquidity", dv, 1, f"มูลค่าซื้อขายเฉลี่ย 20 วัน ${dv / 1e6:.2f}M")
    vr = t.get("volume_ratio")
    if vr is not None and vr >= 5:
        add("Abnormal volume", vr, 1, f"วอลุ่ม {vr:.1f}x ของค่าเฉลี่ย — อาจมีเหตุการณ์พิเศษ ราคาเหวี่ยงแรงได้")
    gap = t.get("avg_abs_gap_20d_percent")
    if gap is not None and gap > 3:
        add("Gap risk", gap, 1, f"ช่องว่างราคาเปิดเฉลี่ย 20 วัน {gap:.2f}% — เสี่ยง gap ข้าม stop")
    if days_to_earnings is not None and 0 <= days_to_earnings <= 7:
        add("Earnings risk", days_to_earnings, 1, f"ประกาศงบในอีก {days_to_earnings} วัน — ราคาอาจกระโดดแรง")
    if market_condition == "Risk-Off":
        add("Market risk", market_condition, 1, "สภาวะตลาดโดยรวมเป็น Risk-Off")
    if t["price"] < 5:
        add("Low price", t["price"], 1, f"ราคา ${t['price']:.2f} (<$5) มักผันผวนและสภาพคล่องต่ำ")
    net_income = _fv(f, "net_income")
    if net_income is not None and net_income < 0:
        add("Unprofitable", net_income, 1, "บริษัทยังขาดทุนในปีบัญชีล่าสุด")

    penalty = sum(item["penalty"] for item in factors)
    if penalty <= 2:
        level = "Low"
    elif penalty <= 4:
        level = "Medium"
    elif penalty <= 6:
        level = "High"
    else:
        level = "Very High"
    return {
        "level": level,
        "penalty_points": penalty,
        "score": max(0.0, COMPONENT_MAX["risk"] - penalty),
        "max": COMPONENT_MAX["risk"],
        "factors": factors,
        "volatility_60d_annualized_percent": t.get("volatility_60d_annualized_percent"),
        "max_drawdown_1y_percent": t.get("max_drawdown_1y_percent"),
    }


# ---------------------------------------------------------------------------
# Multi-timeframe & key levels
# ---------------------------------------------------------------------------
def multi_timeframe(t: dict) -> dict:
    p = t["price"]

    def judge(ma, slope, name, ma_name):
        if ma is None or slope is None:
            return {"trend": "Insufficient data", "evidence": f"ข้อมูลไม่พอคำนวณ {ma_name}"}
        if p > ma and slope > 0:
            trend = "Up"
        elif p < ma and slope < 0:
            trend = "Down"
        else:
            trend = "Sideways"
        return {"trend": trend, "evidence": f"ราคา {p:.2f} {'>' if p > ma else '<'} {ma_name} {ma:.2f}, ความชัน {ma_name} {slope:+.2f}%"}

    short = judge(t.get("ema20"), t.get("ema20_slope_5d_percent"), "short", "EMA20")
    medium = judge(t.get("sma50"), t.get("sma50_slope_10d_percent"), "medium", "SMA50")
    long = judge(t.get("sma200"), t.get("sma200_slope_20d_percent"), "long", "SMA200")
    trends = [x["trend"] for x in (short, medium, long)]
    conflict = ("Up" in trends and "Down" in trends)
    note = None
    if short["trend"] == "Up" and long["trend"] == "Down":
        note = "ระยะสั้นเป็นขาขึ้นแต่เทรนด์ใหญ่ (SMA200) ยังเป็นขาลง — อาจเป็นแค่การดีดตัว"
    elif short["trend"] == "Down" and long["trend"] == "Up":
        note = "เทรนด์ใหญ่ยังเป็นขาขึ้น แต่ระยะสั้นกำลังย่อตัว"
    return {
        "returns": t["returns"],
        "short_term": {**short, "horizon": "1D-1M (EMA20)"},
        "medium_term": {**medium, "horizon": "1M-3M (SMA50)"},
        "long_term": {**long, "horizon": "6M-1Y (SMA200)"},
        "conflict": conflict,
        "note": note,
    }


def key_levels(t: dict) -> list[dict]:
    p = t["price"]
    levels = [
        ("20D high (prior)", t.get("high_20d_prior")),
        ("20D low (prior)", t.get("low_20d_prior")),
        ("SMA20", t.get("sma20")),
        ("SMA50", t.get("sma50")),
        ("SMA200", t.get("sma200")),
        ("52W high (prior)", t.get("high_52w_prior")),
        ("52W low (prior)", t.get("low_52w_prior")),
        ("Bollinger upper", t.get("bb_upper")),
        ("Bollinger lower", t.get("bb_lower")),
    ]
    out = []
    for name, value in levels:
        if value is None:
            continue
        out.append({
            "name": name,
            "price": value,
            "type": "resistance" if value > p else "support",
            "distance_percent": round((value / p - 1) * 100, 2),
        })
    return sorted(out, key=lambda item: item["price"], reverse=True)


# ---------------------------------------------------------------------------
# Confidence (deterministic — ไม่ใช่ให้ LLM เดา %)
# ---------------------------------------------------------------------------
def assess_confidence(t: dict, f: dict | None, news: dict | None, freshness: dict, signals: list[dict], mtf: dict) -> dict:
    factors: list[dict] = []
    points = 0

    def add(name, pts, why):
        nonlocal points
        points += pts
        factors.append({"factor": name, "points": pts, "explanation": why})

    bars = t["history_bars"]
    add("Price history", 2 if bars >= 252 else (1 if bars >= 60 else 0), f"มีแท่งราคา {bars} วัน (ต้องการ ≥252 สำหรับครบทุก indicator)")
    coverage = (f or {}).get("_coverage", {}).get("ratio") if f else None
    if coverage is None:
        add("Fundamentals", 0, "ไม่มีข้อมูลพื้นฐาน")
    else:
        add("Fundamentals", 2 if coverage >= 0.7 else (1 if coverage >= 0.4 else 0), f"ข้อมูลพื้นฐานครบ {coverage * 100:.0f}% ของ field ที่ใช้ให้คะแนน")
    behind = freshness.get("sessions_behind")
    add("Price freshness", 2 if behind == 0 else (1 if behind == 1 else 0),
        f"แท่งล่าสุด {freshness.get('last_bar_date')} (ตามหลัง session ล่าสุด {behind} วัน)" if behind is not None else "ไม่ทราบความสดของข้อมูล")
    n_news = len((news or {}).get("items", []))
    add("News coverage", 1 if n_news else 0, f"มีข่าวที่ระบุวันเวลา {n_news} รายการ (ภายใน 30 วัน)" if news else "ไม่ได้ดึงข่าว")
    bullish = sum(1 for s in signals if s["direction"] == "bullish")
    bearish = sum(1 for s in signals if s["direction"] == "bearish")
    if bullish >= 3 and bearish == 0:
        add("Signal agreement", 2, f"สัญญาณบวก {bullish} ตัว ไม่มีสัญญาณลบ")
    elif bullish > bearish:
        add("Signal agreement", 1, f"สัญญาณบวก {bullish} vs ลบ {bearish}")
    else:
        add("Signal agreement", 0, f"สัญญาณบวก {bullish} vs ลบ {bearish} — ไม่ไปทางเดียวกัน")
    if mtf.get("conflict"):
        add("Timeframe conflict", -1, mtf.get("note") or "เทรนด์ต่าง timeframe ขัดกัน")
    if bullish and bearish:
        add("Conflicting signals", -1, "มีทั้งสัญญาณบวกและลบพร้อมกัน")

    level = "High" if points >= 7 else ("Medium" if points >= 4 else "Low")
    return {"level": level, "points": points, "max_points": 9, "factors": factors}


# ---------------------------------------------------------------------------
# Weighted opportunity score
# ---------------------------------------------------------------------------
def weighted_score(components: dict[str, dict], weights: dict[str, float], *, technical_only: bool = False) -> dict:
    """คะแนนรวม = Σ (คะแนนดิบ / คะแนนเต็ม) × น้ำหนัก — น้ำหนักรวม 100.

    technical_only=True ใช้ตอนคัดรอบแรก (ยังไม่มีข้อมูลพื้นฐาน/ข่าว) โดยปรับน้ำหนัก
    ของ component ที่เหลือให้รวมเป็น 100 และบอกชัดว่าเป็นคะแนนเบื้องต้น
    """
    keys = [k for k in weights if weights[k] > 0 and (not technical_only or k in TECHNICAL_COMPONENTS)]
    total_weight = sum(weights[k] for k in keys) or 1.0
    scale = 100.0 / total_weight if technical_only else 1.0
    breakdown = {}
    total = 0.0
    for key in keys:
        comp = components.get(key)
        if comp is None:
            continue
        weight = weights[key] * scale
        points = comp["score"] / comp["max"] * weight if comp["max"] else 0.0
        breakdown[key] = {"points": round(points, 2), "weight": round(weight, 2), "raw": comp["score"], "raw_max": comp["max"]}
        total += points
    return {"total": round(total, 1), "max": 100, "breakdown": breakdown, "technical_only": technical_only}
