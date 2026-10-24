"""Object-storage key convention: every object of a tenant lives under tenants/<tenant_id>/.

S3 access itself comes with the artifact store (#10/#12); code that builds or accepts a key goes
through these helpers so a key can never leave its tenant's prefix.
"""

from __future__ import annotations

import re
import uuid

_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


def tenant_prefix(tenant_id: uuid.UUID) -> str:
    return f"tenants/{uuid.UUID(str(tenant_id))}/"


def tenant_key(tenant_id: uuid.UUID, *parts: str) -> str:
    """tenants/<tenant_id>/<part>/<part>...; each part is one safe path segment (no '/', no '..')."""
    if not parts:
        raise ValueError("at least one key segment is required")
    for p in parts:
        if not _SEGMENT.match(p) or p in (".", ".."):
            raise ValueError(f"unsafe key segment: {p!r}")
    return tenant_prefix(tenant_id) + "/".join(parts)


def key_in_tenant(tenant_id: uuid.UUID, key: str) -> bool:
    """True if the key is a normalized key under the tenant's prefix."""
    prefix = tenant_prefix(tenant_id)
    if not key.startswith(prefix):
        return False
    rest = key[len(prefix) :].split("/")
    return all(_SEGMENT.match(p) and p not in (".", "..") for p in rest)
