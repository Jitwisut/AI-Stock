#!/usr/bin/env bash
# =============================================================================
# start-beat.sh — รัน Celery Beat Scheduler บน Render
# =============================================================================
set -euo pipefail

echo "============================================"
echo "AI Stock Radar Celery Beat starting"
echo "============================================"

# รอให้ Redis + Worker พร้อม
echo "⏳ Waiting for services..."
sleep 5

echo "🚀 Starting Celery Beat scheduler"
exec celery -A workers.tasks:celery_app beat --loglevel=info
