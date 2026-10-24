"""The 100-point score card of an evaluation trial, from evidence that does not depend on where it came from.

A trial of the `/hw-design` agent leaves two kinds of evidence: the build metrics (the `score` card that
`hwflow` writes into `status.json`, also stored as the run artifact `score.json`), and what the agent wrote and
asked (its documents and its questions to the product person). `score_card` turns an `Evidence` into the card.
Two adapters fill an `Evidence`:

- `evidence_from_project`: the local harness, a project folder (`build/status.json`, the documents, `inbox/`);
- `evidence_from_run`: the platform, a run manifest, the run's events, the final design version's files and the
  `score.json` of the scoring build. The platform reads no `status.json`.

Both give the same card for the same facts (`tests/test_evals_cards.py` proves it on the stored baseline).
Stdlib only: the platform's engine worker imports this module.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
# Documents the card reads. `requirements.yaml`, `conflicts.md` and `architecture.md` also feed the conflicts check.
TEXT_FILES = ("requirements.yaml", "conflicts.md", "architecture.md", "SUMMARY.md", "state.md")
CONFLICT_FILES = ("conflicts.md", "requirements.yaml", "architecture.md")
META_KEYS = ("questions", "rounds", "designer_calls", "cost_usd", "seconds", "tokens", "tokens_by_model")


@dataclass
class Evidence:
    has_design: bool
    metrics: dict = field(default_factory=dict)  # the status.json "score" card of the scoring build; {} = none
    texts: dict[str, str] = field(default_factory=dict)  # TEXT_FILES present -> content
    question_texts: list[str] = field(default_factory=list)  # one text per question round (question_text)
    meta: dict = field(default_factory=dict)  # META_KEYS
    build_timeout: bool = False


def question_text(questions: list[dict]) -> str:
    """The text of one question round that the conflict keywords are matched against (same in both adapters)."""
    return json.dumps(list(questions), indent=1, sort_keys=True, ensure_ascii=False)


def _lower_text(texts: Mapping[str, str], names) -> str:
    return "\n".join(texts[n] for n in names if n in texts).lower()


def _hit(text: str, group: list) -> bool:
    return any(str(k).lower() in text for k in group)


def score_card(ev: Evidence, fx: dict, *, model: str, trial: int) -> dict:
    """The score card of one trial against its fixture: points per item, the total, the metrics (`m_*`)."""
    sc = ev.metrics or {}
    card: dict = {"fixture": fx["id"], "model": model, "trial": trial, "has_design": ev.has_design}
    if ev.build_timeout:
        card["build_timeout"] = True  # stages not finished in time score as missing (0 points)
    card.update({f"m_{k}": v for k, v in sc.items()})
    order = [n for n in TEXT_FILES if n in ev.texts]
    text = _lower_text(ev.texts, order)
    conf_text = _lower_text(ev.texts, CONFLICT_FILES)
    for q in ev.question_texts:  # conflicts raised with the product person count too
        conf_text += "\n" + q.lower()
    pc = fx.get("planted_conflicts") or []
    found = [c["id"] for c in pc if all(_hit(conf_text, g) for g in c["keywords"])]
    er = fx.get("expected_requirements") or []
    req_hits = sum(_hit(text, g) for g in er)
    lim = fx.get("limits") or {}

    def ok(v, f):
        return v is not None and f(v)

    has = ev.has_design
    pts = {
        "design_exists": 5 * has,
        "erc_clean": 10 * ok(sc.get("erc_errors"), lambda v: v == 0),
        "drc_clean": 10 * ok(sc.get("drc_errors"), lambda v: v == 0),
        "routed": 10 * ok(sc.get("unrouted"), lambda v: v == 0),
        "sim_pass": 15 * (sc.get("sim_pass_ratio") or 0),
        "post_pass": 10 * (sc.get("post_pass_ratio") or 0),
        "req_coverage": 10 * (req_hits / len(er) if er else 1.0),
        "req_verified": 10 * (1 - (sc.get("req_unverified") or 0) / sc["req_total"] if sc.get("req_total") else 0),
        "conflicts": 15 * (len(found) / len(pc) if pc else 1.0),
        "area_ok": 2.5 * ok(sc.get("area_mm2"), lambda v: v <= lim.get("max_area_mm2", 1e9)),
        "cost_ok": 2.5 * ok(sc.get("cost_usd"), lambda v: 0 < v <= lim.get("max_cost_usd", 1e9)),
    }
    card.update(
        {
            "points": {k: round(v, 2) for k, v in pts.items()},
            "score": round(sum(pts.values()), 2),
            "conflicts_found": found,
            "req_hits": req_hits,
            **{k: ev.meta.get(k) for k in META_KEYS},
        }
    )
    return card


# ------------------------------------------------------------------ adapter 1: a project folder (local harness)


def read_status_score(proj: Path) -> dict:
    """The score card of a project's build: `build/status.json`, else `status_summary.json` (a stored baseline
    keeps the status without its build folder)."""
    for p in (proj / "build" / "status.json", proj / "status_summary.json"):
        if p.exists():
            return json.loads(p.read_text()).get("score", {}) or {}
    return {}


def question_rounds(proj: Path) -> list[list[dict]]:
    """The question rounds the agent asked, from `inbox/questions_round*.json` (oldest first)."""
    out = []
    for p in sorted((proj / "inbox").glob("questions_round*.json")):
        try:
            out.append(list(json.loads(p.read_text()).get("questions", [])))
        except (ValueError, AttributeError):
            continue
    return out


def evidence_from_project(proj: Path, meta: dict | None = None, *, build_timeout: bool = False) -> Evidence:
    texts = {n: (proj / n).read_text(errors="replace") for n in TEXT_FILES if (proj / n).exists()}
    return Evidence(
        has_design=(proj / "design.yaml").exists(),
        metrics=read_status_score(proj),
        texts=texts,
        question_texts=[question_text(q) for q in question_rounds(proj)],
        meta=dict(meta or {}),
        build_timeout=build_timeout,
    )


# ------------------------------------------------------------------ adapter 2: run manifests and events (platform)


def _sum_usage(stages: list[dict]) -> tuple[dict, dict]:
    """(tokens, tokens_by_model) from the `usage` of the design stages' summaries ({model: {key: n}})."""
    tokens = dict.fromkeys(USAGE_KEYS, 0)
    by_model: dict[str, dict] = {}
    seen = False
    for st in stages:
        for model, u in ((st.get("summary") or {}).get("usage") or {}).items():
            seen = True
            m = by_model.setdefault(model, dict.fromkeys(USAGE_KEYS, 0))
            for k in USAGE_KEYS:
                m[k] += int(u.get(k) or 0)
                tokens[k] += int(u.get(k) or 0)
    return (tokens, by_model) if seen else ({}, {})


