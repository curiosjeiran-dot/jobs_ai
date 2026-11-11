from __future__ import annotations

import argparse
import asyncio
import json

from telegram_parser.client import TelegramParser, TelegramParserError
from telegram_parser.config import Config


def _print(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


async def _run(args: argparse.Namespace) -> int:
    parser = TelegramParser(Config.from_env())
    try:
        if args.command == "status":
            _print(await parser.status())
            return 0
        if args.command == "send-code":
            _print(await parser.send_login_code())
            return 0
        if args.command == "verify":
            _print(await parser.verify_login_code(args.code, args.password or None))
            return 0
        if args.command == "fetch":
            _print(await parser.fetch_all(limit=args.limit))
            return 0
    except TelegramParserError as exc:
        print(str(exc))
        return 1
    finally:
        await parser.disconnect()
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Telegram channel parser")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show auth/config status")
    sub.add_parser("send-code", help="Send Telegram login code")

    verify = sub.add_parser("verify", help="Verify login code (and 2FA if needed)")
    verify.add_argument("--code", default="", help="Login code from Telegram")
    verify.add_argument("--password", default="", help="2FA password if enabled")

    fetch = sub.add_parser("fetch", help="Fetch new posts from channels.yaml")
    fetch.add_argument("--limit", type=int, default=None, help="Messages per channel")

    sub.add_parser("serve", help="Run the HTTP service")

    args = parser.parse_args()
    if args.command == "serve":
        import uvicorn

        config = Config.from_env()
        uvicorn.run(
            "telegram_parser.service:app",
            host=config.host,
            port=config.port,
            reload=False,
        )
        return
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
