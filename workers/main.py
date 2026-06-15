import argparse
import asyncio
import json

from workers.config import get_settings
from workers.services import run_scan


async def run(send_telegram: bool = False) -> dict:
    settings = get_settings()
    return await run_scan(send_telegram=send_telegram, persist=settings.save_signals)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run AI Momentum Stock Radar scanner.")
    parser.add_argument("--send-telegram", action="store_true", help="Send summary to Telegram.")
    parser.add_argument(
        "--daily-digest",
        action="store_true",
        help="สร้างและส่งสรุปรายวัน (หุ้นซิ่ง + ระยะยาว) เข้า Telegram",
    )
    args = parser.parse_args()

    if args.daily_digest:
        from workers.alerts.digest import build_daily_digest, send_daily_digest

        settings = get_settings()
        if settings.telegram_bot_token and settings.telegram_chat_id:
            result = asyncio.run(send_daily_digest())
            print(json.dumps({"status": result["status"], "sent": result["sent"]}, ensure_ascii=False))
            print(result["message"])
        else:
            # ยังไม่ตั้งค่า Telegram — แสดงตัวอย่างข้อความให้ดูก่อน
            print(build_daily_digest())
        return

    result = asyncio.run(run(send_telegram=args.send_telegram))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
