import json
import os
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from parsimon import bench, config, hooks, quality


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("PARSIMON_MODE", raising=False)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "utils").mkdir(parents=True)
    (root / "utils" / "money.py").write_text("def format_price(cents):\n    return f'${cents/100:.2f}'\n")
    big = "\n".join(f"x{i} = {i}" for i in range(500))
    (root / "big.py").write_text("def alpha():\n    return 1\n\n\nclass Beta:\n    pass\n" + big + "\n")
    return root


def _transcript(path: Path, context: int, ts: datetime, edits=(), write_1h=0):
    content = [{"type": "tool_use", "name": "Edit", "input": {"file_path": f}} for f in edits]
    entry = {
        "type": "assistant",
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "message": {"id": "m1", "usage": {"input_tokens": 10, "cache_creation_input_tokens": 0,
                                           "cache_read_input_tokens": context, "output_tokens": 50,
                                           "cache_creation": {"ephemeral_1h_input_tokens": write_1h}},
                    "content": content},
    }
    path.write_text(json.dumps(entry) + "\n")
    return path


# ---------- smart reads ----------

def test_big_read_gets_outline_once(repo):
    payload = {"session_id": "s1", "cwd": str(repo), "tool_name": "Read", "tool_input": {"file_path": str(repo / "big.py")}}
    out = hooks.dispatch("pre-tool-use", payload)
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "def alpha" in reason and "class Beta" in reason
    assert hooks.dispatch("pre-tool-use", payload) is None  # second ask goes through


def test_small_or_ranged_reads_pass(repo):
    small = {"session_id": "s1", "cwd": str(repo), "tool_name": "Read", "tool_input": {"file_path": str(repo / "utils" / "money.py")}}
    ranged = {"session_id": "s1", "cwd": str(repo), "tool_name": "Read", "tool_input": {"file_path": str(repo / "big.py"), "offset": 1, "limit": 20}}
    assert hooks.dispatch("pre-tool-use", small) is None
    assert hooks.dispatch("pre-tool-use", ranged) is None


# ---------- reuse check ----------

def test_reuse_check_flags_near_duplicate_then_allows_retry(repo):
    payload = {
        "session_id": "s2", "cwd": str(repo), "tool_name": "Write",
        "tool_input": {"file_path": str(repo / "cart.py"), "content": "def format_price_v2(c):\n    return str(c)\n"},
    }
    out = hooks.dispatch("pre-tool-use", payload)
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "format_price_v2" in reason and "utils/money.py:1" in reason
    assert hooks.dispatch("pre-tool-use", payload) is None


def test_reuse_check_ignores_genuinely_new_names(repo):
    payload = {
        "session_id": "s3", "cwd": str(repo), "tool_name": "Edit",
        "tool_input": {"file_path": str(repo / "utils" / "money.py"), "old_string": "x", "new_string": "def parse_csv(p):\n    pass"},
    }
    assert hooks.dispatch("pre-tool-use", payload) is None


def test_save_only_skips_reuse_check(repo):
    config.set_mode("save")
    payload = {
        "session_id": "s4", "cwd": str(repo), "tool_name": "Write",
        "tool_input": {"file_path": str(repo / "cart.py"), "content": "def format_price_new(c):\n    return c\n"},
    }
    assert hooks.dispatch("pre-tool-use", payload) is None


# ---------- post-edit checks ----------

def test_post_edit_catches_syntax_error_and_duplicate_body(repo):
    bad = repo / "bad.py"
    bad.write_text("def a(x):\n    y = x + 1\n    return y\n\n\ndef b(x):\n    y = x + 1\n    return y\n")
    out = hooks.dispatch("post-tool-use", {"session_id": "s5", "cwd": str(repo), "tool_name": "Write", "tool_input": {"file_path": str(bad)}})
    assert out["decision"] == "block" and "same body" in out["reason"]
    bad.write_text("def a(:\n")
    out = hooks.dispatch("post-tool-use", {"session_id": "s5", "cwd": str(repo), "tool_name": "Edit", "tool_input": {"file_path": str(bad)}})
    assert "syntax error" in out["reason"]


