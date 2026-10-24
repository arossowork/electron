"""Claude usage-limit messages and how long to wait for the window to reset (stdlib only).

Used by the local `claude -p` harness (hwharness.agent, the evals, ui/server.py) and by the platform's
subscription-token credential adapter (hwplatform.credentials), which turns the wait into a durable workflow
timer instead of a sleep.
"""

from __future__ import annotations

import re
import time

LIMIT_RE = re.compile(r"usage limit|hit your limit|rate limit|limit reached|resets? (at )?\d|overloaded|529", re.I)
DEFAULT_WAIT_S = 900


def limit_wait_seconds(text: str, now: float | None = None) -> int:
    """Seconds until the subscription window resets, from the CLI message; 15 min when unknown.

    Understands "Claude AI usage limit reached|<epoch>" and "... resets 3pm" / "resets at 15:30" (local time).
    """
    now = time.time() if now is None else now
    m = re.search(r"\|(\d{10})", text)
    if m:
        return max(60, int(m.group(1)) - int(now) + 60)
    m = re.search(r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text, re.I)
    if m:
        h, mi = int(m.group(1)), int(m.group(2) or 0)
        if m.group(3):
            h = h % 12 + (12 if m.group(3).lower() == "pm" else 0)
        lt = time.localtime(now)
        target = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, mi, 0, 0, 0, -1))
        if target <= now:
            target += 86400
        return int(target - now) + 60
    return DEFAULT_WAIT_S
