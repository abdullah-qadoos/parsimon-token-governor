# parsimon

**A cache-aware token governor for Claude Code.** It shows where your tokens actually go, trims what gets re-sent every turn, and keeps the agent's code lean.

Most token-saving plugins only tell the model to write less. But in real Claude Code sessions, output is a small slice of the bill. Most of the cost is the context re-sent and re-cached on every turn. Parsimon measures that first, then cuts it.

> Status: **v0.1, week 1**. Token ledger, report, boot audit and lean rules are working. Next up: smart reads, the cache guard and session handoff, code-quality hooks, and a paired benchmark.

## Install (Claude Code)

```
/plugin marketplace add abdullah-qadoos/parsimon
/plugin install parsimon@parsimon
```

Start a new session. Lean mode switches on automatically. Requires `python3` (3.10+) on your PATH, and nothing else: no dependencies, no API keys, no network.

## Commands

| Command | What it does |
|---|---|
| `/parsimon-report` | Splits your recent sessions' cost into cache writes, cache reads, fresh input and output, and flags waste |
| `/parsimon-audit` | Measures the CLAUDE.md files and MCP servers re-sent every turn |
| `/parsimon [lite\|full\|ultra\|off]` | Shows or changes the lean-coding mode |

From a terminal: `python3 hooks/cli.py report --days 14`. Add `--prices 3,3.75,0.3,15` (dollars per million tokens: input, cache write, cache read, output) to see dollars.

## Example report

```
Parsimon report: 3 sessions, 195 model turns
Category          Tokens        Cost   Share
Cache reads        34.7M        3.5M   52.9%
Cache writes        1.8M        2.2M   34.2%
Output            168.6k      843.0k   12.8%
Fresh input         4.3k        4.3k    0.1%

Flags
- 5 cache rebuilds after idle gaps over 5 min re-wrote 1.1M tokens (21.8% of cost).
- 3 sessions grew past 100.0k tokens of context and account for 100.0% of cost.
- Output is only 12.8% of cost, so prompts that just make the model write less can't save much here.
```

(Sample data. Run it on your own sessions to see yours.)

## How it works

- **Ledger:** reads the session transcripts Claude Code already writes to `~/.claude/projects`, de-duplicates streamed messages, and totals each turn's `usage` by category. Costs use relative weights (cache write 1.25×, cache read 0.1×, output 5× input) unless you pass real prices.
- **Cache rebuilds:** flags turns that re-wrote a large prefix after an idle gap longer than the 5-minute cache lifetime.
- **Boot audit:** estimates the tokens in every CLAUDE.md that gets loaded, and lists configured MCP servers.
- **SessionStart hook:** adds a short set of lean rules (under 150 tokens) and a one-line warning if your instruction files are large. Errors never break your session.

## Layout

```
.claude-plugin/   plugin + marketplace manifests
hooks/            hooks.json, run.py (hook entry), cli.py (CLI without install)
skills/           parsimon, parsimon-report, parsimon-audit
src/parsimon/     ledger, report, audit, hooks, config, cli
tests/            pytest suite with synthetic transcripts
DESIGN.md         full design and roadmap
```

## Development

```
pip install -e . pytest
pytest
```

## Credits

The lean-coding rules draw on the "reuse before you write" idea popularised by [Ponytail](https://github.com/DietrichGebert/ponytail) (MIT). Parsimon's focus is different: measuring and cutting the context cost that rules alone can't reach.

## License

MIT
