"""Paired A/B benchmark: the same task, run with Parsimon off and on, several times each.

`run` drives headless Claude Code (`claude -p ... --output-format json`) in a fresh copy
of each task's repo and records cost, turns, token usage and whether the task's check
command passed. `compare` reports the median per-pair cost change with a bootstrap 95%
confidence interval, plus success rates, so savings never come from failing tasks.

tasks.json:
  [{"id": "fix-price", "repo": "bench/repos/shop", "prompt": "...", "check": "{python} -m pytest -q"}]
  {python} in check becomes the interpreter running the benchmark.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .ledger import DEFAULT_WEIGHTS, _usage_numbers, cost_of

ARMS = ("baseline", "parsimon")
ARM_MODE = {"baseline": "off", "parsimon": "full"}


def load_tasks(path: Path) -> list[dict]:
    tasks = json.loads(Path(path).read_text(encoding="utf-8"))
    for t in tasks:
        for key in ("id", "repo", "prompt"):
            if key not in t:
                raise ValueError(f"task {t!r} is missing '{key}'")
    return tasks


def _usage_cost(usage: dict) -> float:
    nums = _usage_numbers(usage)
    return cost_of(nums, nums.pop("cache_write_1h"), DEFAULT_WEIGHTS)


def run_one(task: dict, arm: str, rep: int, base: Path, claude: str, extra_args: list[str], timeout: int) -> dict:
    src = (base / task["repo"]).resolve()
    work = Path(tempfile.mkdtemp(prefix=f"parsimon-{task['id']}-{arm}-"))
    shutil.copytree(src, work, dirs_exist_ok=True)
    env = dict(os.environ, PARSIMON_MODE=ARM_MODE[arm])
    started = time.time()
    row = {"task": task["id"], "arm": arm, "rep": rep, "ok": False}
    try:
        p = subprocess.run(
            [claude, "-p", task["prompt"], "--output-format", "json", *extra_args],
            cwd=work, env=env, capture_output=True, text=True, timeout=timeout,
        )
        out = json.loads(p.stdout) if p.stdout.strip().startswith("{") else {}
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        out = {"error": str(e)}
    row["seconds"] = round(time.time() - started, 1)
    row["session_id"] = out.get("session_id")
    row["turns"] = out.get("num_turns")
    usage = out.get("usage") if isinstance(out.get("usage"), dict) else {}
    row["usage"] = {k: usage.get(k) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")}
    row["cost_usd"] = out.get("total_cost_usd")
    row["cost_units"] = _usage_cost(usage)
    if task.get("check"):
        try:
            c = subprocess.run(task["check"].replace("{python}", f'"{sys.executable}"'), cwd=work, shell=True, capture_output=True, timeout=300)
            row["ok"] = c.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            row["ok"] = False
    else:
        row["ok"] = not out.get("is_error", False) and "error" not in out
    shutil.rmtree(work, ignore_errors=True)
    return row


def run(tasks_file: Path, reps: int, out_file: Path, claude: str = "claude", extra_args: list[str] | None = None,
        timeout: int = 900, seed: int = 0, log=print) -> Path:
    tasks = load_tasks(tasks_file)
    base = Path(tasks_file).resolve().parent
    extra_args = extra_args if extra_args is not None else ["--permission-mode", "acceptEdits"]
    jobs = [(t, arm, r) for t in tasks for r in range(reps) for arm in ARMS]
    random.Random(seed).shuffle(jobs)  # interleave arms so time-of-day effects even out
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "a", encoding="utf-8") as fh:
        for i, (t, arm, r) in enumerate(jobs, 1):
            row = run_one(t, arm, r, base, claude, extra_args, timeout)
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            log(f"[{i}/{len(jobs)}] {t['id']} {arm} rep {r}: ok={row['ok']} cost={row['cost_usd'] or round(row['cost_units'])}")
    return out_file


def _cost(row: dict) -> float | None:
    v = row.get("cost_usd")
    if isinstance(v, (int, float)) and v > 0:
        return float(v)
    v = row.get("cost_units")
    return float(v) if isinstance(v, (int, float)) and v > 0 else None


def bootstrap_ci(values: list[float], iters: int = 5000, seed: int = 0) -> tuple[float, float]:
    rng = random.Random(seed)
    meds = sorted(statistics.median(rng.choices(values, k=len(values))) for _ in range(iters))
    return meds[int(0.025 * iters)], meds[int(0.975 * iters) - 1]


def compare(rows: list[dict]) -> dict:
    by_key: dict[tuple, dict] = {}
    for r in rows:
        by_key.setdefault((r["task"], r["rep"]), {})[r["arm"]] = r
    changes = []
    for pair in by_key.values():
        b, p = pair.get("baseline"), pair.get("parsimon")
        if not b or not p:
            continue
        cb, cp = _cost(b), _cost(p)
        if cb and cp:
            changes.append((cp - cb) / cb)
    success = {
        arm: (sum(1 for r in rows if r["arm"] == arm and r.get("ok")), sum(1 for r in rows if r["arm"] == arm))
        for arm in ARMS
    }
    result = {"pairs": len(changes), "success": success}
    if changes:
        lo, hi = bootstrap_ci(changes) if len(changes) >= 3 else (min(changes), max(changes))
        result.update(median_change=statistics.median(changes), ci=(lo, hi))
    return result


def render(res: dict) -> str:
    lines = [f"Parsimon benchmark: {res['pairs']} paired runs"]
    if "median_change" in res:
        lo, hi = res["ci"]
        lines.append(f"Median cost change with Parsimon: {res['median_change'] * 100:+.1f}% (95% CI {lo * 100:+.1f}% to {hi * 100:+.1f}%)")
        if hi < 0:
            lines.append("The whole interval is below zero: a real saving at this sample size.")
        elif lo > 0:
            lines.append("The whole interval is above zero: Parsimon cost more here.")
        else:
            lines.append("The interval crosses zero: no clear effect yet. Run more reps.")
    for arm, (ok, n) in res["success"].items():
        if n:
            lines.append(f"{arm:<9} task success {ok}/{n} ({ok / n * 100:.0f}%)")
    return "\n".join(lines)


def load_rows(path: Path) -> list[dict]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows
