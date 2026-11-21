"""Per-project memory: a short file of where the work stands, so a fresh session (or /clear)
continues from ~500 words instead of re-sending a long conversation every turn.

Project folders keep one file, <project>/.parsimon/memory.md. The home folder hosts many
unrelated tasks, so it keeps one file per task in ~/.parsimon/memory/<task>.md.
Claude writes the file when the hooks ask; the hooks only decide when, and load it.
"""
from __future__ import annotations

import time
from pathlib import Path

DIR = ".parsimon"
MAX_CHARS = 4_000  # cap on what gets injected at session start
MAX_LIST = 5


def is_home(cwd: str | Path) -> bool:
    try:
        return Path(cwd).resolve() == Path.home().resolve()
    except OSError:
        return False


def project_file(cwd: str | Path) -> Path:
    return Path(cwd) / DIR / "memory.md"


def task_dir(cwd: str | Path) -> Path:
    return Path(cwd) / DIR / "memory"


def _fresh(p: Path, max_age_days: float) -> bool:
    try:
        return time.time() - p.stat().st_mtime <= max_age_days * 86400
    except OSError:
        return False


def _first_line(p: Path) -> str:
    try:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip("# ").strip()
            if line:
                return line[:100]
    except OSError:
        pass
    return ""


def ensure_dir(cwd: str | Path) -> None:
    """Create .parsimon/ with its own .gitignore, so git skips it without touching the user's .gitignore."""
    d = Path(cwd) / DIR
    d.mkdir(parents=True, exist_ok=True)
    gi = d / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n", encoding="utf-8")


def startup_context(cwd: str | Path, max_age_days: float) -> str | None:
    """Text to inject at session start (startup or /clear), or None."""
    if is_home(cwd):
        files = sorted((p for p in task_dir(cwd).glob("*.md") if _fresh(p, max_age_days)),
                       key=lambda p: p.stat().st_mtime, reverse=True)[:MAX_LIST]
        if not files:
            return None
        listing = "\n".join(f"- {DIR}/memory/{p.name}: {_first_line(p)}" for p in files)
        return ("Parsimon task memories in this folder (read the one matching the user's request before "
                f"starting, and keep updating that same file):\n{listing}")
    p = project_file(cwd)
    if not p.is_file() or not _fresh(p, max_age_days):
        return None
    text = p.read_text(encoding="utf-8", errors="replace")[:MAX_CHARS]
    return ("Parsimon project memory (where the last session left off; check details against the files "
            f"before relying on them):\n{text}")


def write_instruction(cwd: str | Path, context: int, files: list[str]) -> str:
    if is_home(cwd):
        target = (f"{DIR}/memory/<short-task-name>.md in {Path(cwd)} (reuse the existing file if this session "
                  "continues a listed task; one file per task)")
    else:
        target = f"{DIR}/memory.md"
    edited = "\n".join(f"- {f}" for f in files[:30]) or "- (none edited yet)"
    return (
        f"Parsimon: context is about {context:,} tokens and every turn re-sends all of it. Before continuing, "
        f"rewrite {target} (rewrite the whole file, don't append; under 400 words): first line '# <task> - <date>', "
        "then goal, decisions and why, current state, files with line ranges, next step, open problems. "
        f"Files edited this session:\n{edited}\n"
        "Then tell the user in one line: type /clear to continue from this memory at a fraction of the cost."
    )
