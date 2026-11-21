---
name: parsimon-resume
description: Continue work from a Parsimon handoff in a fresh, cheap session. Use when the user types /parsimon-resume or asks to pick up where the last session left off.
---

# Parsimon resume

1. Read `.parsimon/handoff.md` in the project root. If it doesn't exist, say so in one line and ask what to work on.
2. Don't re-read every file it lists. Open only what the next step needs, using line ranges.
3. Reply in at most four lines: the goal, where things stand, and the next step you'll take. Then start on it unless the user said to wait.

To write a handoff by hand (when the user types `/parsimon-resume save` or asks for a handoff), write `.parsimon/handoff.md` in under 300 words: goal, decisions and why, current state, files edited, next steps, open problems.
