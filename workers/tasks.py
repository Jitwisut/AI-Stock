from __future__ import annotations

import asyncio

from workers.alerts.digest import send_daily_digest
from workers.config import get_settings
from workers.main import run
from workers.services import create_queued_scan, execute_scan_job


settings = get_settings()

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
    }


def daily_digest() -> dict:
    """สแกนหุ้นซิ่ง + หุ้นระยะยาว แล้วส่งสรุปเข้า Telegram."""
    return asyncio.run(send_daily_digest())


def scan_and_alert() -> dict:
    return asyncio.run(run(send_telegram=True))


def run_scan_job(scan_run_id: int, kind: str = "momentum", send_telegram: bool = False) -> dict:
    return execute_scan_job(scan_run_id, kind=kind, send_telegram=send_telegram)


def enqueue_scan_job(kind: str = "momentum", send_telegram: bool = False) -> dict:
    scan_run = create_queued_scan(kind=kind)
    if not celery_app:
        raise RuntimeError("Celery is not available")
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
