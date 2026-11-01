FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib

WORKDIR /app
RUN useradd --create-home --uid 10001 bot
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=bot:bot . .
RUN install -d -o bot -g bot /data \
    && install -d -o bot -g bot /home/bot/.garminconnect
USER bot
RUN test -w /home/bot/.garminconnect \
    && touch /home/bot/.garminconnect/.write-test \
    && rm /home/bot/.garminconnect/.write-test
ENV DATABASE_PATH=/data/health_bot.sqlite3
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD ["python", "-m", "health_bot.healthcheck"]
CMD ["python", "-m", "health_bot"]
