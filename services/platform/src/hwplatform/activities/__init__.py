"""Temporal activities of the build (ADR 0001, 4.2 to 4.6). They run in `hwplatform-worker`, which is the one
platform process that imports the EDA engine (import-linter: the API and the workflows do not import this
package).

- `records`: record_run / record_stage, the local activities of the workflow: one RunStore change and its
  outbox event per transaction (the events of #11).
- `run`: resolve_run (settings snapshot, code and tool versions, project state, blocked layout) and render_run
  (status.json, REPORT.md, score.json, project state).
- `stage`: run_stage_activity, the generic stage activity: `python -m hwplatform.activities.child` in its own
  process group, heartbeats with the child's last line, kills the group on cancel.
- `agent` (#15): agent_stage, one Claude Agent SDK session of a design stage, on hwflow-agent.
- `design` (#15): record_question (local), get_design_version, create_design_version.
- `evals` (#19): the EvalWorkflow's engine activities (prepare a trial, prepare the scoring build, score a
trial,
  finish the evaluation); `simulate` (#19): simulate_answer, the simulated product person, on hwflow-agent.
- `engine_tools` (#15): engine_tool, one lookup or engine command of an agent tool (EngineToolWorkflow).
"""

from __future__ import annotations

from .deps import Deps


def _has_evals() -> bool:
    """The evaluation activities need hwharness (score card, report; the `evals` extra of the image)."""
    import importlib.util

    try:
        return importlib.util.find_spec("hwharness.evals.cards") is not None
    except ModuleNotFoundError:
        return False


def activities_for(queue: str, deps: Deps) -> list:
    """The activity callables a worker on `queue` registers."""
    from ..contracts import ENGINE_TOOL_QUEUE, Q_AGENT, Q_ENGINE, QUEUE_OF
    from .design import DesignActivities
    from .engine_tools import EngineTools
    from .records import Records
    from .run import RunActivities
    from .stage import StageActivity

    out: list = []
    if queue in set(QUEUE_OF.values()):
        out.append(StageActivity(deps).run_stage_activity)
    if queue in set(ENGINE_TOOL_QUEUE.values()):
        out.append(EngineTools(deps).engine_tool)
    if queue == Q_ENGINE:
        rec, run, des = Records(deps), RunActivities(deps), DesignActivities(deps)
        out += [rec.record_run, rec.record_stage, run.resolve_run, run.render_run]
        out += [des.record_question, des.get_design_version, des.create_design_version]
        if _has_evals():
            from .evals import EvalActivities

            ev = EvalActivities(deps)
            out += [ev.prepare_eval_trial, ev.prepare_score_build, ev.score_trial, ev.finish_eval]
    if queue == Q_AGENT:
        from .agent import AgentActivities  # imports hwharness.prompts (the agent extra)
        from .simulate import SimulateActivities

        out += [AgentActivities(deps).agent_stage, SimulateActivities(deps).simulate_answer]
    return out


__all__ = ["Deps", "activities_for"]
