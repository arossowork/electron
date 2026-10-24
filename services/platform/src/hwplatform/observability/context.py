"""Log context carried in a contextvar: tenant_id, run_id, workflow_id, activity, stage, attempt, request_id.

`bind()` adds fields for the rest of the current task (and the tasks it spawns), `bound()` does it for a block.
The stage child process gets the context as a dict (`export()`) in its job file and binds it again.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar, Token
from typing import Any

_ctx: ContextVar[dict[str, Any]] = ContextVar("hwplatform_log_context", default={})  # noqa: B039


def current() -> dict[str, Any]:
    return dict(_ctx.get())


def bind(**fields: Any) -> Token:
    """Add (or replace) fields; None values are dropped. Returns a token for `reset`."""
    new = dict(_ctx.get())
    for k, v in fields.items():
        if v is None:
            new.pop(k, None)
        else:
            new[k] = v if isinstance(v, int | float | bool) else str(v)
    return _ctx.set(new)


def reset(token: Token) -> None:
    _ctx.reset(token)


@contextlib.contextmanager
def bound(**fields: Any) -> Iterator[None]:
    token = bind(**fields)
    try:
        yield
    finally:
        _ctx.reset(token)


def export() -> dict[str, Any]:
    """The context for a child process (JSON-able)."""
    return current()
