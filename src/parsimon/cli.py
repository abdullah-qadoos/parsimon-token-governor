"""parsimon report | audit | mode"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, audit, config, ledger, report


def _prices(value: str) -> dict[str, float]:
    parts = [float(x) for x in value.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("--prices needs 4 numbers: input,cache_write,cache_read,output ($ per million)")
    return dict(zip(ledger.CATEGORIES, parts))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="parsimon", description="Cache-aware token governor for coding agents")
    p.add_argument("--version", action="version", version=f"parsimon {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("report", help="where your Claude Code tokens and cost went")
    r.add_argument("--days", type=int, default=14, help="only sessions started in the last N days (0 = all)")
    r.add_argument("--project", help="only projects whose folder name contains this text")
    r.add_argument("--top", type=int, default=5, help="how many sessions to list")
    r.add_argument("--root", type=Path, help="transcript folder (default ~/.claude/projects)")
    r.add_argument("--prices", type=_prices, help="input,cache_write,cache_read,output in $ per million tokens")
    r.add_argument("--json", action="store_true", help="print raw numbers as JSON")

    a = sub.add_parser("audit", help="measure the boot payload re-sent every turn")
    a.add_argument("--project", type=Path, default=Path.cwd())

    m = sub.add_parser("mode", help="show or set the lean mode")
    m.add_argument("mode", nargs="?", choices=config.MODES)

    args = p.parse_args(argv)

    if args.cmd == "report":
        sessions = ledger.load_sessions(args.root, days=args.days or None, project=args.project)
        data = report.build(sessions, prices=args.prices)
        if args.json:
            print(json.dumps(data, indent=2, default=str))
        else:
            print(report.render(data, top=args.top))
    elif args.cmd == "audit":
        print(audit.render(audit.run(args.project)))
    elif args.cmd == "mode":
        if args.mode:
            print(f"parsimon mode: {config.set_mode(args.mode)}")
        else:
            print(f"parsimon mode: {config.get_mode()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