def test_code_rules_arrive_once_on_first_edit_then_clean_edits_are_silent(repo):
    payload = {"session_id": "s6", "cwd": str(repo), "tool_name": "Edit",
               "tool_input": {"file_path": str(repo / "utils" / "money.py")}}
    out = hooks.dispatch("post-tool-use", payload)
    assert "code rules" in out["hookSpecificOutput"]["additionalContext"]
    assert hooks.dispatch("post-tool-use", payload) is None


def test_prefix_file_edit_warns_once(repo):
    md = repo / "CLAUDE.md"
    md.write_text("rules")
    payload = {"session_id": "s7", "cwd": str(repo), "tool_name": "Edit", "tool_input": {"file_path": str(md)}}
    assert "cached prefix" in hooks.dispatch("post-tool-use", payload)["reason"]
    assert hooks.dispatch("post-tool-use", payload) is None


# ---------- stop: quality gate and handoff ----------

def test_stop_gate_blocks_on_failing_tests_then_never_loops(repo):
    (repo / ".parsimon.json").write_text(json.dumps({"test_command": "exit 1"}))
    hooks.dispatch("post-tool-use", {"session_id": "s8", "cwd": str(repo), "tool_name": "Edit",
                                     "tool_input": {"file_path": str(repo / "utils" / "money.py")}})
    out = hooks.dispatch("stop", {"session_id": "s8", "cwd": str(repo)})
    assert out["decision"] == "block" and "quality gate" in out["reason"]
    assert hooks.dispatch("stop", {"session_id": "s8", "cwd": str(repo), "stop_hook_active": True}) is None


def test_stop_gate_passes_with_green_tests(repo):
    (repo / ".parsimon.json").write_text(json.dumps({"test_command": "exit 0"}))
    hooks.dispatch("post-tool-use", {"session_id": "s9", "cwd": str(repo), "tool_name": "Edit",
                                     "tool_input": {"file_path": str(repo / "utils" / "money.py")}})
    assert hooks.dispatch("stop", {"session_id": "s9", "cwd": str(repo)}) is None


def _grown(path: Path, start: int, end: int, edits=()):
    """A transcript whose context grew from `start` to `end` tokens this session."""
    first = _transcript(path, start, datetime.now(timezone.utc)).read_text()
    _transcript(path, end, datetime.now(timezone.utc), edits=edits)
    path.write_text(first + path.read_text())
    return path


def test_memory_rewrite_requested_every_40k_tokens(repo, tmp_path):
    payload = {"session_id": "s10", "cwd": str(repo)}
    small = _grown(tmp_path / "a.jsonl", 30_000, 60_000)  # grew 30k: not yet
    assert hooks.dispatch("stop", {**payload, "transcript_path": str(small)}) is None
    big = _grown(tmp_path / "b.jsonl", 30_000, 75_000, edits=[str(repo / "big.py")])
    out = hooks.dispatch("stop", {**payload, "transcript_path": str(big)})
    assert out["decision"] == "block" and ".parsimon/memory.md" in out["reason"] and "big.py" in out["reason"]
    assert (repo / ".parsimon" / ".gitignore").read_text() == "*\n"  # git skips it; user's .gitignore untouched
    assert hooks.dispatch("stop", {**payload, "transcript_path": str(big)}) is None  # not again at the same size
    bigger = _grown(tmp_path / "c.jsonl", 30_000, 120_000)
    assert hooks.dispatch("stop", {**payload, "transcript_path": str(bigger)})["decision"] == "block"


def test_project_memory_loads_on_startup_and_clear_not_resume(repo):
    (repo / ".parsimon").mkdir()
    (repo / ".parsimon" / "memory.md").write_text("# fix cart - 2026-11-22\nNext: add EUR test")
    for source, expected in (("startup", True), ("clear", True), ("resume", False), ("compact", False)):
        ctx = hooks.dispatch("session-start", {"cwd": str(repo), "source": source})["hookSpecificOutput"]["additionalContext"]
        assert ("Next: add EUR test" in ctx) == expected, source


def test_stale_memory_is_not_loaded(repo):
    (repo / ".parsimon").mkdir()
    mem = repo / ".parsimon" / "memory.md"
    mem.write_text("old task")
    old = datetime.now().timestamp() - 30 * 86400
    os.utime(mem, (old, old))
    ctx = hooks.dispatch("session-start", {"cwd": str(repo)})["hookSpecificOutput"]["additionalContext"]
    assert "old task" not in ctx


