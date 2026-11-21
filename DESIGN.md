# Parsimon: a cache-aware token governor for coding agents

> Working name. Alternatives in your style: `parsimon-ctx`, `sieve-ctx`, `distill-ctx`, `frugal-hooks`, `tersa-ctx`.
> Targets Claude Code first, then Codex and OpenClaw.

---

## 1. Why another token tool, and why this one can win

The independent tests are brutal on the existing tools:

| Tool | Layer it works on | Advertised | Measured independently |
|---|---|---|---|
| **Ponytail** | One prompt telling the model to write less code (output tokens) | −22% tokens / −20% cost | **−10.3% cost** (JetBrains, 80 paired tasks); −26% in the author's own Opus benchmark |
| **Caveman** | Shorter prose | −65% | −8.5% |
| **RTK / headroom** | Compress Bash tool output | 60–99% "on pytest output" | "Barely dented" the bill; RTK sometimes *raised* cost |

**Why they disappoint:** in a replay of 500 real Claude Code sessions ($926 bill), the cost split was:

- **Cache writes: 42%** ← nobody targets this
- **Model output: 29%** ← Ponytail and Caveman only touch this
- **Everything else: 29%**, mostly cheap cache reads. Compressing tool output mostly lands here, which is why RTK barely moves the bill.

Also, RTK's rewrite hook only covers the **Bash** tool. Claude Code's own `Read`, `Grep` and `Glob` bypass it completely.

**So Parsimon's thesis:** attack the **biggest slice (cache writes)** and the **re-sent context**, cover the tools others miss, keep Ponytail-style output discipline as a minor layer, and **publish honest paired benchmarks** from day one, because that's exactly where every competitor got caught out.

Sources: JetBrains AI blog (Jul 2026), capitalandcompute.net (Oct 2026), computingforgeeks.com, pasqualepillitteri.it.

---

## 2. What drives cache writes (the target)

Every turn, Claude Code sends a prefix: system prompt, tool definitions, MCP schemas, CLAUDE.md, memory files, then conversation history. The prompt cache only reuses an **unchanged prefix**, so cache writes go up when:

1. **The prefix changes.** Editing CLAUDE.md, toggling MCP servers or skills, or tool lists changing mid-session invalidates everything after the change.
2. **The cache expires.** If you pause longer than the cache TTL (about 5 minutes on the default cache), the next turn re-writes the whole prefix.
3. **History keeps growing.** Every new turn writes its new tail, and big tool results written into history get written once and then re-read on every later turn.
4. **The boot payload is bloated.** Big CLAUDE.md files, lots of MCP tool schemas and always-loaded memory files. One audit measured CLAUDE.md alone at 22–46% of the starting context.

---

## 3. The five layers

| # | Layer | What it does | Hits which cost slice |
|---|---|---|---|
| **L1** | **Boot diet** | Audits CLAUDE.md, memory and MCP schemas, then writes a lean CLAUDE.md with the details moved into on-demand files the agent reads only when needed. Flags unused MCP servers. | Cache writes and reads on **every** turn |
| **L2** | **Cache guard** | Warns before changes that break the prefix mid-session. Tracks idle time and, past the TTL, suggests `/compact` or a fresh session instead of silently re-writing a huge prefix. | Cache writes |
| **L3** | **Session governor** | When context passes a threshold, writes a compact **handoff file** (goal, decisions, touched files, next steps) and recommends starting a fresh session from it, instead of dragging 150k tokens of history forward. | Cache writes and reads, the biggest lever |
| **L4** | **Smart reads** | Hooks on `Read`/`Grep`/`Glob` as well as `Bash`. Big files get an outline first (symbols, line ranges) so the agent reads only the range it needs. Grep output is deduped and capped, and test/build logs are reduced to failures only. | Fresh input, and less history to re-write |
| **L5** | **Lean output + routing** | Ponytail-style rules (reuse before writing, no speculative abstractions, terse summaries) plus guidance to send simple subtasks to a cheaper subagent or model. | Output, which is 29% of cost |

