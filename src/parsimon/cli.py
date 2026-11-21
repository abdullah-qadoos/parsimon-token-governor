"""parsimon report | audit | mode | check | bench"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, audit, bench, config, ledger, quality, report


def _prices(value: str) -> dict[str, float]:
    parts = [float(x) for x in value.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("--prices needs 4 numbers: input,cache_write,cache_read,output ($ per million)")
    return dict(zip(ledger.CATEGORIES, parts, strict=True))


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

    m = sub.add_parser("mode", help="show or set which parts are on: both, save, code, off")
    m.add_argument("mode", nargs="?", choices=config.MODES + tuple(config.LEGACY))

    c = sub.add_parser("check", help="run Parsimon's code checks on files")
    c.add_argument("files", nargs="+", type=Path)
    c.add_argument("--root", type=Path, default=Path.cwd())

    b = sub.add_parser("bench", help="paired A/B benchmark with Parsimon off vs on")
    bsub = b.add_subparsers(dest="bench_cmd", required=True)
    br = bsub.add_parser("run", help="run tasks headless with claude -p")
    br.add_argument("tasks", type=Path, help="tasks.json")
    br.add_argument("--reps", type=int, default=3)
    br.add_argument("--out", type=Path, default=Path("bench/results.jsonl"))
    br.add_argument("--claude", default="claude", help="path to the claude CLI")
    br.add_argument("--timeout", type=int, default=900)
    br.add_argument("--claude-args", default="--permission-mode acceptEdits", help="extra args for claude -p")
    bc = bsub.add_parser("compare", help="summarise a results file")
    bc.add_argument("results", type=Path)

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
        mode = config.set_mode(args.mode) if args.mode else config.get_mode()
        on = config.parts(mode)
        print(f"parsimon: token saver {'on' if 'save' in on else 'off'}, code checks {'on' if 'code' in on else 'off'}")
    elif args.cmd == "check":
        findings = []
        for f in args.files:
            findings += quality.check_file(f.resolve(), args.root.resolve())
        print(quality.summarize(findings, f"{len(findings)} findings") if findings else "No findings.")
        return 1 if findings else 0
    elif args.cmd == "bench":
        if args.bench_cmd == "run":
            bench.run(args.tasks, args.reps, args.out, args.claude, args.claude_args.split(), args.timeout)
            print(bench.render(bench.compare(bench.load_rows(args.out))))
        else:
            print(bench.render(bench.compare(bench.load_rows(args.results))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
