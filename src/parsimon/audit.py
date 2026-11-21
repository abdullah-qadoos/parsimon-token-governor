"""Measure the boot payload Claude Code sends at the start of every turn.

Token counts are estimates (about 4 characters per token for English and code), which
is close enough to rank what to cut. MCP tool schemas can't be measured without
starting the servers, so servers are counted and each is flagged as a likely cost.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

CHARS_PER_TOKEN = 4
CLAUDE_MD_WARN_TOKENS = 2_000  # roughly a 200-line file
MCP_WARN_SERVERS = 3


@dataclass
class Item:
    kind: str
    path: str
    tokens: int
    note: str = ""


def estimate_tokens(text: str) -> int:
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _claude_home() -> Path:
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env) if env else Path.home() / ".claude"


def instruction_files(project: Path, home: Path | None = None) -> list[Path]:
    home = home or _claude_home()
    candidates = [
        home / "CLAUDE.md",
        project / "CLAUDE.md",
        project / ".claude" / "CLAUDE.md",
        project / "CLAUDE.local.md",
    ]
    # CLAUDE.md files in parent folders are also loaded.
    for parent in list(project.resolve().parents)[:4]:
        candidates.append(parent / "CLAUDE.md")
    seen, out = set(), []
    for p in candidates:
        try:
            key = p.resolve()
        except OSError:
            continue
        if key not in seen and p.is_file():
            seen.add(key)
            out.append(p)
    return out


def mcp_servers(project: Path, home: Path | None = None) -> list[tuple[str, str]]:
    """Return (server_name, config_file) pairs from the usual MCP config locations."""
    home = home or _claude_home()
    files = [project / ".mcp.json", home.parent / ".claude.json", home / "settings.json"]
    found = []
    for f in files:
        text = _read(f) if f.is_file() else None
        if not text:
            continue
        try:
            data = json.loads(text)
        except ValueError:
            continue
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        if isinstance(servers, dict):
            found += [(name, str(f)) for name in servers]
        projects = data.get("projects") if isinstance(data, dict) else None
        if isinstance(projects, dict):
            entry = projects.get(str(project.resolve()))
            if isinstance(entry, dict) and isinstance(entry.get("mcpServers"), dict):
                found += [(name, str(f)) for name in entry["mcpServers"]]
    return found


def run(project: Path, home: Path | None = None) -> dict:
    items: list[Item] = []
    for p in instruction_files(project, home):
        text = _read(p) or ""
        tokens = estimate_tokens(text)
        note = ""
        if tokens > CLAUDE_MD_WARN_TOKENS:
            note = "large: move rarely needed sections into docs the agent reads on demand"
        items.append(Item("instructions", str(p), tokens, note))
    servers = mcp_servers(project, home)
    return {
        "items": items,
        "instruction_tokens": sum(i.tokens for i in items),
        "mcp_servers": servers,
    }


def render(data: dict) -> str:
    lines = ["Parsimon boot audit (re-sent with every turn)", ""]
    if data["items"]:
        lines.append(f"{'Est. tokens':>11}  File")
        for i in sorted(data["items"], key=lambda x: x.tokens, reverse=True):
            lines.append(f"{i.tokens:>11,}  {i.path}" + (f"\n{'':>13}{i.note}" if i.note else ""))
        lines.append(f"{data['instruction_tokens']:>11,}  total in instruction files")
    else:
        lines.append("No CLAUDE.md files found.")
    lines.append("")
    servers = data["mcp_servers"]
    if servers:
        names = ", ".join(sorted({n for n, _ in servers}))
        lines.append(f"MCP servers configured: {len(servers)} ({names})")
        lines.append("Each server's tool schemas are sent every turn.")
        if len(servers) > MCP_WARN_SERVERS:
            lines.append("Disable the ones this project doesn't use.")
    else:
        lines.append("No MCP servers configured.")
    return "\n".join(lines)
