from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.types import Message, MessageEntityTextUrl, MessageEntityUrl

from telegram_parser import db
from telegram_parser.channels import sync_channels_from_file
from telegram_parser.config import DATA_DIR, SESSION_PATH, Config


class TelegramParserError(Exception):
    pass


def normalize_username(value: str) -> str:
    text = (value or "").strip()
    text = re.sub(r"^https?://(?:www\.)?(?:t\.me|telegram\.me)/(?:s/)?", "", text, flags=re.I)
    text = text.split("?")[0].split("/")[0].lstrip("@").strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{4,}", text):
        raise TelegramParserError(
            "Channel must be a public username like @telegram or https://t.me/telegram"
        )
    return text


def parse_proxy(proxy_url: str | None) -> dict[str, Any] | None:
    raw = (proxy_url or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = f"socks5://{raw}"
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "socks5").lower()
    if scheme in {"socks5", "socks5h"}:
        proxy_type = "socks5"
    elif scheme in {"socks4", "socks4a"}:
        proxy_type = "socks4"
    elif scheme in {"http", "https"}:
        proxy_type = "http"
    else:
        raise TelegramParserError("Proxy must be socks5://host:port, socks4://…, or http://…")
    if not parsed.hostname or not parsed.port:
        raise TelegramParserError("Proxy needs host and port, e.g. socks5://127.0.0.1:7890")
    proxy: dict[str, Any] = {
        "proxy_type": proxy_type,
        "addr": parsed.hostname,
        "port": int(parsed.port),
        "rdns": True,
    }
    if parsed.username:
        proxy["username"] = unquote(parsed.username)
    if parsed.password:
        proxy["password"] = unquote(parsed.password)
    return proxy


def _extract_links(message: Message) -> list[str]:
    links: list[str] = []
    text = message.message or ""
    if message.entities:
        for ent in message.entities:
            if isinstance(ent, MessageEntityTextUrl) and ent.url:
                links.append(ent.url)
            elif isinstance(ent, MessageEntityUrl):
                links.append(text[ent.offset : ent.offset + ent.length])
    seen: set[str] = set()
    out: list[str] = []
    for link in links:
        if link and link not in seen:
            seen.add(link)
            out.append(link)
    return out


def _message_to_raw(message: Message) -> dict[str, Any]:
    return {
        "id": message.id,
        "date": message.date.isoformat() if message.date else None,
        "text": message.message or "",
        "views": message.views,
        "grouped_id": message.grouped_id,
        "post_author": message.post_author,
    }


