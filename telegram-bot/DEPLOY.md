# Free Cloud Deployment Guide

The trading bot is a Python process that maintains a persistent connection to
Telegram.  It does **not** need inbound HTTP ports — only outbound TLS to
Telegram's servers.

## Option A: Fly.io (recommended — truly free, no credit card)

Fly.io gives **3 shared VMs (256 MB RAM each)** on the free plan.
One is plenty for this bot.

### Prerequisites
- A GitHub account (free — github.com)
- A Fly.io account (free — fly.io)
- The Fly CLI:  `curl -L https://fly.io/install.sh | sh`

### Steps
1. **Create a GitHub repo** and push the project:
   ```bash
   cd /home/yokeshwaran/crypto-bot
   git init
   git add .
   git commit -m "Initial commit"
   gh repo create crypto-bot-telegram --public
   git push -u origin main
   ```

2. **Generate a StringSession** (run locally once):
   ```bash
   venv/bin/python telegram-bot/generate_string_session.py
   ```
   Copy the output string.

3. **Deploy**:
   ```bash
   fly launch --name crypto-bot-telegram \
     --image-from-dockerfile telegram-bot/Dockerfile \
     --no-volume \
     --region iad
   ```
   When prompted, set these secrets in the Fly dashboard:
   ```
   TELEGRAM_API_ID       (from my.telegram.org)
   TELEGRAM_API_HASH     (from my.telegram.org)
   TELEGRAM_CHANNEL      (your signal channel IDs)
   TELEGRAM_STRING_SESSION (from step 2)
   EXCHANGE_PROVIDER     paper
   MODE                  paper
   TRADE_CAPITAL_INR     30000
   DEFAULT_SL_PERCENT    2.0
   TARGET_PRIORITY       T3,T2,T1
   SIGNAL_MAX_AGE_MINUTES 10
   ```

## Option B: Render.com (free background worker)

Render gives **750 worker-hours/month** free — enough for 24/7.

### Steps
1. Push the project to GitHub (same as step 1 above).
2. Go to [render.com](https://render.com), sign in with GitHub.
3. Click **New → Worker**, connect your repo.
4. Select the `telegram-bot/Dockerfile` as the build path.
5. Set the environment variables (same as Fly.io step 3).
6. Create the worker — it starts automatically!

## Option C: Keep the Telegram session on the local machine

If you prefer not to generate a StringSession:
- The Dockerfile mounts the session file via a volume.
- On Fly.io, add a persistent volume and copy `telegram_session.session` into it.
- This is simpler but less portable.

---

### Switching to live trading later
Once you have deposited funds into your Zerodha account:
1. Run `zerodha_login.py` (locally or via `--token`) to get an access token.
2. Set `EXCHANGE_PROVIDER=zerodha` in your cloud env vars.
3. Set `KITE_API_KEY`, `KITE_API_SECRET`, and `KITE_ACCESS_TOKEN`.
4. Restart the deployment — the bot will use the real Zerodha API.
