# Telegram parser

Standalone service that logs into Telegram with a user account (MTProto / Telethon) and fetches posts from public channels. Credentials stay in environment variables. Session files and the local database are not committed.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cp channels.example.yaml channels.yaml
```

Fill `.env`:

- `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` from [my.telegram.org](https://my.telegram.org) → API development tools
- `TELEGRAM_PHONE` in international format, e.g. `+15551234567`
- `TELEGRAM_PROXY_URL` only if Telegram is blocked (e.g. `socks5://127.0.0.1:7890`)

Edit `channels.yaml` with public usernames (`@name` or `https://t.me/name`).

## Login

```bash
python -m telegram_parser send-code
python -m telegram_parser verify --code 12345
# if 2FA is enabled:
python -m telegram_parser verify --code 12345 --password 'your-2fa'
python -m telegram_parser status
```

The session is stored at `data/telegram.session` (gitignored).

## Fetch

```bash
python -m telegram_parser fetch
```

Only messages with text are stored. Empty / media-only posts are skipped. Duplicates are ignored (`channel` + `message_id`).

## HTTP service

```bash
python run.py
# or: python -m telegram_parser serve
```

Listens on `127.0.0.1:8001` by default.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Process + Telegram auth status |
| POST | `/auth/send-code` | Send login code |
| POST | `/auth/verify` | Form fields `code`, `password` |
| POST | `/fetch` | Fetch all enabled channels |
| GET | `/posts?limit=50` | Recent stored posts |
| GET | `/channels` | Channel list / fetch cursors |

While the service is running it also polls on `CADENCE_MINUTES` (default 60).

## What is not in this repo

API keys, phone numbers, bot tokens, session files, SQLite databases, and production channel lists. Keep those in `.env` / `data/` locally.
