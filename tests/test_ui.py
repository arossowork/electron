"""Server logic of the web UI, without a browser or a claude process."""

import gzip
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))
import server  # noqa: E402


class FakeProc:
    def __init__(self, lines, code=0):
        self.stdout = [json.dumps(x) + "\n" for x in lines]
        self.returncode = code

    def wait(self):
        pass

    def poll(self):
        return self.returncode

    def terminate(self):
        pass


@pytest.fixture
def sess(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "PROJECTS", tmp_path)
    (tmp_path / "demo").mkdir()
    s = server.Session("demo")
    s.started = []
    monkeypatch.setattr(s, "_start", lambda text, model: s.started.append(text))
    return s


def kinds(s):
    return [(e["kind"], e.get("text", "")) for e in s.history]


def asst(text=None, tool=None, mid="m1", parent=None, tokens=(1, 2, 3, 4)):
    c = []
    if text:
        c.append({"type": "text", "text": text})
    if tool:
        c.append({"type": "tool_use", "id": tool[0], "name": tool[1], "input": tool[2]})
    u = dict(zip(server.USAGE_FIELDS, tokens))
    return {"type": "assistant", "parent_tool_use_id": parent,
            "message": {"id": mid, "model": "claude-x", "content": c, "usage": u}}


def test_first_result_is_not_the_end(sess):
    """Item 51: an early result event (0 turns) must not produce 'agent finished'; the exit does."""
    res = {"type": "result", "session_id": "s1", "num_turns": 0, "duration_ms": 900, "result": "ok",
           "usage": {}, "modelUsage": {}}
    res2 = {**res, "num_turns": 5, "duration_ms": 4000}
    sess._pump(FakeProc([res, asst(text="still working"), res2]))
    st = [e for e in sess.history if e["kind"] == "status"]
    assert [e.get("busy") for e in st] == [True, False]
    fin = [e for e in st if "finished" in e["text"]]
    assert len(fin) == 1 and "5 turns" in fin[0]["text"]
    assert st[0]["text"] == "agent started"
    # the 'finished' line comes after the agent's text
    order = [e["kind"] for e in sess.history]
    assert order.index("agent") < len(order) - 1 - order[::-1].index("status")


def test_exit_without_result(sess):
    sess._pump(FakeProc([], code=1))
    assert "exited without a result (code 1" in sess.history[-2]["text"]


def test_message_while_busy_is_queued_and_delivered(sess):
    class Busy(FakeProc):
        def poll(self):
            return None

    sess.proc = Busy([])
    sess.send("first", "opus")
    sess.send("second", "sonnet")
    assert sess.queued == ["first", "second"] and sess.started == []
    sess.proc = FakeProc([])  # exited
    sess._deliver_queue()
    assert sess.started == ["first\n\nsecond"] and sess.queued == []


def test_interrupt_terminates(sess):
    calls = []

    class Busy(FakeProc):
        def poll(self):
            return None

        def terminate(self):
            calls.append(1)

    sess.proc = Busy([])
    sess.send("steer", "opus", interrupt=True)
    assert calls == [1] and sess.queued == ["steer"]


def test_stop_clears_queue(sess):
    class Busy(FakeProc):
        def poll(self):
            return None

    sess.proc = Busy([])
    sess.send("x", "opus")
    sess.stop()
    assert sess.queued == []


def test_usage_per_subagent(sess):
    launch = asst(tool=("tu1", "Agent", {"subagent_type": "hw-parts", "description": "Stage 3"}), mid="a")
    sub1 = asst(text="searching", mid="b", parent="tu1", tokens=(10, 20, 300, 40))
    sub1_again = asst(text="searching", mid="b", parent="tu1", tokens=(10, 20, 300, 40))  # streamed twice
    done = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tu1"}]}}
    res = {"type": "result", "session_id": "s", "num_turns": 1, "duration_ms": 10, "usage": {}, "modelUsage": {}}
    sess._pump(FakeProc([launch, sub1, sub1_again, done, res]))
    lines = (sess.dir / ".usage_agents.jsonl").read_text().splitlines()
    rec = json.loads(lines[0])  # written when the subagent ended, not at the end of the turn
    assert rec["agent"] == "hw-parts" and rec["turns"] == 1
    assert rec["tokens"]["claude-x"]["cache_read_input_tokens"] == 300
    assert "orchestrator" in sess.live and sess.live["hw-parts"]["launches"] == 1
    assert not any(e["kind"] == "usage" for e in sess.history)  # live usage is not persisted


