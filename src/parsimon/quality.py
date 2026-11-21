"""Code-quality checks used by the hooks: outlines for big files, a reuse check for new
functions, fast per-file checks after edits, and a gate before the agent says it's done.

Everything degrades gracefully: if ruff or eslint aren't installed, those checks are skipped.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rb", ".java", ".cs", ".php", ".rs", ".kt", ".swift"}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build", ".next", "target", ".parsimon", ".tox", ".mypy_cache", "vendor"}
MAX_FILES = 4000
MAX_FILE_BYTES = 400_000
MAX_REPORT_LINES = 8

# Function / class definitions across common languages: captures the name.
DEF_PATTERNS = [
    re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\("),  # python
    re.compile(r"^\s*class\s+([A-Za-z_]\w*)"),  # python, js, ts, java, cs, php
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\("),  # js/ts
    re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"),  # js arrow
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\("),  # go
    re.compile(r"^\s*(?:pub\s+)?fn\s+([A-Za-z_]\w*)"),  # rust
]
COMMON_NAMES = {"main", "init", "__init__", "setup", "teardown", "run", "handler", "index", "default", "render", "constructor", "get", "set", "test", "app"}
SUFFIX = re.compile(r"(?:_?(?:v\d+|new|old|alt|copy|tmp|temp|helper|impl|util|utils|fn|func|\d+))+$")


# ---------- outline (smart reads) ----------

def count_lines(path: Path) -> int:
    try:
        with open(path, "rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def outline(path: Path, limit: int = 60) -> list[str]:
    """'line: name' entries for the definitions in a file."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    items: list[str] = []
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict):
            return [f"key {k!r}: about {len(json.dumps(v)) // 4:,} tokens" for k, v in list(data.items())[:limit]]
        if isinstance(data, list):
            return [f"top-level list of {len(data):,} items"]
    if path.suffix == ".py":
        try:
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    end = getattr(node, "end_lineno", node.lineno)
                    kind = "class" if isinstance(node, ast.ClassDef) else "def"
                    items.append((node.lineno, f"{node.lineno}-{end}: {kind} {node.name}"))
            return [s for _, s in sorted(items)][:limit]
        except SyntaxError:
            pass
    for i, line in enumerate(text.splitlines(), 1):
        for pat in DEF_PATTERNS:
            m = pat.match(line)
            if m:
                items.append((i, f"{i}: {m.group(1)}"))
                break
    return [s for _, s in items][:limit]


# ---------- reuse check ----------

def norm_name(name: str) -> str:
    base = SUFFIX.sub("", name.lower())
    return re.sub(r"[_\-$]", "", base) or name.lower()


def defined_names(text: str) -> list[str]:
    names = []
    for line in text.splitlines():
        for pat in DEF_PATTERNS:
            m = pat.match(line)
            if m:
                if m.group(1) not in names:
                    names.append(m.group(1))
                break
    return names


def iter_code_files(root: Path):
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix in CODE_EXT:
                try:
                    if p.stat().st_size > MAX_FILE_BYTES:
                        continue
                except OSError:
                    continue
                yield p
                count += 1
                if count >= MAX_FILES:
                    return


def index_definitions(root: Path, exclude: Path | None = None) -> dict[str, list[tuple[str, int, str]]]:
    """normalized name -> [(relative file, line, original name)]"""
    idx: dict[str, list[tuple[str, int, str]]] = {}
    ex = exclude.resolve() if exclude else None
    for p in iter_code_files(root):
        try:
            if ex and p.resolve() == ex:
                continue
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            for pat in DEF_PATTERNS:
                m = pat.match(line)
                if m:
                    name = m.group(1)
                    idx.setdefault(norm_name(name), []).append((p.relative_to(root).as_posix(), i, name))
                    break
    return idx


