FROM python:3.11-slim

WORKDIR /app

COPY . .
RUN pip install --no-cache-dir -e .

# Default: run API. Override with `command:` in docker-compose for worker/beat.
CMD ["uvicorn", "apps.api.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
