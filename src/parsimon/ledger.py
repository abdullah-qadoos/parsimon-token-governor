"""Read Claude Code session transcripts and split token use by billing category.

Claude Code writes one JSONL file per session under ~/.claude/projects/<project>/.
Each assistant turn carries a `usage` block. Streaming can log the same API message
several times, so turns are de-duplicated by message id (or request id), keeping the
largest usage seen.

Categories:
  input        fresh, uncached input tokens
  cache_write  tokens written to the prompt cache (cache_creation_input_tokens)
  cache_read   tokens re-read from the prompt cache
  output       tokens the model generated

Cost is reported in "input-equivalent tokens" using relative price weights, because
absolute prices differ by model and change over time. Pass real per-million prices with
--prices to get dollars.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

# Relative to one fresh input token. Typical Anthropic ratios: a 5-minute cache write
# costs 1.25x input, a cache read 0.1x, and output about 5x.
DEFAULT_WEIGHTS = {"input": 1.0, "cache_write": 1.25, "cache_read": 0.1, "output": 5.0}

# A 1-hour cache write costs 2x input instead of 1.25x, so 1.6x the 5-minute write price.
WRITE_1H_FACTOR = 2.0 / 1.25

CATEGORIES = ("input", "cache_write", "cache_read", "output")

# Prompt cache lifetimes. A gap longer than the session's TTL before a turn usually means
# the whole prefix was written to the cache again. Sessions that write 1-hour cache use the long one.
CACHE_TTL_SECONDS = 5 * 60
CACHE_TTL_1H_SECONDS = 60 * 60

# A turn counts as a cache rebuild if it writes at least this many tokens after an idle gap.
REBUILD_MIN_TOKENS = 20_000


@dataclass
class Turn:
    timestamp: datetime | None
    model: str
    input: int = 0
    cache_write: int = 0
    cache_read: int = 0
    output: int = 0
    cache_write_1h: int = 0  # the part of cache_write that went to the 1-hour cache

    @property
    def context(self) -> int:
        """Total prompt size the model saw on this turn."""
        return self.input + self.cache_write + self.cache_read

    def total(self) -> int:
        return self.context + self.output


@dataclass
class Session:
    session_id: str
    project: str
    path: Path
    turns: list[Turn] = field(default_factory=list)

    def sums(self) -> dict[str, int]:
        s = {c: 0 for c in CATEGORIES}
        for t in self.turns:
            for c in CATEGORIES:
                s[c] += getattr(t, c)
        return s

    @property
    def write_1h(self) -> int:
        return sum(t.cache_write_1h for t in self.turns)

    @property
    def ttl(self) -> int:
        return ttl_for(self.write_1h)

    def cost(self, weights: dict[str, float] = DEFAULT_WEIGHTS) -> float:
        return cost_of(self.sums(), self.write_1h, weights)

    @property
    def peak_context(self) -> int:
        return max((t.context for t in self.turns), default=0)

    @property
    def start(self) -> datetime | None:
        stamps = [t.timestamp for t in self.turns if t.timestamp]
        return min(stamps) if stamps else None

    def cache_rebuilds(self) -> list[tuple[Turn, float]]:
        """Turns that re-wrote a large prefix after the cache had likely expired.

        Returns (turn, idle_seconds) pairs.
        """
        found = []
        prev = None
        for t in self.turns:
            if prev and prev.timestamp and t.timestamp:
                gap = (t.timestamp - prev.timestamp).total_seconds()
                if gap > self.ttl and t.cache_write >= REBUILD_MIN_TOKENS:
                    found.append((t, gap))
            prev = t
        return found


def ttl_for(write_1h_tokens: int) -> int:
    return CACHE_TTL_1H_SECONDS if write_1h_tokens else CACHE_TTL_SECONDS


def cost_of(sums: dict[str, int], write_1h: int, unit: dict[str, float]) -> float:
    """Cost of token sums; `unit` prices cache_write at the 5-minute rate, 1-hour writes cost more."""
    return sum(sums[c] * unit[c] for c in CATEGORIES) + write_1h * unit["cache_write"] * (WRITE_1H_FACTOR - 1)


def _parse_time(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _usage_numbers(usage: dict) -> dict[str, int]:
    def num(key):
        v = usage.get(key)
        return int(v) if isinstance(v, (int, float)) else 0

    cache_write = num("cache_creation_input_tokens")
    nested = usage.get("cache_creation")
    nested = nested if isinstance(nested, dict) else {}
    if not cache_write:
        cache_write = sum(int(v) for v in nested.values() if isinstance(v, (int, float)))
    w1h = nested.get("ephemeral_1h_input_tokens")
    return {
        "cache_write_1h": min(int(w1h), cache_write) if isinstance(w1h, (int, float)) else 0,
        "input": num("input_tokens"),
        "cache_write": cache_write,
        "cache_read": num("cache_read_input_tokens"),
        "output": num("output_tokens"),
    }


def parse_session(path: Path, project: str | None = None) -> Session:
    """Parse one transcript file. Malformed lines are skipped, never fatal."""
    turns: dict[str, Turn] = {}
    order: list[str] = []
    session_id = path.stem
    anon = 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            session_id = entry.get("sessionId", session_id)
            msg = entry.get("message")
            if entry.get("type") != "assistant" or not isinstance(msg, dict):
                continue
            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue
            key = msg.get("id") or entry.get("requestId")
            if not key:
                anon += 1
                key = f"_anon{anon}"
            nums = _usage_numbers(usage)
            turn = Turn(timestamp=_parse_time(entry.get("timestamp")), model=str(msg.get("model", "")), **nums)
            old = turns.get(key)
            if old is None:
                turns[key] = turn
                order.append(key)
            elif turn.total() > old.total():
                turn.timestamp = old.timestamp or turn.timestamp
                turns[key] = turn
    return Session(
        session_id=session_id,
        project=project or path.parent.name,
        path=path,
        turns=[turns[k] for k in order],
    )


def default_root() -> Path:
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(env) if env else Path.home() / ".claude"
    return base / "projects"


def find_transcripts(root: Path | None = None) -> Iterator[Path]:
    root = root or default_root()
    if not root.exists():
        return iter(())
    # Main sessions sit at <project>/<id>.jsonl; subagent runs sit deeper
    # (<project>/<id>/subagents/*.jsonl) and cost tokens too, so include them.
    return iter(sorted(root.rglob("*.jsonl")))


def load_sessions(
    root: Path | None = None,
    days: int | None = None,
    project: str | None = None,
    now: datetime | None = None,
) -> list[Session]:
    now = now or datetime.now(timezone.utc)
    root = root or default_root()
    sessions = []
    for path in find_transcripts(root):
        rel = path.relative_to(root).parts
        proj_name = rel[0] if len(rel) > 1 else path.parent.name
        if project and project.lower() not in proj_name.lower():
            continue
        s = parse_session(path, proj_name)
        if not s.turns:
            continue
        if days is not None and s.start and (now - s.start).total_seconds() > days * 86400:
            continue
        sessions.append(s)
    return sessions


def totals(sessions: Iterable[Session]) -> dict[str, int]:
    out = {c: 0 for c in CATEGORIES}
    for s in sessions:
        for c, v in s.sums().items():
            out[c] += v
    return out
