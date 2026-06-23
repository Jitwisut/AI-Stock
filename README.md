# AI Momentum Stock Radar

MVP สำหรับสแกนหุ้นอเมริกาที่มี momentum ระยะสั้น แล้วสรุปเป็นภาษาไทยสำหรับส่งเข้า Telegram

ระบบนี้เป็นตัวช่วยคัดกรอง ไม่ใช่คำแนะนำทางการเงิน และตั้งใจใช้คำว่า "น่าสนใจสำหรับเฝ้าดู", "ควรรอ confirmation", และ "ความเสี่ยงสูง"

## สิ่งที่ทำได้ตอนนี้

- สแกน universe เริ่มต้น เช่น AAPL, AMD, NVDA, TSLA, MSFT
- คำนวณคะแนน momentum, relative volume, catalyst, premarket gap, breakout, risk
- สรุป Top 5 เป็นภาษาไทย
- ส่ง Telegram ได้เมื่อใส่ `TELEGRAM_BOT_TOKEN` และ `TELEGRAM_CHAT_ID`
- เปิด API สำหรับ run scan, latest signals, history, signal detail, stocks, settings status
- มี dashboard หน้า Top Signals สำหรับดู score breakdown, catalyst, risk warning และ run scan
- ดึงราคาตลาดจริงผ่าน Polygon/Massive ใน production เมื่อ `MARKET_DATA_PROVIDER=polygon`
- มี mock market/news provider สำหรับ dev เท่านั้น และปิดได้ด้วย `ALLOW_MOCK_DATA=false`
- ดึง universe หุ้นสหรัฐจาก Nasdaq Trader ได้เมื่อ `UNIVERSE_MODE=all`
- แนะนำหุ้นซิ่งน่าเฝ้าดูจาก scoring engine และใช้ OpenAI สรุปเมื่อใส่ `OPENAI_API_KEY`

## เริ่มใช้งาน

```bash
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m workers.main
```

## รัน API

```bash
uvicorn apps.api.app.main:app --reload
```

จากนั้นเปิด:

- `GET /health`
- `GET /health/live`
- `GET /health/ready`
- `POST /scan/run` ต้องส่ง `X-Admin-API-Key` ถ้าตั้ง `ADMIN_API_KEY`
- `GET /scan/runs/latest`
- `GET /scan/runs/{id}`
- `GET /signals/latest`
- `GET /signals`
- `GET /signals/{id}`
- `GET /stocks`
- `GET /settings/status`

## รัน Dashboard

```bash
cd apps/dashboard
npm install
npm run dev
```

จากนั้นเปิด `http://localhost:3000`

## Docker Compose

```bash
docker compose up --build
```

## Telegram

ใส่ค่าใน `.env`:

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
SEND_TELEGRAM=true
```

แล้วรัน:

```bash
python -m workers.main --send-telegram
```

## Live Data / Production

สำหรับ production ให้ใช้ Polygon/Massive และปิด mock data:

```env
APP_ENV=production
MARKET_DATA_PROVIDER=polygon
POLYGON_API_KEY=...
USE_LIVE_DATA=true
ALLOW_MOCK_DATA=false
SAVE_SIGNALS=true
ADMIN_API_KEY=...
BACKEND_CORS_ORIGINS=https://your-dashboard.example.com
UNIVERSE_MODE=all
SCAN_UNIVERSE_LIMIT=0
AI_CANDIDATE_LIMIT=50
```

ถ้าต้องการใช้ข้อมูลจำลองสำหรับ dev/offline ให้เปลี่ยนเป็น `USE_LIVE_DATA=false` และ `ALLOW_MOCK_DATA=true`

`SCAN_UNIVERSE_LIMIT=0` หมายถึงใช้รายชื่อทั้งหมดจากตลาดสหรัฐ

`POST /scan/run` จะ enqueue งานเข้า Celery แล้วคืนสถานะ queued:

```bash
curl -X POST "http://localhost:8000/scan/run?kind=momentum" \
  -H "X-Admin-API-Key: $ADMIN_API_KEY"
```

## Persistence

ตั้งค่าใน `.env`:

```env
SAVE_SIGNALS=true
DATABASE_URL=postgresql+psycopg://stock_radar:stock_radar@localhost:5432/stock_radar
```

แล้วเรียก:

```bash
curl -X POST "http://localhost:8000/scan/run" -H "X-Admin-API-Key: $ADMIN_API_KEY"
```
# touch
