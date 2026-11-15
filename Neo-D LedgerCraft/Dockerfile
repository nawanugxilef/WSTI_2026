# Neo-D LedgerCraft Telegram bot for self-hosting. See "Run it yourself" in README.md.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 NEO_IN_DOCKER=1
WORKDIR /app
COPY requirements.txt .
# tzdata: slim images have no system time zones (receipts use Australia/Sydney).
RUN pip install --no-cache-dir -r requirements.txt tzdata
COPY . .
CMD ["python", "-m", "src.interfaces.telegram_bot"]
