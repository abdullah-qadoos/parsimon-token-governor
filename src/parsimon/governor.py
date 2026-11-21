"""Cache guard (L2) and session governor (L3).

Reads the live session transcript to find the current context size, when the model
last answered, and which files were touched, so a hook can warn about an expired cache
or ask for a handoff once the session gets large.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .ledger import CACHE_TTL_SECONDS, _parse_time, _usage_numbers

TAIL_BYTES = 2_000_000
HANDOFF_DIR = ".parsimon"
HANDOFF_FILE = "handoff.md"
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
    info = {"context": 0, "last_turn": None, "files": []}
    if not transcript:
        return info
    files: list[str] = []
    for e in _entries(transcript):
        msg = e.get("message")
        if e.get("type") != "assistant" or not isinstance(msg, dict):
            continue
        usage = msg.get("usage")
        if isinstance(usage, dict):
            n = _usage_numbers(usage)
            ctx = n["input"] + n["cache_write"] + n["cache_read"]
            if ctx:
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
    return info


def idle_seconds(last_turn: datetime | None, now: datetime | None = None) -> float:
    if not last_turn:
        return 0.0
    now = now or datetime.now(timezone.utc)
    return (now - last_turn).total_seconds()


def cache_expired(snap: dict, min_tokens: int, now: datetime | None = None) -> bool:
    return snap["context"] >= min_tokens and idle_seconds(snap["last_turn"], now) > CACHE_TTL_SECONDS


def is_prefix_file(path: str | None) -> bool:
    return bool(path) and Path(path).name in PREFIX_FILES


def handoff_path(cwd: str | Path) -> Path:
    return Path(cwd) / HANDOFF_DIR / HANDOFF_FILE


def handoff_instruction(cwd: str | Path, snap: dict) -> str:
    rel = f"{HANDOFF_DIR}/{HANDOFF_FILE}"
    files = "\n".join(f"- {f}" for f in snap["files"][:30]) or "- (none edited yet)"
    return (
        f"Parsimon: this session's context is about {snap['context']:,} tokens, and every turn re-sends all of it. "
        f"Before continuing, write a handoff to {rel} (create the folder if needed) in under 300 words with: "
        "goal, decisions made and why, current state, next steps, open problems. "
        f"Files edited this session (include them):\n{files}\n"
        "Then tell the user in one line: start a fresh session and run /parsimon-resume to continue cheaply."
    )
