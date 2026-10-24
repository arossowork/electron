"""The authentication interface the API depends on.

The API only needs one thing from an auth backend: turn a bearer token into a Principal (user, tenant).
The built-in LocalAuthBackend (auth/local.py) also issues tokens (signup, login, refresh, logout).
In M2 an OIDC backend (Keycloak) implements `authenticate` by validating the IdP's JWT against its
JWKS and mapping the subject and a tenant claim to a users row and a membership; the /auth routes of
the local backend are then not mounted.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass


class AuthError(Exception):
    """Credentials missing, invalid or expired. The API answers 401."""


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    tenant_id: uuid.UUID


class AuthBackend(ABC):
    name: str = "abstract"
    #: True if this backend issues tokens itself (the /auth routes are mounted).
    issues_tokens: bool = False

    @abstractmethod
    async def authenticate(self, bearer_token: str) -> Principal:
        """Validate an access token. Raise AuthError if it is not acceptable."""
