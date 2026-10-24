"""simulate_answer (#19) with a fake runner and a fake credential port: no database, no network, no model."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from temporalio.exceptions import ApplicationError

from hwplatform.activities import agent as agent_mod
from hwplatform.activities import simulate as sim_mod
from hwplatform.activities.deps import Deps
from hwplatform.agent.runner import KEY_INVALID, RATE_LIMITED, SessionResult
from hwplatform.contracts import RunCtx, SimulateInput, errors
from hwplatform.credentials import Credential
from hwplatform.settings import Settings

QS = [{"id": "q1", "text": "What supply voltage?"}, {"id": "q2", "text": "Which colour?"}]
SPEC = "- The supply voltage is 12 V.\n- Budget 30 USD."


class FakeCreds:
    def __init__(self):
        self.invalid = 0
        self.usage = []

    async def mark_invalid(self, tenant_id, cred):
        self.invalid += 1

    async def record_usage(self, tenant_id, model, u, run_id=None):
        self.usage.append((model, u))

    def limit_wait(self, cred, text, retry_after_s, now=None):
        return retry_after_s or 0


class FakeRunner:
    def __init__(self, result=None):
        self.specs = []
        self.result = result

    async def ask(self, spec, hooks):
        from hwharness.evals.simulator import deterministic_answers

        self.specs.append(spec)
        if self.result is not None:
            return self.result
        await hooks.on_usage("claude-sonnet-5-5", {"input_tokens": 5, "output_tokens": 3})
        ans = deterministic_answers(SPEC, QS)
        return SessionResult(None, text="\n".join(f"{k}: {v}" for k, v in ans.items()))


@pytest.fixture
def wired(monkeypatch, tmp_path):
    creds = FakeCreds()
    answered = []
    monkeypatch.setattr(agent_mod.AgentActivities, "credentials", lambda self: creds)

    async def cred(self, c, tenant_id, details):
        return Credential("api_key", "db", "sk-ant-test-0000")

    monkeypatch.setattr(agent_mod.AgentActivities, "_credential", cred)

    async def answer(self, qid, answers, by):
        answered.append((qid, answers, by))

    monkeypatch.setattr(sim_mod.QuestionStore, "answer", answer)

    def make(runner):
        deps = Deps(Settings(worker_dir=str(tmp_path)), db=object(), runner=runner)
        return sim_mod.SimulateActivities(deps)

    return make, creds, answered


def inp():
    ctx = RunCtx(str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), "design-x")
    return SimulateInput(ctx, str(uuid.uuid4()), "requirements", QS, SPEC, "", 1)


def test_answers_from_the_persona_with_the_tenants_credential(wired):
    make, creds, answered = wired
    runner = FakeRunner()
    i = inp()
    got = asyncio.run(make(runner).simulate_answer(i))
    assert "12 V" in got["q1"] and got["q2"] == "No preference, use your judgment."
    (spec,) = runner.specs
    assert spec.api_key == "sk-ant-test-0000" and not spec.oauth_token
    assert (
        SPEC in spec.system and "What supply voltage?" in spec.prompt
    )  # the persona sees the spec, the question
    assert answered == [(i.question_id, got, None)]  # answered by the simulator, not by a person
    assert creds.usage and not spec.ws.exists()  # usage is recorded and the workspace is removed


def test_rejected_key_is_not_retried(wired):
    make, creds, _ = wired
    with pytest.raises(ApplicationError) as e:
        asyncio.run(
            make(FakeRunner(SessionResult(None, error=KEY_INVALID, text="401"))).simulate_answer(inp())
        )
    assert e.value.type == errors.LlmKeyInvalid and e.value.non_retryable and creds.invalid == 1


def test_rate_limit_carries_the_wait(wired):
    make, _, answered = wired
    with pytest.raises(ApplicationError) as e:
        asyncio.run(
            make(
                FakeRunner(SessionResult(None, error=RATE_LIMITED, text="limit", retry_after_s=90))
            ).simulate_answer(inp())
        )
    assert e.value.type == errors.LlmRateLimited and e.value.details[0] == {"retry_after_s": 90}
    assert not answered
