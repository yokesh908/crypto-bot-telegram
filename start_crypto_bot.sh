#!/bin/bash
while true; do
  pkill -9 -f assistant.py 2>/dev/null
  sleep 2
  pkill -9 -f run.py 2>/dev/null
  sleep 1
  cd /home/yokeshwaran/crypto-bot/telegram-bot
  /home/yokeshwaran/crypto-bot/venv/bin/python -u run.py >> /home/yokeshwaran/crypto-bot/bot.log 2>&1
  sleep 5
done
