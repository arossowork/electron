"""UUIDv7 (time-ordered) run and event ids (ADR 0001, 3.1). Stdlib only: hwplatform imports it (it replaces
`hwplatform.ids`)."""

from __future__ import annotations

import os
import threading
import time
import uuid

_lock = threading.Lock()
_last = 0


def uuid7() -> uuid.UUID:
    """48-bit unix ms, version 7, 74 random bits; strictly increasing inside one process."""
    global _last
    ms = time.time_ns() // 1_000_000
    b = bytearray(ms.to_bytes(6, "big") + os.urandom(10))
    b[6] = (b[6] & 0x0F) | 0x70
    b[8] = (b[8] & 0x3F) | 0x80
    value = int.from_bytes(b, "big")
    with _lock:
        if value <= _last:
            value = _last + 1
        _last = value
    return uuid.UUID(int=value)
