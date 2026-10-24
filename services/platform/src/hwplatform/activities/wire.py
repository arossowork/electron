"""StageOut <-> the engine's StageResult JSON (`StageResult.to_json()`), without importing the engine
pipeline."""

from __future__ import annotations

from hwflow.artifacts import ref_from_dict, ref_to_dict

from ..contracts import StageOut

SUMMARY_MAX = 4096  # ADR 4.3: a stage summary is capped at 4 KiB on the wire


def out_to_json(o: StageOut) -> dict:
    return {
        "stage": o.stage,
        "verdict": o.verdict,
        "cache_key": o.cache_key,
        "cached": o.cached,
        "result": ref_to_dict(o.result),
        "outputs": {k: ref_to_dict(v) for k, v in o.outputs.items()},
        "state": {k: ref_to_dict(v) for k, v in o.state.items()},
        "summary": dict(o.summary),
        "seconds": o.seconds,
        "tool_versions": dict(o.tool_versions),
        "error": dict(o.error) if o.error else None,
    }


def _cap(summary: dict) -> dict:
    import json

    if len(json.dumps(summary, default=str)) <= SUMMARY_MAX:
        return summary
    small = {k: v for k, v in summary.items() if len(json.dumps(v, default=str)) <= 256}
    return {**small, "truncated": True}


def out_from_json(j: dict, attempt: int = 1) -> StageOut:
    return StageOut(
        stage=j["stage"],
        verdict=j["verdict"],
        cache_key=j.get("cache_key") or "",
        cached=bool(j.get("cached")),
        result=ref_from_dict(j["result"]),
        outputs={k: ref_from_dict(v) for k, v in (j.get("outputs") or {}).items()},
        state={k: ref_from_dict(v) for k, v in (j.get("state") or {}).items()},
        summary=_cap(dict(j.get("summary") or {})),
        seconds=float(j.get("seconds") or 0.0),
        tool_versions=dict(j.get("tool_versions") or {}),
        error=dict(j["error"]) if j.get("error") else None,
        attempt=attempt,
    )
