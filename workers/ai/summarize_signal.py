from __future__ import annotations

from workers.ai.gemini_client import generate_gemini_text
from workers.ai.prompt_templates import build_signal_prompt
from workers.data_models import SignalScore


def render_fallback_summary(signals: list[SignalScore], market_label: str = "US Premarket") -> str:
    lines = [
        "AI Momentum Stock Radar",
        f"ตลาด: {market_label}",
        "",
    ]

    for index, signal in enumerate(signals, start=1):
        lines.extend(
            [
                f"{index}. {signal.symbol} - Score {signal.score:.0f}/100",
                f"เปลี่ยนแปลงล่าสุด: {signal.gap_percent:+.1f}%",
                f"Relative Volume: {signal.relative_volume:.1f}x",
                f"Catalyst: {signal.catalyst}",
                "เหตุผล:",
                f"- {signal.reason}",
                "ความเสี่ยง:",
                *[f"- {risk}" for risk in signal.risks],
                "แผนเฝ้าดู:",
                *[f"- {item}" for item in signal.watch_plan],
                "",
            ]
        )

    lines.append("Disclaimer: ข้อมูลนี้เป็นระบบช่วยคัดกรอง ไม่ใช่คำแนะนำทางการเงิน")
    return "\n".join(lines)


async def summarize_signals(
    signals: list[SignalScore],
    *,
    ai_provider: str = "gemini",
    openai_api_key: str | None = None,
    gemini_api_key: str | None = None,
    openai_model: str = "gpt-4.1-mini",
    gemini_model: str = "gemini-2.5-flash",
    market_label: str = "US Premarket",
) -> str:
    prompt = build_signal_prompt(signals)
    if ai_provider == "gemini" and gemini_api_key:
        try:
            return await generate_gemini_text(
                prompt,
                api_key=gemini_api_key,
                model=gemini_model,
                temperature=0.3,
            )
        except Exception:
            return render_fallback_summary(signals, market_label=market_label)

    if ai_provider != "openai" or not openai_api_key:
        return render_fallback_summary(signals, market_label=market_label)

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=openai_api_key)
        response = await client.responses.create(
            model=openai_model,
            input=prompt,
            temperature=0.3,
        )
        return response.output_text
    except Exception:
        return render_fallback_summary(signals, market_label=market_label)
