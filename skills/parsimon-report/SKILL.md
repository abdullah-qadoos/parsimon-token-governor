---
name: parsimon-report
description: Show where the user's Claude Code tokens and cost went (cache writes, cache reads, fresh input, output) across past sessions, with the biggest wastes flagged. Use when the user types /parsimon-report or asks where their tokens, usage or cost are going.
---

# Parsimon report

Run:

```bash
python3 "$(cat ~/.config/parsimon/root)/hooks/cli.py" report --days 14
```

Options: `--days N` (default 14), `--project <substring>` to filter by project folder, `--top N` sessions to list.

Then reply with three short lines, not the whole table again:

1. The biggest cost category and its share.
2. The single biggest waste the report flags (for example cache rebuilds after idle gaps, or oversized sessions).
3. One concrete change the user can make this week.

If `~/.config/parsimon/root` doesn't exist yet, tell the user to start a new Claude Code session once so the plugin can register itself, then run it again.
