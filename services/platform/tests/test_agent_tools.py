"""The agent's MCP tools (hwplatform.agent.tools), the engine-tool arguments and URL check, the workspace
snapshots and the stage prompts. A fake backend stands in for the platform; no database, S3 or API call."""

from __future__ import annotations

import asyncio
import io
import json
import socket
import tarfile
from pathlib import Path

import pytest
from hwflow.artifacts import ArtifactRef, LocalCAS, put_tree, read_tree

from hwplatform.activities.engine_tools import Refused, argv_for, check_url
from hwplatform.agent import workspace as wsmod
from hwplatform.agent.sandbox import REF_DIR
from hwplatform.agent.tools import TOOL_NAMES, BuildResult, EngineResult, ToolBox, mcp_server

STATUS = ArtifactRef("5" * 64, 2, "application/json", "status.json")


class FakeBackend:
    def __init__(self):
        self.builds: list[dict] = []
        self.engine_calls: list[tuple[str, dict, bool]] = []
        self.fetched: list[Path] = []

    async def build(self, ws, options):
        self.builds.append(options)
        n = len(self.builds)
        return BuildResult(f"run-{n}", f"build-wf-{n}", "succeeded", "PASS", "# REPORT\nPASS", STATUS, b"{}")

    async def engine(self, tool, args, ws, status_json):
        self.engine_calls.append((tool, args, ws is not None))
        if tool == "fetch_url":
            return EngineResult("fetched 3 bytes", True, None, {"file": STATUS, "text": STATUS})
        return EngineResult(f"{tool} ok", True, None, {})

    async def apply(self, tree, ws):
        return []

    async def fetch_file(self, ref, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("x")
        self.fetched.append(dest)

    async def cancel_builds(self, ids):
        return None


def call(box, name, args=None):
    return asyncio.run(box.call(name, args or {}))


def text(r) -> str:
    return r["content"][0]["text"]


@pytest.fixture
def box(tmp_path):
    return ToolBox(tmp_path, FakeBackend(), stage="circuit")


def test_build_writes_the_report_and_links_the_build(box, tmp_path):
    r = call(box, "trace")
    assert r.get("is_error") and "run `build` first" in text(r)
    r = call(box, "build", {"until": "sim", "mc": 9999, "reuse": True})
    assert not r.get("is_error") and "verdict PASS" in text(r)
    assert box.backend.builds == [{"until": "sim", "mc": 500, "reuse": True}]
    assert (tmp_path / "build" / "REPORT.md").read_text() == "# REPORT\nPASS"
    assert (tmp_path / "build" / "status.json").read_bytes() == b"{}"
    assert box.builds == ["build-wf-1"] and box.verdict == "PASS" and box.status_json == STATUS
    call(box, "check")
    assert box.backend.builds[-1] == {"until": "check"} and box.builds == ["build-wf-1", "build-wf-2"]
    assert not call(box, "trace").get("is_error")
    assert box.backend.engine_calls[-1] == ("trace", {}, True)
    assert call(box, "build", {"until": "nope"}).get("is_error")


def test_lookups_go_to_the_engine_without_the_workspace(box):
    call(box, "lib_show", {"lib_id": "Device:R"})
    call(box, "parts_search", {"category": "Resistors", "filters": {"Resistance": "10k"}, "text": "0603"})
    assert box.backend.engine_calls == [
        ("lib_show", {"lib_id": "Device:R"}, False),
        (
            "parts_search",
            {"category": "Resistors", "filters": {"Resistance": "10k"}, "text": "0603", "limit": 15},
            False,
        ),
    ]
    assert call(box, "lib_show", {}).get("is_error")  # missing lib_id: invalid input, not a crash
    assert call(box, "no_such_tool").get("is_error")


def test_fetch_url_saves_under_datasheets(box, tmp_path):
    r = call(box, "fetch_url", {"url": "https://example.com/a/TPS62840.pdf", "name": "../../etc/x y.pdf"})
    assert not r.get("is_error")
    names = sorted(p.relative_to(tmp_path).as_posix() for p in box.backend.fetched)
    assert names == ["datasheets/etc_x_y.pdf", "datasheets/etc_x_y.pdf.txt"]


def test_ask_ends_the_turn(box):
    assert call(box, "ask_product_person", {"questions": []}).get("is_error")
    assert call(box, "ask_product_person", {"questions": [{"text": "q"}] * 5}).get("is_error")
    r = call(
        box,
        "ask_product_person",
        {"questions": [{"id": "q1", "text": "Battery?", "options": ["CR2032", "AA"]}, {"text": "Size?"}]},
    )
    assert not r.get("is_error") and box.ended
    assert box.questions == [
        {"id": "q1", "text": "Battery?", "options": ["CR2032", "AA"]},
        {"id": "q2", "text": "Size?"},
    ]
    assert call(box, "build", {"until": "sim"}).get("is_error") and not box.backend.builds
    assert not call(box, "log_decision", {"text": "chose X", "source": "datasheet p3"}).get("is_error")
    assert box.decisions == [{"stage": "circuit", "text": "chose X", "source": "datasheet p3"}]


def test_mcp_server_lists_every_tool(box):
    srv = mcp_server(box)
    assert srv["type"] == "sdk" and srv["name"] == "hwflow"
    assert len(TOOL_NAMES) == 14 and "ask_product_person" in TOOL_NAMES and "fetch_url" in TOOL_NAMES


# -- engine tool arguments and the URL check ----------


def test_argv_for_never_lets_a_value_become_an_option():
    assert argv_for("lib_sym", {"query": "op amp"}, ".") == ["lib", "sym", "--", "op", "amp"]
    assert argv_for("lib_show", {"lib_id": "--help"}, ".") == ["lib", "show", "--", "--help"]
    assert argv_for("parts_search", {"category": "Resistors", "filters": {"a": "1"}, "text": "-x"}, "p") == [
        "parts",
        "Resistors",
        "a=1",
        "--text=-x",
        "--limit=15",
    ]
    assert argv_for("optimize", {"budget": 10**6, "apply": True}, "p") == [
        "optimize",
        "p",
        "--budget=2000",
        "--apply",
    ]
    assert argv_for("fw", {"scaffold": True}, "p") == ["fw", "p", "--scaffold"]
    for tool, args in [
        ("parts_search", {"category": "--help"}),
        ("parts_search", {"category": "R", "filters": {"--x": "1"}}),
        ("parts_search", {"category": "R", "filters": {"a": "1\n2"}}),
        ("lib_sym", {"query": ""}),
        ("lib_sym", {"query": "a\x00b"}),
        ("build", {}),
    ]:
        with pytest.raises(Refused):
            argv_for(tool, args, ".")


def _resolver(*addrs):
    def resolve(host, port, type=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in addrs]

    return resolve


def test_check_url_only_public_http():
    assert (
        check_url("https://www.ti.com/lit/ds/x.pdf", _resolver("23.1.2.3"))
        == "https://www.ti.com/lit/ds/x.pdf"
    )
    for url, addrs in [
        ("http://metadata/", ("169.254.169.254",)),
        ("http://x/", ("10.0.0.5",)),
        ("http://x/", ("127.0.0.1",)),
        ("http://x/", ("23.1.2.3", "192.168.1.1")),  # one private address is enough to refuse
        ("http://x/", ("::ffff:127.0.0.1",)),
        ("http://x/", ("fd00::1",)),
        ("http://x:8080/", ("23.1.2.3",)),
        ("ftp://x/", ("23.1.2.3",)),
        ("file:///etc/passwd", ()),
        ("http://user:pw@x/", ("23.1.2.3",)),
        ("http://hwflow-postgres:443/", ("172.18.0.2",)),
    ]:
        with pytest.raises(Refused):
            check_url(url, _resolver(*addrs))


# -- workspace snapshots ----------


def test_snapshot_and_version_tree(tmp_path):
    cas = LocalCAS(tmp_path / "cas")
    ws = tmp_path / "ws"
    for rel, body in {
        "design.yaml": "a: 1\n",
        "notes/n.md": "n",
        "build/REPORT.md": "r",
        "build/approved.json": "{}",
        ".claude/settings.json": "{}",
        ".mcp.json": "{}",
        f"{REF_DIR}/CLAUDE.md": "ref",
    }.items():
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(body)
    (ws / "evil").symlink_to("/etc/passwd")
    snap = set(read_tree(cas, wsmod.snapshot(cas, ws)))
    assert snap == {"design.yaml", "notes/n.md", "build/REPORT.md", "build/approved.json"}
    ver = set(read_tree(cas, wsmod.version_tree(cas, ws)))
    assert ver == {"design.yaml", "notes/n.md", "build/approved.json"}

    # an uploaded project with .claude/ and .mcp.json: they never reach the session's workspace
    src = tmp_path / "upload"
    (src / ".claude").mkdir(parents=True)
    (src / ".claude" / "settings.json").write_text('{"hooks": {}}')
    (src / ".mcp.json").write_text("{}")
    (src / "design.yaml").write_text("a: 1\n")
    tree = put_tree(cas, src, name="project")
    dest = tmp_path / "dest"
    wsmod.materialize(cas, tree, dest)
    assert sorted(p.name for p in dest.iterdir()) == ["design.yaml"]


def test_session_roundtrip_keeps_whole_lines_and_refuses_odd_members(tmp_path):
    cas = LocalCAS(tmp_path / "cas")
    cfg = tmp_path / "cfg"
    t = cfg / "projects" / "-ws" / "abc.jsonl"
    t.parent.mkdir(parents=True)
    t.write_bytes(b'{"a":1}\n{"b":2}\n{"partial')
    (cfg / ".credentials.json").write_text("never saved")
    ref = wsmod.save_session(cas, cfg)
    assert ref is not None and ref.media_type == wsmod.SESSION_MEDIA_TYPE
    out = tmp_path / "out"
    wsmod.restore_session(cas, ref, out)
    assert (out / "projects" / "-ws" / "abc.jsonl").read_bytes() == b'{"a":1}\n{"b":2}\n'
    assert not (out / ".credentials.json").exists()
    assert wsmod.save_session(cas, tmp_path / "none") is None

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name in ("../escape.jsonl", "projects/../../x.jsonl", "settings.json", "/abs.jsonl"):
            info = tarfile.TarInfo(name)
            info.size = 1
            tf.addfile(info, io.BytesIO(b"x"))
        link = tarfile.TarInfo("projects/link")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
        tf.addfile(link)
    bad = cas.put_bytes(buf.getvalue(), media_type=wsmod.SESSION_MEDIA_TYPE)
    out2 = tmp_path / "o2" / "cfg"
    wsmod.restore_session(cas, bad, out2)
    assert not (tmp_path / "o2").exists() or not any((tmp_path / "o2").rglob("*.jsonl"))
    assert not (tmp_path / "escape.jsonl").exists() and not (out2 / "projects" / "link").exists()


def test_install_references_read_only(tmp_path):
    root = tmp_path / "prompts"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "design-yaml.md").write_text("doc")
    (root / "CLAUDE.md").write_text("rules")
    ws = tmp_path / "ws"
    ws.mkdir()
    wsmod.install_references(root, ws)
    p = ws / REF_DIR / "docs" / "design-yaml.md"
    assert p.read_text() == "doc" and oct(p.stat().st_mode)[-3:] == "444"


