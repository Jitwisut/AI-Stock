#!/usr/bin/env bash
# =============================================================================
# start-api.sh — รัน API บน Render
# =============================================================================
# Render inject $PORT ให้อัตโนมัติ (default 10000)
# Render inject $DATABASE_URL, $REDIS_URL จาก service links
# =============================================================================
set -euo pipefail

PORT="${PORT:-8000}"

echo "============================================"
echo "AI Stock Radar API starting on port $PORT"
echo "============================================"

# รอให้ database พร้อม (Render อาจยังไม่ ready ตอน container start)
echo "⏳ Waiting for database..."
for i in $(seq 1 30); do
    if python -c "
from workers.config import get_settings
from workers.repository import database_available
s = get_settings()
ok = database_available(s.database_url)
exit(0 if ok else 1)
" 2>/dev/null; then
        echo "✅ Database ready"
        break
    fi
    echo "   ... retry $i/30"
    sleep 2
done

# Run database migrations (schema.sql)
echo "🔄 Running database migrations..."
python -c "
from workers.config import get_settings
from workers.repository import run_migrations
run_migrations(get_settings().database_url)
print('Migrations complete')
" || echo "⚠️ Migration warning (non-fatal)"

echo "🚀 Starting uvicorn on 0.0.0.0:$PORT"
exec uvicorn apps.api.app.main:app --host 0.0.0.0 --port "$PORT" --log-level info
