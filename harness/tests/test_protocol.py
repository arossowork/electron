"""The product-person protocol and the hooks hwharness registers in the engine."""

import shutil
from pathlib import Path

import pytest

from hwflow.core.design import Design

EX = Path(__file__).resolve().parents[2] / "examples" / "sensor_preamp"


def _copy_example(tmp_path):
    proj = tmp_path / "p"
    shutil.copytree(EX, proj, ignore=shutil.ignore_patterns("build"))
    return proj


@pytest.mark.slow
def test_cost_changes_need_the_product_person(tmp_path, capsys):
    """Backlog 44: after `hwflow approve`, a change of layers, board size or BOM cost is a check error until
    conflicts.md or an inbox question records it (then a warning), and `approve` takes the new baseline."""
    import json

    import yaml

    from hwflow.cli import main
    from hwharness.protocol import approval_findings
    proj = _copy_example(tmp_path)
    raw = yaml.safe_load((proj / "design.yaml").read_text())
    ref = next(r for r, c in raw["components"].items() if not c.get("virtual"))
    raw["components"][ref]["price_usd"] = 1.0
    (proj / "design.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    d = Design.load(proj)
    assert [x["sev"] for x in approval_findings(d)] == ["info"]           # no baseline yet
    assert main(["approve", str(proj), "--note", "round 1 q2"]) == 0
    a = json.loads((proj / "build" / "approved.json").read_text())
    assert a["note"] == "round 1 q2" and a["layers"] == d.board["layers"]
    assert approval_findings(Design.load(proj)) == []
    raw = yaml.safe_load((proj / "design.yaml").read_text())
    raw["board"]["layers"] = 4
    raw["board"]["width_mm"] = d.board["width_mm"] + 0.4                     # inside the 0.5 mm size tolerance
    raw["components"][ref]["price_usd"] = 1.06                              # +6 %, above the 5 % default
    (proj / "design.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    f = approval_findings(Design.load(proj))
    assert [(x["sev"], x["msg"].split(" ")[0]) for x in f] == [("error", "board.layers"), ("error", "BOM")]
    assert main(["check", str(proj)]) == 1
    (proj / "conflicts.md").write_text("## C9: 4 layers for the decap loop\nchange: layers\n")
    inbox = proj / "inbox"
    inbox.mkdir()
    (inbox / "questions_round2.json").write_text(json.dumps(
        {"round": 2, "questions": [{"id": "q1", "text": "+$ for the relay?", "change": "cost"}]}))
    f = approval_findings(Design.load(proj))
    assert [x["sev"] for x in f] == ["warn", "warn"] and "not answered yet" in f[1]["msg"]
    (inbox / "answers.md").write_text("## Round 2\nq1: yes, take the better relay\n")
    f = approval_findings(Design.load(proj))
    assert "answered in round 2 q1" in f[1]["msg"]
    capsys.readouterr()
    assert main(["check", str(proj)]) == 0
    main(["approve", str(proj)])
    assert approval_findings(Design.load(proj)) == []


def _req_proj(tmp_path, reqs):
    import yaml
    proj = tmp_path / "r"
    proj.mkdir()
    (proj / "design.yaml").write_text(yaml.safe_dump({"project": "r", "components": {}}))
    (proj / "requirements.yaml").write_text(yaml.safe_dump({"product": "x", "requirements": reqs}, sort_keys=False))
    return proj


REQS = [
    {"id": "R7", "priority": "must", "text": "Battery life 12 months or more", "acceptance": "P_avg <= 246.6 uW",
     "verification": "analysis", "source": "brief"},
    {"id": "R10", "priority": "must", "text": "Energy per sample 11.6 mJ or less, derived from R7",
     "acceptance": "E <= 11.6 mJ", "verification": "analysis", "source": "derived from R7"},
    {"id": "R16", "priority": "must", "text": "Drift 22.8 ppm", "acceptance": "Datasheet: crystal tolerance",
     "verification": "datasheet", "source": "lead default"},
    {"id": "R20", "priority": "should", "text": "Margin", "acceptance": "x", "verification": "analysis", "source": "s"},
]


def test_acceptance_diff_needs_a_product_decision(tmp_path):
    """A requirement's acceptance, priority, method or text numbers changed after `approve --requirements` is a check
    error on a must unless an answered question, a cited PM answer, a pending conflicts.md entry or a notes/
    re-allocation of a derived limit records it."""
    import copy
    import json

    import yaml

    from hwharness.protocol import acceptance_findings, approval_findings, approve, approve_requirements
    proj = _req_proj(tmp_path, REQS)
    d = Design.load(proj)
    assert [x["sev"] for x in acceptance_findings(d)] == ["info"]
    approve_requirements(d, "round 1")
    assert approval_findings(d)[0]["sev"] == "info"           # requirements only: no layers baseline yet
    assert acceptance_findings(d) == []
    r = copy.deepcopy(REQS)
    r[0]["text"] = "Battery life 12 months or more (PM round 1, q1 and R3)"   # identifiers are not spec numbers
    r[1]["text"], r[1]["acceptance"] = "Energy per sample 12.8 mJ or less, derived from R7", "E <= 12.8 mJ"
    r[2]["acceptance"] = "Analysis: calibrated against the 40 MHz crystal"
    r[3]["priority"] = "could"
    r.append({"id": "R44", "priority": "must", "text": "New 5 V rail", "acceptance": "y", "verification": "analysis"})
    (proj / "requirements.yaml").write_text(yaml.safe_dump({"requirements": r}, sort_keys=False))
    f = {x["msg"].split(":")[0]: x for x in acceptance_findings(Design.load(proj))}
    assert set(f) == {"R10", "R16", "R20", "R44"}
    assert [f[k]["sev"] for k in ("R10", "R16", "R20", "R44")] == ["error", "error", "warn", "error"]
    assert "(11.6 -> 12.8)" in f["R10"]["msg"] and "should -> could" in f["R20"]["msg"]
    # the records that make it a warning
    (proj / "notes").mkdir()
    (proj / "notes" / "realloc.md").write_text("R10: 12.8 mJ, R7 still 12.3 months")
    r[1]["source"] = "derived from R7; re-allocated in notes/realloc.md"
    r[2]["source"] = "lead default; PM round 2, q3"
    (proj / "requirements.yaml").write_text(yaml.safe_dump({"requirements": r}, sort_keys=False))
    inbox = proj / "inbox"
    inbox.mkdir()
    (inbox / "answers.md").write_text("## Round 2\nq3: yes, calibrate\n")
    (inbox / "questions_round3.json").write_text(json.dumps(
        {"round": 3, "questions": [{"id": "q1", "text": "a 5 V rail?", "reqs": ["R44"]}]}))
    (proj / "conflicts.md").write_text("## C30\nchange: R20\n")
    f = {x["msg"].split(":")[0]: x for x in acceptance_findings(Design.load(proj))}
    assert all(x["sev"] == "warn" for x in f.values())
    assert "notes/realloc.md" in f["R10"]["msg"] and "PM round 2, q3" in f["R16"]["msg"]
    assert "not answered yet" in f["R44"]["msg"] and "conflicts.md" in f["R20"]["msg"]
    (proj / "notes" / "realloc.md").unlink()                   # a re-allocation needs its note
    assert next(x for x in acceptance_findings(Design.load(proj)) if x["msg"].startswith("R10"))["sev"] == "error"
    d = Design.load(proj)
    approve_requirements(d)
    assert acceptance_findings(d) == []
    approve(d)                                                  # the design baseline keeps the requirements
    assert "requirements" in json.loads((proj / "build" / "approved.json").read_text())
    assert acceptance_findings(Design.load(proj)) == []


def test_pending_cost_question_lowers_cost_errors(tmp_path):
    import json

    from hwharness.protocol import pending_cost_filter
    proj = _req_proj(tmp_path, REQS)
    d = Design.load(proj)
    f = [{"sev": "error", "code": "cost-limit", "msg": "R42: over"}, {"sev": "error", "code": "abs-max", "msg": "x"}]
    assert pending_cost_filter(d, f) == f
    inbox = proj / "inbox"
    inbox.mkdir()
    (inbox / "questions_round2.json").write_text(json.dumps(
        {"round": 2, "questions": [{"id": "q1", "text": "cost?", "change": "cost"}]}))
    g = pending_cost_filter(Design.load(proj), f)
    assert [x["sev"] for x in g] == ["warn", "error"] and "not answered yet" in g[0]["msg"]
    (inbox / "answers.md").write_text("## Round 2\nq1: the limit stays\n")
    assert [x["sev"] for x in pending_cost_filter(Design.load(proj), f)] == ["error", "error"]
