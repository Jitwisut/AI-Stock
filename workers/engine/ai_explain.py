"""AI Analysis Layer — LLM อธิบายผลที่ระบบคำนวณแล้วเท่านั้น.

- Input เป็น structured JSON ที่มาจาก engine (ไม่มีข้อความอิสระจากเว็บอื่นนอกจากหัวข้อข่าว)
- AI ห้ามสร้างตัวเลข/ข่าว/งบ/target price และห้ามแก้คะแนน
- Output ถูกตรวจ: ตัวเลขทุกตัวต้องหาได้ใน input, ข่าวต้องมีอยู่จริงใน input,
  ห้ามคำชี้นำ — รายการที่ไม่ผ่านจะถูกตัดทิ้งและรายงานใน validation
- แคชตาม hash ของ input: ข้อมูลไม่เปลี่ยน = ไม่เรียก LLM ซ้ำ
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re

from workers.config import get_settings
from workers.engine import store
from workers.engine.runtime import CACHE, iso, utcnow

logger = logging.getLogger(__name__)

AI_TTL = 12 * 3600.0
FORBIDDEN = ("แน่นอน", "การันตี", "รับประกัน", "guarantee", "ต้องขึ้น", "ขึ้นแน่", "ราคาเป้าหมาย", "target price", "ซื้อเลย", "เข้าเลย", "รวยแน่")
# ตัวเลขที่เป็นชื่อ indicator/ช่วงเวลา ไม่ใช่ข้อมูลตลาด — อนุญาตเสมอ
_ALLOWED_CONSTANTS = {1, 2, 3, 4, 5, 6, 7, 9, 10, 12, 14, 20, 26, 30, 50, 52, 60, 72, 100, 200, 252}


def build_ai_input(a: dict) -> dict:
    """ย่อผลวิเคราะห์ให้เหลือเฉพาะข้อมูลที่ AI ต้องใช้ (ตัด fetched_at ออกเพื่อให้ hash คงที่)."""
    f = a.get("fundamentals") or {}

    def fval(key):
        item = f.get(key) or {}
        value = item.get("value")
        return {"value": value if value is not None else "N/A", "as_of": item.get("as_of")}

    t = a["technical"]
    quote = a.get("quote") or {}
    return {
        "symbol": a["symbol"],
        "company_name": a.get("company_name"),
        "price": {
            "eod_close": t["price"],
            "eod_date": t["date"],
            "latest_quote": (quote.get("price") or {}).get("value"),
            "latest_quote_time": quote.get("market_timestamp"),
            "quote_is_realtime": quote.get("is_realtime"),
        },
        "technical": {k: t.get(k) for k in (
            "sma20", "sma50", "sma200", "ema20", "ema50", "rsi14", "macd", "macd_signal", "macd_hist", "atr14",
            "atr_percent", "bb_upper", "bb_lower", "volume", "avg_volume_20d", "volume_ratio", "avg_dollar_volume_20d",
            "high_20d_prior", "low_20d_prior", "high_52w_prior", "low_52w_prior", "pct_from_52w_high",
            "distance_sma50_percent", "distance_sma200_percent", "relative_strength_3m_vs_spy",
            "volatility_60d_annualized_percent", "max_drawdown_1y_percent",
        )},
        "momentum_returns_percent": t["returns"],
        "multi_timeframe": {k: a["multi_timeframe"][k] for k in ("short_term", "medium_term", "long_term", "note")},
        "signals": [{"id": s["id"], "name": s["name"], "direction": s["direction"], "evidence": s["evidence"]} for s in a["signals"]],
        "score": {
            "opportunity_score": a["opportunity_score"]["total"],
            "breakdown": a["opportunity_score"]["breakdown"],
            "component_rules": {k: v.get("rules") for k, v in a["components"].items()},
        },
        "risk": {"level": a["risk"]["level"], "factors": [{"factor": x["factor"], "explanation": x["explanation"]} for x in a["risk"]["factors"]]},
        "confidence": {"level": a["confidence"]["level"], "factors": [x["explanation"] for x in a["confidence"]["factors"]]},
        "fundamentals": {k: fval(k) for k in (
            "market_cap", "sector", "industry", "pe_ratio", "forward_pe", "eps_ttm", "revenue_growth_percent",
            "revenue_growth_prior_year_percent", "earnings_growth_percent", "profit_margin_percent", "roe_percent",
            "debt_to_equity", "free_cash_flow", "dividend_yield_percent", "next_earnings_date", "last_quarter_eps",
        )} if f else "Unavailable",
        "days_to_earnings": a.get("days_to_earnings"),
        "news": [
            {"title": n["title"], "source": n["source"], "published_at": n["published_at"], "age_hours": n["age_hours"]}
            for n in (a.get("news") or {}).get("items", [])
        ],
        "market_context": {"condition": (a.get("market_context") or {}).get("condition"), "evidence": (a.get("market_context") or {}).get("evidence")},
        "sector_context": {k: (a.get("sector_context") or {}).get(k) for k in ("etf", "return_1m_percent", "return_3m_percent", "stock_vs_sector_1m_percent", "above_sma50", "above_sma200")} if a.get("sector_context") else "Unavailable",
        "key_levels": a["key_levels"],
        "data_freshness": {
            "eod_last_bar": a["data_freshness"]["price_eod"]["last_bar_date"],
            "eod_is_stale": a["data_freshness"]["price_eod"]["is_stale"],
            "financials_period_end": a["data_freshness"]["financials"]["period_end"],
            "news_status": a["data_freshness"]["news"]["status"],
        },
        "unavailable_parts": [u["part"] for u in a.get("unavailable", [])],
    }


PROMPT = """คุณคือนักวิเคราะห์หุ้นที่ต้องอธิบายผลวิเคราะห์ที่ระบบคำนวณไว้แล้ว ตอบเป็นภาษาไทย

