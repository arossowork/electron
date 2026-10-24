"""Real Claude smoke of the simulated product person (#19), marker `anthropic_smoke`. Collected only on request and
with a credential in the environment (conftest.py `pytest_ignore_collect`), so CI and the default suite never call
the API. One short Haiku turn with no tools through `SdkRunner.ask`, the persona prompt of `hwharness.evals.simulator`
and a tiny fixture: it must answer from the hidden specification and keep to the "No preference" rule for what the
specification does not say.

    CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat... \
        uv run --package hwplatform --extra agent pytest -q -s -m anthropic_smoke services/platform/tests/test_eval_smoke.py

(ANTHROPIC_API_KEY works too and wins when both are set.) A whole evaluation over a stack with a worker and an agent
worker is `evals/harness.py platform run --fixtures led_driver --wait` (see evals/README.md).
"""

from __future__ import annotations

import asyncio
import os

import pytest

from hwplatform.agent.runner import Hooks, SdkRunner, SessionSpec

pytestmark = pytest.mark.anthropic_smoke
MODEL = os.environ.get("HWPLATFORM_AGENT_SMOKE_MODEL", "claude-haiku-4-5-20251001")
SPEC = "- The supply is a 9 V battery.\n- The LED current is 20 mA.\n- Budget: under 5 USD."
QUESTIONS = [
    {"id": "q1", "text": "What is the supply voltage?"},
    {"id": "q2", "text": "What LED current do you want?"},
    {"id": "q3", "text": "Which colour should the enclosure be?"},
]


def test_the_simulated_product_person_answers_from_the_specification(tmp_path):
    from hwharness.evals.simulator import parse_answers, system_prompt, user_prompt

    for d in ("ws", "home/.claude", "bin", "tmp"):
        (tmp_path / d).mkdir(parents=True)
    cred = (
        {"api_key": os.environ["ANTHROPIC_API_KEY"]}
        if os.environ.get("ANTHROPIC_API_KEY")
        else {"oauth_token": os.environ["CLAUDE_CODE_OAUTH_TOKEN"]}
    )
    spec = SessionSpec(
        ws=tmp_path / "ws",
        home=tmp_path / "home",
        config_dir=tmp_path / "home" / ".claude",
        bin_dir=tmp_path / "bin",
        tmp_dir=tmp_path / "tmp",
        system=system_prompt(SPEC),
        model=MODEL,
        prompt=user_prompt(QUESTIONS, 1),
        max_budget_usd=0.5,
        **cred,
    )
    usage = []

    async def on_usage(model, u):
        usage.append((model, u))

    res = asyncio.run(SdkRunner().ask(spec, Hooks(on_usage)))
    assert res.error is None, res.text[:300]
    answers = parse_answers(res.text, QUESTIONS)
    print(f"\nanswers: {answers}\nusage: {usage}")
    assert "9" in answers["q1"] and "20" in answers["q2"]
    assert "9 V" not in answers["q3"]  # nothing in the specification about the enclosure
    assert usage
