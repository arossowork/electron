"""Validation of the options a client may send when it starts a run (ADR 6: 422 outside the allowlist).

The platform does not import the engine, so the allowlist is written out here: it is the part of
`hwflow.pipeline.BuildOptions` that a remote caller may set. Not allowed: `stages` (single-stage commands),
`workers` (a worker setting), `build`, `layout_search` (CLI plumbing). Values are normalized (defaults filled,
types fixed) so the same request always gives the same `options` and the same `options_hash`.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

BUILD_STAGES = ("check", "sch", "sim", "layout", "post", "fab", "em")
DESIGN_STAGES = ("requirements", "partition", "parts", "circuit", "review", "layout", "firmware")

# name -> (type, default, min, max)
_BUILD = {
    "until": (str, "fab", None, None),
    "mc": (int, 0, 0, 500),
    "reuse": (bool, False, None, None),
    "force": (bool, False, None, None),
    "rebuild": (bool, False, None, None),
    "auto": (bool, False, None, None),
    "attempts": (int, 4, 1, 16),
    "retry_layout": (bool, False, None, None),
    "seeds": (int, 4, 1, 16),
    "iters": (int, 30000, 1, 200_000),
    "passes": (int, 30, 1, 100),
    "brief": (bool, False, None, None),
}
# `rebuild` only skips the cache; it does not change what a build produces, so it is not in the hash.
_NOT_IN_HASH = {"rebuild"}
_SETTINGS = re.compile(r"^layout\.(budget_s|seed0|asic\.[A-Za-z0-9_.]+)$")


class OptionsError(ValueError):
    pass


def _scalar(v: Any) -> bool:
    return isinstance(v, bool | int | float | str)


def normalize_build_options(raw: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(raw) - set(_BUILD))
    if unknown:
        raise OptionsError(
            f"options not allowed: {', '.join(unknown)} (allowed: {', '.join(sorted(_BUILD))})"
        )
    out: dict[str, Any] = {}
    for name, (typ, default, lo, hi) in _BUILD.items():
        v = raw.get(name, default)
        if typ is bool:
            if not isinstance(v, bool):
                raise OptionsError(f"option {name} must be true or false")
        elif typ is int:
            if isinstance(v, bool) or not isinstance(v, int) or not (lo <= v <= hi):
                raise OptionsError(f"option {name} must be an integer from {lo} to {hi}")
        elif v not in BUILD_STAGES:
            raise OptionsError(f"option {name} must be one of {', '.join(BUILD_STAGES)}")
        out[name] = v
    return out


def normalize_settings_overrides(raw: dict[str, Any]) -> dict[str, Any]:
    for k, v in raw.items():
        if not _SETTINGS.match(k):
            raise OptionsError(f"settings override not allowed: {k}")
        if not _scalar(v) or (isinstance(v, str) and len(v) > 200):
            raise OptionsError(f"settings override {k} must be a number, boolean or short string")
    return dict(sorted(raw.items()))


def normalize_design_options(raw: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(raw) - {"brief", "answerer", "stages"})
    if unknown:
        raise OptionsError(f"options not allowed: {', '.join(unknown)} (allowed: brief, answerer, stages)")
    brief = raw.get("brief")
    if not isinstance(brief, str) or not brief.strip() or len(brief) > 20_000:
        raise OptionsError("option brief is required: text of 1 to 20000 characters")
    if raw.get("answerer", "human") != "human":
        raise OptionsError("option answerer must be human (the simulator is for evals)")
    stages = raw.get("stages", [])
    if (
        not isinstance(stages, list)
        or any(s not in DESIGN_STAGES for s in stages)
        or len(set(stages)) != len(stages)
    ):
        raise OptionsError(f"option stages must be a list without repeats of: {', '.join(DESIGN_STAGES)}")
    return {"brief": brief, "answerer": "human", "stages": stages}


def options_hash(options: dict[str, Any], settings_overrides: dict[str, Any] | None = None) -> str:
    """12 hex of the result-relevant options (ADR 3.1). The ADR also folds in the settings snapshot (`key_dump`),
    which the worker resolves later (#9); until then only what the client sent counts."""
    rel = {k: v for k, v in options.items() if k not in _NOT_IN_HASH}
    doc = {"options": rel, "settings_overrides": settings_overrides or {}}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:12]
