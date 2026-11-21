# parsimon

**A cache-aware token governor for Claude Code.** It shows where your tokens actually go, cuts what gets re-sent every turn, and stops the agent writing code that shouldn't exist.

Most token-saving plugins are a prompt telling the model to write less. But in real sessions, output is a small slice of the bill: most of it is context that gets re-sent and re-cached on every turn. Parsimon measures that, cuts it with hooks that actually run, and checks the agent's code instead of just asking nicely.

## Install (Claude Code)

```
/plugin marketplace add abdullah-qadoos/parsimon-token-governor
/plugin install parsimon@parsimon
```

Start a new session. Needs Python 3.10+ on your PATH (`python3`, or `python` on Windows), and nothing else: no dependencies, no API keys, no network. If `ruff` or a project-local `eslint` is installed, the code checks use them too.

## What it does

| Layer | When | What happens |
|---|---|---|
| **Lean rules** | Session start | Adds under 150 tokens of rules: reuse before writing, read line ranges, keep output short |
| **Boot audit** | Session start, `/parsimon-audit` | Warns when CLAUDE.md files are heavy and lists MCP servers re-sent every turn |
| **Smart reads** | Before `Read` | A text file over 400 lines with no line range gets an outline (functions and classes with line numbers) instead, so the agent reads only what it needs. Asking again reads the whole file |
| **Reuse check** | Before `Write`/`Edit` | If the edit adds a function that looks like an existing one (`format_price_v2` next to `format_price`), the agent is told where the existing one is. Retrying the same edit goes through |
| **Edit checks** | After `Write`/`Edit` | Syntax errors, duplicate function bodies, `foo`/`foo_new` variants, plus ruff or eslint findings, sent back in at most 8 lines. Unused imports and undefined names wait for the quality gate, since mid-edit they are usually just half-finished |
| **Quality gate** | Before the agent finishes | Re-checks every edited file and runs your `test_command`. The agent can't say "done" while they fail. Never loops |
| **Cache guard** | Your next prompt | After an idle gap longer than the cache lifetime (5 minutes, or 1 hour when the session uses the 1-hour cache) with a big context, tells you the cache has expired and suggests `/compact` or a fresh session. Also warns when CLAUDE.md or MCP config is edited mid-session |
| **Handoff** | Before the agent finishes | Once context passes 120k tokens, asks the agent (once) to write `.parsimon/handoff.md`. `/parsimon-resume` continues from it in a fresh, cheap session |
| **Report** | `/parsimon-report` | Splits your real sessions' cost into cache writes, cache reads, input and output, and flags cache rebuilds and oversized sessions |
| **Benchmark** | `parsimon bench` | Paired A/B runs, Parsimon off vs on, with the median cost change, a 95% confidence interval and the task success rate |

## Commands

| Command | |
|---|---|
| `/parsimon [lite\|full\|ultra\|off]` | Show or change the mode. `lite` keeps reads, checks and the report but skips the reuse block and quality gate. `off` disables every hook |
| `/parsimon-report` | Where your tokens went |
| `/parsimon-audit` | What's in the boot payload |
| `/parsimon-review` | A delete / reuse / simplify list for the current diff, backed by the checks |
| `/parsimon-resume` | Continue from `.parsimon/handoff.md` |

## Per-project settings

Optional `.parsimon.json` in the project root:

```json
{
  "test_command": "pytest -q -x",
  "test_timeout": 120,
  "read_gate_lines": 400,
  "handoff_at_tokens": 120000,
  "idle_warn_tokens": 40000,
  "reuse_check": true,
  "edit_checks": true
}
```

`PARSIMON_MODE=off` in the environment overrides everything, which is how the benchmark runs its baseline arm.

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
skills/           parsimon, -report, -audit, -review, -resume
src/parsimon/     ledger, report, audit, governor, quality, bench, hooks, state, config, cli
bench/            sample tasks + a tiny repo
tests/            31 tests
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
