"""User settings in ~/.config/parsimon/. Only the mode and the plugin path are stored."""
from __future__ import annotations

import json
import os
from pathlib import Path

MODES = ("lite", "full", "ultra", "off")
DEFAULT_MODE = "full"


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


def get_mode() -> str:
    env = os.environ.get("PARSIMON_MODE", "").lower()
    if env in MODES:
        return env
    mode = str(load().get("mode", DEFAULT_MODE)).lower()
    return mode if mode in MODES else DEFAULT_MODE


def set_mode(mode: str) -> str:
    mode = mode.lower()
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    data = load()
    data["mode"] = mode
    config_dir().mkdir(parents=True, exist_ok=True)
    _config_file().write_text(json.dumps(data, indent=2), encoding="utf-8")
    return mode


def register_root(root: Path) -> None:
    """Remember where the plugin lives so skills can call its CLI."""
    config_dir().mkdir(parents=True, exist_ok=True)
    (config_dir() / "root").write_text(str(root.resolve()), encoding="utf-8")
