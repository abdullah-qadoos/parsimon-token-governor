---
name: parsimon
description: Parsimon token saver and code checks. Use when the user types /parsimon (both|save|code|off|report|audit|review|resume) or asks where their tokens go.
---

# Parsimon

Run the CLI as:

```bash
PY=$(python3 -c "" 2>/dev/null && echo python3 || echo python); P="$(cat ~/.config/parsimon/root)/hooks/cli.py"
```

If `~/.config/parsimon/root` doesn't exist, tell the user to start a new Claude Code session once so the plugin can register itself.

## `/parsimon` (no argument), `both`, `save`, `code`, `off`

Two parts the user can switch on independently:

- **save** (token saver): short context rules, outline-first reads of big files, expired-cache warning, cached-prefix edit warning, per-project memory (`.parsimon/memory.md`) loaded on startup and /clear.
- **code** (code checks): lean coding rules after the first edit, reuse check for near-duplicate functions, syntax/duplicate/lint checks after edits, quality gate with the project's tests before finishing.

Run `$PY "$P" mode <both|save|code|off>` (no argument shows the current state) and repeat its one-line output. The change applies from the next hook call; rules already sent this session stay in context.

## `/parsimon report`

Run `$PY "$P" report --days 14` (`--project <text>`, `--top N`). Reply in three lines: the biggest cost category and its share, the biggest waste it flags, one concrete change for this week.

## `/parsimon audit`

Run `$PY "$P" audit` from the project root. Summarise: total estimated boot tokens, the largest item, up to three cuts (move rarely needed CLAUDE.md sections into on-demand docs, disable unused MCP servers). Don't edit CLAUDE.md or MCP settings unless asked.

## `/parsimon review`

1. `git diff --stat`, then `git diff` (or against the base the user names). Read only changed hunks.
2. Run `$PY "$P" check <changed code files>`.
3. For each new function, search the repo (`rg -n "def <name>|function <name>|<name> ="`) for something that already does the job.
4. List, most valuable first, each with file:line and one line of reasoning: **Delete** (dead code, unused params), **Reuse** (name the existing code), **Simplify** (single-use abstractions, hand-rolled stdlib), **Keep** (trust-boundary validation, data-loss handling, security). Don't apply changes unless asked; end with the estimated lines that could go.

## `/parsimon resume`

Read `.parsimon/memory.md` (in the home folder: the matching `.parsimon/memory/<task>.md`). If missing, say so in one line and ask what to work on. Open only what the next step needs, using line ranges. Reply in at most four lines (goal, state, next step), then start unless told to wait. `/parsimon resume save` rewrites the memory now: the whole file, under 400 words, first line `# <task> - <date>`, then goal, decisions and why, current state, files with line ranges, next step, open problems.
