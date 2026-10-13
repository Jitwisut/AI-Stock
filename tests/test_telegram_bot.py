import asyncio

import pytest

from apps.api.app import main
from workers.scanner.fetch_prices import MarketDataUnavailableError


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AAPL", "AAPL"),
        ("aapl", "AAPL"),
        ("$nvda", "NVDA"),
        ("#tsla", "TSLA"),
        (" brk.b ", "BRK.B"),
        ("BF-B", "BF-B"),
        ("", None),
        ("1234", None),
        ("AAPL;DROP", None),
        ("TOOLONGSYM", None),
        ("ราคา", None),
    ],
)
def test_normalize_symbol(raw, expected):
    assert main._normalize_symbol(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("250", 250.0), ("1,000", 1000.0), ("$200.5", 200.5), ("-5", None), ("0", None), ("nan", None), ("inf", None), ("abc", None)],
)
def test_parse_positive_number(raw, expected):
    assert main._parse_positive_number(raw) == expected


def test_unknown_symbol_is_not_reported_as_rate_limit():
    # ข้อความจริงจาก production: error รวมทุก provider มี "429" ของ Yahoo ปนอยู่
    exc = MarketDataUnavailableError(
        "Live market data unavailable for ZZZZQ: nasdaq_quote: Missing table number; "
        "nasdaq: No Nasdaq daily history found for ZZZZQ; "
        "yahoo_chart: HTTP Error 429: Too Many Requests; stooq: No Stooq daily history found for ZZZZQ"
    )
    assert "ไม่พบข้อมูลหุ้น ZZZZQ" in main._analysis_error_reply(exc, "ZZZZQ")


def test_real_rate_limit_and_generic_errors_do_not_leak_details():
    assert "rate limit" in main._analysis_error_reply(RuntimeError("HTTP 429 Too Many Requests"), "AAPL")
    reply = main._analysis_error_reply(RuntimeError("postgresql://user:secret@db/x"), "AAPL")
    assert "secret" not in reply


def test_clean_ai_text_strips_markdown_and_cuts_on_boundary():
    raw = "### **ภาพรวม**\n---\n*   **จุดแข็ง:** ดี\n\n\n\nต่อ " + "ยาวมาก " * 400
    cleaned = main._clean_ai_text(raw, limit=200)
    assert "**" not in cleaned and "###" not in cleaned and "---" not in cleaned
    assert "• จุดแข็ง: ดี" in cleaned
    assert len(cleaned) <= 202
    assert cleaned.endswith("…")


def test_looks_like_symbol_query():
    assert main._looks_like_symbol_query("voo")
    assert main._looks_like_symbol_query("$TSLA")
    assert not main._looks_like_symbol_query("AAPL น่าซื้อไหม")
    assert not main._looks_like_symbol_query("12345")


def test_analysis_buttons_reject_non_symbols():
    assert main._analysis_buttons("Apple Inc. very long company name") is None
    buttons = main._analysis_buttons("aapl")
    assert buttons["inline_keyboard"][0][0]["callback_data"] == "a:AAPL"


def test_strip_source_suffix():
    assert main._strip_source_suffix("Apple rises - Benzinga", "Benzinga") == "Apple rises"
    assert main._strip_source_suffix("Apple rises", "Benzinga") == "Apple rises"


def test_formatters_tolerate_missing_numbers():
    line = main._format_surge_line({"symbol": "ABC", "relative_volume": None, "latest_price": None}, 1)
    assert "vol N/A" in line and "$0.00" in line
    text = main._format_daily_picks(
        {"picks": [{"symbol": "ABC", "relative_volume": None, "price": 1.5, "change_percent": None}]}
    )
    assert "ABC" in text and "vol N/A" in text


def test_duplicate_updates_are_dropped():
    main._SEEN_UPDATE_IDS.clear()
    assert main._is_duplicate_update(42) is False
    assert main._is_duplicate_update(42) is True
    assert main._is_duplicate_update(None) is False


class _FakeTelegram:
    def __init__(self):
        self.sent = []

    is_configured = True

    async def send_message(self, text, chat_id=None, parse_mode=None, reply_markup=None):
        self.sent.append(text)
        return True


def _run(command, chat_type="private", monkeypatch=None):
    fake = _FakeTelegram()
    monkeypatch.setattr("workers.alerts.telegram.TelegramClient", lambda *a, **k: fake)
    asyncio.run(main._handle_bot_command(command, "1", chat_type))
    return fake.sent


def test_group_ignores_commands_for_other_bots(monkeypatch):
    monkeypatch.setattr(main, "_BOT_USERNAME", "MyRadarBot")
    assert _run("/help@OtherBot", "group", monkeypatch) == []
    assert _run("/help@myradarbot", "group", monkeypatch)
    # ในกลุ่ม ข้อความทั่วไป / คำสั่งที่ไม่รู้จัก → เงียบ
    assert _run("hello everyone", "group", monkeypatch) == []
    assert _run("/unknowncmd", "group", monkeypatch) == []


def test_invalid_arguments_get_usage_without_side_effects(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("must not touch storage on invalid input")

    monkeypatch.setattr("workers.bot_features.portfolio_add", _boom)
    monkeypatch.setattr("workers.bot_features.watch_add", _boom)
    assert "มากกว่า 0" in _run("/port add AAPL -5 0", monkeypatch=monkeypatch)[0]
    assert "มากกว่า 0" in _run("/port add AAPL nan 10", monkeypatch=monkeypatch)[0]
    assert "ไม่ใช่สัญลักษณ์" in _run("/port add ??? 1 10", monkeypatch=monkeypatch)[0]
    assert "มากกว่า 0" in _run("/watch AAPL -1", monkeypatch=monkeypatch)[0]
    assert "ใช้แบบนี้" in _run("/watch", monkeypatch=monkeypatch)[0]
    assert "คำสั่งพอร์ต" in _run("/port foo", monkeypatch=monkeypatch)[0]
