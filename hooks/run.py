#!/usr/bin/env python3
"""Hook entry point. Claude Code runs: python3 run.py <event> with the hook JSON on stdin.

Runs straight from the plugin folder, so no pip install is needed. A hook must never
break the user's session: any error is swallowed and the hook exits 0 with no output.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))


def main() -> int:
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        payload = {}
    try:
        from parsimon import hooks

        out = hooks.dispatch(event, payload)
    except Exception:  # never take the session down
        return 0
    if out:
        sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
