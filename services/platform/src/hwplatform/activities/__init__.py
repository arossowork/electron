"""Temporal activities of the build (ADR 0001, 4.2 to 4.6). They run in `hwplatform-worker`, which is the one
platform process that imports the EDA engine (import-linter: the API and the workflows do not import this
package).

- `records`: record_run / record_stage, the local activities of the workflow: one RunStore change and its
  outbox event per transaction (the events of #11).
- `run`: resolve_run (settings snapshot, code and tool versions, project state, blocked layout) and render_run
  (status.json, REPORT.md, score.json, project state).
- `stage`: run_stage_activity, the generic stage activity: `python -m hwplatform.activities.child` in its own
  process group, heartbeats with the child's last line, kills the group on cancel.
"""

from __future__ import annotations

from .deps import Deps


def activities_for(queue: str, deps: Deps) -> list:
    """The activity callables a worker on `queue` registers."""
    from ..contracts import Q_ENGINE, QUEUE_OF
    from .records import Records
    from .run import RunActivities
    from .stage import StageActivity

    out: list = []
    if queue in set(QUEUE_OF.values()):
        out.append(StageActivity(deps).run_stage_activity)
    if queue == Q_ENGINE:
        rec, run = Records(deps), RunActivities(deps)
        out += [rec.record_run, rec.record_stage, run.resolve_run, run.render_run]
    return out


__all__ = ["Deps", "activities_for"]
