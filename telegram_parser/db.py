from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from telegram_parser.config import DATA_DIR

DB_PATH = DATA_DIR / "parser.db"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                username TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                last_fetched_at TEXT,
                last_message_id INTEGER,
                error_message TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                posted_at TEXT,
                text TEXT NOT NULL DEFAULT '',
                post_url TEXT NOT NULL DEFAULT '',
                links_json TEXT NOT NULL DEFAULT '[]',
                views INTEGER,
                raw_json TEXT NOT NULL DEFAULT '{}',
                fetched_at TEXT NOT NULL,
                UNIQUE(channel_id, message_id),
                FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_posts_posted_at ON posts(posted_at DESC);
            CREATE INDEX IF NOT EXISTS idx_posts_channel ON posts(channel_id, posted_at DESC);
            """
        )


def upsert_channel(*, username: str, category: str = "", enabled: bool = True) -> dict[str, Any]:
    handle = username.strip().lstrip("@")
    now = _utc_now()
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT * FROM channels WHERE lower(username) = lower(?)", (handle,)
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE channels SET category = ?, enabled = ?
                WHERE id = ?
                """,
                (category.strip(), 1 if enabled else 0, existing["id"]),
            )
            row = conn.execute(
                "SELECT * FROM channels WHERE id = ?", (existing["id"],)
            ).fetchone()
            return dict(row)
        cur = conn.execute(
            """
            INSERT INTO channels (title, username, category, enabled, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (handle, handle, category.strip(), 1 if enabled else 0, now),
        )
        row = conn.execute(
            "SELECT * FROM channels WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)


def list_channels(*, enabled_only: bool = False) -> list[dict[str, Any]]:
    query = "SELECT * FROM channels"
    if enabled_only:
        query += " WHERE enabled = 1"
    query += " ORDER BY category COLLATE NOCASE, username COLLATE NOCASE"
    with get_connection() as conn:
        rows = conn.execute(query).fetchall()
    return [dict(r) for r in rows]


def update_channel(channel_id: int, **fields: Any) -> None:
    allowed = {
        "title",
        "last_fetched_at",
        "last_message_id",
        "error_message",
        "enabled",
        "category",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    if "enabled" in updates:
        updates["enabled"] = 1 if updates["enabled"] else 0
    cols = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [channel_id]
    with get_connection() as conn:
        conn.execute(f"UPDATE channels SET {cols} WHERE id = ?", values)


def upsert_post(
    *,
    channel_id: int,
    message_id: int,
    posted_at: str | None,
    text: str,
    links: list[str],
    views: int | None,
    raw: dict[str, Any],
    post_url: str = "",
) -> bool:
    cleaned = (text or "").strip()
    if not cleaned or cleaned == "[media without text]":
        return False
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM posts WHERE channel_id = ? AND message_id = ?",
            (channel_id, message_id),
        ).fetchone()
        if existing:
            return False
        conn.execute(
            """
            INSERT INTO posts (
                channel_id, message_id, posted_at, text, post_url, links_json,
                views, raw_json, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                channel_id,
                message_id,
                posted_at,
                cleaned,
                (post_url or "").strip(),
                json.dumps(links, ensure_ascii=False),
                views,
                json.dumps(raw, ensure_ascii=False, default=str),
                _utc_now(),
            ),
        )
        return True


def list_posts(*, limit: int = 50) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT p.*, c.title AS channel_title, c.username AS channel_username,
                   c.category AS category
            FROM posts p
            JOIN channels c ON c.id = p.channel_id
            ORDER BY COALESCE(p.posted_at, p.fetched_at) DESC, p.message_id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        try:
            item["links"] = json.loads(item.get("links_json") or "[]")
        except json.JSONDecodeError:
            item["links"] = []
        out.append(item)
    return out