ข้อมูลทั้งหมดที่คุณมีอยู่ใน INPUT (JSON) ด้านล่างเท่านั้น

กฎที่ห้ามละเมิด:
1. ห้ามสร้างตัวเลขใหม่ ห้ามคำนวณตัวเลขใหม่ ใช้ได้เฉพาะตัวเลขที่ปรากฏใน INPUT ตามที่เขียนไว้
2. ห้ามสร้างหรืออ้างข่าว งบการเงิน เหตุการณ์ หรือข้อมูลใดที่ไม่อยู่ใน INPUT
3. ห้ามให้ราคาเป้าหมาย (target price) ห้ามพูดว่าราคาจะขึ้น/ลงแน่นอน ห้ามแนะนำให้ซื้อหรือขาย
4. ห้ามแก้หรือโต้แย้งคะแนน — คะแนนมาจาก algorithm คุณมีหน้าที่อธิบายว่าคะแนนมาจากอะไร
5. ทุกเหตุผลต้องอ้างข้อมูลจริงใน INPUT (ระบุชื่อ field หรือค่าที่ใช้) ห้ามใช้คำกว้าง ๆ เช่น "sentiment ดี" โดยไม่มีข้อมูลรองรับ
6. ค่าที่เป็น "N/A" หรือ "Unavailable" ให้ถือว่าไม่มีข้อมูล และระบุใน insufficient_data
7. ข่าว: แยก FACT (หัวข้อ แหล่ง เวลาเผยแพร่) ออกจากการตีความของคุณ และคำนึงถึงอายุข่าว (age_hours) ข่าวเก่าห้ามตีความว่าเป็นเหตุการณ์ใหม่
8. ถ้าข้อมูลไม่พอจะสรุปเรื่องใด ให้เขียนว่า "Insufficient data" ในเรื่องนั้น
9. แยกให้ชัดว่าหุ้นขึ้นเพราะทั้ง sector/ตลาดขึ้น (ดู sector_context และ market_context) หรือเป็นปัจจัยเฉพาะบริษัท

ตอบเป็น JSON object เดียว ตาม schema นี้เท่านั้น:
{
  "summary": "สรุป 2-3 ประโยคว่าหุ้นนี้มีอะไรน่าสนใจ/น่ากังวล",
  "why_interesting": [{"point": "...", "evidence": "field หรือค่าจาก INPUT ที่ใช้"}],
  "signal_explanations": [{"signal_id": "id จาก INPUT.signals", "explanation": "..."}],
  "risk_explanations": [{"risk": "ชื่อ factor จาก INPUT.risk.factors", "explanation": "..."}],
  "bull_case": ["เหตุผลฝั่งบวกที่อ้างข้อมูล"],
  "bear_case": ["เหตุผลฝั่งลบที่อ้างข้อมูล"],
  "scenarios_to_watch": [{"scenario": "...", "trigger": "อ้างอิงระดับราคาจาก INPUT.key_levels หรือเหตุการณ์ใน INPUT"}],
  "news_assessment": [{"title": "หัวข้อข่าวตรงตาม INPUT", "classification": "positive|neutral|negative", "interpretation": "การตีความของ AI"}],
  "sector_vs_company": "...",
  "insufficient_data": ["ข้อมูลที่ขาดหรือไม่พอ"],
  "overall_view": "positive|neutral|negative|insufficient_data"
}