class TelegramParser:
    CONNECT_TIMEOUT_SEC = 15.0

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config.from_env()
        self._client: TelegramClient | None = None
        self._auth_state: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    def _session_exists(self) -> bool:
        return Path(f"{SESSION_PATH}.session").exists()

    async def connect(self, *, force: bool = False) -> TelegramClient:
        missing = self.config.missing()
        if missing:
            raise TelegramParserError(
                "Set credentials in .env first: " + ", ".join(missing)
            )
        try:
            api_id = int(self.config.api_id)
        except (TypeError, ValueError) as exc:
            raise TelegramParserError("TELEGRAM_API_ID must be a number") from exc

        async with self._lock:
            if self._client is not None and self._client.is_connected() and not force:
                return self._client
            if self._client is not None:
                try:
                    await self._client.disconnect()
                except Exception:  # noqa: BLE001
                    pass
                self._client = None

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            proxy = parse_proxy(self.config.proxy_url)
            kwargs: dict[str, Any] = {
                "session": str(SESSION_PATH),
                "api_id": api_id,
                "api_hash": self.config.api_hash,
                "connection_retries": 1 if proxy else 2,
                "retry_delay": 1,
                "timeout": 6,
                "auto_reconnect": False,
            }
            if proxy:
                kwargs["proxy"] = proxy
            client = TelegramClient(**kwargs)
            try:
                await asyncio.wait_for(client.connect(), timeout=self.CONNECT_TIMEOUT_SEC)
            except Exception as exc:  # noqa: BLE001
                try:
                    await client.disconnect()
                except Exception:  # noqa: BLE001
                    pass
                raise TelegramParserError(
                    "Cannot reach Telegram servers. If Telegram is blocked, set "
                    "TELEGRAM_PROXY_URL (e.g. socks5://127.0.0.1:7890). "
                    f"Details: {exc}"
                ) from exc
            self._client = client
            return client

    async def disconnect(self) -> None:
        async with self._lock:
            if self._client is not None:
                try:
                    await self._client.disconnect()
                except Exception:  # noqa: BLE001
                    pass
                self._client = None

    async def status(self) -> dict[str, Any]:
        missing = self.config.missing()
        payload: dict[str, Any] = {
            "configured": not missing,
            "missing": missing,
            "session_file": self._session_exists(),
            "authorized": False,
            "user": None,
        }
        if missing:
            return payload
        try:
            client = await self.connect()
            authorized = await asyncio.wait_for(client.is_user_authorized(), timeout=10)
            payload["authorized"] = authorized
            if authorized:
                me = await asyncio.wait_for(client.get_me(), timeout=10)
                payload["user"] = {
                    "id": me.id,
                    "username": me.username,
                    "first_name": me.first_name,
                }
        except TelegramParserError as exc:
            payload["error"] = str(exc)
        return payload

    async def send_login_code(self) -> dict[str, Any]:
        await self.disconnect()
        client = await self.connect(force=True)
        if await asyncio.wait_for(client.is_user_authorized(), timeout=10):
            return {"already_authorized": True}
        result = await asyncio.wait_for(
            client.send_code_request(self.config.phone),
            timeout=30,
        )
        self._auth_state["phone_code_hash"] = result.phone_code_hash
        self._auth_state["phone"] = self.config.phone
        return {"already_authorized": False, "sent": True}

    async def verify_login_code(
        self, code: str, password: str | None = None
    ) -> dict[str, Any]:
        code = (code or "").strip()
        if not code and not password:
            raise TelegramParserError("Enter the login code from Telegram")
        client = await self.connect(force=True)
        if await asyncio.wait_for(client.is_user_authorized(), timeout=10):
            self._auth_state.clear()
            return {"authorized": True}
        phone_code_hash = self._auth_state.get("phone_code_hash")
        phone = self._auth_state.get("phone") or self.config.phone
        if not phone_code_hash:
            raise TelegramParserError("Send a login code first")
        try:
            await asyncio.wait_for(
                client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash),
                timeout=30,
            )
        except SessionPasswordNeededError:
            if not password:
                raise TelegramParserError("2FA password required") from None
            try:
                await asyncio.wait_for(client.sign_in(password=password), timeout=30)
            except PasswordHashInvalidError as exc:
                raise TelegramParserError("Invalid 2FA password") from exc
        except PhoneCodeInvalidError as exc:
            raise TelegramParserError("Invalid login code") from exc
        except PhoneCodeExpiredError as exc:
            self._auth_state.clear()
            raise TelegramParserError("Login code expired — send a new one") from exc
        except FloodWaitError as exc:
            raise TelegramParserError(f"Telegram rate limit: wait {exc.seconds}s") from exc
        if not await client.is_user_authorized():
            raise TelegramParserError("Authorization failed")
        self._auth_state.clear()
        me = await client.get_me()
        return {
            "authorized": True,
            "user": {
                "id": me.id,
                "username": me.username,
                "first_name": me.first_name,
            },
        }

    async def fetch_channel(self, channel: dict[str, Any], *, limit: int | None = None) -> dict[str, Any]:
        limit = limit or self.config.fetch_limit
        client = await self.connect()
        if not await client.is_user_authorized():
            raise TelegramParserError("Telegram session not authorized — run login first")
        username = channel["username"]
        try:
            entity = await client.get_entity(username)
        except Exception as exc:  # noqa: BLE001
            raise TelegramParserError(f"Cannot resolve @{username}: {exc}") from exc
        try:
            await client(JoinChannelRequest(entity))
        except Exception:  # noqa: BLE001
            pass
        min_id = int(channel.get("last_message_id") or 0)
        messages: list[Message] = []
        try:
            async for message in client.iter_messages(entity, limit=limit, min_id=min_id):
                if isinstance(message, Message) and (message.message or message.media):
                    messages.append(message)
        except FloodWaitError as exc:
            raise TelegramParserError(f"Telegram rate limit: wait {exc.seconds}s") from exc
        except Exception as exc:  # noqa: BLE001
            raise TelegramParserError(f"Failed to fetch @{username}: {exc}") from exc

        saved = 0
        newest_id = channel.get("last_message_id")
        for message in messages:
            text = (message.message or "").strip()
            if newest_id is None or message.id > int(newest_id):
                newest_id = message.id
            if not text:
                continue
            posted_at = (
                message.date.astimezone(timezone.utc).isoformat() if message.date else None
            )
            created = db.upsert_post(
                channel_id=channel["id"],
                message_id=message.id,
                posted_at=posted_at,
                text=text,
                links=_extract_links(message),
                views=message.views,
                raw=_message_to_raw(message),
                post_url=f"https://t.me/{username}/{message.id}",
            )
            if created:
                saved += 1
        title = getattr(entity, "title", None) or channel.get("title") or username
        db.update_channel(
            channel["id"],
            title=title,
            last_fetched_at=datetime.now(timezone.utc).isoformat(),
            last_message_id=newest_id,
            error_message=None,
        )
        return {
            "channel_id": channel["id"],
            "username": username,
            "scanned": len(messages),
            "saved": saved,
        }

    async def fetch_all(self, *, limit: int | None = None) -> dict[str, Any]:
        db.init_db()
        sync_channels_from_file()
        channels = db.list_channels(enabled_only=True)
        if not channels:
            return {"channels": 0, "saved": 0, "results": [], "errors": []}
        results = []
        errors = []
        saved_total = 0
        for channel in channels:
            try:
                result = await self.fetch_channel(channel, limit=limit)
                results.append(result)
                saved_total += int(result["saved"])
            except TelegramParserError as exc:
                db.update_channel(channel["id"], error_message=str(exc))
                errors.append({"channel": channel["username"], "error": str(exc)})
        return {
            "channels": len(channels),
            "saved": saved_total,
            "results": results,
            "errors": errors,
        }
