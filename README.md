# parsimon

**A cache-aware token governor for Claude Code.** It shows where your tokens actually go, cuts what gets re-sent every turn, and stops the agent writing code that shouldn't exist.

Most token-saving plugins are a prompt telling the model to write less. But in real sessions, output is a small slice of the bill: most of it is context that gets re-sent and re-cached on every turn. Parsimon measures that, cuts it with hooks that actually run, and checks the agent's code instead of just asking nicely.

## Install (Claude Code)

```
/plugin marketplace add abdullah-qadoos/parsimon-token-governor
/plugin install parsimon@parsimon
```

Start a new session. Needs Python 3.10+ on your PATH (`python3`, or `python` on Windows), and nothing else: no dependencies, no API keys, no network. If `ruff` or a project-local `eslint` is installed, the code checks use them too.

## Two parts: turn on either, both or none

| `/parsimon save`: token saver | `/parsimon code`: code checks |
|---|---|
| **Context rules** at session start, about 50 tokens: search before reading, read line ranges, keep output short | **Lean coding rules**, sent once **after the first edit**, so sessions that never edit code don't pay for them |
| **Smart reads**: a text file over 400 lines or 20 KB, read with no range, gets an outline instead (functions and classes with line numbers; top-level keys and sizes for JSON). Asking again reads the whole file | **Reuse check**: an edit that adds a function like an existing one (`format_price_v2` next to `format_price`) is told where the existing one is. Retrying the same edit goes through |
| **Cache guard**: after an idle gap longer than the cache lifetime (5 minutes, or 1 hour on the 1-hour cache) with a big context, tells you the cache expired. Also warns when CLAUDE.md or MCP config is edited mid-session | **Edit checks**: syntax errors, duplicate function bodies, `foo`/`foo_new` variants, ruff or eslint findings, in at most 8 lines. Unused imports and undefined names wait for the quality gate, since mid-edit they are usually just half-finished |
| **Project memory**: each project keeps `.parsimon/memory.md`, a short note of where the work stands. Once the session has grown by 80k tokens, the agent writes it, then rewrites it every further 40k. Below 80k, starting fresh costs more than it saves, because a new session re-saves about 20k tokens of Claude Code's own setup. On startup and `/clear` it's loaded, so a fresh session continues where the last one stopped for about 1k tokens instead of re-sending the whole conversation. In your home folder it keeps one memory per task and lists them. `.parsimon/` carries its own `.gitignore`, so it's never committed | **Quality gate**: before the agent finishes, re-checks every edited file and runs your `test_command`. The agent can't say "done" while they fail. Never loops |
| **Boot audit warning** when CLAUDE.md files are heavy | |

Both parts are on by default. With both on, Parsimon adds about 140 tokens to each request, against about 620 in v0.2.

## Commands

One skill, `/parsimon`:

| Command | |
|---|---|
| `/parsimon` | Show which parts are on |
| `/parsimon both` · `save` · `code` · `off` | Turn on both parts, one of them, or none |
| `/parsimon report` | Where your tokens went: cache writes, cache reads, input and output across your real sessions, with cache rebuilds and oversized sessions flagged |
| `/parsimon audit` | What's in the boot payload (CLAUDE.md files, MCP servers) |
| `/parsimon review` | A delete / reuse / simplify list for the current diff, backed by the checks |
| `/parsimon resume` | Continue from the project memory (`/parsimon resume save` writes it now) |

`parsimon bench` runs paired A/B benchmarks, Parsimon off vs on (see below).

## Per-project settings

Optional `.parsimon.json` in the project root:

```json
{
  "test_command": "pytest -q -x",
  "test_timeout": 120,
  "read_gate_lines": 400,
  "read_gate_bytes": 20000,
  "memory": true,
  "memory_min_history_tokens": 80000,
  "memory_every_tokens": 40000,
  "memory_max_age_days": 14,
  "idle_warn_tokens": 40000,
  "reuse_check": true,
  "edit_checks": true
}
```

`PARSIMON_MODE=both|save|code|off` in the environment overrides the saved setting, which is how the benchmark runs its baseline arm. Old modes (`full`, `lite`, `ultra`) are read as `both`.

## Benchmark

```
python3 hooks/cli.py bench run bench/tasks.json --reps 5
python3 hooks/cli.py bench compare bench/results.jsonl
```

Each task runs in a fresh copy of its repo with `claude -p`, alternating Parsimon off and on in random order. The task's `check` command decides success, so savings from failing tasks don't count. Example output:

```
Parsimon benchmark: 15 paired runs
Median cost change with Parsimon: -X.X% (95% CI ... to ...)
baseline  task success 15/15 (100%)
parsimon  task success 15/15 (100%)
```

No numbers are published yet. Run it on your own tasks, and only trust the confidence interval.

## How it stays cheap and safe

- Every block or warning fires at most once per item per session, and is kept short, because hook output is re-sent on later turns too.
- Hooks never crash a session. Any internal error means no output.
- The stop gate respects `stop_hook_active`, so it can't loop.
- State lives in `~/.config/parsimon/` and is cleaned up after 7 days.

## Layout

```
.claude-plugin/   plugin + marketplace manifests
hooks/            hooks.json, run.py (hook entry), cli.py (CLI without install)
skills/           parsimon (one skill: mode switch, report, audit, review, resume)
src/parsimon/     ledger, report, audit, governor, memory, quality, bench, hooks, state, config, cli
bench/            sample tasks + a tiny repo
tests/            40 tests
DESIGN.md         design notes
```

## Development

```
pip install -e . pytest
pytest
```

## Credits

The reuse-before-you-write rules draw on the idea popularised by [Ponytail](https://github.com/DietrichGebert/ponytail) (MIT). Parsimon goes after the context cost and code checks that rules alone can't reach.

## License

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Released under the [MIT License](LICENSE).
