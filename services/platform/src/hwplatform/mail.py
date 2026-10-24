"""Outgoing e-mail: an interface only (#26). Delivery is deferred, there is no SMTP service in M1.

Verification tokens and invitations go through `EmailSender`. The dev implementation writes the message
to stdout through the logging system (so the redaction of API keys applies); the token is in the message
on purpose, it is how a developer completes the flow without a mail server. Production needs a real
implementation (SMTP or a provider API) passed to `create_app(email_sender=...)`.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

log = logging.getLogger("hwplatform.mail")


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    body: str
    kind: str = ""  # verify | verify_notice | invite
    token: str | None = None  # the secret inside the body, for tests and the dev sender


class EmailSender(ABC):
    @abstractmethod
    async def send(self, message: Email) -> None:
        """Deliver the message. Must not raise for an unknown or unreachable recipient."""


class LoggingEmailSender(EmailSender):
    """Dev sender: logs the message to stdout (redacted by the log record factory)."""

    async def send(self, message: Email) -> None:
        log.info("E-MAIL to=%s subject=%r\n%s", message.to, message.subject, message.body)


class InMemoryEmailSender(EmailSender):
    """Keeps the messages; used by tests."""

    def __init__(self) -> None:
        self.outbox: list[Email] = []

    async def send(self, message: Email) -> None:
        self.outbox.append(message)

    def last(self, to: str, kind: str | None = None) -> Email | None:
        for m in reversed(self.outbox):
            if m.to == to and (kind is None or m.kind == kind):
                return m
        return None
