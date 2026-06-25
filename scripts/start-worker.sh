#!/usr/bin/env bash
# =============================================================================
# start-worker.sh — รัน Celery Worker บน Render
# =============================================================================
set -euo pipefail

echo "============================================"
echo "AI Stock Radar Celery Worker starting"
echo "============================================"

# รอ database + redis
echo "⏳ Waiting for Redis & Database..."
sleep 5

CONCURRENCY="${CELERY_CONCURRENCY:-4}"

echo "🚀 Starting Celery worker (concurrency=$CONCURRENCY)"
exec celery -A workers.tasks:celery_app worker --loglevel=info --concurrency="$CONCURRENCY"
