from __future__ import annotations

import json
import csv
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import StringIO
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from workers.config import get_settings
from workers.data_models import MarketSnapshot

# Cache snapshot ระดับ module เพื่อลดการยิงซ้ำไป provider เดียวกัน (โดยเฉพาะ Yahoo
# ที่ rate-limit แรง) — TTL สั้นพอให้ข้อมูล daily ยังใหม่ แต่กัน 429 จากการเรียกถี่
_SNAPSHOT_CACHE: dict[str, tuple[float, MarketSnapshot]] = {}
_SNAPSHOT_CACHE_TTL = 120.0  # วินาที


def _http_get_with_retry(request: Request, *, timeout: int = 15, retries: int = 3) -> bytes:
    """ยิง HTTP request พร้อม retry + exponential backoff เมื่อเจอ 429/5xx."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except HTTPError as exc:
            last_exc = exc
            # 429 (rate limit) หรือ 5xx → รอแล้วลองใหม่; error อื่น (404 ฯลฯ) เลิกทันที
            if exc.code == 429 or 500 <= exc.code < 600:
                if attempt < retries - 1:
                    time.sleep(0.8 * (2**attempt))  # 0.8s, 1.6s, 3.2s
                    continue
            raise
        except (URLError, TimeoutError) as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(0.8 * (2**attempt))
                continue
            raise
    if last_exc:
        raise last_exc
    raise MarketDataUnavailableError("HTTP request failed without exception")

# Nasdaq's public quote API rejects non-browser User-Agents with a JS/Cloudflare
# challenge, so requests must look like a real browser.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


class MarketDataUnavailableError(RuntimeError):
    pass


# Nasdaq แยก endpoint ตามชนิดสินทรัพย์: หุ้นสามัญใช้ assetclass=stocks ส่วน ETF
# (VOO, SPY, QQQ) ต้องใช้ assetclass=etf — ถ้าใส่ผิดชนิด API จะตอบ HTTP 200
# พร้อม data=null ไม่ใช่ error จึงต้องไล่ลองทีละชนิดแล้วดูว่ามี data ไหม
_NASDAQ_ASSET_CLASSES = ("stocks", "etf")
_NASDAQ_ASSET_CLASS_CACHE: dict[str, str] = {}


def _nasdaq_asset_class_order(symbol: str) -> list[str]:
    """ลำดับ assetclass ที่จะลอง — เอาชนิดที่เคยสำเร็จของ symbol นี้ขึ้นก่อน."""
    cached = _NASDAQ_ASSET_CLASS_CACHE.get(symbol.upper())
    if not cached:
        return list(_NASDAQ_ASSET_CLASSES)
    return [cached] + [item for item in _NASDAQ_ASSET_CLASSES if item != cached]


def fetch_nasdaq_json(url: str, symbol: str, params: dict, *, timeout: int = 15) -> dict:
    """ยิง Nasdaq public API โดยไล่ assetclass จนกว่าจะได้ payload ที่มี data.

    คืน payload ล่าสุดถ้าไม่มีชนิดไหนสำเร็จ เพื่อให้ผู้เรียกโยน error ของตัวเอง
    (เช่น "No Nasdaq daily history found for X") ตามเดิม.
    """
    payload: dict = {}
    for asset_class in _nasdaq_asset_class_order(symbol):
        request = Request(
            f"{url}?{urlencode({**params, 'assetclass': asset_class})}",
            headers=_BROWSER_HEADERS,
        )
        payload = json.loads(_http_get_with_retry(request, timeout=timeout).decode("utf-8"))
        if payload.get("data"):
            # จำเฉพาะชนิดที่สำเร็จ (ไม่ cache ความล้มเหลว เพราะอาจเป็นแค่ error ชั่วคราว)
            _NASDAQ_ASSET_CLASS_CACHE[symbol.upper()] = asset_class
            return payload
    return payload


class MarketDataProvider:
    name = "unknown"

    def get_snapshot(self, symbol: str) -> MarketSnapshot:
        raise NotImplementedError

    def get_snapshots(self, symbols: list[str]) -> dict[str, MarketSnapshot]:
        # ดึงแบบขนาน (per-symbol providers อย่าง Nasdaq/Yahoo/Stooq) เพื่อให้
        # การสแกน universe ~80 ตัวเสร็จในไม่กี่วินาทีแทนการรอทีละตัว
        if not symbols:
            return {}
        workers = max(1, min(get_settings().scan_workers, len(symbols)))
        snapshots: dict[str, MarketSnapshot] = {}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(self.get_snapshot, symbol): symbol for symbol in symbols
            }
            for future in as_completed(futures):
                try:
                    snapshot = future.result()
                except Exception:
                    continue
                snapshots[futures[future].upper()] = snapshot
        return snapshots


class MockMarketDataProvider(MarketDataProvider):
    name = "mock"

    _snapshots = {
        "AAPL": MarketSnapshot("AAPL", 201.2, 204.0, 206.7, 205.3, 207.5, 78_000_000, 58_000_000),
        "AMD": MarketSnapshot("AMD", 162.4, 169.0, 170.3, 171.0, 168.6, 93_000_000, 29_000_000),
        "MSFT": MarketSnapshot("MSFT", 468.1, 470.2, 472.0, 473.0, 475.5, 31_000_000, 25_000_000),
        "NVDA": MarketSnapshot("NVDA", 142.8, 151.2, 153.9, 154.8, 150.1, 220_000_000, 68_000_000),
        "TSLA": MarketSnapshot("TSLA", 182.3, 193.8, 194.1, 195.4, 191.0, 185_000_000, 62_000_000),
        "META": MarketSnapshot("META", 506.0, 512.5, 514.0, 515.0, 520.0, 18_000_000, 16_500_000),
        "AVGO": MarketSnapshot("AVGO", 1720.0, 1785.0, 1792.4, 1800.0, 1815.0, 7_400_000, 5_100_000),
        "PLTR": MarketSnapshot("PLTR", 27.8, 30.2, 30.7, 31.0, 29.9, 122_000_000, 44_000_000),
    }

    def get_snapshot(self, symbol: str) -> MarketSnapshot:
        snapshot = self._snapshots.get(
            symbol,
            MarketSnapshot(symbol, 100.0, 100.0, 100.0, 100.0, 100.0, 1_000_000, 1_000_000),
        )
        return _with_data_source(snapshot, self.name)


class YahooMarketDataProvider(MarketDataProvider):
    name = "yahoo_quote"
    quote_url = "https://query1.finance.yahoo.com/v7/finance/quote"

    def get_snapshot(self, symbol: str) -> MarketSnapshot:
        params = urlencode({"symbols": symbol})
        request = Request(
            f"{self.quote_url}?{params}",
            headers={"User-Agent": "stock-radar-ai/0.1"},
        )
        with urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))

        results = payload.get("quoteResponse", {}).get("result", [])
        if not results:
            raise ValueError(f"No Yahoo Finance quote found for {symbol}")

        return _yahoo_quote_to_market_snapshot(results[0], default_symbol=symbol)

    def get_snapshots(self, symbols: list[str]) -> dict[str, MarketSnapshot]:
        snapshots: dict[str, MarketSnapshot] = {}
        normalized = [symbol.upper() for symbol in symbols if symbol]
        for start in range(0, len(normalized), 100):
            chunk = normalized[start : start + 100]
            if not chunk:
                continue
            params = urlencode({"symbols": ",".join(chunk)})
            request = Request(
                f"{self.quote_url}?{params}",
                headers={"User-Agent": "stock-radar-ai/0.1"},
            )
            with urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))

            for quote in payload.get("quoteResponse", {}).get("result", []):
                symbol = (quote.get("symbol") or "").upper()
                if not symbol:
                    continue
                try:
                    snapshot = _yahoo_quote_to_market_snapshot(quote, default_symbol=symbol)
                except ValueError:
                    continue
                snapshots[symbol] = snapshot
        return snapshots


class YahooChartMarketDataProvider(MarketDataProvider):
    name = "yahoo_chart"
    chart_url = "https://query1.finance.yahoo.com/v8/finance/chart"

    def get_snapshot(self, symbol: str) -> MarketSnapshot:
        request = Request(
            f"{self.chart_url}/{symbol}?{urlencode({'range': '1mo', 'interval': '1d'})}",
            headers=_BROWSER_HEADERS,
        )
        payload = json.loads(_http_get_with_retry(request, timeout=15).decode("utf-8"))

        results = payload.get("chart", {}).get("result", [])
        if not results:
            raise ValueError(f"No Yahoo Finance chart found for {symbol}")

        chart = results[0]
        meta = chart.get("meta", {})
        quote = chart.get("indicators", {}).get("quote", [{}])[0]
        closes = quote.get("close") or []
        highs = quote.get("high") or []
        volumes = quote.get("volume") or []

        points = [
            {
                "close": closes[index],
                "high": highs[index],
                "volume": volumes[index],
            }
            for index in range(min(len(closes), len(highs), len(volumes)))
            if closes[index] is not None and highs[index] is not None and volumes[index] is not None
        ]
        if len(points) < 2:
            raise ValueError(f"Not enough Yahoo Finance chart history for {symbol}")

        recent = points[-1]
        previous = points[-2]
        previous_points = points[-21:-1]
        latest_price = meta.get("regularMarketPrice") or recent["close"]
        prior_high = max(point["high"] for point in previous_points)
        average_volume = int(
            sum(point["volume"] for point in previous_points) / max(1, len(previous_points))
        )

        return MarketSnapshot(
            symbol=symbol,
            previous_close=float(previous["close"]),
            latest_price=float(latest_price),
            premarket_price=float(latest_price),
            day_high=float(recent["high"]),
            prior_high=float(prior_high),
            volume=int(recent["volume"]),
            average_volume_20d=max(1, average_volume),
            updated_at=datetime.now(timezone.utc),
            data_source=self.name,
        )


class StooqMarketDataProvider(MarketDataProvider):
    name = "stooq"
    daily_url = "https://stooq.com/q/d/l/"

    def get_snapshot(self, symbol: str) -> MarketSnapshot:
        stooq_symbol = f"{symbol.lower()}.us"
        params = urlencode({"s": stooq_symbol, "i": "d"})
        request = Request(
            f"{self.daily_url}?{params}",
            headers={"User-Agent": "stock-radar-ai/0.1"},
        )
        with urlopen(request, timeout=15) as response:
            content = response.read().decode("utf-8")

        rows = [
            row
            for row in csv.DictReader(StringIO(content))
            if row.get("Close") not in {None, "", "N/D"}
        ]
        if len(rows) < 2:
            raise ValueError(f"No Stooq daily history found for {symbol}")

        recent = rows[-1]
        previous = rows[-2]
        previous_rows = rows[-21:-1]
        prior_high = max(_float(row["High"]) for row in previous_rows if row.get("High"))
        average_volume = int(
            sum(_float(row["Volume"]) for row in previous_rows if row.get("Volume"))
            / max(1, len(previous_rows))
        )

        return MarketSnapshot(
            symbol=symbol,
            previous_close=_float(previous["Close"]),
            latest_price=_float(recent["Close"]),
            premarket_price=_float(recent["Close"]),
            day_high=_float(recent["High"]),
            prior_high=prior_high,
            volume=int(_float(recent["Volume"])),
            average_volume_20d=max(1, average_volume),
            updated_at=datetime.now(timezone.utc),
            data_source=self.name,
        )


class FallbackMarketDataProvider(MarketDataProvider):
    def __init__(self, primary: MarketDataProvider, fallback: MarketDataProvider) -> None:
        self.primary = primary
        self.fallback = fallback
        self.name = f"{primary.name}_fallback_{fallback.name}"

    def get_snapshot(self, symbol: str) -> MarketSnapshot:
        try:
            return self.primary.get_snapshot(symbol)
        except Exception:
            return self.fallback.get_snapshot(symbol)


class LiveFallbackMarketDataProvider(MarketDataProvider):
    def __init__(self, providers: list[MarketDataProvider]) -> None:
        self.providers = providers
        self.name = "_".join(provider.name for provider in providers)

    def get_snapshot(self, symbol: str) -> MarketSnapshot:
        # คืนจาก cache ถ้ายังไม่หมดอายุ — ลดการยิงซ้ำที่ทำให้ Yahoo โดน 429
        key = symbol.upper()
        cached = _SNAPSHOT_CACHE.get(key)
        if cached and (time.time() - cached[0]) < _SNAPSHOT_CACHE_TTL:
            return cached[1]

        errors: list[str] = []
        for provider in self.providers:
            try:
                snapshot = provider.get_snapshot(symbol)
                _SNAPSHOT_CACHE[key] = (time.time(), snapshot)
                return snapshot
            except Exception as exc:
                errors.append(f"{provider.name}: {exc}")
        raise MarketDataUnavailableError(
            f"Live market data unavailable for {symbol}: {'; '.join(errors)}"
        )

    def get_snapshots(self, symbols: list[str]) -> dict[str, MarketSnapshot]:
        pending = {symbol.upper() for symbol in symbols}
        snapshots: dict[str, MarketSnapshot] = {}
        errors: list[str] = []
        bulk_request = len(pending) >= 500
        for provider in self.providers:
            if not pending:
                break
            try:
                provider_snapshots = provider.get_snapshots(sorted(pending))
            except Exception as exc:
                errors.append(f"{provider.name}: {exc}")
                continue
            for symbol, snapshot in provider_snapshots.items():
                normalized = symbol.upper()
                if normalized in pending:
                    snapshots[normalized] = snapshot
                    pending.remove(normalized)
            if bulk_request and provider_snapshots:
                break
        if not snapshots and errors:
            raise MarketDataUnavailableError("; ".join(errors))
        return snapshots


class PolygonMarketDataProvider(MarketDataProvider):
    """Polygon/Massive snapshot provider for production full-market scans."""

    name = "polygon"
    snapshot_url = "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers"

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("POLYGON_API_KEY is required for Polygon market data")
        self.api_key = api_key

    def get_snapshot(self, symbol: str) -> MarketSnapshot:
        snapshots = self.get_snapshots([symbol])
        try:
            return snapshots[symbol.upper()]
        except KeyError as exc:
            raise ValueError(f"No Polygon snapshot found for {symbol}") from exc

    def get_snapshots(self, symbols: list[str]) -> dict[str, MarketSnapshot]:
        requested = {symbol.upper() for symbol in symbols}
        params = urlencode({"apiKey": self.api_key})
        request = Request(
            f"{self.snapshot_url}?{params}",
            headers={"User-Agent": "stock-radar-ai/0.1"},
        )
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))

        snapshots: dict[str, MarketSnapshot] = {}
        for item in payload.get("tickers", []):
            symbol = (item.get("ticker") or "").upper()
            if requested and symbol not in requested:
                continue
            try:
                snapshots[symbol] = _polygon_snapshot_to_market_snapshot(item)
            except (TypeError, ValueError, KeyError):
                continue
        return snapshots


class NasdaqScreenerMarketDataProvider(MarketDataProvider):
    """Bulk all-market quote table from Nasdaq public screener."""

    name = "nasdaq_screener"
    screener_url = "https://api.nasdaq.com/api/screener/stocks"

    def get_snapshot(self, symbol: str) -> MarketSnapshot:
        snapshots = self.get_snapshots([symbol])
        try:
            return snapshots[symbol.upper()]
        except KeyError as exc:
            raise ValueError(f"No Nasdaq screener quote found for {symbol}") from exc

    def get_snapshots(self, symbols: list[str]) -> dict[str, MarketSnapshot]:
        requested = {symbol.upper() for symbol in symbols if symbol}
        if not requested:
            return {}

        all_snapshots = self.get_all_snapshots()
        return {
            symbol: snapshot
            for symbol, snapshot in all_snapshots.items()
            if symbol in requested
        }

    def get_all_snapshots(self) -> dict[str, MarketSnapshot]:
        snapshots: dict[str, MarketSnapshot] = {}
        for row in self._fetch_rows():
            try:
                snapshot = _nasdaq_screener_row_to_market_snapshot(row)
            except (TypeError, ValueError, KeyError):
                continue
            snapshots[snapshot.symbol] = snapshot
        return snapshots

    def _fetch_rows(self) -> list[dict]:
        params = urlencode(
            {
                "tableonly": "true",
                "limit": 10000,
                "offset": 0,
                "download": "true",
            }
        )
        request = Request(f"{self.screener_url}?{params}", headers=_BROWSER_HEADERS)
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return ((payload.get("data") or {}).get("rows")) or []


class NasdaqQuoteMarketDataProvider(MarketDataProvider):
    """Per-symbol Nasdaq quote detail with the provider's actual timestamp."""

    name = "nasdaq_quote"
    info_url = "https://api.nasdaq.com/api/quote/{symbol}/info"

    def get_snapshot(self, symbol: str) -> MarketSnapshot:
        payload = fetch_nasdaq_json(
            self.info_url.format(symbol=symbol.upper()),
            symbol,
            {},
        )

        primary_data = ((payload.get("data") or {}).get("primaryData")) or {}
        return _nasdaq_quote_primary_data_to_market_snapshot(symbol, primary_data)


