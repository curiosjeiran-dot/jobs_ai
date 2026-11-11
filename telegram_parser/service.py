from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import JSONResponse

from telegram_parser import db
from telegram_parser.client import TelegramParser, TelegramParserError
from telegram_parser.config import Config

_parser: TelegramParser | None = None
_stop = asyncio.Event()
_poll_task: asyncio.Task | None = None
_last_auto_fetch_at: str | None = None


def parser() -> TelegramParser:
    global _parser
    if _parser is None:
        _parser = TelegramParser(Config.from_env())
    return _parser


async def _poll_loop() -> None:
    global _last_auto_fetch_at
    config = parser().config
    while not _stop.is_set():
        wait_for = 5.0
        if _last_auto_fetch_at:
            try:
                last = datetime.fromisoformat(_last_auto_fetch_at.replace("Z", "+00:00"))
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                elapsed = (datetime.now(timezone.utc) - last).total_seconds()
                wait_for = max(5.0, config.cadence_minutes * 60 - elapsed)
            except ValueError:
                wait_for = 5.0
        try:
            await asyncio.wait_for(_stop.wait(), timeout=wait_for)
            break
        except asyncio.TimeoutError:
            pass
        if parser().config.missing():
            continue
        try:
            await parser().fetch_all()
            _last_auto_fetch_at = datetime.now(timezone.utc).isoformat()
        except TelegramParserError:
            continue


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _poll_task
    db.init_db()
    _stop.clear()
    _poll_task = asyncio.create_task(_poll_loop(), name="telegram-poller")
    yield
    _stop.set()
    if _poll_task:
        _poll_task.cancel()
        try:
            await _poll_task
        except asyncio.CancelledError:
            pass
    await parser().disconnect()


app = FastAPI(title="Telegram parser", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, Any]:
    status = await parser().status()
    return {"status": "ok", "telegram": status}


@app.post("/auth/send-code")
async def send_code() -> JSONResponse:
    try:
        result = await parser().send_login_code()
    except TelegramParserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(result)


@app.post("/auth/verify")
async def verify(
    code: str = Form(""),
    password: str = Form(""),
) -> JSONResponse:
    try:
        result = await parser().verify_login_code(code, password or None)
    except TelegramParserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(result)


@app.post("/fetch")
async def fetch() -> JSONResponse:
    try:
        result = await parser().fetch_all()
    except TelegramParserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(result)


@app.get("/posts")
async def posts(limit: int = 50) -> dict[str, Any]:
    db.init_db()
    items = db.list_posts(limit=max(1, min(200, limit)))
    return {"count": len(items), "posts": items}


@app.get("/channels")
async def channels() -> dict[str, Any]:
    db.init_db()
    return {"channels": db.list_channels()}
