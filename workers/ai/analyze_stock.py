from __future__ import annotations

from workers.ai.gemini_client import generate_gemini
from workers.data_models import NewsItem, SignalScore, Stock
from workers.recommendations import recommendation_for, surge_grade, surge_plan, surge_score


def render_fallback_stock_analysis(stock: Stock, signal: SignalScore, news_items: list[NewsItem]) -> str:
    recommendation = recommendation_for(signal)
    grade = surge_grade(signal)
    plan = surge_plan(signal)
    news_lines = "\n".join(
        f"- {item.title} ({item.source})" for item in news_items[:3]
    ) or "- ยังไม่พบข่าวล่าสุดที่ชัดเจน"
    risks = "\n".join(f"- {risk}" for risk in signal.risks)
    watch_plan = "\n".join(f"- {item}" for item in signal.watch_plan)

    return f"""ภาพรวม {stock.symbol} - {stock.name}
คะแนนรวม {signal.score:.0f}/100 และคำแนะนำเชิงเฝ้าดู: {recommendation["label"]}

ราคาและโมเมนตัม
- ราคาล่าสุดประมาณ ${signal.latest_price:.2f}
- เปลี่ยนแปลงเทียบ previous close {signal.gap_percent:+.2f}%
- Relative volume {signal.relative_volume:.2f}x
- เกรดหุ้นซิ่ง: {grade["grade"]} ({grade["label"]})

เหตุผลหลัก
- {signal.reason}
- Catalyst: {signal.catalyst}

ข่าวล่าสุด
{news_lines}

ความเสี่ยงที่ต้องระวัง
{risks}

แผนเฝ้าดู
{watch_plan}
- โซนอ้างอิง: {plan.get("entry_note") or "รอราคาและวอลุ่มยืนยัน"}

หมายเหตุ: ข้อมูลนี้เป็นเครื่องมือช่วยคัดกรอง ไม่ใช่คำแนะนำทางการเงิน"""


async def analyze_stock_with_ai(
    stock: Stock,
    signal: SignalScore,
    news_items: list[NewsItem],
    *,
    ai_provider: str = "gemini",
    openai_api_key: str | None = None,
    gemini_api_key: str | None = None,
    openai_model: str = "gpt-4.1-mini",
    gemini_model: str = "gemini-2.5-flash",
) -> tuple[str, str]:
    prompt = _build_stock_analysis_prompt(stock, signal, news_items)
    if ai_provider == "gemini" and gemini_api_key:
        try:
            result = await generate_gemini(
                prompt,
                api_key=gemini_api_key,
                model=gemini_model,
                temperature=0.25,
            )
            # บอกรุ่นที่ตอบจริง เพื่อไม่ให้ผู้ใช้เข้าใจผิดเมื่อรุ่นหลักตกไปใช้ตัวสำรอง
            return result.text, f"gemini:{result.model}"
        except Exception:
            return render_fallback_stock_analysis(stock, signal, news_items), "rules"

    if ai_provider != "openai" or not openai_api_key:
        return render_fallback_stock_analysis(stock, signal, news_items), "rules"

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=openai_api_key)
        response = await client.responses.create(
            model=openai_model,
            input=prompt,
            temperature=0.25,
        )
        return response.output_text, "openai"
    except Exception:
        return render_fallback_stock_analysis(stock, signal, news_items), "rules"


def _build_stock_analysis_prompt(
    stock: Stock,
    signal: SignalScore,
    news_items: list[NewsItem],
) -> str:
    news = [
        {
            "title": item.title,
            "source": item.source,
            "published_at": item.published_at.isoformat(),
            "summary": item.summary,
        }
        for item in news_items[:5]
    ]
    data = {
        "symbol": stock.symbol,
        "name": stock.name,
        "sector": stock.sector,
        "market_cap": stock.market_cap,
        "score": signal.score,
        "latest_price": signal.latest_price,
        "gap_percent": signal.gap_percent,
        "relative_volume": signal.relative_volume,
        "score_breakdown": {
            "momentum": signal.momentum_score,
            "volume": signal.volume_score,
            "catalyst": signal.catalyst_score,
            "premarket_gap": signal.premarket_gap_score,
            "breakout": signal.breakout_score,
            "risk": signal.risk_score,
        },
        "surge_score": round(surge_score(signal), 2),
        "surge_grade": surge_grade(signal),
        "recommendation": recommendation_for(signal),
        "risks": signal.risks,
        "watch_plan": signal.watch_plan,
        "news": news,
    }
    return (
        "วิเคราะห์หุ้นรายตัวเป็นภาษาไทยสำหรับนักลงทุนรายย่อยที่ต้องการเฝ้าดูหุ้น US "
        "ใช้โครงสร้าง: ภาพรวม, สิ่งที่หนุน, จุดที่ต้องระวัง, ระดับราคาที่ควรจับตา, "
        "สถานการณ์ที่ควรรอ confirmation, สรุปแบบไม่ชี้นำให้ซื้อขายทันที "
        "ห้ามใช้คำว่า ซื้อตัวนี้, การันตีขึ้น, เข้าเลย, รวยแน่ และต้องย้ำว่าไม่ใช่คำแนะนำทางการเงิน\n\n"
        f"data={data}"
    )
