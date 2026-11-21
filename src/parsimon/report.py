"""Turn ledger data into a plain-text report with the biggest wastes flagged."""
from __future__ import annotations

from .ledger import CATEGORIES, DEFAULT_WEIGHTS, Session, totals

LABELS = {
    "input": "Fresh input",
    "cache_write": "Cache writes",
    "cache_read": "Cache reads",
    "output": "Output",
}

LONG_SESSION_CONTEXT = 100_000


def _fmt(n: float) -> str:
    n = float(n)
    for unit, size in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if abs(n) >= size:
            return f"{n / size:.1f}{unit}"
    return f"{n:.0f}"


def build(sessions: list[Session], weights: dict[str, float] | None = None, prices: dict[str, float] | None = None) -> dict:
    """Return the report as data, so it can be tested and printed separately.

    prices: optional dollars per million tokens per category. When given, costs are in
    dollars; otherwise in input-equivalent tokens using `weights`.
    """
    weights = weights or DEFAULT_WEIGHTS
    unit_cost = {c: prices[c] / 1e6 for c in CATEGORIES} if prices else weights
    tok = totals(sessions)
    cost = {c: tok[c] * unit_cost[c] for c in CATEGORIES}
    total_cost = sum(cost.values()) or 1.0

    rebuild_tokens = 0
    rebuild_count = 0
    for s in sessions:
        for turn, _gap in s.cache_rebuilds():
            rebuild_count += 1
            rebuild_tokens += turn.cache_write
    long_sessions = [s for s in sessions if s.peak_context >= LONG_SESSION_CONTEXT]
    long_cost = sum(_session_cost(s, unit_cost) for s in long_sessions)

    ranked = sorted(sessions, key=lambda s: _session_cost(s, unit_cost), reverse=True)
    return {
        "sessions": len(sessions),
        "turns": sum(len(s.turns) for s in sessions),
        "tokens": tok,
        "cost": cost,
        "total_cost": sum(cost.values()),
        "share": {c: cost[c] / total_cost for c in CATEGORIES},
        "dollars": bool(prices),
        "rebuilds": {
            "count": rebuild_count,
            "tokens": rebuild_tokens,
            "cost": rebuild_tokens * unit_cost["cache_write"],
            "share": rebuild_tokens * unit_cost["cache_write"] / total_cost,
        },
        "long_sessions": {
            "count": len(long_sessions),
            "cost": long_cost,
            "share": long_cost / total_cost,
        },
        "top": [
            {
                "session": s.session_id[:8],
                "project": s.project,
                "turns": len(s.turns),
                "peak_context": s.peak_context,
                "cost": _session_cost(s, unit_cost),
            }
            for s in ranked
        ],
    }


def _session_cost(s: Session, unit_cost: dict[str, float]) -> float:
    sums = s.sums()
    return sum(sums[c] * unit_cost[c] for c in CATEGORIES)


def render(data: dict, top: int = 5) -> str:
    if not data["sessions"]:
        return "No Claude Code sessions with token usage found for this filter."
    money = (lambda v: f"${v:,.2f}") if data["dollars"] else (lambda v: _fmt(v))
    unit = "dollars" if data["dollars"] else "input-equivalent tokens"
    lines = [
        f"Parsimon report: {data['sessions']} sessions, {data['turns']} model turns",
        f"Cost shown in {unit}" + ("" if data["dollars"] else " (cache write 1.25x, cache read 0.1x, output 5x)"),
        "",
        f"{'Category':<14}{'Tokens':>10}{'Cost':>12}{'Share':>8}",
    ]
    for c in sorted(CATEGORIES, key=lambda k: data["cost"][k], reverse=True):
        lines.append(
            f"{LABELS[c]:<14}{_fmt(data['tokens'][c]):>10}{money(data['cost'][c]):>12}{data['share'][c] * 100:>7.1f}%"
        )
    lines.append(f"{'Total':<14}{'':>10}{money(data['total_cost']):>12}")
    lines += ["", "Flags"]
    r = data["rebuilds"]
    if r["count"]:
        lines.append(
            f"- {r['count']} cache rebuilds after idle gaps over 5 min re-wrote {_fmt(r['tokens'])} tokens "
            f"({r['share'] * 100:.1f}% of cost). Compact or start fresh after long breaks."
        )
    ls = data["long_sessions"]
    if ls["count"]:
        lines.append(
            f"- {ls['count']} sessions grew past {_fmt(LONG_SESSION_CONTEXT)} tokens of context and account for "
            f"{ls['share'] * 100:.1f}% of cost. Hand off to a fresh session earlier."
        )
    if data["share"]["output"] < 0.25:
        lines.append(
            f"- Output is only {data['share']['output'] * 100:.1f}% of cost, so prompts that just make the model "
            "write less can't save much here. Context is where the money goes."
        )
    if lines[-1] == "Flags":
        lines.append("- Nothing unusual found.")
    lines += ["", f"Top {min(top, len(data['top']))} sessions by cost"]
    lines.append(f"{'Session':<10}{'Project':<28}{'Turns':>6}{'Peak ctx':>10}{'Cost':>12}")
    for row in data["top"][:top]:
        lines.append(
            f"{row['session']:<10}{row['project'][-27:]:<28}{row['turns']:>6}"
            f"{_fmt(row['peak_context']):>10}{money(row['cost']):>12}"
        )
    return "\n".join(lines)
