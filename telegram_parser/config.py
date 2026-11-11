from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "data"))
SESSION_PATH = DATA_DIR / "telegram"
CHANNELS_FILE = Path(os.environ.get("CHANNELS_FILE", ROOT / "channels.yaml"))


def _load_dotenv() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


_load_dotenv()


@dataclass(frozen=True)
class Config:
    api_id: str
    api_hash: str
    phone: str
    proxy_url: str
    fetch_limit: int
    cadence_minutes: int
    host: str
    port: int

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            api_id=(os.environ.get("TELEGRAM_API_ID") or "").strip(),
            api_hash=(os.environ.get("TELEGRAM_API_HASH") or "").strip(),
            phone=(os.environ.get("TELEGRAM_PHONE") or "").strip(),
            proxy_url=(os.environ.get("TELEGRAM_PROXY_URL") or "").strip(),
            fetch_limit=max(1, min(100, int(os.environ.get("FETCH_LIMIT") or 30))),
            cadence_minutes=max(1, int(os.environ.get("CADENCE_MINUTES") or 60)),
            host=(os.environ.get("HOST") or "127.0.0.1").strip(),
            port=int(os.environ.get("PORT") or 8001),
        )

    def missing(self) -> list[str]:
        missing = []
        if not self.api_id:
            missing.append("TELEGRAM_API_ID")
        if not self.api_hash:
            missing.append("TELEGRAM_API_HASH")
        if not self.phone:
            missing.append("TELEGRAM_PHONE")
        return missing
