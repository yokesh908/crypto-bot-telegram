FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Install system dependencies needed by the Python packages
RUN apt-get update && \
    apt-get -y install --no-install-recommends gcc libffi-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first (better caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the telegram-bot source
COPY telegram-bot/ ./telegram-bot/

WORKDIR /app/telegram-bot

# The bot runs as a foreground process (unbuffered logs for Render/Fly)
CMD ["python", "-u", "run.py"]