class NasdaqMarketDataProvider(MarketDataProvider):
    """ราคา EOD รายวันจริงจาก Nasdaq public API (ฟรี ไม่ต้องใช้ key)."""

    name = "nasdaq"
    historical_url = "https://api.nasdaq.com/api/quote/{symbol}/historical"
    bulk_screener_threshold = 200

    def get_snapshots(self, symbols: list[str]) -> dict[str, MarketSnapshot]:
        if len(symbols) >= self.bulk_screener_threshold:
            try:
                snapshots = NasdaqScreenerMarketDataProvider().get_snapshots(symbols)
                if snapshots:
                    return snapshots
            except Exception:
                # ถ้า bulk screener ล่ม ให้ fallback แค่หัวแถวของ surge universe
                # เพื่อไม่ให้ request ค้างจากการยิงหุ้นหลายพันตัวทีละ symbol.
                return super().get_snapshots(symbols[: self.bulk_screener_threshold])
        return super().get_snapshots(symbols)

    def get_snapshot(self, symbol: str) -> MarketSnapshot:
        today = datetime.now(timezone.utc).date()
        payload = fetch_nasdaq_json(
            self.historical_url.format(symbol=symbol.upper()),
            symbol,
            {
                "fromdate": (today - timedelta(days=60)).isoformat(),
                "todate": today.isoformat(),
                "limit": 40,
            },
        )

        rows = (((payload.get("data") or {}).get("tradesTable") or {}).get("rows")) or []
        # rows เรียงใหม่→เก่า (newest first); กรองเฉพาะแถวที่มีราคาปิดใช้ได้
        parsed = [row for row in rows if row.get("close")]
        if len(parsed) < 2:
            raise ValueError(f"No Nasdaq daily history found for {symbol}")

        recent = parsed[0]
        previous = parsed[1]
        previous_rows = parsed[1:21]
        prior_high = max(
            _money(row["high"]) for row in previous_rows if row.get("high")
        )
        average_volume = int(
            sum(_money(row["volume"]) for row in previous_rows if row.get("volume"))
            / max(1, len(previous_rows))
        )

        return MarketSnapshot(
            symbol=symbol.upper(),
            previous_close=_money(previous["close"]),
            latest_price=_money(recent["close"]),
            premarket_price=_money(recent["close"]),
            day_high=_money(recent.get("high") or recent["close"]),
            prior_high=prior_high,
            volume=int(_money(recent.get("volume") or 0)),
            average_volume_20d=max(1, average_volume),
            updated_at=datetime.now(timezone.utc),
            data_source=self.name,
        )


