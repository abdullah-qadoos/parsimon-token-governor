"""Hook handlers. Each returns the JSON object Claude Code expects, or None for no output.

Two independent parts, switched with /parsimon both|save|code|off:

  save (token saver)                         code (code-quality flags)
  session-start: short context rules         post-tool-use: lean coding rules on the first edit
  pre-tool-use: outline-first big Reads      pre-tool-use: reuse check on new functions
  user-prompt-submit: expired-cache warning  post-tool-use: syntax, duplicate and lint checks
  post-tool-use: cached-prefix edit warning  stop: quality gate (checks + test_command)
  session-start + stop: per-project memory

Rule of thumb: every block or warning fires at most once per thing per session, and the
text it adds is short, because anything a hook says is itself re-sent on later turns.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import audit, config, governor, memory, quality, state

# Sent at session start when "save" is on.
SAVE_RULES = (
    "Parsimon: search before reading; read line ranges, not whole large files; keep command output "
    "short (tail/grep, run only the failing test); don't re-read unchanged files; keep replies short."
)
# Sent once, after the first edit, when "code" is on: sessions that never edit code don't pay for it.
CODE_RULES = (
    "Parsimon code rules: before new code, check whether it needs to exist and whether the codebase, stdlib "
    "or an installed dependency already does it. Edit existing functions instead of adding near-duplicates; "
    "no single-use abstractions or dead code. Keep validation, error handling and security."
)

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
EDIT_TOOLS = {"Edit", "Write", "MultiEdit"}
# Unused import / undefined name / unused variable: usually just a half-finished multi-step edit
# (import added before its use). Reported by the stop gate instead of after every edit.
MID_EDIT = re.compile(r":\d+:\d+: F(401|821|841) ")
TEXT_EXT = {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".csv", ".log", ".html", ".css", ".scss", ".sql", ".xml", ".sh", ".cfg", ".ini", ".env"}


def _deny(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _target(payload: dict) -> tuple[Path | None, Path]:
    root = Path(payload.get("cwd") or ".")
    inp = payload.get("tool_input") or {}
    fp = inp.get("file_path") or inp.get("notebook_path")
    if not fp:
        return None, root
    p = Path(fp)
    return (p if p.is_absolute() else root / p), root


# ---------- session start ----------

def session_start(payload: dict, on: set[str]) -> dict | None:
    try:
        config.register_root(PLUGIN_ROOT)
        state.prune()
    except OSError:
        pass
    if "save" not in on:
        return None
    parts = [SAVE_RULES]
    cwd = payload.get("cwd")
    if cwd:
        settings = config.project_settings(cwd)
        # "resume" already has the conversation; "compact" already has a summary.
        if settings["memory"] and payload.get("source", "startup") in ("startup", "clear"):
            mem = memory.startup_context(cwd, settings["memory_max_age_days"])
            if mem:
                parts.append(mem)
        data = audit.run(Path(cwd))
        if data["instruction_tokens"] > audit.CLAUDE_MD_WARN_TOKENS:
            parts.append(
                f"Note for the user (mention once, briefly): instruction files load about "
                f"{data['instruction_tokens']:,} tokens every turn. /parsimon audit shows what to trim."
            )
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "\n".join(parts)}}


# ---------- L2: expired cache ----------

def user_prompt_submit(payload: dict, on: set[str]) -> dict | None:
    if "save" not in on:
        return None
    settings = config.project_settings(payload.get("cwd"))
    snap = governor.snapshot(payload.get("transcript_path"))
    if not governor.cache_expired(snap, settings["idle_warn_tokens"]):
        return None
    sid = payload.get("session_id", "")
    st = state.load(sid)
    stamp = snap["last_turn"].isoformat() if snap["last_turn"] else ""
    if st.get("idle_warned_for") == stamp:
        return None
    st["idle_warned_for"] = stamp
    state.save(sid, st)
    minutes = int(governor.idle_seconds(snap["last_turn"]) // 60)
    cwd = payload.get("cwd") or "."
    has_memory = memory.project_file(cwd).is_file() or any(memory.task_dir(cwd).glob("*.md"))
    tip = ("Type /clear to continue from this project's Parsimon memory instead." if has_memory
           else "For a cheaper restart, run /compact.")
    return {
        "systemMessage": (
            f"Parsimon: {minutes} min idle, so the prompt cache has expired. This turn re-writes about "
            f"{snap['context']:,} tokens of context. {tip}"
        )
    }


# ---------- L4 + quality: before a tool runs ----------

def pre_tool_use(payload: dict, on: set[str]) -> dict | None:
    tool = payload.get("tool_name")
    inp = payload.get("tool_input") or {}
    path, root = _target(payload)
    if path is None:
        return None
    settings = config.project_settings(root)
    sid = payload.get("session_id", "")

    if tool == "Read" and "save" in on:
        if inp.get("offset") or inp.get("limit") or not path.is_file():
            return None
        if path.suffix.lower() not in quality.CODE_EXT | TEXT_EXT:
            return None  # images, PDFs, notebooks are read differently: leave them alone
        lines = quality.count_lines(path)
        size = path.stat().st_size
        if lines <= settings["read_gate_lines"] and size <= settings["read_gate_bytes"]:
            return None
        st = state.load(sid)
        gated = st.setdefault("read_gated", [])
        key = str(path.resolve())
        if key in gated:  # second request for the whole file: let it through
            return None
        gated.append(key)
        state.save(sid, st)
        items = quality.outline(path)
        listing = "\n".join(items) if items else "(no definitions found)"
        return _deny(
            f"Parsimon: {path.name} has {lines:,} lines (about {size // 4:,} tokens). Read only the part you need "
            "with offset/limit, or search it with Grep. "
            f"Outline:\n{listing}\nIf you really need the whole file, request the same Read again."
        )

    if tool in EDIT_TOOLS and "code" in on and settings["reuse_check"] and _inside(path, root):
        names = quality.new_definitions(tool, inp, path)
        if not names:
            return None
        st = state.load(sid)
        warned = st.setdefault("reuse_warned", [])
        names = [n for n in names if f"{path}:{n}" not in warned]
        hits = quality.reuse_matches(root, path, names)
        if not hits:
            return None
        warned += [f"{path}:{n}" for n in names]
        state.save(sid, st)
        return _deny(
            quality.summarize(hits, "Parsimon reuse check: this edit adds functions that may already exist.")
            + "\nReuse or extend the existing code. If the new one is genuinely different, retry the same edit."
        )
    return None


# ---------- quality + L2: after a tool runs ----------

def post_tool_use(payload: dict, on: set[str]) -> dict | None:
    tool = payload.get("tool_name")
    if tool not in EDIT_TOOLS:
        return None
    path, root = _target(payload)
    if path is None:
        return None
    sid = payload.get("session_id", "")
    st = state.load(sid)
    edited = st.setdefault("edited", [])
    if str(path) not in edited:
        edited.append(str(path))
    messages = []
    rules = ""
    if "code" in on and not st.get("code_rules_sent"):
        st["code_rules_sent"] = True
        rules = CODE_RULES
    if "save" in on and governor.is_prefix_file(str(path)) and not st.get("prefix_warned"):
        st["prefix_warned"] = True
        messages.append(
            f"Parsimon: {path.name} is part of the cached prefix. Changing it mid-session re-writes the cache on "
            "the next turn; batch such edits or make them at the start of a session."
        )
    state.save(sid, st)
    settings = config.project_settings(root)
    if "code" in on and settings["edit_checks"] and _inside(path, root):
        findings = [f for f in quality.check_file(path, root) if not MID_EDIT.search(f)]
        if findings:
            messages.append(quality.summarize(findings, f"Parsimon checks on {path.name}: fix these now."))
    if messages:
        return {"decision": "block", "reason": "\n".join(messages + ([rules] if rules else []))}
    if rules:
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": rules}}
    return None


# ---------- quality gate, then memory ----------

def stop(payload: dict, on: set[str]) -> dict | None:
    if payload.get("stop_hook_active"):
        return None  # already continuing because of a Stop hook: never loop
    sid = payload.get("session_id", "")
    root = Path(payload.get("cwd") or ".")
    settings = config.project_settings(root)
    st = state.load(sid)

    if "code" in on and st.get("edited") and not st.get("gate_passed_for") == len(st["edited"]):
        findings = []
        for f in st["edited"]:
            p = Path(f)
            if _inside(p, root):
                findings += quality.check_file(p, root)
        test_msg = ""
        if settings["test_command"]:
            ok, test_msg = quality.run_tests(settings["test_command"], root, int(settings["test_timeout"]))
            if ok:
                test_msg = ""
        if findings or test_msg:
            body = quality.summarize(findings, "Parsimon quality gate: not done yet.") if findings else "Parsimon quality gate: not done yet."
            if test_msg:
                body += "\n" + test_msg
            return {"decision": "block", "reason": body}
        st["gate_passed_for"] = len(st["edited"])
        state.save(sid, st)

    if "save" not in on:
        return None
    if not settings["memory"]:
        return None
    snap = governor.snapshot(payload.get("transcript_path"))
    # Growth since this session started is the history a fresh start would drop. A fresh start re-writes
    # ~20k of system prompt and tools, so memory + /clear only pays off once that history is large
    # (benchmark: at ~10k it cost 13% more). Then refresh the memory every `memory_every_tokens`.
    first = snap["first_context"] + int(settings["memory_min_history_tokens"])
    if snap["context"] < st.get("memory_next", first):
        return None
    st["memory_next"] = snap["context"] + int(settings["memory_every_tokens"])
    state.save(sid, st)
    try:
        memory.ensure_dir(root)
    except OSError:
        return None
    return {"decision": "block", "reason": memory.write_instruction(root, snap["context"], snap["files"])}


HANDLERS = {
    "session-start": session_start,
    "user-prompt-submit": user_prompt_submit,
    "pre-tool-use": pre_tool_use,
    "post-tool-use": post_tool_use,
    "stop": stop,
}


def dispatch(event: str, payload: dict) -> dict | None:
    handler = HANDLERS.get(event)
    if not handler:
        return None
    on = config.parts(config.get_mode())
    if not on:
        if event == "session-start":
            try:
                config.register_root(PLUGIN_ROOT)
            except OSError:
                pass
        return None
    return handler(payload, on)