> **Week-1 spike:** confirm exactly what Claude Code hooks can change in the current version (for example, whether `PreToolUse` can rewrite `Read` arguments to a line range, and whether `PostToolUse` output can be replaced). Design L4 around what's actually possible, not what we assume.

---

## 4. Architecture

```
            Claude Code / Codex / OpenClaw
                     │ hooks + one skill
   ┌─────────────────┼──────────────────────────────┐
   │ SessionStart    │ PreToolUse / PostToolUse     │ Stop / PreCompact
   ▼                 ▼                              ▼
 L1 boot audit    L4 smart reads + log filter     L3 handoff writer
 L2 cache guard   (Read, Grep, Glob, Bash)        L2 idle/TTL tracker
   │                 │                              │
   └────────► parsimon core (Python, local) ◄───────┘
                     │
        ┌────────────┴─────────────┐
        ▼                          ▼
  Transcript ledger           `parsimon report`
  (parses ~/.claude JSONL:    cost split per session:
   cache write / cache read /  cache write · cache read ·
   input / output per turn)    input · output · $ saved
```

**Everything runs locally:** a Python package plus hook scripts, no proxy server and no API keys. Everything it learns comes from the session transcripts Claude Code already writes.

---

## 5. The differentiator: honest benchmarks built in

`parsimon bench` runs **paired A/B tasks**: the same task, repo and model, with and without Parsimon, several times each. It reports:

- **Median cost change** with a confidence interval, not one lucky run
- The cost **split by category** (cache write, cache read, input, output), so people can see *where* the savings come from
- **Task success rate**, because saving tokens by failing tasks doesn't count
- A per-layer **ablation** (L1 only, L1 + L2, …) to show which layer earns its keep

The README leads with this table. If L4 turns out to barely help, the table says so. That honesty is the brand, because it's exactly what JetBrains caught everyone else on.

---

## 6. Repo layout

```
parsimon/
├── src/parsimon/
│   ├── ledger.py         # parse Claude Code JSONL → per-turn token/cost split
│   ├── boot_audit.py     # L1: CLAUDE.md / memory / MCP schema sizing + lean rewrite
│   ├── cache_guard.py    # L2: prefix-change + idle/TTL detection
│   ├── governor.py       # L3: context threshold → handoff.md
│   ├── reads.py          # L4: outline-first reads, grep dedupe, log filters
│   ├── filters/          # pytest, jest, npm, pip, docker, git log reducers
│   ├── rules/LEAN.md     # L5 output discipline (skill text)
│   ├── bench/            # paired A/B runner + stats (bootstrap CI)
│   └── cli.py            # parsimon install | audit | report | bench | doctor
├── hooks/claude_code/    # hook scripts + settings snippet
├── skill/                # SKILL.md for /parsimon
├── tests/
├── bench_tasks/          # 20–40 real tasks across 3 sample repos
└── README.md
```

---

## 7. Build plan (about 3 weeks alongside work)

| Week | Deliverable | Done when |
|---|---|---|
| **1** | `ledger` + `parsimon report` (cost split from your own past sessions), L1 boot audit, the hook-capability spike | You can see the cache-write share of your own real sessions, and know which hooks can do what |
| **2** | L2 cache guard, L3 handoff governor, L4 log filters + Grep dedupe, outline-first reads if hooks allow | All layers installable with `parsimon install claude` |
| **3** | `parsimon bench` with 20+ paired tasks, ablation table, README, PyPI `v0.1.0` | Published benchmark with median cost change and CI, plus task success rate |

---

## 8. What success looks like

- **Target:** beat Ponytail's measured **−10%** median cost with **no drop in task success**, measured the same paired way.
- **Stretch:** −25% or better on long sessions, where L3 (the session governor) should matter most.
