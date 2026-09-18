from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


class GeminiGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class GeminiResult:
    text: str
    model: str  # รุ่นที่ตอบจริง — อาจไม่ใช่รุ่นหลักถ้ารุ่นหลักโดน 429/503 แล้วตกไปใช้ตัวสำรอง


# รุ่นสำรองเมื่อรุ่นหลักใช้ไม่ได้ (429 โควตาหมด / 503 คนใช้เยอะ / 404 รุ่นถูกปลด)
# ตั้งทับได้ด้วย GEMINI_FALLBACK_MODELS=a,b,c
_DEFAULT_FALLBACK_MODELS = ("gemini-3-flash-preview", "gemini-2.5-flash")

# รุ่นที่เพิ่งโดน 429 จะถูกข้ามชั่วคราว ไม่ต้องเสีย round trip ทุกคำขอ
# (เช่น 3.1-pro บน free tier มีโควตา 0 → 429 ทุกครั้ง)
_QUOTA_COOLDOWN_SECONDS = 10 * 60.0
_COOLDOWN_UNTIL: dict[str, float] = {}


def _fallback_models() -> tuple[str, ...]:
    import os

    raw = os.getenv("GEMINI_FALLBACK_MODELS")
    if raw is None:
        return _DEFAULT_FALLBACK_MODELS
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _candidate_models(model: str) -> list[str]:
    ordered = [model] + [item for item in _fallback_models() if item != model]
    now = time.time()
    available = [item for item in ordered if _COOLDOWN_UNTIL.get(item, 0) <= now]
    # ถ้าทุกรุ่นติด cooldown ก็ยังลองตามลำดับเดิม ดีกว่าไม่ลองเลย
    return available or ordered


async def generate_gemini(
    prompt: str,
    *,
    api_key: str,
    model: str,
    temperature: float,
    response_mime_type: str | None = None,
    timeout: float = 60.0,
) -> GeminiResult:
    if not api_key:
        raise GeminiGenerationError("GEMINI_API_KEY is required")

    last_error: Exception | None = None
    for candidate in _candidate_models(model):
        started = time.perf_counter()
        try:
            text = await _generate_once(
                prompt,
                api_key=api_key,
                model=candidate,
                temperature=temperature,
                response_mime_type=response_mime_type,
                timeout=timeout,
            )
        except GeminiGenerationError as exc:
            last_error = exc
            status = getattr(exc, "status_code", None)
            logger.warning(
                "ai_request_failed",
                extra={"event": "ai_request_failed", "model": candidate, "status": status},
            )
            if status == 429:
                _COOLDOWN_UNTIL[candidate] = time.time() + _QUOTA_COOLDOWN_SECONDS
            if status in {404, 429, 500, 503} or status is None:
                continue
            raise
        logger.info(
            "ai_response",
            extra={
                "event": "ai_response",
                "model": candidate,
                "requested_model": model,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "chars": len(text),
            },
        )
        return GeminiResult(text=text, model=candidate)
    raise last_error or GeminiGenerationError("Gemini request failed")


async def generate_gemini_text(
    prompt: str,
    *,
    api_key: str,
    model: str,
    temperature: float,
) -> str:
    result = await generate_gemini(prompt, api_key=api_key, model=model, temperature=temperature)
    return result.text


async def _generate_once(
    prompt: str,
    *,
    api_key: str,
    model: str,
    temperature: float,
    response_mime_type: str | None,
    timeout: float,
) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    generation_config: dict = {"temperature": temperature}
    if response_mime_type:
        generation_config["responseMimeType"] = response_mime_type
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": generation_config,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    # ส่ง key ทาง header เท่านั้น ไม่ใส่ใน URL เพื่อไม่ให้หลุดไปใน log
                    "x-goog-api-key": api_key,
                },
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise GeminiGenerationError(f"Gemini network error ({model}): {type(exc).__name__}") from exc

    if response.status_code >= 400:
        error = GeminiGenerationError(
            f"Gemini API error {response.status_code} ({model}): {response.text[:200]}"
        )
        error.status_code = response.status_code  # type: ignore[attr-defined]
        raise error

    data = response.json()
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    # รุ่น 3.x อาจแนบ "thought" parts มาด้วย — เอาเฉพาะคำตอบจริง
    text = "\n".join(
        part.get("text", "") for part in parts if part.get("text") and not part.get("thought")
    ).strip()
    if not text:
        raise GeminiGenerationError(f"Gemini API returned no text ({model})")
    return text
