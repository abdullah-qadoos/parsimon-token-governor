---
name: parsimon-review
description: Review the current changes for code that shouldn't exist (duplicates of existing functions, dead code, single-use abstractions, hand-rolled stdlib features) backed by tool results. Use when the user types /parsimon-review or asks to clean up or simplify a diff.
---

# Parsimon review

1. Get the change: `git diff --stat` then `git diff` (or `git diff main...` if the user names a base). Read only changed hunks.
2. Run the checks on each changed code file:

```bash
$(python3 -c "" 2>/dev/null && echo python3 || echo python) "$(cat ~/.config/parsimon/root)/hooks/cli.py" check <file> [<file> ...]
```

3. For every new function in the diff, search the repo (`rg -n "def <name>|function <name>|<name> ="`) for something that already does the job.
4. Report a short list, most valuable first, each with file:line and one line of reasoning:
   - **Delete:** dead code, unused params, commented-out code
   - **Reuse:** new code that duplicates something existing (name the existing one)
   - **Simplify:** abstractions with one user, hand-written stdlib features, needless dependencies
   - **Keep:** anything protecting trust boundaries, data loss or security, even if it looks verbose
5. Don't apply changes unless the user asks. End with the estimated lines that could go.
