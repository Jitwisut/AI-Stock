"""บริบทตลาด: ดัชนีหลัก (ผ่าน ETF ที่ track ดัชนี), VIX และ sector ETF.

- S&P 500 / Nasdaq-100 / Dow / Russell 2000 ใช้ SPY / QQQ / DIA / IWM (Nasdaq historical)
  เพราะ Nasdaq public API ไม่ให้ index quote — ระบุไว้ใน field "proxy"
- VIX ใช้ไฟล์ประวัติทางการของ Cboe
- Market Condition (Bullish / Neutral / Risk-Off) มาจากกฎที่ระบุหลักฐานทุกข้อ
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime

from workers.engine import indicators as ind
from workers.engine import sources
from workers.engine.bars import get_daily_bars
from workers.engine.runtime import CACHE, ProviderUnavailableError, iso, utcnow

logger = logging.getLogger(__name__)

CONTEXT_TTL = 10 * 60.0

INDEX_PROXIES = {
    "SPY": "S&P 500",
    "QQQ": "Nasdaq-100",
    "DIA": "Dow Jones Industrial Average",
    "IWM": "Russell 2000",
}

# ชื่อ sector ของ Nasdaq → SPDR sector ETF
SECTOR_ETFS = {
    "Technology": "XLK",
    "Finance": "XLF",
    "Energy": "XLE",
    "Health Care": "XLV",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Industrials": "XLI",
    "Basic Materials": "XLB",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Telecommunications": "XLC",
}
SEMICONDUCTOR_ETF = "SMH"


def sector_etf_for(sector: str | None, industry: str | None) -> str | None:
    if industry and "semiconductor" in industry.lower():
        return SEMICONDUCTOR_ETF
    return SECTOR_ETFS.get(sector or "")


def trend_snapshot(symbol: str) -> dict:
    series = get_daily_bars(symbol)
    closes = [bar.close for bar in series.bars]
    i = len(closes) - 1
    sma50, sma200 = ind.sma(closes, 50)[i], ind.sma(closes, 200)[i]
    rsi14 = ind.rsi(closes)[i]
    return {
        "symbol": symbol,
        "close": closes[i],
        "last_bar_date": series.last_bar_date.isoformat() if series.last_bar_date else None,
        "return_1d_percent": _r(ind.pct_return(closes, i, 1)),
        "return_1m_percent": _r(ind.pct_return(closes, i, 21)),
        "return_3m_percent": _r(ind.pct_return(closes, i, 63)),
        "sma50": _r(sma50),
        "sma200": _r(sma200),
        "above_sma50": sma50 is not None and closes[i] > sma50,
        "above_sma200": sma200 is not None and closes[i] > sma200,
        "rsi14": _r(rsi14),
        "freshness": series.freshness(),
    }


def _r(value, digits: int = 2):
    return round(value, digits) if isinstance(value, (int, float)) else None


def vix_snapshot() -> dict:
    return CACHE.get_or_compute("vix", CONTEXT_TTL, _fetch_vix)


def _fetch_vix() -> dict:
    fetched = utcnow()
    text = sources.raw_text(
        sources.CBOE, "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv", accept="text/csv"
    )
    rows = list(csv.DictReader(io.StringIO(text)))
    closes = []
    for row in rows[-260:]:
        try:
            closes.append((datetime.strptime(row["DATE"], "%m/%d/%Y").date(), float(row["CLOSE"])))
        except (KeyError, ValueError):
            continue
    if not closes:
        raise ProviderUnavailableError(sources.CBOE, "VIX history empty")
    last_day, last = closes[-1]
    avg20 = sum(value for _d, value in closes[-20:]) / min(20, len(closes))
    return {
        "symbol": "VIX",
        "close": last,
        "date": last_day.isoformat(),
        "avg_20d": round(avg20, 2),
        "source": "cboe_vix_history",
        "fetched_at": iso(fetched),
        "note": "Cboe official daily close (end-of-day)",
    }


def get_market_context() -> dict:
    return CACHE.get_or_compute("market_context", CONTEXT_TTL, _build_context)


def _build_context() -> dict:
    from concurrent.futures import ThreadPoolExecutor

    indices, errors = {}, []
    vix = None
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {symbol: pool.submit(trend_snapshot, symbol) for symbol in INDEX_PROXIES}
        vix_future = pool.submit(vix_snapshot)
        for symbol, future in futures.items():
            try:
                indices[symbol] = {"name": INDEX_PROXIES[symbol], "proxy": f"{symbol} ETF", **future.result()}
            except ProviderUnavailableError as exc:
                errors.append(str(exc))
        try:
            vix = vix_future.result()
        except ProviderUnavailableError as exc:
            errors.append(str(exc))

    condition, evidence = classify_market(indices.get("SPY"), indices.get("QQQ"), vix)
    return {
        "condition": condition,
        "evidence": evidence,
        "indices": indices,
        "vix": vix,
        "errors": errors,
        "generated_at": iso(utcnow()),
    }


def classify_market(spy: dict | None, qqq: dict | None, vix: dict | None) -> tuple[str, list[str]]:
    """กฎจัดสภาวะตลาด — ทุกข้อสรุปต้องมีหลักฐานเป็นตัวเลข.

    Risk-Off: SPY ต่ำกว่า SMA200 หรือ VIX > 25
    Bullish : SPY และ QQQ อยู่เหนือทั้ง SMA50/SMA200 และ VIX < 20
    ที่เหลือ: Neutral
    """
    if spy is None:
        return "Unknown", ["ไม่มีข้อมูล SPY — ประเมินสภาวะตลาดไม่ได้ (Insufficient data)"]
    evidence = [
        f"SPY ปิด {spy['close']} เทียบ SMA50 {spy['sma50']} / SMA200 {spy['sma200']} (วันที่ {spy['last_bar_date']})"
    ]
    if qqq:
        evidence.append(f"QQQ ปิด {qqq['close']} เทียบ SMA50 {qqq['sma50']} / SMA200 {qqq['sma200']}")
    vix_value = vix["close"] if vix else None
    if vix_value is not None:
        evidence.append(f"VIX ปิด {vix_value} (ค่าเฉลี่ย 20 วัน {vix['avg_20d']}, วันที่ {vix['date']})")
    else:
        evidence.append("ไม่มีข้อมูล VIX")

    if not spy["above_sma200"] or (vix_value is not None and vix_value > 25):
        return "Risk-Off", evidence
    qqq_ok = qqq is None or (qqq["above_sma50"] and qqq["above_sma200"])
    if spy["above_sma50"] and spy["above_sma200"] and qqq_ok and vix_value is not None and vix_value < 20:
        return "Bullish", evidence
    return "Neutral", evidence


def sector_context(sector: str | None, industry: str | None, stock_return_1m: float | None) -> dict | None:
    etf = sector_etf_for(sector, industry)
    if not etf:
        return None
    key = f"sector:{etf}"
    try:
        snap = CACHE.get_or_compute(key, CONTEXT_TTL, lambda: trend_snapshot(etf))
    except ProviderUnavailableError:
        return {"etf": etf, "status": "unavailable"}
    relative = None
    if stock_return_1m is not None and snap.get("return_1m_percent") is not None:
        relative = round(stock_return_1m - snap["return_1m_percent"], 2)
    return {
        "etf": etf,
        "sector": sector,
        "industry": industry,
        **snap,
        "stock_vs_sector_1m_percent": relative,
        "interpretation_basis": "ถ้าหุ้นขึ้นใกล้เคียง sector ETF แปลว่าอาจเป็นแรงของทั้งกลุ่ม ไม่ใช่ปัจจัยเฉพาะบริษัท",
    }