# -- stage prompts (hwharness.prompts) ----------


def test_stage_prompts_cover_every_design_stage():
    from hwharness.prompts import MODEL_IDS, answers_message, stage_prompt

    from hwplatform.contracts import DESIGN_STAGES

    for stage in DESIGN_STAGES:
        sp = stage_prompt(stage)
        assert sp.model in MODEL_IDS.values(), (stage, sp.model)
        assert "There is no shell" in sp.system and "ask_product_person" in sp.system
        assert sp.max_turns and sp.max_turns > 0
    assert stage_prompt("circuit", models={"circuit": "haiku"}).model == "claude-haiku-4-5-20251001"
    assert stage_prompt("circuit", models={"circuit": "claude-x"}).model == "claude-x"
    with pytest.raises(KeyError):
        stage_prompt("nope")
    msg = answers_message([{"id": "q1", "text": "Battery?", "default": "AA"}, {"id": "q2", "text": "Size"}],
                          {"q2": "50 mm", "note": "keep it cheap"}, 2)  # fmt: skip
    assert (
        "round 2" in msg and "q1 (Battery?): AA" in msg and "q2 (Size): 50 mm" in msg and "note: keep" in msg
    )


def test_engine_tool_policies_cover_every_tool():
    from hwplatform.contracts import ENGINE_TOOL_QUEUE, ENGINE_TOOLS

    assert set(ENGINE_TOOL_QUEUE) <= set(ENGINE_TOOLS)
    assert json.dumps(sorted(ENGINE_TOOLS))
