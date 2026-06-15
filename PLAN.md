# PLAN.md - AI Momentum Stock Radar

เป้าหมาย: ทำระบบ AI ช่วยสแกนหุ้นอเมริกาที่มีโอกาสวิ่งแรงระยะสั้น โดยระบบไม่เทรดเอง แต่ส่งสรุปเข้า Telegram ให้คนตัดสินใจเอง

## MVP

1. ดึงรายชื่อหุ้น US จาก universe เริ่มต้น
2. ดึงราคาล่าสุดและข้อมูล premarket
3. คำนวณ relative volume, gap up, breakout
4. ดึงข่าวล่าสุดของหุ้น top candidates
5. ให้ AI หรือ fallback template สรุป Top 5 ภาษาไทย
6. ส่งเข้า Telegram

## Scoring

- Momentum Score: 25
- Relative Volume: 25
- News Catalyst: 20
- Premarket Gap: 10
- Technical Breakout: 10
- Risk Filter: 10

## Alert Rules

ระบบต้องไม่บอกว่า "ซื้อแน่นอน" และต้องไม่ใช้คำว่า "เข้าเลย", "รวยแน่", หรือ "การันตีขึ้น"
