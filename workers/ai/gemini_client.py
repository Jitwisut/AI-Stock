from __future__ import annotations

import httpx


class GeminiGenerationError(RuntimeError):
    pass


# โควตา free tier ของ gemini-2.5-flash คือ 20 requests/วัน/โปรเจกต์ ซึ่งหมดเร็วมาก
# เมื่อบอท+หน้าเว็บเรียกกันทั้งวัน — พอหมดแล้วระบบจะตกไปใช้ fallback แบบกฎเงียบ ๆ
# จึงลองรุ่นสำรองต่อให้อัตโนมัติเมื่อเจอ 429 (โควตาหมด) หรือ 404 (รุ่นถูกปลด)
_FALLBACK_MODELS = ("gemini-flash-latest",)


async def generate_gemini_text(
    prompt: str,
    *,
    api_key: str,
    model: str,
    temperature: float,
) -> str:
    if not api_key:
        raise GeminiGenerationError("GEMINI_API_KEY is required")

    candidates = [model] + [item for item in _FALLBACK_MODELS if item != model]
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            return await _generate_once(
                prompt, api_key=api_key, model=candidate, temperature=temperature
            )
        except GeminiGenerationError as exc:
            last_error = exc
            if " 429" in str(exc) or " 404" in str(exc):
                continue
            raise
    raise last_error or GeminiGenerationError("Gemini request failed")


async def _generate_once(
    prompt: str,
    *,
    api_key: str,
    model: str,
    temperature: float,
) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature},
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            url,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            json=payload,
        )

    if response.status_code >= 400:
        raise GeminiGenerationError(
            f"Gemini API error {response.status_code}: {response.text[:300]}"
        )

    data = response.json()
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    text = "\n".join(part.get("text", "") for part in parts if part.get("text")).strip()
    if not text:
        raise GeminiGenerationError("Gemini API returned no text")
    return text
