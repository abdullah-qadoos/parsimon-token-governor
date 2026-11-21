"""User settings in ~/.config/parsimon/. Only the mode (which parts are on) and the plugin path are stored."""
from __future__ import annotations

import json
import os
from pathlib import Path

# Two independent parts: "save" (token saver) and "code" (code-quality flags).
MODES = ("both", "save", "code", "off")
DEFAULT_MODE = "both"
LEGACY = {"full": "both", "lite": "both", "ultra": "both"}  # pre-0.3 modes


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "parsimon"


def _config_file() -> Path:
    return config_dir() / "config.json"


def load() -> dict:
    try:
        data = json.loads(_config_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _norm(mode: str) -> str | None:
    mode = LEGACY.get(mode.lower(), mode.lower())
    return mode if mode in MODES else None


def parts(mode: str) -> set[str]:
    return {"save", "code"} if mode == "both" else set() if mode == "off" else {mode}


def get_mode() -> str:
    env = _norm(os.environ.get("PARSIMON_MODE", ""))
    if env:
        return env
    return _norm(str(load().get("mode", DEFAULT_MODE))) or DEFAULT_MODE


def set_mode(mode: str) -> str:
    norm = _norm(mode)
    if not norm:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    data = load()
    data["mode"] = norm
    config_dir().mkdir(parents=True, exist_ok=True)
    _config_file().write_text(json.dumps(data, indent=2), encoding="utf-8")
    return norm


PROJECT_DEFAULTS = {
    "read_gate_lines": 400,  # Read of a bigger file with no line range gets an outline first
    "read_gate_bytes": 20_000,  # ...and so does any file this large (big JSON is often few lines)
    "memory": True,  # per-project memory file, loaded on startup and /clear
    "memory_every_tokens": 40_000,  # rewrite the memory each time this session's context grows by this much
    "memory_max_age_days": 14,  # older memories aren't loaded automatically
    "idle_warn_tokens": 40_000,  # only warn about an expired cache if the prefix is this big
    "reuse_check": True,
    "edit_checks": True,
    "test_command": None,  # e.g. "pytest -q -x"; run by the stop gate when set
    "test_timeout": 120,
}


def project_settings(cwd: str | Path | None) -> dict:
    """Defaults, overridden by the global config, overridden by <project>/.parsimon.json."""
    out = dict(PROJECT_DEFAULTS)
    glob = load().get("project_defaults")
    if isinstance(glob, dict):
        out.update({k: v for k, v in glob.items() if k in PROJECT_DEFAULTS})
    if cwd:
        try:
            local = json.loads((Path(cwd) / ".parsimon.json").read_text(encoding="utf-8"))
            if isinstance(local, dict):
                out.update({k: v for k, v in local.items() if k in PROJECT_DEFAULTS})
        except (OSError, ValueError):
            pass
    return out


def register_root(root: Path) -> None:
    """Remember where the plugin lives so skills can call its CLI."""
    config_dir().mkdir(parents=True, exist_ok=True)
    (config_dir() / "root").write_text(str(root.resolve()), encoding="utf-8")
