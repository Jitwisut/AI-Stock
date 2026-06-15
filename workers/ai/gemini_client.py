from __future__ import annotations

import httpx


class GeminiGenerationError(RuntimeError):
    pass


async def generate_gemini_text(
    prompt: str,
    *,
    api_key: str,
    model: str,
    temperature: float,
) -> str:
    if not api_key:
        raise GeminiGenerationError("GEMINI_API_KEY is required")

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
