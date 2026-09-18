"""Discovery engine: real market data → indicators → deterministic scores → AI explanation.

หลักการ: ทุกตัวเลขต้องมาจาก provider จริงพร้อม source/timestamp — ถ้าไม่มีให้เป็น None
(หน้าเว็บแสดง N/A) ห้ามเดา ห้ามใช้ข้อมูลจำลอง และ AI มีหน้าที่อธิบายเท่านั้น ไม่ให้คะแนน
"""