INPUT:
"""


def _hash(data: dict) -> str:
    """hash ของ "snapshot ข้อมูล" — ตัดค่าที่เปลี่ยนทุกนาทีโดยที่ข้อมูลพื้นฐานไม่ได้เปลี่ยน
    (ราคา quote สด, อายุข่าว, P/E ที่คิดจากราคาสด) ไม่อย่างนั้นแคชจะไม่เคยโดนระหว่างตลาดเปิด
    และเรียก LLM ทุกครั้งที่เปิดหน้า
    """
    stable = json.loads(json.dumps(data, default=str))
    stable.get("price", {}).pop("latest_quote", None)
    stable.get("price", {}).pop("latest_quote_time", None)
    for item in stable.get("news", []):
        item.pop("age_hours", None)
    if isinstance(stable.get("fundamentals"), dict):
        stable["fundamentals"].pop("pe_ratio", None)
        stable["fundamentals"].pop("forward_pe", None)
    return hashlib.sha256(json.dumps(stable, sort_keys=True).encode("utf-8")).hexdigest()


def _numbers_in(value) -> list[float]:
    found: list[float] = []

    def walk(node):
        if isinstance(node, bool) or node is None:
            return
        if isinstance(node, (int, float)):
            found.append(float(node))
        elif isinstance(node, str):
            for match in re.findall(r"-?\d[\d,]*\.?\d*", node):
                try:
                    found.append(float(match.replace(",", "")))
                except ValueError:
                    pass
        elif isinstance(node, dict):
            for item in node.values():
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(value)
    return found


def _traceable(number: float, allowed: list[float]) -> bool:
    absolute = abs(number)
    if absolute in _ALLOWED_CONSTANTS or float(absolute).is_integer() and 1900 <= absolute <= 2100:
        return True
    for ref in allowed:
        for candidate in (ref, abs(ref), ref / 1e6, ref / 1e9, ref / 1e12, ref * 100):
            if candidate == 0:
                if absolute == 0:
                    return True
                continue
            if abs(abs(candidate) - absolute) <= max(0.051, abs(candidate) * 0.01):
                return True
    return False


def validate_output(output: dict, ai_input: dict) -> tuple[dict, dict]:
    allowed = _numbers_in(ai_input)
    news_titles = {n["title"].strip().lower(): n for n in ai_input.get("news", [])}
    signal_ids = {s["id"] for s in ai_input.get("signals", [])}
    removed: list[dict] = []

    def check_text(text: str) -> str | None:
        lowered = text.lower()
        for word in FORBIDDEN:
            if word.lower() in lowered:
                return f"forbidden phrase: {word}"
        bad = [n for n in _numbers_in(text) if not _traceable(n, allowed)]
        if bad:
            return f"untraceable numbers: {bad[:5]}"
        return None

    clean: dict = {}
    for key in ("summary", "sector_vs_company"):
        text = output.get(key)
        if isinstance(text, str):
            problem = check_text(text)
            if problem:
                removed.append({"field": key, "reason": problem, "text": text[:200]})
            else:
                clean[key] = text
    for key in ("bull_case", "bear_case", "insufficient_data"):
        items = []
        for text in output.get(key) or []:
            if not isinstance(text, str):
                continue
            problem = None if key == "insufficient_data" else check_text(text)
            if problem:
                removed.append({"field": key, "reason": problem, "text": text[:200]})
            else:
                items.append(text)
        clean[key] = items
    for key, text_fields in (("why_interesting", ("point", "evidence")), ("signal_explanations", ("explanation",)),
                             ("risk_explanations", ("explanation",)), ("scenarios_to_watch", ("scenario", "trigger"))):
        items = []
        for item in output.get(key) or []:
            if not isinstance(item, dict):
                continue
            if key == "signal_explanations" and item.get("signal_id") not in signal_ids:
                removed.append({"field": key, "reason": "signal not in input", "text": str(item)[:200]})
                continue
            problem = next((p for p in (check_text(str(item.get(f, ""))) for f in text_fields) if p), None)
            if problem:
                removed.append({"field": key, "reason": problem, "text": str(item)[:200]})
            else:
                items.append(item)
        clean[key] = items
    news_items = []
    for item in output.get("news_assessment") or []:
        if not isinstance(item, dict):
            continue
        fact = news_titles.get(str(item.get("title", "")).strip().lower())
        if fact is None:
            removed.append({"field": "news_assessment", "reason": "news not in input (possible fabrication)", "text": str(item.get("title"))[:200]})
            continue
        problem = check_text(str(item.get("interpretation", "")))
        if problem:
            removed.append({"field": "news_assessment", "reason": problem, "text": str(item)[:200]})
            continue
        classification = item.get("classification") if item.get("classification") in {"positive", "neutral", "negative"} else "neutral"
        news_items.append({
            "fact": {"title": fact["title"], "source": fact["source"], "published_at": fact["published_at"], "age_hours": fact["age_hours"]},
            "ai_classification": classification,
            "ai_interpretation": item.get("interpretation"),
        })
    clean["news_assessment"] = news_items
    view = output.get("overall_view")
    clean["overall_view"] = view if view in {"positive", "neutral", "negative", "insufficient_data"} else "insufficient_data"
    return clean, {"removed_items": removed, "removed_count": len(removed), "passed": not removed}


async def explain(analysis: dict, *, force: bool = False) -> dict:
    settings = get_settings()
    ai_input = build_ai_input(analysis)
    input_hash = _hash(ai_input)
    base = {"input_hash": input_hash, "requested_model": settings.gemini_model}

    if not force:
        hit, cached = CACHE.get(f"ai:{input_hash}")
        if hit:
            return {**cached, "cache": "memory"}
        row = _load_db(input_hash)
        if row:
            CACHE.set(f"ai:{input_hash}", row, AI_TTL)
            return {**row, "cache": "database"}

    if settings.ai_provider != "gemini" or not settings.gemini_api_key:
        return {**base, "status": "unavailable", "reason": "AI provider not configured"}

    from workers.ai.gemini_client import GeminiGenerationError, generate_gemini

    logger.info("ai_request", extra={"event": "ai_request", "symbol": analysis["symbol"], "input_hash": input_hash[:12]})
    try:
        result = await generate_gemini(
            PROMPT + json.dumps(ai_input, ensure_ascii=False, default=str),
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            temperature=0.2,
            response_mime_type="application/json",
            timeout=90.0,
        )
    except GeminiGenerationError as exc:
        return {**base, "status": "unavailable", "reason": str(exc)[:200]}

    try:
        raw = json.loads(_strip_fences(result.text))
        if not isinstance(raw, dict):
            raise ValueError("not an object")
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("ai_parsing_error", extra={"event": "ai_parsing_error", "symbol": analysis["symbol"], "model": result.model, "error": str(exc)})
        return {**base, "status": "unavailable", "reason": "AI returned invalid JSON", "model": result.model}

    output, validation = validate_output(raw, ai_input)
    if validation["removed_count"]:
        logger.warning("ai_validation_removed", extra={"event": "ai_validation", "symbol": analysis["symbol"], "removed": validation["removed_count"]})
    payload = {
        **base,
        "status": "ok",
        "model": result.model,
        "generated_at": iso(utcnow()),
        "output": output,
        "validation": validation,
        "role": "AI อธิบายผลคำนวณเท่านั้น ไม่ได้เป็นผู้ให้คะแนนหรือเลือกหุ้น",
        "disclaimer": "ข้อมูลเพื่อการศึกษา ไม่ใช่คำแนะนำการลงทุน",
    }
    CACHE.set(f"ai:{input_hash}", payload, AI_TTL)
    _save_db(input_hash, analysis["symbol"], result.model, payload)
    return {**payload, "cache": "miss"}


def explain_sync(analysis: dict, *, force: bool = False) -> dict:
    return asyncio.run(explain(analysis, force=force))


def _strip_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        cleaned = cleaned.rsplit("```", 1)[0]
    return cleaned.strip()


def _load_db(input_hash: str) -> dict | None:
    try:
        from sqlalchemy import text

        with store._engine().connect() as conn:
            row = conn.execute(
                text("SELECT output FROM ai_analysis_cache WHERE input_hash = :h AND created_at > NOW() - INTERVAL '12 hours'"),
                {"h": input_hash},
            ).first()
        return row[0] if row else None
    except Exception:
        return None


def _save_db(input_hash: str, symbol: str, model: str, payload: dict) -> None:
    try:
        from sqlalchemy import text

        with store._engine().begin() as conn:
            conn.execute(
                text("""INSERT INTO ai_analysis_cache (input_hash, symbol, model, output) VALUES (:h, :s, :m, CAST(:o AS JSONB))
                        ON CONFLICT (input_hash) DO UPDATE SET output = EXCLUDED.output, model = EXCLUDED.model, created_at = NOW()"""),
                {"h": input_hash, "s": symbol, "m": model, "o": json.dumps(payload, default=str, ensure_ascii=False)},
            )
    except Exception:
        logger.debug("ai cache write failed", exc_info=True)
