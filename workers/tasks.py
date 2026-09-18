from __future__ import annotations

import asyncio

from workers.alerts.digest import send_daily_digest
from workers.config import get_settings
from workers.main import run
from workers.repository import run_migrations
from workers.services import (
    create_queued_scan,
    execute_scan_job,
    redis_available,
    start_scan_in_background,
)


settings = get_settings()
run_migrations(settings.database_url)

try:
    from celery import Celery
except Exception:
    Celery = None


celery_app = Celery("stock_radar", broker=settings.redis_url) if Celery else None

if celery_app:
    from celery.schedules import crontab

    celery_app.conf.timezone = "Asia/Bangkok"
    celery_app.conf.beat_schedule = {
        # สรุปรายวันส่ง Telegram: เช้า 08:00 และ ค่ำ 20:00 (เวลาไทย)
        "daily-digest-morning-th": {
            "task": "workers.tasks.daily_digest",
            "schedule": crontab(hour=8, minute=0),
        },
        "daily-digest-evening-th": {
            "task": "workers.tasks.daily_digest",
            "schedule": crontab(hour=20, minute=0),
        },
        "momentum-scan-market-open-th": {
            "task": "workers.tasks.scheduled_scan",
            "schedule": crontab(hour=21, minute=35),
            "args": ("momentum", False),
        },
        "surge-scan-market-open-th": {
            "task": "workers.tasks.scheduled_scan",
            "schedule": crontab(hour=21, minute=45),
            "args": ("surge", False),
        },
        "longterm-scan-morning-th": {
            "task": "workers.tasks.scheduled_scan",
            "schedule": crontab(hour=7, minute=30),
            "args": ("longterm", False),
        },
        "universe-refresh-morning-th": {
            "task": "workers.tasks.scheduled_scan",
            "schedule": crontab(hour=6, minute=30),
            "args": ("universe_refresh", False),
        },
        # เตือนราคาตาม watchlist — ทุก 15 นาทีระหว่างตลาดสหรัฐเปิด (20:30-04:00 TH)
        "price-alerts": {
            "task": "workers.tasks.price_alerts",
            "schedule": crontab(minute="*/15", hour="20-23,0-4"),
        },
    }


def daily_digest() -> dict:
    """สแกนหุ้นซิ่ง + หุ้นระยะยาว แล้วส่งสรุปเข้า Telegram."""
    return asyncio.run(send_daily_digest())


def price_alerts() -> dict:
    """เช็ค watchlist ของผู้ใช้บอทแล้วส่งเตือนตัวที่ถึงราคาเป้าหมาย."""
    from workers.bot_features import send_price_alerts

    return asyncio.run(send_price_alerts())


def scan_and_alert() -> dict:
    return asyncio.run(run(send_telegram=True))


def run_scan_job(scan_run_id: int, kind: str = "momentum", send_telegram: bool = False) -> dict:
    return execute_scan_job(scan_run_id, kind=kind, send_telegram=send_telegram)


def enqueue_scan_job(kind: str = "momentum", send_telegram: bool = False) -> dict:
    current_settings = get_settings()
    # ไม่มี Celery worker (หรือ Redis ที่เป็น broker ต่อไม่ติด) → รันในเธรดของ web process
    # แล้วคืน queued ทันที ห้ามรันใน request เพราะนานเกิน timeout ของ proxy
    if (
        not current_settings.background_workers_enabled
        or not celery_app
        or not redis_available(current_settings.redis_url)
    ):
        return start_scan_in_background(kind=kind, send_telegram=send_telegram)

    scan_run = create_queued_scan(kind=kind)
    celery_app.send_task(
        "workers.tasks.run_scan_job",
        args=[scan_run["id"], kind, send_telegram],
    )
    return scan_run


def scheduled_scan(kind: str = "momentum", send_telegram: bool = False) -> dict:
    scan_run = create_queued_scan(kind=kind)
    return execute_scan_job(scan_run["id"], kind=kind, send_telegram=send_telegram)


if celery_app:
    celery_app.task(name="workers.tasks.daily_digest")(daily_digest)
    celery_app.task(name="workers.tasks.scan_and_alert")(scan_and_alert)
    celery_app.task(name="workers.tasks.run_scan_job")(run_scan_job)
    celery_app.task(name="workers.tasks.scheduled_scan")(scheduled_scan)
    celery_app.task(name="workers.tasks.price_alerts")(price_alerts)