def test_history_paging(sess):
    for i in range(10):
        sess.emit({"kind": "tool", "text": f"t{i}"})
    pg = sess.history_page(limit=4)
    assert [e["text"] for e in pg["events"]] == ["t6", "t7", "t8", "t9"] and pg["start"] == 6
    older = sess.history_page(before=pg["start"], limit=4)
    assert [e["text"] for e in older["events"]] == ["t2", "t3", "t4", "t5"]
    assert sess.history_page(before=2, limit=10)["start"] == 0
    s2 = server.Session("demo")  # reload: indexes continue
    assert s2.n_events == 10 and s2.history[-1]["i"] == 9


def test_questions_count(sess):
    inbox = sess.dir / "inbox"
    inbox.mkdir()
    (inbox / "questions.json").write_text(json.dumps({"round": 2, "questions": [
        {"id": "q1", "text": "a", "options": ["x"]}, {"id": "q1", "question": "b"}, {"text": "c"}, "d"]}))
    q = sess.questions()
    assert q["count"] == 4 and len({x["id"] for x in q["questions"]}) == 4
    (inbox / "questions.json").write_text("{half")
    assert sess.questions() is None
    (inbox / "questions.json").write_text(json.dumps([{"id": "a", "text": "t"}]))
    assert sess.questions()["count"] == 1


def test_baseline_questions_counts():
    base = ROOT / "evals/baselines/home-thermostat-2026-10-19/project/inbox"
    got = [server.normalize_questions(json.loads(p.read_text()))["count"] for p in sorted(base.glob("questions_round*.json"))]
    assert got == [4, 4, 3, 3, 2, 1]


def test_markdown_renderer_escapes():
    node = subprocess.run(["which", "node"], capture_output=True, text=True).stdout.strip()
    if not node:
        pytest.skip("node not installed")
    js = (ROOT / "ui/static/md.js").read_text() + """
const out = renderMarkdown(process.env.MD); process.stdout.write(out);"""
    def run(md):
        return subprocess.run([node, "-e", js], capture_output=True, text=True, check=True, env={"MD": md}).stdout
    assert "<script>" not in run("<script>alert(1)</script> **b**") and "<strong>b</strong>" in run("**b**")
    assert "href" not in run("[x](javascript:alert(1))") and 'href="https://a.b"' in run("[x](https://a.b)")
    assert "<table>" in run("| a | b |\n|---|---|\n| 1 | 2 |")
    assert "<li>" in run("- one\n- two") and "<pre><code>" in run("```\nx < y\n```") and "x &lt; y" in run("```\nx < y\n```")
    assert "<img" not in run("<img src=x onerror=alert(1)>")


def test_stage_times_and_cache_ttl():
    sys.path.insert(0, str(ROOT / "scripts"))
    import usage_tally as ut
    base = ROOT / "evals/baselines/home-thermostat-2026-10-19"
    rows = ut.stage_times(base / "run/ui_history.jsonl.gz")
    stages = {r["stage"]: r["seconds"] for r in rows[1:]}
    assert len(stages) == 25 and 20 * 60 < stages["Stage 2 architecture thermostat"] < 25 * 60
    assert rows[0]["turns"] == 13
    out = ut.cache_ttl_check(base / "metrics.json", base / "run/usage.jsonl")
    assert round(out["metrics"]["reads_per_write"]) == 45
