"""Transactional e-mails of the auth flows (texts in docs/specs/design-system.md, section 12)."""

from regista_api.core.email import EmailMessage


def invitation_link(public_url: str, token: str) -> str:
    # The token travels in the URL fragment, which browsers never send to a server.
    return f"{public_url.rstrip('/')}/invite#{token}"


def invitation_email(*, to: str, link: str, days: int) -> EmailMessage:
    return EmailMessage(
        to=to,
        subject="Seu acesso ao Regista",
        text=(
            "Você foi convidado para o Regista, a plataforma de automações da Artemisys.\n\n"
            f"Crie sua senha e ative a verificação em duas etapas por este link:\n{link}\n\n"
            f"O link vale por {days} dias e só pode ser usado uma vez."
        ),
    )


def reinvitation_email(*, to: str, link: str, days: int) -> EmailMessage:
    return EmailMessage(
        to=to,
        subject="Novo convite para o Regista",
        text=(
            "Um administrador reenviou seu convite. A senha e a verificação em duas etapas "
            "anteriores deixaram de valer, e o link antigo também.\n\n"
            f"Crie uma nova senha e ative a verificação em duas etapas por este link:\n{link}\n\n"
            f"O link vale por {days} dias e só pode ser usado uma vez."
        ),
    )


def password_changed_email(*, to: str) -> EmailMessage:
    return EmailMessage(
        to=to,
        subject="Sua senha do Regista foi alterada",
        text=(
            "A senha da sua conta no Regista foi alterada e os outros aparelhos foram "
            "desconectados.\n\nSe não foi você, fale com o administrador do seu escritório."
        ),
    )
