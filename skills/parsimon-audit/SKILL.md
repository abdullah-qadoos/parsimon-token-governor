---
name: parsimon-audit
description: Measure the boot payload Claude Code re-sends every turn (CLAUDE.md files, memory files, MCP servers) and suggest what to cut. Use when the user types /parsimon-audit or asks why each session starts so expensive.
---

# Parsimon audit

Run from the project root:

```bash
python3 "$(cat ~/.config/parsimon/root)/hooks/cli.py" audit
```

Then summarise in a few lines: the total estimated boot tokens, the largest item, and up to three specific cuts. Good cuts are moving rarely needed sections of CLAUDE.md into separate docs the agent reads only when needed, and disabling MCP servers this project doesn't use.

Don't edit CLAUDE.md or MCP settings yourself unless the user asks you to.