def test_home_folder_lists_task_memories_and_asks_for_one_file_per_task(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".parsimon" / "memory").mkdir(parents=True)
    (home / ".parsimon" / "memory" / "qc-427.md").write_text("# QC handover 427 - 2026-11-21\nlong details")
    monkeypatch.setattr(Path, "home", lambda: home)
    ctx = hooks.dispatch("session-start", {"cwd": str(home)})["hookSpecificOutput"]["additionalContext"]
    assert "qc-427.md: QC handover 427" in ctx and "long details" not in ctx  # listed, not loaded
    tr = _grown(tmp_path / "t.jsonl", 30_000, 80_000)
    out = hooks.dispatch("stop", {"session_id": "s20", "cwd": str(home), "transcript_path": str(tr)})
    assert "memory/<short-task-name>.md" in out["reason"]


def test_idle_warning_points_to_clear_when_memory_exists(repo, tmp_path):
    (repo / ".parsimon").mkdir()
    (repo / ".parsimon" / "memory.md").write_text("x")
    tr = _transcript(tmp_path / "t.jsonl", 80_000, datetime.now(timezone.utc) - timedelta(minutes=12))
    out = hooks.dispatch("user-prompt-submit", {"session_id": "s21", "cwd": str(repo), "transcript_path": str(tr)})
    assert "/clear" in out["systemMessage"]


# ---------- cache guard ----------

def test_idle_expired_cache_warns_user_once(repo, tmp_path):
    tr = _transcript(tmp_path / "t.jsonl", 80_000, datetime.now(timezone.utc) - timedelta(minutes=12))
    payload = {"session_id": "s11", "cwd": str(repo), "transcript_path": str(tr), "prompt": "go on"}
    out = hooks.dispatch("user-prompt-submit", payload)
    assert "cache has expired" in out["systemMessage"] and "80,010" in out["systemMessage"]
    assert hooks.dispatch("user-prompt-submit", payload) is None


def test_recent_or_small_context_does_not_warn(repo, tmp_path):
    fresh = _transcript(tmp_path / "a.jsonl", 80_000, datetime.now(timezone.utc) - timedelta(minutes=1))
    small = _transcript(tmp_path / "b.jsonl", 5_000, datetime.now(timezone.utc) - timedelta(minutes=30))
    for tr in (fresh, small):
        assert hooks.dispatch("user-prompt-submit", {"session_id": "s12", "cwd": str(repo), "transcript_path": str(tr)}) is None


def test_off_mode_disables_every_hook(repo):
    config.set_mode("off")
    payload = {"session_id": "s13", "cwd": str(repo), "tool_name": "Read", "tool_input": {"file_path": str(repo / "big.py")}}
    assert hooks.dispatch("pre-tool-use", payload) is None


def test_name_normalisation():
    assert quality.norm_name("format_price_v2") == quality.norm_name("format_price")
    assert quality.norm_name("parse_helper") == quality.norm_name("parse")


# ---------- benchmark ----------

def test_bench_compare_reports_median_and_ci():
    rows = []
    for rep in range(6):
        rows.append({"task": "t", "rep": rep, "arm": "baseline", "cost_usd": 1.0, "ok": True})
        rows.append({"task": "t", "rep": rep, "arm": "parsimon", "cost_usd": 0.8 + rep * 0.01, "ok": True})
    res = bench.compare(rows)
    assert res["pairs"] == 6
    assert res["median_change"] == pytest.approx(-0.175, abs=0.01)
    assert res["ci"][1] < 0
    assert "below zero" in bench.render(res)


