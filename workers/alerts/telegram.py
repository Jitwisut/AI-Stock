from __future__ import annotations


class TelegramClient:
    def __init__(self, bot_token: str | None, chat_id: str | None) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token)

    async def send_message(
        self,
        text: str,
        chat_id: str | None = None,
        parse_mode: str | None = None,
        reply_markup: dict | None = None,
    ) -> bool:
        if not self.is_configured:
            return False

        import httpx

        # Telegram sendMessage มี hard limit 4096 ตัวอักษร — เกินแล้วจะโดน 400
        # และข้อความทั้งก้อนจะไม่ถูกส่งเลย จึงตัดไว้ก่อนกันพัง แทนที่จะปล่อยให้ raise
        if len(text) > 4096:
            text = text[:4090].rstrip() + "\n…"

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id or self.chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
        return True

    async def answer_callback_query(self, callback_query_id: str, text: str = "") -> bool:
        """ตอบ callback ของปุ่ม inline — ถ้าไม่ตอบ Telegram จะโชว์ spinner ค้างบนปุ่ม."""
        if not self.is_configured:
            return False

        import httpx

        url = f"https://api.telegram.org/bot{self.bot_token}/answerCallbackQuery"
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                url, json={"callback_query_id": callback_query_id, "text": text[:200]}
            )
        return True
