FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ /app/

ENV DATA_DIR=/data \
    PYTHONUNBUFFERED=1

# A simple loop instead of cron: fewer moving parts, logs go straight to `docker logs`
CMD ["sh", "-c", "while true; do python sync.py; sleep $(( ${SYNC_INTERVAL_MINUTES:-60} * 60 )); done"]
