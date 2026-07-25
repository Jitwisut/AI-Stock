from __future__ import annotations

from functools import lru_cache
from urllib.request import Request, urlopen

from workers.data_models import Stock


DEFAULT_UNIVERSE = [
    Stock("AAPL", "Apple Inc.", sector="Technology", market_cap=3_000_000_000_000),
    Stock("AMD", "Advanced Micro Devices, Inc.", sector="Semiconductors", market_cap=250_000_000_000),
    Stock("AMZN", "Amazon.com, Inc.", sector="Consumer Discretionary"),
    Stock("ARM", "Arm Holdings plc", sector="Semiconductors"),
    Stock("BAC", "Bank of America Corporation", sector="Financials"),
    Stock("COIN", "Coinbase Global, Inc.", sector="Financials"),
    Stock("CRM", "Salesforce, Inc.", sector="Software"),
    Stock("DIS", "The Walt Disney Company", sector="Communication Services"),
    Stock("GOOG", "Alphabet Inc. Class C", sector="Communication Services"),
    Stock("GOOGL", "Alphabet Inc. Class A", sector="Communication Services"),
    Stock("INTC", "Intel Corporation", sector="Semiconductors"),
    Stock("JPM", "JPMorgan Chase & Co.", sector="Financials"),
    Stock("LLY", "Eli Lilly and Company", sector="Healthcare"),
    Stock("MARA", "MARA Holdings, Inc.", sector="Financials"),
    Stock("MSFT", "Microsoft Corporation", sector="Technology", market_cap=3_200_000_000_000),
    Stock("MU", "Micron Technology, Inc.", sector="Semiconductors"),
    Stock("NFLX", "Netflix, Inc.", sector="Communication Services"),
    Stock("NVDA", "NVIDIA Corporation", sector="Semiconductors", market_cap=3_000_000_000_000),
    Stock("ORCL", "Oracle Corporation", sector="Software"),
    Stock("QCOM", "QUALCOMM Incorporated", sector="Semiconductors"),
    Stock("RIVN", "Rivian Automotive, Inc.", sector="Consumer Discretionary"),
    Stock("SHOP", "Shopify Inc.", sector="Technology"),
    Stock("SMCI", "Super Micro Computer, Inc.", sector="Technology"),
    Stock("SOFI", "SoFi Technologies, Inc.", sector="Financials"),
    Stock("META", "Meta Platforms, Inc.", sector="Communication Services", market_cap=1_200_000_000_000),
    Stock("TSLA", "Tesla, Inc.", sector="Consumer Discretionary", market_cap=580_000_000_000),
    Stock("UBER", "Uber Technologies, Inc.", sector="Technology"),
    Stock("AVGO", "Broadcom Inc.", sector="Semiconductors", market_cap=700_000_000_000),
    Stock("PLTR", "Palantir Technologies Inc.", sector="Software", market_cap=70_000_000_000),
    Stock("XOM", "Exxon Mobil Corporation", sector="Energy"),
    # --- ขยายชุด curated ให้ครอบคลุม ~80 ตัว (large/mid cap สภาพคล่องสูง) ---
    # เทค/ซอฟต์แวร์/เซมิ
    Stock("ADBE", "Adobe Inc.", sector="Software"),
    Stock("CSCO", "Cisco Systems, Inc.", sector="Technology"),
    Stock("IBM", "International Business Machines", sector="Technology"),
    Stock("NOW", "ServiceNow, Inc.", sector="Software"),
    Stock("INTU", "Intuit Inc.", sector="Software"),
    Stock("AMAT", "Applied Materials, Inc.", sector="Semiconductors"),
    Stock("LRCX", "Lam Research Corporation", sector="Semiconductors"),
    Stock("KLAC", "KLA Corporation", sector="Semiconductors"),
    Stock("TXN", "Texas Instruments Incorporated", sector="Semiconductors"),
    Stock("TSM", "Taiwan Semiconductor Manufacturing", sector="Semiconductors"),
    Stock("MRVL", "Marvell Technology, Inc.", sector="Semiconductors"),
    Stock("PANW", "Palo Alto Networks, Inc.", sector="Software"),
    Stock("CRWD", "CrowdStrike Holdings, Inc.", sector="Software"),
    Stock("SNOW", "Snowflake Inc.", sector="Software"),
    Stock("DDOG", "Datadog, Inc.", sector="Software"),
    Stock("NET", "Cloudflare, Inc.", sector="Software"),
    Stock("ABNB", "Airbnb, Inc.", sector="Consumer Discretionary"),
    Stock("PYPL", "PayPal Holdings, Inc.", sector="Financials"),
    # การเงิน
    Stock("V", "Visa Inc.", sector="Financials"),
    Stock("MA", "Mastercard Incorporated", sector="Financials"),
    Stock("GS", "The Goldman Sachs Group, Inc.", sector="Financials"),
    Stock("MS", "Morgan Stanley", sector="Financials"),
    Stock("WFC", "Wells Fargo & Company", sector="Financials"),
    # สุขภาพ
    Stock("UNH", "UnitedHealth Group Incorporated", sector="Healthcare"),
    Stock("JNJ", "Johnson & Johnson", sector="Healthcare"),
    Stock("MRK", "Merck & Co., Inc.", sector="Healthcare"),
    Stock("PFE", "Pfizer Inc.", sector="Healthcare"),
    Stock("ABBV", "AbbVie Inc.", sector="Healthcare"),
    Stock("TMO", "Thermo Fisher Scientific Inc.", sector="Healthcare"),
    Stock("ISRG", "Intuitive Surgical, Inc.", sector="Healthcare"),
    # อุปโภคบริโภค/ค้าปลีก
    Stock("WMT", "Walmart Inc.", sector="Consumer Staples"),
    Stock("COST", "Costco Wholesale Corporation", sector="Consumer Staples"),
    Stock("PG", "The Procter & Gamble Company", sector="Consumer Staples"),
    Stock("KO", "The Coca-Cola Company", sector="Consumer Staples"),
    Stock("PEP", "PepsiCo, Inc.", sector="Consumer Staples"),
    Stock("MCD", "McDonald's Corporation", sector="Consumer Discretionary"),
    Stock("NKE", "NIKE, Inc.", sector="Consumer Discretionary"),
    Stock("SBUX", "Starbucks Corporation", sector="Consumer Discretionary"),
    Stock("HD", "The Home Depot, Inc.", sector="Consumer Discretionary"),
    Stock("LOW", "Lowe's Companies, Inc.", sector="Consumer Discretionary"),
    # อุตสาหกรรม/พลังงาน/สื่อสาร
    Stock("CAT", "Caterpillar Inc.", sector="Industrials"),
    Stock("BA", "The Boeing Company", sector="Industrials"),
    Stock("GE", "GE Aerospace", sector="Industrials"),
    Stock("CVX", "Chevron Corporation", sector="Energy"),
    Stock("T", "AT&T Inc.", sector="Communication Services"),
    Stock("VZ", "Verizon Communications Inc.", sector="Communication Services"),
    # โมเมนตัม/หุ้นยอดนิยมสายเทรด
    Stock("MSTR", "MicroStrategy Incorporated", sector="Software"),
    Stock("HOOD", "Robinhood Markets, Inc.", sector="Financials"),
    Stock("DKNG", "DraftKings Inc.", sector="Consumer Discretionary"),
    Stock("RBLX", "Roblox Corporation", sector="Communication Services"),
    Stock("DELL", "Dell Technologies Inc.", sector="Technology"),
    Stock("ASTS", "AST SpaceMobile, Inc.", sector="Communication Services"),
    Stock("RKLB", "Rocket Lab USA, Inc.", sector="Industrials"),
    Stock("CVNA", "Carvana Co.", sector="Consumer Discretionary"),
    Stock("F", "Ford Motor Company", sector="Consumer Discretionary"),
    Stock("GM", "General Motors Company", sector="Consumer Discretionary"),
    # --- ขยายเพิ่มอีก ~60 ตัวเพื่อให้หน้าระยะยาวมีตัวเลือกกว้างขึ้น (ยังคง large/mid cap คุณภาพ) ---
    # การเงิน/ธนาคาร/ตลาดทุน
    Stock("SCHW", "The Charles Schwab Corporation", sector="Financials"),
    Stock("AXP", "American Express Company", sector="Financials"),
    Stock("C", "Citigroup Inc.", sector="Financials"),
    Stock("USB", "U.S. Bancorp", sector="Financials"),
    Stock("PNC", "The PNC Financial Services Group", sector="Financials"),
    Stock("BLK", "BlackRock, Inc.", sector="Financials"),
    Stock("SPGI", "S&P Global Inc.", sector="Financials"),
    Stock("ICE", "Intercontinental Exchange, Inc.", sector="Financials"),
    Stock("CME", "CME Group Inc.", sector="Financials"),
    Stock("FI", "Fiserv, Inc.", sector="Financials"),
    # สุขภาพ
    Stock("CVS", "CVS Health Corporation", sector="Healthcare"),
    Stock("CI", "The Cigna Group", sector="Healthcare"),
    Stock("VRTX", "Vertex Pharmaceuticals Incorporated", sector="Healthcare"),
    Stock("REGN", "Regeneron Pharmaceuticals, Inc.", sector="Healthcare"),
    Stock("GILD", "Gilead Sciences, Inc.", sector="Healthcare"),
    Stock("BMY", "Bristol-Myers Squibb Company", sector="Healthcare"),
    Stock("MDT", "Medtronic plc", sector="Healthcare"),
    Stock("SYK", "Stryker Corporation", sector="Healthcare"),
    Stock("BSX", "Boston Scientific Corporation", sector="Healthcare"),
    Stock("ELV", "Elevance Health, Inc.", sector="Healthcare"),
    # อุปโภคบริโภค/ค้าปลีก
    Stock("TGT", "Target Corporation", sector="Consumer Staples"),
    Stock("TJX", "The TJX Companies, Inc.", sector="Consumer Discretionary"),
    Stock("YUM", "Yum! Brands, Inc.", sector="Consumer Discretionary"),
    Stock("CMG", "Chipotle Mexican Grill, Inc.", sector="Consumer Discretionary"),
    Stock("ROST", "Ross Stores, Inc.", sector="Consumer Discretionary"),
    Stock("EL", "The Estee Lauder Companies Inc.", sector="Consumer Staples"),
    Stock("CL", "Colgate-Palmolive Company", sector="Consumer Staples"),
    Stock("MO", "Altria Group, Inc.", sector="Consumer Staples"),
    Stock("PM", "Philip Morris International Inc.", sector="Consumer Staples"),
    Stock("BKNG", "Booking Holdings Inc.", sector="Consumer Discretionary"),
    Stock("MAR", "Marriott International, Inc.", sector="Consumer Discretionary"),
    Stock("HLT", "Hilton Worldwide Holdings Inc.", sector="Consumer Discretionary"),
    Stock("ORLY", "O'Reilly Automotive, Inc.", sector="Consumer Discretionary"),
    Stock("EBAY", "eBay Inc.", sector="Consumer Discretionary"),
    # อุตสาหกรรม
    Stock("HON", "Honeywell International Inc.", sector="Industrials"),
    Stock("MMM", "3M Company", sector="Industrials"),
    Stock("LMT", "Lockheed Martin Corporation", sector="Industrials"),
    Stock("RTX", "RTX Corporation", sector="Industrials"),
    Stock("NOC", "Northrop Grumman Corporation", sector="Industrials"),
    Stock("DE", "Deere & Company", sector="Industrials"),
    Stock("UNP", "Union Pacific Corporation", sector="Industrials"),
    Stock("UPS", "United Parcel Service, Inc.", sector="Industrials"),
    Stock("FDX", "FedEx Corporation", sector="Industrials"),
    Stock("ETN", "Eaton Corporation plc", sector="Industrials"),
    # พลังงาน/สาธารณูปโภค/วัสดุ
    Stock("COP", "ConocoPhillips", sector="Energy"),
    Stock("SLB", "Schlumberger Limited", sector="Energy"),
    Stock("EOG", "EOG Resources, Inc.", sector="Energy"),
    Stock("NEE", "NextEra Energy, Inc.", sector="Utilities"),
    Stock("DUK", "Duke Energy Corporation", sector="Utilities"),
    Stock("SO", "The Southern Company", sector="Utilities"),
    Stock("LIN", "Linde plc", sector="Materials"),
    Stock("APD", "Air Products and Chemicals, Inc.", sector="Materials"),
    Stock("SHW", "The Sherwin-Williams Company", sector="Materials"),
    # เทค/ซอฟต์แวร์/เซมิเพิ่มเติม
    Stock("WDAY", "Workday, Inc.", sector="Software"),
    Stock("TEAM", "Atlassian Corporation", sector="Software"),
    Stock("ZS", "Zscaler, Inc.", sector="Software"),
    Stock("FTNT", "Fortinet, Inc.", sector="Software"),
    Stock("ADSK", "Autodesk, Inc.", sector="Software"),
    Stock("CDNS", "Cadence Design Systems, Inc.", sector="Software"),
    Stock("SNPS", "Synopsys, Inc.", sector="Software"),
    Stock("HPQ", "HP Inc.", sector="Technology"),
    Stock("ON", "ON Semiconductor Corporation", sector="Semiconductors"),
    Stock("MPWR", "Monolithic Power Systems, Inc.", sector="Semiconductors"),
    # สื่อสาร/REIT
    Stock("CMCSA", "Comcast Corporation", sector="Communication Services"),
    Stock("TMUS", "T-Mobile US, Inc.", sector="Communication Services"),
    Stock("EA", "Electronic Arts Inc.", sector="Communication Services"),
    Stock("AMT", "American Tower Corporation", sector="Real Estate"),
    Stock("PLD", "Prologis, Inc.", sector="Real Estate"),
    Stock("EQIX", "Equinix, Inc.", sector="Real Estate"),
    Stock("O", "Realty Income Corporation", sector="Real Estate"),
]

