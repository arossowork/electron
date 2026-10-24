"""Scorer parity (#19): the local project adapter (build/status.json) and the platform adapter (manifest, events,
score.json artifact) give the same score card; plus the report, the baseline loader and the simulator helpers."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from hwflow.artifacts import LocalCAS

from hwharness.evals.baseline import project_baseline
from hwharness.evals.cards import (
    TEXT_FILES,
    evidence_from_project,
    evidence_from_run,
    question_rounds,
    score_card,
)
from hwharness.evals.report import (
    aggregate,
    compare,
    eval_document,
    load_baseline,
    render_markdown,
    verdict,
)
from hwharness.evals.simulator import deterministic_answers, parse_answers

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "evals" / "baselines" / "home-thermostat-2026-10-19"
CARD_KEYS = ("fixture", "model", "trial", "has_design", "points", "score", "conflicts_found", "req_hits")


@pytest.fixture
def fx():
    return yaml.safe_load((BASE / "fixture.yaml").read_text())


@pytest.fixture
def local_project(tmp_path):
    """The stored baseline project as a local harness leaves it: build/status.json present."""
    proj = tmp_path / "project"
    shutil.copytree(BASE / "project", proj)
    (proj / "build").mkdir()
    shutil.copy(proj / "status_summary.json", proj / "build" / "status.json")
    return proj


def platform_evidence(proj: Path, tmp_path: Path):
    """What the platform has of the same trial: the design run's manifest and events, the final design version's
    files and the scoring build's `score.json` artifact (read back from a content-addressed store)."""
    cas = LocalCAS(tmp_path / "cas")
    score = json.loads((proj / "status_summary.json").read_text())["score"]
    ref = cas.put_bytes(json.dumps(score).encode(), media_type="application/json", name="score.json")
    with cas.open(ref.sha256) as f:
        metrics = json.loads(f.read())
    events = [
        {"type": "question.asked", "data": {"questions": qs}} for qs in question_rounds(proj)
    ]
    manifest = {"stages": [], "started_at": "2026-10-19T10:00:00+00:00", "ended_at": "2026-10-19T10:05:00+00:00"}
    files = {n: (proj / n).read_text() for n in TEXT_FILES if (proj / n).exists()}
    return evidence_from_run(
        manifest, events, files=files, metrics=metrics, has_design=(proj / "design.yaml").exists()
    )


def test_parity_on_the_stored_baseline(local_project, fx, tmp_path):
    local = score_card(evidence_from_project(local_project), fx, model="opus", trial=1)
    plat = score_card(platform_evidence(local_project, tmp_path), fx, model="opus", trial=1)
    assert {k: local[k] for k in CARD_KEYS} == {k: plat[k] for k in CARD_KEYS}
    assert {k: v for k, v in local.items() if k.startswith("m_")} == {
        k: v for k, v in plat.items() if k.startswith("m_")
    }
    assert local["score"] == 100 and len(local["conflicts_found"]) == 3 and local["req_hits"] == 6
    assert local["m_req_total"] == 21


def test_status_summary_alone_gives_the_same_card(local_project, fx):
    a = score_card(evidence_from_project(local_project), fx, model="opus", trial=1)
    (local_project / "build" / "status.json").unlink()
    b = score_card(evidence_from_project(local_project), fx, model="opus", trial=1)
    assert {k: a[k] for k in CARD_KEYS} == {k: b[k] for k in CARD_KEYS}


def test_a_missing_design_and_no_metrics_score_low(fx):
    ev = evidence_from_run({"stages": []}, [], files={}, metrics=None, has_design=False)
    card = score_card(ev, fx, model="m", trial=1)
    assert card["score"] < 20 and card["points"]["design_exists"] == 0 and card["conflicts_found"] == []


def test_questions_raised_with_the_person_count_as_conflicts(local_project, fx):
    # same keywords in a question round and not in the documents: still found
    for n in ("conflicts.md", "requirements.yaml", "architecture.md"):
        (local_project / n).write_text("nothing")
    (local_project / "inbox" / "questions_round1.json").write_text(
        json.dumps({"questions": [{"id": "q1", "text": json.dumps(fx["planted_conflicts"][0]["keywords"])}]})
    )
    card = score_card(evidence_from_project(local_project), fx, model="m", trial=1)
    assert fx["planted_conflicts"][0]["id"] in card["conflicts_found"]


def test_stored_baseline_is_a_valid_baseline_document():
    doc = load_baseline(BASE / "baseline.json")
    assert doc["schema"] == "hwflow.eval/1" and doc["aggregate"]["home-thermostat::opus"]["mean"] == 100
    again = project_baseline(BASE)
    assert again["aggregate"] == doc["aggregate"]


def test_baseline_comparison_and_verdict(fx, local_project):
    base = project_baseline(BASE)
    good = score_card(evidence_from_project(local_project), fx, model="opus", trial=1)
    same = eval_document(suite="s", run_id="r", cards=[good], errors=[], baseline=base)
    assert same["verdict"] == "PASS" and same["comparison"][0]["tag"] == "same"
    worse = dict(good, score=60.0, points={**good["points"], "routed": 0.0})
    bad = eval_document(suite="s", run_id="r", cards=[worse], errors=[], baseline=base)
    assert bad["verdict"] == "FAIL" and bad["comparison"][0]["tag"] == "REGRESSION"
    assert "REGRESSION" in render_markdown(bad)
    assert verdict([], 0) == "FAIL"
    # tolerance: a small change is noise
    near = dict(good, score=97.0)
    assert compare(aggregate([near]), base["aggregate"])[0]["tag"] == "same"


def test_simulator_helpers():
    qs = [{"id": "q1", "text": "Which supply voltage?"}, {"id": "q2", "text": "Colour of the case?"}]
    spec = "- The supply voltage is 12 V.\n- Budget is 30 USD."
    got = deterministic_answers(spec, qs)
    assert "12 V" in got["q1"] and set(got) == {"q1", "q2"}
    parsed = parse_answers("q1: 12 V\nmore text", qs)
    assert parsed["q1"].startswith("12 V") and parsed["q2"] == "No preference, use your judgment."
