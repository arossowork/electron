"""Stage time budgets (`hwflow budget`, docs/stage-budget.md)."""

import json

from hwflow.pipeline import budget, timing

M, T0 = 60.0, 1_700_000_000.0


def _marks(tmp_path, seq):
    for at, st in seq:
        timing.mark(tmp_path, st, at=T0 + at * M)


def test_budget_flags_current_stage_and_merges_rework(tmp_path):
    _marks(tmp_path, [(0, "1-requirements"), (6, "wait"), (40, "1-requirements"), (43, "1b-partition"),
                      (50, "2-architecture"), (65, "3-parts"), (80, "2-architecture-rework"), (90, "3-parts")])
    s = budget.status(tmp_path, now=T0 + 118 * M, env="")
    rows = {r["stage"]: r for r in s["stages"]}
    assert rows["1-requirements"]["min"] == 9.0 and rows["1-requirements"]["status"] == "near"
    assert rows["wait"]["budget_min"] is None and s["wait_min"] == 34.0 and s["work_min"] == 84.0
    # the rework mark counts against the stage-2 budget (same stage number), as rework
    assert rows["2-architecture"]["min"] == 25.0 and rows["2-architecture"]["rework_min"] == 10.0
    assert rows["2-architecture"]["status"] == "over"
    assert s["current"] == "3-parts" and rows["3-parts"]["min"] == 43.0 and rows["3-parts"]["status"] == "over"
    assert s["action"].startswith("over budget")
    text = budget.summary(tmp_path, now=T0 + 118 * M, env="")
    assert "now 3-parts, 43 of 30 min (over)" in text and "| wait | 34 | - | none | 0 |" in text


def test_budget_overrides_and_near(tmp_path):
    (tmp_path / "budget.yaml").write_text("3: 60\n7-layout: 300\n")
    _marks(tmp_path, [(0, "3-parts")])
    b = budget.budgets(tmp_path, env="4-circuit=55,bad=x")
    assert b["3-parts"] == 60 and b["7-layout"] == 300 and b["4-circuit"] == 55
    s = budget.status(tmp_path, now=T0 + 50 * M, env="")
    assert s["stages"][0]["status"] == "near" and s["action"].startswith("near budget")
    assert "budget 60 min" in budget.line("3-parts-fix", tmp_path)
    assert budget.line("x-unknown") == ""


def test_budget_cli(tmp_path, capsys, monkeypatch):
    from hwflow.cli import main
    monkeypatch.delenv("HWFLOW_STAGE_BUDGETS", raising=False)
    assert main(["time", str(tmp_path), "--mark", "4-circuit"]) == 0
    assert "budget 40 min" in capsys.readouterr().out
    assert main(["budget", str(tmp_path), "--json"]) == 0
    s = json.loads(capsys.readouterr().out)
    assert s["current"] == "4-circuit" and s["stages"][0]["status"] == "ok"
    assert main(["budget", str(tmp_path / "missing")]) == 0
    assert "no stage marks" in capsys.readouterr().out
