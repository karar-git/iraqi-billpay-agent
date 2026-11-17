# Chat app for the bill-pay agent. Build from the repository root:
#   docker build -f web/Dockerfile -t billpay-chat .
#   docker run -p 8080:8080 billpay-chat
# Offline by default (rule-based stand-in core, synthetic in-memory wallets, no keys).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent ./agent
COPY core ./core
COPY payments ./payments
COPY wallet ./wallet
COPY data/wallet_seed.json ./data/wallet_seed.json
COPY web ./web

RUN useradd --create-home app && chown -R app /app
USER app

EXPOSE 8080
CMD ["sh", "-c", "uvicorn web.app:app --host 0.0.0.0 --port ${PORT}"]
