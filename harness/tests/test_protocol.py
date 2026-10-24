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
