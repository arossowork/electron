"""The simulated product person of the evaluations.

It answers the agent's questions from a fixture's hidden specification only. The local harness runs it as a
`claude -p` session (`evals/harness.py`); the platform's `simulate_answer` activity runs it as a one-turn Agent SDK
call with the tenant's credential. Both use `PM_PROMPT`, so the persona is the same. `deterministic_answers` is
the offline stand-in for tests: no model, same input, same answers. Stdlib only.
"""

from __future__ import annotations

import json
import re

PM_PROMPT = """You are the product manager in a hardware project. You are not an engineer.
Answer the engineer's questions using ONLY the facts below. Keep each answer to one or two sentences.
Rules, in this order:
1. A number in the facts (size, cost, battery life, current, accuracy) is a hard limit. Keep it, unless a
   preference rule in the facts says you may relax it, and the engineer showed numbers that prove the conflict.
2. If the engineer proves a conflict and the facts give a preference rule, follow that rule exactly.
3. If the engineer proves a conflict and no rule covers it, answer: "That limit is firm. Find a solution
   inside it, or tell me the smallest change you need and its cost." Do not accept a relaxed limit yourself.
4. If a question is not covered by the facts, answer "No preference, use your judgment."
Never invent new requirements. Answer every question by its id, as "q1: ...".

FACTS:
{spec}
"""

NO_PREFERENCE = "No preference, use your judgment."


def system_prompt(hidden_spec: str) -> str:
    return PM_PROMPT.format(spec=hidden_spec or "Requirements are final.")


def user_prompt(questions: list[dict], round_no: int | None = None) -> str:
    """The message with the engineer's questions (the local harness sends the whole questions.json)."""
    doc: dict = {"questions": questions}
    if round_no is not None:
        doc = {"round": round_no, **doc}
    return "Questions from the engineer:\n" + json.dumps(doc, indent=1)


_LINE = re.compile(r"^\s*(?:[-*]\s*)?\**(?P<id>[A-Za-z][\w.-]*)\**\s*[:)]\s*(?P<body>.*)$")


def parse_answers(text: str, questions: list[dict]) -> dict[str, str]:
    """`{question id: answer}` from the simulator's reply ("q1: ...", one per question, continuation lines
    allowed). A question the reply skipped gets NO_PREFERENCE, the persona's own rule 4."""
    ids = {str(q.get("id")): str(q.get("id")) for q in questions}
    lower = {k.lower(): k for k in ids}
    got: dict[str, list[str]] = {}
    cur: str | None = None
    for line in (text or "").splitlines():
        m = _LINE.match(line)
        if m and m["id"].lower() in lower:
            cur = lower[m["id"].lower()]
            got[cur] = [m["body"].strip()]
        elif cur is not None and line.strip():
            got[cur].append(line.strip())
    return {k: " ".join(got[k]).strip() if k in got and any(got[k]) else NO_PREFERENCE for k in ids}


_WORD = re.compile(r"[a-z0-9]{4,}")


def deterministic_answers(hidden_spec: str, questions: list[dict]) -> dict[str, str]:
    """The offline persona: for each question, the fact line of the specification that shares the most words
    with it (at least two), else NO_PREFERENCE. Ties go to the earlier line."""
    facts = [ln.strip().lstrip("-* ").strip() for ln in (hidden_spec or "").splitlines() if ln.strip()]
    out = {}
    for q in questions:
        words = set(_WORD.findall(f"{q.get('text', '')} {' '.join(map(str, q.get('options') or []))}".lower()))
        best, score = None, 1
        for f in facts:
            s = len(words & set(_WORD.findall(f.lower())))
            if s > score:
                best, score = f, s
        out[str(q.get("id"))] = best or NO_PREFERENCE
    return out