def _yahoo_quote_to_market_snapshot(quote: dict, default_symbol: str = "") -> MarketSnapshot:
    symbol = (quote.get("symbol") or default_symbol).upper()
    if not symbol:
        raise ValueError("Missing Yahoo Finance quote symbol")

    latest_price, session, updated_at = _best_yahoo_quote_price(quote)
    previous_close = _number(quote, "regularMarketPreviousClose", "previousClose")
    day_high = _number(
        quote,
        "regularMarketDayHigh",
        "regularMarketPrice",
        "postMarketPrice",
        "preMarketPrice",
        default=latest_price,
    )
    volume = int(
        _number(
            quote,
            "regularMarketVolume",
            "preMarketVolume",
            "postMarketVolume",
            "averageDailyVolume10Day",
            default=0,
        )
    )
    average_volume = int(
        _number(
            quote,
            "averageDailyVolume10Day",
            "averageDailyVolume3Month",
            default=volume or 1,
        )
    )

    # Yahoo's quote endpoint does not provide a rolling 20-day high. Use the
    # previous close as the prior reference for pure quote snapshots; surge
    # scans that start from historical/bulk data preserve their richer prior_high
    # when refreshed with this live quote.
    prior_high = previous_close
    data_source = YahooMarketDataProvider.name if session == "regular" else f"{YahooMarketDataProvider.name}_{session}"
    return MarketSnapshot(
        symbol=symbol,
        previous_close=previous_close,
        latest_price=latest_price,
        premarket_price=latest_price,
        day_high=max(day_high, latest_price),
        prior_high=prior_high,
        volume=volume,
        average_volume_20d=max(1, average_volume),
        updated_at=updated_at,
        data_source=data_source,
    )


