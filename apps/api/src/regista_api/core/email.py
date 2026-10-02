"""E-mail behind an interface. Real SMTP arrives in M7/M8; until then dev prints to the console."""

import sys
from dataclasses import dataclass
from typing import Protocol

from regista_api.core.config import Settings


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    text: str


class EmailSender(Protocol):
    async def send(self, message: EmailMessage) -> None: ...


class ConsoleEmailSender:
    """Dev only. Writes straight to stdout, deliberately outside the logging pipeline,
    because invitation links carry tokens and logs must never contain them."""

    async def send(self, message: EmailMessage) -> None:
        bar = "=" * 72
        sys.stdout.write(
            f"\n{bar}\n[e-mail de desenvolvimento]\nPara: {message.to}\n"
            f"Assunto: {message.subject}\n\n{message.text}\n{bar}\n"
        )
        sys.stdout.flush()


class MemoryEmailSender:
    """Tests: keeps messages in `outbox`."""

    def __init__(self) -> None:
        self.outbox: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.outbox.append(message)


def create_email_sender(settings: Settings) -> EmailSender:
    if settings.email_backend == "memory":
        return MemoryEmailSender()
    return ConsoleEmailSender()
