#!/usr/bin/env bash
# =============================================================================
# start-beat.sh — รัน Celery Beat Scheduler บน Render
# =============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"

echo "============================================"
echo "AI Stock Radar Celery Beat starting"
echo "============================================"

# รอให้ Redis + Worker พร้อม
echo "⏳ Waiting for services..."
sleep 5

echo "🚀 Starting Celery Beat scheduler"
exec python -m celery -A workers.tasks:celery_app beat --loglevel=info