def _nasdaq_quote_primary_data_to_market_snapshot(
    symbol: str,
    primary_data: dict,
) -> MarketSnapshot:
    latest_price = _table_number(primary_data.get("lastSalePrice"))
    change_percent = _table_number(primary_data.get("percentageChange"), default=0)
    denominator = 1 + (change_percent / 100)
    previous_close = latest_price / denominator if denominator > 0 else latest_price
    volume = int(_table_number(primary_data.get("volume"), default=0))
    updated_at = _parse_nasdaq_quote_timestamp(primary_data.get("lastTradeTimestamp"))
    is_realtime = bool(primary_data.get("isRealTime"))
    data_source = NasdaqQuoteMarketDataProvider.name if is_realtime else f"{NasdaqQuoteMarketDataProvider.name}_delayed"
    return MarketSnapshot(
        symbol=symbol.upper(),
        previous_close=previous_close,
        latest_price=latest_price,
        premarket_price=latest_price,
        day_high=max(latest_price, previous_close),
        prior_high=previous_close,
        volume=volume,
        average_volume_20d=max(1, volume),
        updated_at=updated_at,
        data_source=data_source,
    )


def _parse_nasdaq_quote_timestamp(value) -> datetime:
    if not isinstance(value, str) or not value.strip():
        return datetime.now(timezone.utc)
    cleaned = value.strip().replace(" ET", "")
    for fmt in ("%b %d, %Y %I:%M %p", "%b %d, %Y"):
        try:
            parsed = datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
        if fmt == "%b %d, %Y":
            # Nasdaq sometimes provides date only; use an approximate US close
            # time so stale-date comparisons are honest instead of "now".
            parsed = parsed.replace(hour=21, minute=0)
        return parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _best_yahoo_quote_price(quote: dict) -> tuple[float, str, datetime]:
    state = str(quote.get("marketState") or "").upper()
    candidates = [
        ("regular", _optional_number(quote, "regularMarketPrice"), quote.get("regularMarketTime")),
        ("pre", _optional_number(quote, "preMarketPrice"), quote.get("preMarketTime")),
        ("post", _optional_number(quote, "postMarketPrice"), quote.get("postMarketTime")),
    ]
    usable = [
        (session, float(price), int(timestamp or 0))
        for session, price, timestamp in candidates
        if isinstance(price, (int, float)) and price > 0
    ]
    if not usable:
        raise ValueError("Missing Yahoo quote price")

    if state in {"PRE", "PREPRE"}:
        preferred = next((item for item in usable if item[0] == "pre"), None)
        if preferred:
            return preferred[1], preferred[0], _datetime_from_epoch_seconds(preferred[2])
    if state in {"POST", "POSTPOST"}:
        preferred = next((item for item in usable if item[0] == "post"), None)
        if preferred:
            return preferred[1], preferred[0], _datetime_from_epoch_seconds(preferred[2])
    if state in {"REGULAR", "OPEN"}:
        preferred = next((item for item in usable if item[0] == "regular"), None)
        if preferred:
            return preferred[1], preferred[0], _datetime_from_epoch_seconds(preferred[2])

    latest = max(usable, key=lambda item: item[2])
    return latest[1], latest[0], _datetime_from_epoch_seconds(latest[2])


