from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from telegram_parser import db
from telegram_parser.config import CHANNELS_FILE


def load_channel_specs(path: Path | None = None) -> list[dict[str, Any]]:
    file_path = path or CHANNELS_FILE
    if not file_path.exists():
        example = file_path.with_name("channels.example.yaml")
        raise FileNotFoundError(
            f"Channel list not found: {file_path}. Copy {example.name} to {file_path.name}."
        )
    data = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
    items = data.get("channels") or []
    specs = []
    for item in items:
        username = str(item.get("username") or "").strip().lstrip("@")
        if not username:
            continue
        specs.append(
            {
                "username": username,
                "category": str(item.get("category") or "").strip(),
                "enabled": bool(item.get("enabled", True)),
            }
        )
    return specs


def sync_channels_from_file(path: Path | None = None) -> list[dict[str, Any]]:
    db.init_db()
    synced = []
    for spec in load_channel_specs(path):
        synced.append(
            db.upsert_channel(
                username=spec["username"],
                category=spec["category"],
                enabled=spec["enabled"],
            )
        )
    return synced
