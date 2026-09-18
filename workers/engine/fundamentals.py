"""ข้อมูลพื้นฐาน + quote จาก Nasdaq — ทุก field มี source / as_of / fetched_at.

field ที่ provider ไม่มีจะเป็น value=None (หน้าเว็บแสดง N/A) ห้ามเติมค่าเอง
ค่าที่ "คำนวณ" จากข้อมูลดิบ (เช่น P/E, ROE) ระบุสูตรใน source ให้ตรวจสอบได้
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime

from workers.engine import market_calendar as cal
from workers.engine import sources
from workers.engine.runtime import CACHE, ProviderUnavailableError, iso, utcnow

logger = logging.getLogger(__name__)

FUNDAMENTALS_TTL = 12 * 3600.0
QUOTE_TTL = 30.0


def field(value, source: str, as_of: str | None = None, fetched_at: datetime | None = None, note: str | None = None) -> dict:
    item = {"value": value, "source": source, "as_of": as_of, "fetched_at": iso(fetched_at or utcnow())}
    if note:
        item["note"] = note
    return item


def na(source: str, reason: str) -> dict:
    return {"value": None, "source": source, "as_of": None, "fetched_at": iso(utcnow()), "note": reason}


def _money(text) -> float | None:
    if text is None:
        return None
    raw = str(text).strip()
    if raw in {"", "N/A", "--", "-", "NA"}:
        return None
    negative = raw.startswith("(") or raw.startswith("-") or raw.startswith("$-") or raw.startswith("-$")
    cleaned = re.sub(r"[^0-9.]", "", raw)
    if not cleaned:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return -value if negative else value


def _parse_et_timestamp(text: str | None) -> datetime | None:
    if not text:
        return None
    cleaned = text.replace(" ET", "").strip()
    for fmt in ("%b %d, %Y %I:%M %p", "%b %d, %Y"):
        try:
            parsed = datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
        if fmt == "%b %d, %Y":
            return datetime.combine(parsed.date(), cal.REGULAR_CLOSE, tzinfo=cal.NY)
        return parsed.replace(tzinfo=cal.NY)  # Nasdaq ระบุเวลาเป็น ET — ไม่ใช่ UTC
    return None


# ---------------------------------------------------------------------------
# Quote (ราคาล่าสุด)
# ---------------------------------------------------------------------------
def get_quote(symbol: str) -> dict:
    return CACHE.get_or_compute(f"quote:{symbol.upper()}", QUOTE_TTL, lambda: _fetch_quote(symbol))


def _fetch_quote(symbol: str) -> dict:
    fetched = utcnow()
    payload, asset_class = sources.nasdaq_asset_json("quote/{symbol}/info", symbol)
    data = payload.get("data") or {}
    primary = data.get("primaryData") or {}
    if not primary.get("lastSalePrice"):
        raise ProviderUnavailableError(sources.NASDAQ, f"no quote for {symbol}")
    traded_at = _parse_et_timestamp(primary.get("lastTradeTimestamp"))
    realtime = bool(primary.get("isRealTime"))
    src = "nasdaq_quote_info"
    ts = iso(traded_at)
    return {
        "symbol": symbol.upper(),
        "company_name": data.get("companyName"),
        "exchange": data.get("exchange"),
        "asset_class": asset_class,
        "stock_type": data.get("stockType"),
        "price": field(_money(primary.get("lastSalePrice")), src, ts, fetched),
        "change": field(_money(primary.get("netChange")), src, ts, fetched),
        "change_percent": field(_money(primary.get("percentageChange")), src, ts, fetched),
        "volume": field(_money(primary.get("volume")), src, ts, fetched, note="cumulative session volume"),
        "market_status": data.get("marketStatus"),
        "is_realtime": realtime,
        "delay_note": "Nasdaq real-time (Nasdaq Last Sale)" if realtime else "Delayed quote (Nasdaq; typically 15 min)",
        "market_timestamp": ts,
        "fetched_at": iso(fetched),
        "currency": "USD",
    }


# ---------------------------------------------------------------------------
# Fundamentals
# ---------------------------------------------------------------------------
def get_fundamentals(symbol: str, price: float | None = None) -> dict:
    """คืน dict ของ field ข้อมูลพื้นฐาน (แคช 12 ชม.) — price ใช้คำนวณ P/E ณ ตอนเรียก."""
    raw = CACHE.get_or_compute(f"fund:{symbol.upper()}", FUNDAMENTALS_TTL, lambda: _fetch_fundamentals_raw(symbol))
    return _derive(symbol, raw, price)


def _safe(fn, name: str, symbol: str):
    try:
        return fn()
    except ProviderUnavailableError as exc:
        logger.info("fundamental_part_unavailable", extra={"event": "api_error", "symbol": symbol, "part": name, "error": str(exc)})
        return None


def _fetch_fundamentals_raw(symbol: str) -> dict:
    from concurrent.futures import ThreadPoolExecutor

    fetched = utcnow()
    upper = symbol.upper()
    parts = {
        "summary": lambda: sources.nasdaq_asset_json("quote/{symbol}/summary", symbol)[0],
        "annual": lambda: sources.nasdaq_json(f"company/{upper}/financials", {"frequency": 1}, timeout=20),
        "eps": lambda: sources.nasdaq_json(f"quote/{upper}/eps"),
        "earnings": lambda: sources.nasdaq_json(f"analyst/{upper}/earnings-date"),
    }
    # 4 endpoint อิสระต่อกัน — ยิงพร้อมกันแทนการรอทีละตัว
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {name: pool.submit(_safe, fn, name, symbol) for name, fn in parts.items()}
        raw = {name: future.result() for name, future in futures.items()}
    if not any(raw.values()):
        raise ProviderUnavailableError(sources.NASDAQ, f"no fundamental data for {symbol}")
    return {**raw, "fetched_at": fetched}


def _table(annual: dict | None, name: str) -> tuple[list[str], dict[str, list[float | None]]]:
    table = ((annual or {}).get("data") or {}).get(name) or {}
    headers = table.get("headers") or {}
    periods = [headers.get(f"value{i}") for i in range(2, 6) if headers.get(f"value{i}")]
    rows: dict[str, list[float | None]] = {}
    for row in table.get("rows") or []:
        label = str(row.get("value1") or "").strip()
        if label:
            # Nasdaq financials หน่วยเป็นพันดอลลาร์
            values = [_money(row.get(f"value{i}")) for i in range(2, 2 + len(periods))]
            rows[label] = [value * 1000 if value is not None else None for value in values]
    return periods, rows


def _period_iso(text: str | None) -> str | None:
    if not text:
        return None
    try:
        return datetime.strptime(text, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return text


def _growth(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old <= 0:
        return None  # ฐานติดลบ/ศูนย์ → % growth ไม่มีความหมาย ไม่รายงาน
    return (new - old) / old * 100


def _derive(symbol: str, raw: dict, price: float | None) -> dict:
    fetched = raw.get("fetched_at") or utcnow()
    out: dict[str, dict] = {}
    summary = ((raw.get("summary") or {}).get("data") or {}).get("summaryData") or {}
    s_src = "nasdaq_summary"

    def sval(key):
        return (summary.get(key) or {}).get("value")

    out["sector"] = field(sval("Sector"), s_src, None, fetched) if sval("Sector") else na(s_src, "not provided")
    out["industry"] = field(sval("Industry"), s_src, None, fetched) if sval("Industry") else na(s_src, "not provided")
    market_cap = _money(sval("MarketCap"))
    out["market_cap"] = field(market_cap, s_src, None, fetched) if market_cap else na(s_src, "not provided")
    avg_volume = _money(sval("AverageVolume"))
    out["average_volume_provider"] = (
        field(avg_volume, s_src, None, fetched, note="Nasdaq 'Average Volume' (provider-defined window)")
        if avg_volume else na(s_src, "not provided")
    )
    yield_pct = _money(sval("Yield"))
    out["dividend_yield_percent"] = (
        field(yield_pct, s_src, None, fetched) if yield_pct is not None else na(s_src, "no dividend data")
    )
    out["annualized_dividend"] = (
        field(_money(sval("AnnualizedDividend")), s_src, None, fetched)
        if _money(sval("AnnualizedDividend")) is not None else na(s_src, "no dividend data")
    )

    # ---------- annual statements ----------
    f_src = "nasdaq_financials_annual"
    periods, income = _table(raw.get("annual"), "incomeStatementTable")
    _p2, balance = _table(raw.get("annual"), "balanceSheetTable")
    _p3, cashflow = _table(raw.get("annual"), "cashFlowTable")
    fy = _period_iso(periods[0]) if periods else None
    revenue = income.get("Total Revenue") or []
    net_income = income.get("Net Income") or []

    def at(values, i):
        return values[i] if len(values) > i else None

    out["fiscal_period_end"] = field(fy, f_src, fy, fetched) if fy else na(f_src, "no annual statements")
    out["revenue"] = field(at(revenue, 0), f_src, fy, fetched) if at(revenue, 0) is not None else na(f_src, "not provided")
    growth0 = _growth(at(revenue, 0), at(revenue, 1))
    growth1 = _growth(at(revenue, 1), at(revenue, 2))
    out["revenue_growth_percent"] = (
        field(round(growth0, 2), f_src, fy, fetched, note="latest fiscal year vs prior fiscal year")
        if growth0 is not None else na(f_src, "needs 2 fiscal years with positive base")
    )
    out["revenue_growth_prior_year_percent"] = (
        field(round(growth1, 2), f_src, _period_iso(periods[1]) if len(periods) > 1 else None, fetched)
        if growth1 is not None else na(f_src, "needs 3 fiscal years")
    )
    ni_growth = _growth(at(net_income, 0), at(net_income, 1))
    out["earnings_growth_percent"] = (
        field(round(ni_growth, 2), f_src, fy, fetched, note="net income, latest FY vs prior FY")
        if ni_growth is not None else na(f_src, "prior-year net income not positive or missing")
    )
    out["net_income"] = field(at(net_income, 0), f_src, fy, fetched) if at(net_income, 0) is not None else na(f_src, "not provided")
    if at(revenue, 0) and at(net_income, 0) is not None:
        out["profit_margin_percent"] = field(round(at(net_income, 0) / at(revenue, 0) * 100, 2), f"computed: net income / revenue ({f_src})", fy, fetched)
    else:
        out["profit_margin_percent"] = na(f_src, "revenue or net income missing")
    equity = at(balance.get("Total Equity") or balance.get("Stock Holders Equity") or [], 0)
    if equity and equity > 0 and at(net_income, 0) is not None:
        out["roe_percent"] = field(round(at(net_income, 0) / equity * 100, 2), f"computed: net income / total equity ({f_src})", fy, fetched)
    else:
        out["roe_percent"] = na(f_src, "equity missing or not positive")
    debt_parts = [at(balance.get("Long-Term Debt") or [], 0), at(balance.get("Short-Term Debt / Current Portion of Long-Term Debt") or [], 0)]
    if equity and equity > 0 and any(part is not None for part in debt_parts):
        debt = sum(part or 0 for part in debt_parts)
        out["debt_to_equity"] = field(round(debt / equity, 3), f"computed: (short+long-term debt) / total equity ({f_src})", fy, fetched)
    else:
        out["debt_to_equity"] = na(f_src, "debt or positive equity missing")
    ocf = at(cashflow.get("Net Cash Flow-Operating") or [], 0)
    capex = at(cashflow.get("Capital Expenditures") or [], 0)
    if ocf is not None and capex is not None:
        out["free_cash_flow"] = field(ocf - abs(capex), f"computed: operating cash flow - capex ({f_src})", fy, fetched)
    else:
        out["free_cash_flow"] = na(f_src, "cash flow rows missing")
    if fy:
        age_days = (date.today() - date.fromisoformat(fy)).days if re.match(r"\d{4}-\d{2}-\d{2}", fy) else None
        out["financials_age_days"] = field(age_days, "computed", fy, fetched)

    # ---------- EPS / valuation ----------
    e_src = "nasdaq_eps (Zacks; may be adjusted EPS)"
    eps_rows = ((raw.get("eps") or {}).get("data") or {}).get("earningsPerShare") or []
    reported = [row for row in eps_rows if row.get("type") == "PreviousQuarter" and row.get("earnings") is not None]
    upcoming = [row for row in eps_rows if row.get("type") == "UpcomingQuarter" and row.get("consensus") is not None]
    if len(reported) >= 4:
        ttm = sum(float(row["earnings"]) for row in reported[-4:])
        out["eps_ttm"] = field(round(ttm, 3), f"computed: sum of last 4 reported quarters ({e_src})", reported[-1].get("period"), fetched)
        if price and ttm > 0:
            out["pe_ratio"] = field(round(price / ttm, 2), "computed: last price / EPS TTM", reported[-1].get("period"), fetched)
        else:
            out["pe_ratio"] = na("computed", "EPS TTM not positive (P/E not meaningful)" if ttm <= 0 else "price unavailable")
    else:
        out["eps_ttm"] = na(e_src, "fewer than 4 reported quarters")
        out["pe_ratio"] = na("computed", "EPS TTM unavailable")
    if len(upcoming) >= 4 and price:
        fwd = sum(float(row["consensus"]) for row in upcoming[:4])
        out["forward_pe"] = (
            field(round(price / fwd, 2), "computed: price / next 4 quarters consensus EPS", upcoming[0].get("period"), fetched)
            if fwd > 0 else na("computed", "forward EPS not positive")
        )
    else:
        out["forward_pe"] = na(e_src, "provider gives fewer than 4 forward quarters of consensus")
    if reported:
        last = reported[-1]
        consensus = last.get("consensus")
        surprise = None
        if consensus not in (None, 0):
            surprise = (float(last["earnings"]) - float(consensus)) / abs(float(consensus)) * 100
        out["last_quarter_eps"] = field(
            {"period": last.get("period"), "actual": last.get("earnings"), "consensus": consensus,
             "surprise_percent": round(surprise, 2) if surprise is not None else None},
            e_src, last.get("period"), fetched,
        )
    else:
        out["last_quarter_eps"] = na(e_src, "no reported quarters")
    if len(reported) >= 2:
        out["eps_quarterly_trend"] = field(
            [{"period": r.get("period"), "actual": r.get("earnings")} for r in reported[-4:]], e_src, reported[-1].get("period"), fetched
        )

    # ---------- earnings date ----------
    text = (((raw.get("earnings") or {}).get("data") or {}).get("reportText")) or ""
    match = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", text)
    if match:
        day = datetime.strptime(match.group(1), "%m/%d/%Y").date()
        estimated = "estimated" in text.lower()
        out["next_earnings_date"] = field(
            day.isoformat(), "nasdaq_earnings_date (Zacks)", None, fetched,
            note="estimated by Zacks algorithm" if estimated else "announced",
        )
    elif "TBA" in text:
        out["next_earnings_date"] = na("nasdaq_earnings_date (Zacks)", "provider reports TBA (date not yet announced)")
    else:
        out["next_earnings_date"] = na("nasdaq_earnings_date", "not provided by provider")

    required = ["market_cap", "revenue_growth_percent", "earnings_growth_percent", "profit_margin_percent",
                "roe_percent", "debt_to_equity", "free_cash_flow", "pe_ratio"]
    present = sum(1 for key in required if out.get(key, {}).get("value") is not None)
    out["_coverage"] = {"present": present, "required": len(required), "ratio": round(present / len(required), 2),
                        "missing": [key for key in required if out.get(key, {}).get("value") is None]}
    return out
