"""Hook handlers. Each returns the JSON object Claude Code expects, or None for no output.

Events and what they do:
  session-start       lean rules + boot-size warning                    (L1, L5)
  user-prompt-submit  warn when an idle gap has expired a large cache     (L2)
  pre-tool-use        outline-first for big Reads; reuse check on edits  (L4, quality)
  post-tool-use       fast checks on edited files; prefix-change warning (quality, L2)
  stop                quality gate, then handoff when context is large  (quality, L3)

Rule of thumb: every block or warning fires at most once per thing per session, and the
text it adds is short, because anything a hook says is itself re-sent on later turns.
"""
from __future__ import annotations

from pathlib import Path

from . import audit, config, governor, quality, state

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
EDIT_TOOLS = {"Edit", "Write", "MultiEdit"}
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

def session_start(payload: dict, mode: str) -> dict | None:
    try:
        config.register_root(PLUGIN_ROOT)
        state.prune()
    except OSError:
        pass
    parts = [RULES[mode]]
    cwd = payload.get("cwd")
    if cwd:
        data = audit.run(Path(cwd))
        if data["instruction_tokens"] > audit.CLAUDE_MD_WARN_TOKENS:
            parts.append(
                f"Note for the user (mention once, briefly): instruction files load about "
                f"{data['instruction_tokens']:,} tokens every turn. /parsimon-audit shows what to trim."
            )
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "\n".join(parts)}}


# ---------- L2: expired cache ----------

def user_prompt_submit(payload: dict, mode: str) -> dict | None:
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
    return {
        "systemMessage": (
            f"Parsimon: {minutes} min idle, so the prompt cache has expired. This turn re-writes about "
            f"{snap['context']:,} tokens of context. For a cheaper restart, run /compact or start fresh with /parsimon-resume."
        )
    }


# ---------- L4 + quality: before a tool runs ----------

def pre_tool_use(payload: dict, mode: str) -> dict | None:
    tool = payload.get("tool_name")
    inp = payload.get("tool_input") or {}
    path, root = _target(payload)
    if path is None:
        return None
    settings = config.project_settings(root)
    sid = payload.get("session_id", "")

    if tool == "Read":
        if inp.get("offset") or inp.get("limit") or not path.is_file():
            return None
        if path.suffix.lower() not in quality.CODE_EXT | TEXT_EXT:
            return None  # images, PDFs, notebooks are read differently: leave them alone
        lines = quality.count_lines(path)
        if lines <= settings["read_gate_lines"]:
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
            f"Parsimon: {path.name} has {lines:,} lines. Read only the part you need with offset/limit. "
            f"Outline:\n{listing}\nIf you really need the whole file, request the same Read again."
        )

    if tool in EDIT_TOOLS and mode in ("full", "ultra") and settings["reuse_check"] and _inside(path, root):
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

def post_tool_use(payload: dict, mode: str) -> dict | None:
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
    if governor.is_prefix_file(str(path)) and not st.get("prefix_warned"):
        st["prefix_warned"] = True
        messages.append(
            f"Parsimon: {path.name} is part of the cached prefix. Changing it mid-session re-writes the cache on "
            "the next turn; batch such edits or make them at the start of a session."
        )
    state.save(sid, st)
    settings = config.project_settings(root)
    if settings["edit_checks"] and _inside(path, root):
        findings = quality.check_file(path, root)
        if findings:
            messages.append(quality.summarize(findings, f"Parsimon checks on {path.name}: fix these now."))
    if not messages:
        return None
    return {"decision": "block", "reason": "\n".join(messages)}


# ---------- quality gate, then L3 handoff ----------

def stop(payload: dict, mode: str) -> dict | None:
    if payload.get("stop_hook_active"):
        return None  # already continuing because of a Stop hook: never loop
    sid = payload.get("session_id", "")
    root = Path(payload.get("cwd") or ".")
    settings = config.project_settings(root)
    st = state.load(sid)

    if mode in ("full", "ultra") and st.get("edited") and not st.get("gate_passed_for") == len(st["edited"]):
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

    snap = governor.snapshot(payload.get("transcript_path"))
    if snap["context"] >= settings["handoff_at_tokens"] and not st.get("handoff_requested"):
        st["handoff_requested"] = True
        state.save(sid, st)
        return {"decision": "block", "reason": governor.handoff_instruction(root, snap)}
    return None


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
    mode = config.get_mode()
    if mode == "off":
        if event == "session-start":
            try:
                config.register_root(PLUGIN_ROOT)
            except OSError:
                pass
        return None
    return handler(payload, mode)
