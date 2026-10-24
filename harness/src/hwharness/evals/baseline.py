"""A baseline document from a stored project, so a stored baseline (`evals/baselines/<name>/`) can be loaded as the
baseline input of an evaluation, locally or on the platform.

The card is computed with the local adapter (`build/status.json`, else the stored `status_summary.json`, the
documents and `inbox/`). No build and no model call: it reads files.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from .cards import evidence_from_project, question_rounds, score_card
from .report import eval_document


def project_baseline(base_dir: Path | str, *, model: str = "opus", suite: str = "baseline") -> dict:
    """The eval document of `<base_dir>/project`, scored against `<base_dir>/fixture.yaml`."""
    base = Path(base_dir)
    proj = base / "project"
    fx = yaml.safe_load((base / "fixture.yaml").read_text())
    rounds = question_rounds(proj)
    meta = {"questions": sum(len(q) for q in rounds), "rounds": len(rounds)}
    card = score_card(evidence_from_project(proj, meta), fx, model=model, trial=1)
    return eval_document(
        suite=suite,
        run_id=None,
        cards=[card],
        errors=[],
        baseline=None,
        options={"source": base.name, "fixtures": [fx["id"]], "models": [model]},
    )


def write_project_baseline(base_dir: Path | str, **kw) -> Path:
    out = Path(base_dir) / "baseline.json"
    out.write_text(json.dumps(project_baseline(base_dir, **kw), indent=1, sort_keys=True) + "\n")
    return out