def _datetime_from_epoch_seconds(value) -> datetime:
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(value, timezone.utc)
    return datetime.now(timezone.utc)


def _money(value: str | int | float) -> float:
    """แปลงค่าราคา/วอลุ่มจาก Nasdaq เช่น "$205.19" หรือ "112,345,300"."""
    if isinstance(value, (int, float)):
        return float(value)
    return float(value.replace("$", "").replace(",", "").strip() or 0)


def _optional_number(quote: dict, *keys: str) -> float | None:
    for key in keys:
        value = quote.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return None


def _number(quote: dict, *keys: str, default: float | None = None) -> float:
    for key in keys:
        value = quote.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    if default is not None:
        return float(default)
    raise ValueError(f"Missing quote numeric field from {keys}")


def _float(value: str | int | float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return float(value.replace(",", ""))


def _polygon_snapshot_to_market_snapshot(item: dict) -> MarketSnapshot:
    day = item.get("day") or {}
    previous_day = item.get("prevDay") or {}
    last_trade = item.get("lastTrade") or {}
    symbol = (item.get("ticker") or "").upper()
    previous_close = _positive_float(previous_day.get("c") or previous_day.get("close"))
    latest_price = _positive_float(
        last_trade.get("p")
        or item.get("fmv")
        or day.get("c")
        or day.get("close")
        or previous_close
    )
    day_high = _positive_float(day.get("h") or day.get("high") or latest_price)
    prior_high = _positive_float(previous_day.get("h") or previous_day.get("high") or previous_close)
    volume = int(_positive_float(day.get("v") or day.get("volume") or 0, allow_zero=True))
    average_volume = int(
        _positive_float(previous_day.get("v") or previous_day.get("volume") or volume or 1)
    )

    updated_ns = item.get("updated") or last_trade.get("t")
    updated_at = _datetime_from_polygon_timestamp(updated_ns)
    return MarketSnapshot(
        symbol=symbol,
        previous_close=previous_close,
        latest_price=latest_price,
        premarket_price=latest_price,
        day_high=day_high,
        prior_high=prior_high,
        volume=volume,
        average_volume_20d=max(1, average_volume),
        updated_at=updated_at,
        data_source=PolygonMarketDataProvider.name,
    )


def _nasdaq_screener_row_to_market_snapshot(row: dict) -> MarketSnapshot:
    normalized = {str(key).lower(): value for key, value in row.items()}
    symbol = str(normalized.get("symbol") or "").upper().strip()
    latest_price = _table_number(
        normalized.get("lastsale")
        or normalized.get("last sale")
        or normalized.get("lastsaleprice")
        or normalized.get("price")
    )
    change_percent = _table_number(
        normalized.get("pctchange")
        or normalized.get("percentagechange")
        or normalized.get("percentchange")
    )
    denominator = 1 + (change_percent / 100)
    previous_close = latest_price / denominator if denominator > 0 else latest_price
    volume = int(_table_number(normalized.get("volume"), default=0))

    if not symbol:
        raise ValueError("Missing Nasdaq screener symbol")

    return MarketSnapshot(
        symbol=symbol,
        previous_close=previous_close,
        latest_price=latest_price,
        premarket_price=latest_price,
        day_high=max(latest_price, previous_close),
        prior_high=previous_close,
        volume=volume,
        average_volume_20d=max(1, volume),
        updated_at=datetime.now(timezone.utc),
        data_source=NasdaqScreenerMarketDataProvider.name,
    )


def _table_number(value, default: float | None = None) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        if default is not None:
            return float(default)
        raise ValueError("Missing table number")
    cleaned = (
        str(value)
        .replace("$", "")
        .replace("%", "")
        .replace(",", "")
        .replace("+", "")
        .strip()
    )
    if cleaned in {"", "-", "--", "N/A", "n/a"}:
        if default is not None:
            return float(default)
        raise ValueError("Missing table number")
    return float(cleaned)


def _positive_float(value, allow_zero: bool = False) -> float:
    if value is None:
        raise ValueError("Missing numeric value")
    number = float(value)
    if number < 0 or (number == 0 and not allow_zero):
        raise ValueError("Expected positive numeric value")
    return number


def _datetime_from_polygon_timestamp(value) -> datetime:
    if isinstance(value, (int, float)) and value > 0:
        # Polygon snapshot timestamps are nanoseconds in many payloads.
        if value > 10_000_000_000_000:
            return datetime.fromtimestamp(value / 1_000_000_000, timezone.utc)
        return datetime.fromtimestamp(value / 1000, timezone.utc)
    return datetime.now(timezone.utc)


def get_market_data_provider(use_live_data: bool | None = None) -> MarketDataProvider:
    settings = get_settings()
    live = settings.use_live_data if use_live_data is None else use_live_data
    provider_name = settings.market_data_provider
    allow_mock = settings.allow_mock_data and settings.app_env != "production"

    if settings.app_env == "production" and not live:
        raise RuntimeError("USE_LIVE_DATA=true is required in production")

    if not live:
        if allow_mock:
            return MockMarketDataProvider()
        raise RuntimeError("Mock market data is disabled")

    providers: list[MarketDataProvider] = []
    if provider_name in {"polygon", "massive"}:
        if settings.polygon_api_key:
            providers.append(PolygonMarketDataProvider(settings.polygon_api_key))
        providers.extend(
            [
                NasdaqQuoteMarketDataProvider(),
                NasdaqMarketDataProvider(),
                YahooChartMarketDataProvider(),
                StooqMarketDataProvider(),
            ]
        )
    elif provider_name == "stooq":
        providers.extend([StooqMarketDataProvider(), NasdaqQuoteMarketDataProvider(), NasdaqMarketDataProvider()])
    elif provider_name == "yahoo":
        providers.extend(
            [
                YahooMarketDataProvider(),
                NasdaqQuoteMarketDataProvider(),
                YahooChartMarketDataProvider(),
                NasdaqMarketDataProvider(),
            ]
        )
    else:
        # ค่าเริ่มต้น: Nasdaq ให้ราคา EOD จริงรายวันได้ฟรีและเสถียรสุด
        # แล้วค่อย fallback ไป Yahoo/Stooq หากบางตัวดึงไม่ได้
        providers.extend(
            [
                NasdaqQuoteMarketDataProvider(),
                NasdaqMarketDataProvider(),
                YahooChartMarketDataProvider(),
                StooqMarketDataProvider(),
            ]
        )

    if not providers:
        raise RuntimeError("No live market data provider is configured")
    if len(providers) == 1:
        return providers[0]
    return LiveFallbackMarketDataProvider(providers)


def get_realtime_market_data_provider() -> MarketDataProvider | None:
    """Best available intraday/extended-hours quote source for refreshing finalists."""
    settings = get_settings()
    if not settings.use_live_data:
        return None

    providers: list[MarketDataProvider] = []
    if settings.polygon_api_key:
        providers.append(PolygonMarketDataProvider(settings.polygon_api_key))
    providers.extend(
        [
            NasdaqQuoteMarketDataProvider(),
            YahooMarketDataProvider(),
            YahooChartMarketDataProvider(),
        ]
    )

    if len(providers) == 1:
        return providers[0]
    return LiveFallbackMarketDataProvider(providers)


def _with_data_source(snapshot: MarketSnapshot, data_source: str) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=snapshot.symbol,
        previous_close=snapshot.previous_close,
        latest_price=snapshot.latest_price,
        premarket_price=snapshot.premarket_price,
        day_high=snapshot.day_high,
        prior_high=snapshot.prior_high,
        volume=snapshot.volume,
        average_volume_20d=snapshot.average_volume_20d,
        updated_at=snapshot.updated_at,
        data_source=data_source,
    )
