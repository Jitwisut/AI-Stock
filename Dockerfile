FROM python:3.11-slim

WORKDIR /app

COPY . .
RUN pip install --no-cache-dir -e .

# เก็บ commit info ลงไฟล์ให้ deploy-notify endpoint อ่านตอน runtime
# Railway: git history มีตอน build → ใช้ git log ได้
# Vercel: ใช้ VERCEL_GIT_COMMIT_SHA env var ใน endpoint โดยตรง
RUN git log -1 --format="%h (%D) %s" > /app/COMMIT_INFO.txt 2>/dev/null || \
    echo "(no git history)" > /app/COMMIT_INFO.txt

# Default: run API. Override with `command:` in docker-compose for worker/beat.
CMD ["uvicorn", "apps.api.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
