"""Small per-session state, so hooks can act once per session instead of nagging every turn."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import config

MAX_AGE_SECONDS = 7 * 86400


def _path(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "unknown")[:80]
    return config.config_dir() / "state" / f"{safe}.json"


def load(session_id: str) -> dict:
    try:
        data = json.loads(_path(session_id).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(session_id: str, data: dict) -> None:
    p = _path(session_id)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def prune() -> None:
    """Delete state files older than a week."""
    folder = config.config_dir() / "state"
    if not folder.is_dir():
        return
    cutoff = time.time() - MAX_AGE_SECONDS
    for f in folder.glob("*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass
