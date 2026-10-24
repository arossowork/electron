"""simulate_answer (ADR 0001, 7.7), on the hwflow-agent queue: the evaluations' simulated product person.

The same persona as the local harness (`hwharness.evals.simulator`: the product manager who answers only
from the
fixture's hidden specification), run as one Agent SDK turn with no tools. It uses the credential port like
`agent_stage`: the tenant's credential (an API key, or for an operator tenant the mounted credential or its
subscription token), and the same errors (`LlmKeyInvalid`, `LlmRateLimited` with `retry_after_s`), which the
DesignWorkflow turns into waits. The answers are written to the question's row (answered by nobody: the
simulator) and returned to the workflow.

The hidden specification travels in the activity input (Temporal history), never into the agent's sandbox.
Tests inject a runner with a deterministic `ask` (hwharness.evals.simulator.deterministic_answers).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid

from temporalio import activity
from temporalio.exceptions import ApplicationError

from ..agent import workspace as wsmod
from ..agent.runner import KEY_EXHAUSTED, KEY_INVALID, RATE_LIMITED, Hooks, SessionSpec
from ..contracts import A_SIMULATE_ANSWER, HEARTBEAT_EVERY_S, SimulateInput, errors
from ..questions import QuestionNotFound, QuestionNotOpen, QuestionStore
from .agent import AgentActivities
from .deps import Deps

log = logging.getLogger("hwplatform.simulate")


class SimulateActivities:
    def __init__(self, deps: Deps):
        self.deps = deps

    @activity.defn(name=A_SIMULATE_ANSWER)
    async def simulate_answer(self, inp: SimulateInput) -> dict:
        from hwharness.evals.simulator import parse_answers, system_prompt, user_prompt
        from hwharness.prompts import model_id

        ctx, settings = inp.ctx, self.deps.settings
        agent = AgentActivities(self.deps)
        creds = agent.credentials()
        cred = await agent._credential(creds, ctx.tenant_id, {})
        dirs = wsmod.Dirs(self.deps.root, ctx.run_id, f"pm-{inp.question_id[:8]}")
        dirs.reset()

        async def on_usage(model: str, u: dict) -> None:
            await creds.record_usage(ctx.tenant_id, model, u, run_id=uuid.UUID(ctx.run_id))

        spec = SessionSpec(
            ws=dirs.ws,
            home=dirs.home,
            config_dir=dirs.config,
            bin_dir=dirs.bin,
            tmp_dir=dirs.tmp,
            system=system_prompt(inp.persona),
            model=model_id(inp.model or settings.eval_pm_model),
            prompt=user_prompt(inp.questions, inp.round or None),
            api_key=cred.secret if cred.type == "api_key" else "",
            oauth_token=cred.secret if cred.type == "subscription_token" else "",
            max_budget_usd=settings.agent_max_budget_usd,
            fallback_model=settings.agent_fallback_model or None,
        )

        async def beat() -> None:
            while True:
                await asyncio.sleep(HEARTBEAT_EVERY_S)
                activity.heartbeat("simulating the product person")

        hb = asyncio.create_task(beat())
        try:
            res = await self.deps.runner.ask(spec, Hooks(on_usage))
        finally:
            hb.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await hb
            dirs.remove()
        if res.error in (KEY_INVALID, KEY_EXHAUSTED):
            await creds.mark_invalid(ctx.tenant_id, cred)
            raise ApplicationError(
                "the Anthropic API rejected the tenant's key"
                if res.error == KEY_INVALID
                else "the tenant's Anthropic account has no credit left",
                {"reason": res.error},
                type=errors.LlmKeyInvalid,
                non_retryable=True,
            )
        if res.error == RATE_LIMITED:
            raise ApplicationError(
                "the Anthropic API rate-limited the simulated product person",
                {"retry_after_s": creds.limit_wait(cred, res.text, res.retry_after_s) or None},
                type=errors.LlmRateLimited,
                non_retryable=True,
            )
        if res.error:
            raise ApplicationError(f"simulator session failed: {res.text[:500]}", type="SimulatorFailed")
        answers = parse_answers(res.text, inp.questions)
        try:
            await QuestionStore(self.deps.db, ctx.tenant_id).answer(inp.question_id, answers, None)
        except (QuestionNotOpen, QuestionNotFound):
            pass  # a retried call, or the question was closed meanwhile: the workflow goes on with `answers`
        return answers
