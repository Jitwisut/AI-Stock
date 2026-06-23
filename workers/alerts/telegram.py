from __future__ import annotations


class TelegramClient:
    def __init__(self, bot_token: str | None, chat_id: str | None) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token)

    async def send_message(self, text: str, chat_id: str | None = None, parse_mode: str | None = None) -> bool:
        if not self.is_configured:
            return False

        import httpx

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id or self.chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
        return True