def new_definitions(tool: str, tool_input: dict, file_path: Path) -> list[str]:
    """Names this edit adds that weren't in the text it replaces."""
    if tool == "Write":
        new_text = tool_input.get("content") or ""
        try:
            old_text = file_path.read_text(encoding="utf-8", errors="replace") if file_path.exists() else ""
        except OSError:
            old_text = ""
    elif tool == "Edit":
        new_text, old_text = tool_input.get("new_string") or "", tool_input.get("old_string") or ""
    elif tool == "MultiEdit":
        edits = tool_input.get("edits") or []
        new_text = "\n".join(e.get("new_string", "") for e in edits if isinstance(e, dict))
        old_text = "\n".join(e.get("old_string", "") for e in edits if isinstance(e, dict))
    else:
        return []
    before = set(defined_names(old_text))
    return [n for n in defined_names(new_text) if n not in before and n not in COMMON_NAMES and not n.startswith("test")]


def reuse_matches(root: Path, file_path: Path, names: list[str]) -> list[str]:
    if not names:
        return []
    idx = index_definitions(root, exclude=file_path)
    hits = []
    for name in names:
        for rel, line, orig in idx.get(norm_name(name), [])[:3]:
            hits.append(f"`{name}` looks like existing `{orig}` at {rel}:{line}")
    return hits


# ---------- per-file checks ----------

def _tool(name: str, root: Path) -> str | None:
    for cand in (root / ".venv" / "bin" / name, root / "venv" / "bin" / name, root / "node_modules" / ".bin" / name):
        if cand.exists():
            return str(cand)
    return shutil.which(name)


def _run(cmd: list[str], cwd: Path, timeout: int = 30) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired):
        return 0, ""


def _python_findings(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(text)
    except SyntaxError as e:
        return [f"{path.name}:{e.lineno}: syntax error: {e.msg}"]
    except OSError:
        return []
    out = []
    seen_bodies: dict[str, str] = {}
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = ast.dump(ast.Module(body=node.body, type_ignores=[]))
            if len(node.body) >= 2 or len(body) > 200:
                h = hashlib.sha1(body.encode()).hexdigest()
                if h in seen_bodies:
                    out.append(f"{path.name}:{node.lineno}: `{node.name}` has the same body as `{seen_bodies[h]}`")
                else:
                    seen_bodies[h] = node.name
            key = norm_name(node.name)
            if key in names and names[key] != node.name:
                out.append(f"{path.name}:{node.lineno}: `{node.name}` looks like a variant of `{names[key]}`")
            names.setdefault(key, node.name)
    return out


def check_file(path: Path, root: Path) -> list[str]:
    """Short findings for one file. Empty list means clean (or nothing to check)."""
    if not path.exists() or path.suffix not in CODE_EXT:
        return []
    findings: list[str] = []
    if path.suffix == ".py":
        findings += _python_findings(path)
        ruff = _tool("ruff", root)
        if ruff and not any("syntax error" in f for f in findings):
            code, out = _run([ruff, "check", "--output-format", "concise", "--select", "F,E9,B", "--quiet", str(path)], root)
            if code:
                findings += [ln.replace(str(path), path.name) for ln in out.splitlines() if ln.strip() and not ln.startswith("Found")]
    elif path.suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
        eslint = root / "node_modules" / ".bin" / "eslint"
        if eslint.exists():
            code, out = _run([str(eslint), "--format", "unix", str(path)], root)
            if code:
                findings += [ln.replace(str(path), path.name) for ln in out.splitlines() if ln.strip() and ":" in ln]
    return findings


def summarize(findings: list[str], header: str) -> str:
    shown = findings[:MAX_REPORT_LINES]
    more = len(findings) - len(shown)
    lines = [header] + [f"- {f}" for f in shown]
    if more > 0:
        lines.append(f"- ...and {more} more")
    return "\n".join(lines)


def run_tests(command: str, root: Path, timeout: int) -> tuple[bool, str]:
    try:
        p = subprocess.run(command, cwd=root, shell=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"`{command}` timed out after {timeout}s"
    except OSError as e:
        return False, f"`{command}` could not run: {e}"
    if p.returncode == 0:
        return True, ""
    tail = [ln for ln in (p.stdout + p.stderr).strip().splitlines() if ln.strip()][-6:]
    return False, f"`{command}` failed:\n" + "\n".join(tail)
