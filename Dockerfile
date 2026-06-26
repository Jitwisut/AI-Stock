FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for psycopg + healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY . .
RUN pip install --no-cache-dir -e .

# เก็บ commit info ลงไฟล์ให้ deploy-notify endpoint อ่านตอน runtime
RUN git log -1 --format="%h (%D) %s" > /app/COMMIT_INFO.txt 2>/dev/null || \
    echo "(no git history)" > /app/COMMIT_INFO.txt

COPY scripts/ /app/scripts/
RUN chmod +x /app/scripts/*.sh

EXPOSE 8000

# Default: run API. Render จะ override ด้วย startCommand ใน render.yaml
CMD ["/app/scripts/start-api.sh"]
