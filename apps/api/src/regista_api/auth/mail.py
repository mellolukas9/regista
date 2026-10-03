import structlog

from regista_api.auth.deps import AppState
from regista_api.auth.emails import (
    invitation_email,
    invitation_link,
    password_changed_email,
    reinvitation_email,
)

log = structlog.get_logger()


async def send_invitation(state: AppState, *, to: str, token: str, reinvite: bool = False) -> None:
    """Send after the transaction commits. A delivery failure must not undo the invitation (the
    administrator can use "Reenviar convite"), and the link, which carries a token, is never
    logged."""
    link = invitation_link(state.settings.public_url, token)
    build = reinvitation_email if reinvite else invitation_email
    try:
        await state.email.send(build(to=to, link=link, days=state.settings.invitation_days))
    except Exception as exc:
        log.error("invitation_email_failed", error=type(exc).__name__)


async def send_password_changed(state: AppState, *, to: str) -> None:
    try:
        await state.email.send(password_changed_email(to=to))
    except Exception as exc:
        log.error("password_changed_email_failed", error=type(exc).__name__)
