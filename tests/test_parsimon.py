import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from parsimon import audit, config, hooks, ledger, report
from parsimon.cli import main

T0 = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)


def _assistant(msg_id, ts, inp=0, cw=0, cr=0, out=0, nested=False):
    usage = {"input_tokens": inp, "cache_read_input_tokens": cr, "output_tokens": out}
    if nested:
        usage["cache_creation"] = {"ephemeral_5m_input_tokens": cw}
    else:
        usage["cache_creation_input_tokens"] = cw
    return {
        "type": "assistant",
        "sessionId": "sess-1234-abcd",
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "message": {"id": msg_id, "model": "claude-sonnet", "usage": usage},
    }


def write_session(folder: Path, name: str, entries) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.jsonl"
    with open(path, "w") as fh:
        for e in entries:
            fh.write((e if isinstance(e, str) else json.dumps(e)) + "\n")
    return path


@pytest.fixture
def transcripts(tmp_path):
    root = tmp_path / "projects"
    write_session(
        root / "-home-me-shop",
        "s1",
        [
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            _assistant("m1", T0, inp=10, cw=30_000, cr=0, out=200),
            # same message logged twice while streaming: must count once, largest wins
            _assistant("m2", T0 + timedelta(seconds=30), inp=5, cw=1_000, cr=30_000, out=50),
            _assistant("m2", T0 + timedelta(seconds=31), inp=5, cw=1_000, cr=30_000, out=400),
            # 12-minute idle gap, then the whole prefix is written again
            _assistant("m3", T0 + timedelta(minutes=13), inp=5, cw=31_000, cr=0, out=100, nested=True),
            "not json at all",
        ],
    )
    write_session(
        root / "-home-me-api",
        "s2",
        [_assistant("x1", T0 - timedelta(days=30), inp=100, cw=500, cr=1_000, out=10)],
    )
    return root


def test_dedupes_streamed_messages_and_reads_nested_cache_creation(transcripts):
    s = ledger.parse_session(transcripts / "-home-me-shop" / "s1.jsonl")
    assert len(s.turns) == 3
    assert s.sums() == {"input": 20, "cache_write": 62_000, "cache_read": 30_000, "output": 700}
    assert s.session_id == "sess-1234-abcd"


def test_detects_cache_rebuild_after_idle_gap(transcripts):
    s = ledger.parse_session(transcripts / "-home-me-shop" / "s1.jsonl")
    rebuilds = s.cache_rebuilds()
    assert len(rebuilds) == 1
    turn, gap = rebuilds[0]
    assert turn.cache_write == 31_000 and gap > 600


def test_days_and_project_filters(transcripts):
    now = T0 + timedelta(hours=1)
    assert len(ledger.load_sessions(transcripts, now=now)) == 2
    assert len(ledger.load_sessions(transcripts, days=7, now=now)) == 1
    assert [s.project for s in ledger.load_sessions(transcripts, project="api", now=now)] == ["-home-me-api"]


def test_report_shares_and_flags(transcripts):
    sessions = ledger.load_sessions(transcripts, days=7, now=T0 + timedelta(hours=1))
    data = report.build(sessions)
    expected_cw = 62_000 * 1.25
    total = 20 * 1 + expected_cw + 30_000 * 0.1 + 700 * 5
    assert data["cost"]["cache_write"] == pytest.approx(expected_cw)
    assert data["share"]["cache_write"] == pytest.approx(expected_cw / total)
    assert data["rebuilds"]["count"] == 1
    text = report.render(data)
    assert "Cache writes" in text and "cache rebuilds" in text


def test_report_in_dollars(transcripts):
    sessions = ledger.load_sessions(transcripts, days=0 or None)
    data = report.build(sessions, prices={"input": 3, "cache_write": 3.75, "cache_read": 0.3, "output": 15})
    assert data["dollars"] and "$" in report.render(data)


def test_empty_report():
    assert "No Claude Code sessions" in report.render(report.build([]))


def test_audit_finds_large_claude_md_and_mcp(tmp_path):
    proj, home = tmp_path / "proj", tmp_path / "home" / ".claude"
    proj.mkdir()
    home.mkdir(parents=True)
    (proj / "CLAUDE.md").write_text("x" * 12_000)
    (proj / ".mcp.json").write_text(json.dumps({"mcpServers": {"github": {}, "slack": {}}}))
    data = audit.run(proj, home)
    assert data["instruction_tokens"] == 3_000
    assert {n for n, _ in data["mcp_servers"]} == {"github", "slack"}
    assert "large" in audit.render(data)


def test_session_start_hook_modes(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("PARSIMON_MODE", raising=False)
    out = hooks.dispatch("session-start", {"cwd": str(tmp_path)})
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "Parsimon" in ctx and audit.estimate_tokens(ctx) < 200
    assert (tmp_path / "cfg" / "parsimon" / "root").exists()
    config.set_mode("off")
    assert hooks.dispatch("session-start", {"cwd": str(tmp_path)}) is None
    config.set_mode("ultra")
    assert "Ultra" in hooks.dispatch("session-start", {})["hookSpecificOutput"]["additionalContext"]


def test_cli_report_runs(transcripts, capsys):
    assert main(["report", "--root", str(transcripts), "--days", "0"]) == 0
    assert "Parsimon report" in capsys.readouterr().out


def test_subagent_transcripts_count_under_their_project(transcripts):
    write_session(
        transcripts / "-home-me-shop" / "sess-1234" / "subagents",
        "agent-a",
        [_assistant("sub1", T0, inp=1, cw=2_000, cr=0, out=30)],
    )
    sessions = ledger.load_sessions(transcripts, project="shop", now=T0 + timedelta(hours=1))
    assert len(sessions) == 2
    assert {s.project for s in sessions} == {"-home-me-shop"}
