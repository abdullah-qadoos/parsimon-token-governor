"""Cache guard (L2) and session governor (L3).

Reads the live session transcript to find the current context size, when the model
last answered, and which files were touched, so a hook can warn about an expired cache
or ask for a memory update once the session gets large.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .ledger import _parse_time, _usage_numbers, ttl_for

TAIL_BYTES = 2_000_000
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
PREFIX_FILES = {"CLAUDE.md", "CLAUDE.local.md", ".mcp.json", "settings.json", "settings.local.json"}


def _entries(transcript: str | Path, tail: bool = True):
    path = Path(transcript)
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            if tail and size > TAIL_BYTES:
                fh.seek(size - TAIL_BYTES)
                fh.readline()  # drop the partial first line
            data = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for line in data.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict):
            out.append(e)
    return out


def snapshot(transcript: str | Path | None) -> dict:
    """Current context size, time of the last model turn, and touched files."""
    info = {"context": 0, "first_context": 0, "last_turn": None, "files": [], "ttl": ttl_for(0)}
    if not transcript:
        return info
    files: list[str] = []
    write_1h = 0
    for e in _entries(transcript):
        msg = e.get("message")
        if e.get("type") != "assistant" or not isinstance(msg, dict):
            continue
        usage = msg.get("usage")
        if isinstance(usage, dict):
            n = _usage_numbers(usage)
            ctx = n["input"] + n["cache_write"] + n["cache_read"]
            write_1h += n["cache_write_1h"]
            if ctx:
                info["first_context"] = info["first_context"] or ctx  # approximate if the tail cut the start
                info["context"] = ctx
                info["last_turn"] = _parse_time(e.get("timestamp")) or info["last_turn"]
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") in EDIT_TOOLS
                ):
                    inp = block.get("input") or {}
                    fp = inp.get("file_path") or inp.get("notebook_path")
                    if fp and fp not in files:
                        files.append(fp)
    info["files"] = files
    info["ttl"] = ttl_for(write_1h)
    return info


def idle_seconds(last_turn: datetime | None, now: datetime | None = None) -> float:
    if not last_turn:
        return 0.0
    now = now or datetime.now(timezone.utc)
    return (now - last_turn).total_seconds()


def cache_expired(snap: dict, min_tokens: int, now: datetime | None = None) -> bool:
    return snap["context"] >= min_tokens and idle_seconds(snap["last_turn"], now) > snap.get("ttl", ttl_for(0))


def is_prefix_file(path: str | None) -> bool:
    return bool(path) and Path(path).name in PREFIX_FILES