def run_meta(manifest: dict, events: list[dict]) -> dict:
    """The card's meta (questions, rounds, designer calls, seconds, tokens) from the design run's manifest and
    its events. A round is one `question.asked` event; a designer call is one finished stage or one round."""
    asked = [e for e in events if e.get("type") == "question.asked"]
    questions = sum(len((e.get("data") or {}).get("questions") or []) for e in asked)
    stages = [s for s in manifest.get("stages", []) if s.get("status") == "succeeded"]
    tokens, by_model = _sum_usage(stages)
    meta = {
        "questions": questions,
        "rounds": len(asked),
        "designer_calls": len(stages) + len(asked),
        "cost_usd": None,  # a subscription is not billed; tokens are the effort measure
        "seconds": None,
        "tokens": tokens or None,
        "tokens_by_model": by_model or None,
    }
    from datetime import datetime

    a, b = manifest.get("started_at"), manifest.get("ended_at")
    if a and b:
        meta["seconds"] = round((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds(), 1)
    return meta


def evidence_from_run(
    manifest: dict,
    events: list[dict],
    *,
    files: Mapping[str, str],
    metrics: dict | None,
    has_design: bool,
) -> Evidence:
    """Evidence of a platform trial. `manifest`: the design run's manifest; `events`: its events (envelopes with
    `type` and `data`); `files`: the TEXT_FILES of the final design version; `metrics`: the `score.json` of the
    scoring build (None when it produced none)."""
    asked = [e for e in events if e.get("type") == "question.asked"]
    return Evidence(
        has_design=has_design,
        metrics=dict(metrics or {}),
        texts={n: files[n] for n in TEXT_FILES if n in files},
        question_texts=[question_text((e.get("data") or {}).get("questions") or []) for e in asked],
        meta=run_meta(manifest, events),
    )
