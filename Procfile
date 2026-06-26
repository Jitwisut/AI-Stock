web: PYTHONPATH=. python -m uvicorn apps.api.app.main:app --host 0.0.0.0 --port $PORT
worker: PYTHONPATH=. python -m celery -A workers.tasks:celery_app worker --loglevel=info --concurrency=4
beat: PYTHONPATH=. python -m celery -A workers.tasks:celery_app beat --loglevel=info