SURGE_UNIVERSE = [
    # หุ้นเล็ก/low-float ที่มักโผล่ในกลุ่ม top movers ระยะสั้น
    # ใช้เฉพาะหน้า "หุ้นซิ่ง" เพื่อไม่ให้ dashboard หลักถูกครอบด้วยหุ้นเสี่ยงสูง
    Stock("MTEN", "Mingteng International Corporation Inc.", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("CHAI", "Core AI Holdings, Inc.", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("AZI", "Autozi Internet Technology (Global) Ltd.", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("VSME", "VS Media Holdings Limited", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("DSY", "Big Tree Cloud Holdings Limited", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("PPCB", "Propanc Biopharma, Inc.", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("SUNE", "SUNation Energy, Inc.", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("CUPR", "Cuprina Holdings (Cayman) Limited", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("HUBC", "Hub Cyber Security Ltd.", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("HOLO", "MicroCloud Hologram Inc.", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("MLGO", "MicroAlgo Inc.", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("TOP", "TOP Financial Group Limited", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("HKD", "AMTD Digital Inc.", exchange="NYSE", sector="High Risk Mover"),
    Stock("LUCY", "Innovative Eyewear, Inc.", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("SINT", "SiNtx Technologies, Inc.", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("PRZO", "ParaZero Technologies Ltd.", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("BENF", "Beneficient", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("WLGS", "Wang & Lee Group, Inc.", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("BNAI", "Brand Engagement Network Inc.", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("LGVN", "Longeveron Inc.", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("PHIO", "Phio Pharmaceuticals Corp.", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("GCTK", "GlucoTrack, Inc.", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("CYN", "Cyngn Inc.", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("WKEY", "WISeKey International Holding AG", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("MULN", "Mullen Automotive, Inc.", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("JZXN", "JZXN", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("BURU", "BURU", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("CTNT", "CTNT", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("NUKK", "NUKK", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("BFRG", "BFRG", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("NXU", "NXU", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("VVPR", "VVPR", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("KXIN", "KXIN", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("SXTC", "SXTC", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("TIRX", "TIRX", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("MEGL", "MEGL", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("HUDI", "HUDI", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("WBUY", "WBUY", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("ZAPP", "ZAPP", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("SRM", "SRM", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("IVP", "IVP", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("GSIW", "GSIW", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("GDTC", "GDTC", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("LASE", "LASE", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("DATS", "DATS", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("DUO", "DUO", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("SGBX", "SGBX", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("QMMM", "QMMM", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("BTOG", "BTOG", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("LGMK", "LGMK", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("GRRR", "GRRR", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("WINT", "WINT", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("KALA", "KALA", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("NBY", "NBY", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("DRMA", "DRMA", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("NXL", "NXL", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("REVB", "REVB", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("TENX", "TENX", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("GOVX", "GOVX", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("APVO", "APVO", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("CING", "CING", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("ZVSA", "ZVSA", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("SNGX", "SNGX", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("HSDT", "HSDT", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("GNPX", "GNPX", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("RVSN", "RVSN", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("LIDR", "LIDR", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("PRST", "PRST", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("MARK", "MARK", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("FFIE", "FFIE", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("GNS", "GNS", exchange="NYSE", sector="High Risk Mover"),
    Stock("JAGX", "JAGX", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("BDRX", "BDRX", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("TPST", "TPST", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("KULR", "KULR", exchange="NYSE American", sector="High Risk Mover"),
    Stock("WULF", "WULF", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("CIFR", "CIFR", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("BTBT", "BTBT", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("HIVE", "HIVE", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("APLD", "APLD", exchange="NASDAQ", sector="High Risk Mover"),
    Stock("RUM", "RUM", exchange="NASDAQ", sector="High Risk Mover"),
]


NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

EXCHANGE_NAMES = {
    "A": "NYSE American",
    "N": "NYSE",
    "P": "NYSE Arca",
    "Q": "NASDAQ",
    "V": "Investors Exchange",
    "Z": "Cboe BZX",
}


def fetch_stock_universe(mode: str = "default", limit: int = 0) -> list[Stock]:
    if mode == "all":
        stocks = fetch_full_us_stock_universe()
    else:
        stocks = DEFAULT_UNIVERSE

    if limit > 0:
        return stocks[:limit]
    return stocks


def fetch_surge_stock_universe(
    extra_symbols: list[str] | None = None,
    *,
    mode: str = "all",
    limit: int = 0,
) -> list[Stock]:
    """Universe สำหรับหน้า "หุ้นซิ่ง" โดยเฉพาะ.

    ค่า default ใช้โหมด all เพื่อไล่ทั้งตลาด US แล้วจัดอันดับจาก % change
    เหมาะกับการหา top movers แบบ CHAI/MTEN/VSME ที่อาจไม่ได้อยู่ใน curated list.
    ถ้าดึงรายชื่อทั้งตลาดไม่ได้หรืออยากสแกนเร็ว ให้ตั้ง mode="curated".
    """
    stocks: list[Stock] = []
    for symbol in extra_symbols or []:
        normalized = symbol.strip().upper()
        if normalized:
            stocks.append(
                Stock(
                    normalized,
                    normalized,
                    exchange="US",
                    sector="High Risk Mover",
                ),
            )
    stocks.extend(SURGE_UNIVERSE)
    if mode == "all":
        stocks.extend(fetch_full_us_stock_universe())
    elif mode not in {"curated", "watchlist"}:
        stocks.extend(fetch_full_us_stock_universe())

    deduped = _dedupe_stocks(stocks)
    if limit > 0:
        return deduped[:limit]
    return deduped


@lru_cache
def fetch_full_us_stock_universe() -> list[Stock]:
    stocks: list[Stock] = []
    for fetcher in (_fetch_nasdaq_listed, _fetch_other_listed):
        try:
            stocks.extend(fetcher())
        except Exception:
            continue

    if not stocks:
        return DEFAULT_UNIVERSE

    unique: dict[str, Stock] = {}
    for stock in stocks:
        unique.setdefault(stock.symbol, stock)
    return sorted(unique.values(), key=lambda item: item.symbol)


def _fetch_nasdaq_listed() -> list[Stock]:
    rows = _read_pipe_file(NASDAQ_LISTED_URL)
    stocks: list[Stock] = []
    for row in rows:
        symbol = row.get("Symbol", "").strip()
        name = row.get("Security Name", symbol)
        if not symbol or symbol == "File Creation Time":
            continue
        if row.get("Test Issue", "N") == "Y" or row.get("ETF", "N") == "Y":
            continue
        if not _is_common_stock(symbol, name):
            continue
        stocks.append(
            Stock(
                symbol=symbol,
                name=name,
                exchange="NASDAQ",
                is_active=True,
            )
        )
    return stocks


def _fetch_other_listed() -> list[Stock]:
    rows = _read_pipe_file(OTHER_LISTED_URL)
    stocks: list[Stock] = []
    for row in rows:
        symbol = row.get("ACT Symbol", "").strip()
        name = row.get("Security Name", symbol)
        if not symbol or symbol == "File Creation Time":
            continue
        if row.get("Test Issue", "N") == "Y" or row.get("ETF", "N") == "Y":
            continue
        if not _is_common_stock(symbol, name):
            continue
        exchange_code = row.get("Exchange", "").strip()
        stocks.append(
            Stock(
                symbol=symbol,
                name=name,
                exchange=EXCHANGE_NAMES.get(exchange_code, exchange_code or "US"),
                is_active=True,
            )
        )
    return stocks


def _read_pipe_file(url: str) -> list[dict[str, str]]:
    request = Request(url, headers={"User-Agent": "stock-radar-ai/0.1"})
    with urlopen(request, timeout=20) as response:
        lines = response.read().decode("utf-8", errors="replace").splitlines()

    header = lines[0].split("|")
    rows = []
    for line in lines[1:]:
        values = line.split("|")
        if len(values) != len(header):
            continue
        rows.append(dict(zip(header, values)))
    return rows


def _dedupe_stocks(stocks: list[Stock]) -> list[Stock]:
    unique: dict[str, Stock] = {}
    for stock in stocks:
        unique.setdefault(stock.symbol.upper(), stock)
    return list(unique.values())


def _is_common_stock(symbol: str, name: str) -> bool:
    lowered = name.lower()
    blocked_terms = (
        "warrant",
        "right",
        "unit",
        "preferred",
        "depositary",
        "note",
        "bond",
        "debenture",
        "etn",
        "closed end fund",
        "closed-end fund",
        "beneficial interest",
    )
    if any(term in lowered for term in blocked_terms):
        return False
    if symbol.endswith(("W", "R", "U")) and "common" not in lowered:
        return False
    return True
