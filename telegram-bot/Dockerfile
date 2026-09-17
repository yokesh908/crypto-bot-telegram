FROM python:3.14-slim-trixie

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

# The bot runs as a foreground process
CMD ["python", "run.py"]