def test_bench_run_with_fake_claude(tmp_path):
    fake = tmp_path / "claude"
    fake.write_text(
        "#!/usr/bin/env python3\nimport json, os\n"
        "cost = 0.5 if os.environ.get('PARSIMON_MODE') == 'both' else 1.0\n"
        "print(json.dumps({'session_id': 'x', 'num_turns': 3, 'total_cost_usd': cost, "
        "'usage': {'input_tokens': 10, 'cache_creation_input_tokens': 100, 'cache_read_input_tokens': 1000, 'output_tokens': 50}}))\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    if os.name == "nt":  # Windows can't exec a shebang script: wrap it
        wrapper = tmp_path / "claude.cmd"
        wrapper.write_text(f'@"{sys.executable}" "{fake}" %*\n')
        fake = wrapper
    tasks = Path(__file__).resolve().parents[1] / "bench" / "tasks.json"
    out = bench.run(tasks, reps=1, out_file=tmp_path / "r.jsonl", claude=str(fake), log=lambda *_: None)
    res = bench.compare(bench.load_rows(out))
    assert res["pairs"] == 3 and res["median_change"] == pytest.approx(-0.5)
    assert res["success"]["baseline"][1] == 3


def test_read_gate_ignores_binary_files(repo):
    pdf = repo / "manual.pdf"
    pdf.write_bytes(b"%PDF-1.4\n" + b"line\n" * 2000)
    payload = {"session_id": "s14", "cwd": str(repo), "tool_name": "Read", "tool_input": {"file_path": str(pdf)}}
    assert hooks.dispatch("pre-tool-use", payload) is None


# ---------- fixes: 1-hour cache, mid-edit lint ----------

def test_one_hour_cache_does_not_warn_after_12_minutes_but_does_after_an_hour(repo, tmp_path):
    now = datetime.now(timezone.utc)
    short = _transcript(tmp_path / "a.jsonl", 80_000, now - timedelta(minutes=12), write_1h=2_000)
    assert hooks.dispatch("user-prompt-submit", {"session_id": "h1", "cwd": str(repo), "transcript_path": str(short)}) is None
    long = _transcript(tmp_path / "b.jsonl", 80_000, now - timedelta(minutes=61), write_1h=2_000)
    out = hooks.dispatch("user-prompt-submit", {"session_id": "h1", "cwd": str(repo), "transcript_path": str(long)})
    assert "cache has expired" in out["systemMessage"]


def test_mid_edit_lint_is_deferred_to_the_stop_gate(repo, monkeypatch):
    unused = "f.py:1:8: F401 [*] `os` imported but unused"
    monkeypatch.setattr(quality, "check_file", lambda p, r: [unused])
    payload = {"session_id": "h2", "cwd": str(repo), "tool_name": "Edit", "tool_input": {"file_path": str(repo / "utils" / "money.py")}}
    assert "F401" not in json.dumps(hooks.dispatch("post-tool-use", payload))
    out = hooks.dispatch("stop", {"session_id": "h2", "cwd": str(repo)})
    assert out["decision"] == "block" and "F401" in out["reason"]


# ---------- two parts: save and code ----------

def test_code_only_skips_token_saver(repo, tmp_path):
    config.set_mode("code")
    read = {"session_id": "p1", "cwd": str(repo), "tool_name": "Read", "tool_input": {"file_path": str(repo / "big.py")}}
    assert hooks.dispatch("pre-tool-use", read) is None
    assert hooks.dispatch("session-start", {"cwd": str(repo)}) is None
    tr = _transcript(tmp_path / "t.jsonl", 150_000, datetime.now(timezone.utc))
    assert hooks.dispatch("stop", {"session_id": "p1", "cwd": str(repo), "transcript_path": str(tr)}) is None


def test_save_only_sends_no_code_rules_or_checks(repo):
    config.set_mode("save")
    bad = repo / "bad.py"
    bad.write_text("def a(:\n")
    assert hooks.dispatch("post-tool-use", {"session_id": "p2", "cwd": str(repo), "tool_name": "Write",
                                            "tool_input": {"file_path": str(bad)}}) is None


def test_legacy_modes_mean_both():
    config.set_mode("full")
    assert config.parts(config.get_mode()) == {"save", "code"}


def test_big_json_with_few_lines_gets_key_outline(repo):
    blob = repo / "data.json"
    blob.write_text(json.dumps({"users": ["x" * 50] * 600, "meta": {"v": 1}}))
    out = hooks.dispatch("pre-tool-use", {"session_id": "p3", "cwd": str(repo), "tool_name": "Read",
                                          "tool_input": {"file_path": str(blob)}})
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "key 'users'" in reason and "key 'meta'" in reason and "Grep" in reason


def test_fresh_session_with_big_system_prompt_does_not_overwrite_memory(repo, tmp_path):
    # Found live: a 2-line question in a new session (35k of system prompt and tools) asked for a rewrite.
    tr = _grown(tmp_path / "q.jsonl", 35_000, 36_000)
    assert hooks.dispatch("stop", {"session_id": "s22", "cwd": str(repo), "transcript_path": str(tr)}) is None
