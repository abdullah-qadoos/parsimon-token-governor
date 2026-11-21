"""Hook handlers. Each returns the JSON object Claude Code expects, or None for no output."""
from __future__ import annotations

from pathlib import Path

from . import audit, config

# Kept short on purpose: this text is itself re-sent every turn, so every word costs.
RULES = {
    "lite": (
        "Parsimon lite: build what was asked; if a smaller solution exists, mention it in one line."
    ),
    "full": (
        "Parsimon (lean mode). Before new code: does it need to exist, does the codebase, stdlib, "
        "platform or an installed dependency already do it? Reuse first; edit existing functions, no "
        "near-duplicates or single-use abstractions, no dead code. Keep validation, error handling and "
        "security. Context: search before reading, read line ranges not whole large files, keep command "
        "output short (tail/grep, run the failing test only), don't re-read unchanged files. Replies: what "
        "changed, where, how verified."
    ),
}
RULES["ultra"] = RULES["full"] + " Ultra: challenge speculative requirements and prefer deleting code to adding it."

PLUGIN_ROOT = Path(__file__).resolve().parents[2]


def session_start(payload: dict) -> dict | None:
    try:
        config.register_root(PLUGIN_ROOT)
    except OSError:
        pass
    mode = config.get_mode()
    if mode == "off":
        return None
    parts = [RULES[mode]]
    cwd = payload.get("cwd")
    if cwd:
        data = audit.run(Path(cwd))
        if data["instruction_tokens"] > audit.CLAUDE_MD_WARN_TOKENS:
            parts.append(
                f"Note for the user (mention once, briefly): instruction files load about "
                f"{data['instruction_tokens']:,} tokens every turn. /parsimon-audit shows what to trim."
            )
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "\n".join(parts),
        }
    }


HANDLERS = {"session-start": session_start}


def dispatch(event: str, payload: dict) -> dict | None:
    handler = HANDLERS.get(event)
    return handler(payload) if handler else None
