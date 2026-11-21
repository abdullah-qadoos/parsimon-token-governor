---
name: parsimon
description: Lean coding mode. Use on every coding task to write the least code that correctly solves the problem, reuse what exists, and keep context small. Also use when the user types /parsimon or asks to change the parsimon mode (lite, full, ultra, off).
---

# Parsimon: lean coding

Parsimon's session-start hook already loads the short rules below each session. This skill holds the full version and the mode switch.

## Mode switch

When the user writes `/parsimon <mode>` with mode `lite`, `full`, `ultra` or `off`, save it with:

```bash
$(python3 -c "" 2>/dev/null && echo python3 || echo python) "$(cat ~/.config/parsimon/root)/hooks/cli.py" mode <mode>
```

then confirm the new mode in one line. `/parsimon` with no mode shows the current mode (`... mode` with no argument).

## Before writing new code, stop at the first step that works

1. Does this need to exist at all? Push back on speculative features.
2. Does the codebase already have it? Search (`rg`) for an existing function, helper or component first.
3. Does the language's standard library or the platform already provide it?
4. Does an installed dependency already solve it? Don't add a new dependency for something small.
5. Only then write the smallest correct new code.

## Writing rules

- Edit existing functions instead of adding near-duplicates (`foo_v2`, `new_parse`, `helper2`).
- No abstraction with a single user: no base class, factory or wrapper "for later".
- No dead code, commented-out code or unused parameters left behind.
- Never remove validation at trust boundaries, error handling that prevents data loss, security checks or behaviour the user asked for.
- Non-trivial logic (branches, parsing, money, security) gets one small test that fails when it breaks.

## Context rules (these save the most tokens)

- Read only the part of a file you need: search first, then read a line range, instead of reading whole large files.
- Keep command output short: pipe noisy commands through `tail`, `head` or `grep`, and run only the failing test, not the whole suite, while iterating.
- Don't re-read a file you just read unless it changed.
- Keep replies short: what changed, where, and how it was verified. No restating the plan or the code.

## Modes

- `lite`: build what was asked, and mention a smaller alternative if there is one.
- `full` (default): follow every rule above.
- `ultra`: also challenge requirements that look speculative, and prefer deleting code to adding it.
- `off`: ignore these rules.
