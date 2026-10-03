"""Errors the agent tells apart, each with the text and exit code the operator gets."""

EXIT_ERROR = 1
EXIT_REVOKED = 3

REVOKED_MESSAGE = "Esta máquina foi revogada no Regista. Para usar de novo, cadastre outra vez."
REJECTED_MESSAGE = (
    "O Regista não aceita mais a identidade desta máquina: ela foi revogada ou cadastrada de "
    "novo em outro lugar. Para usar de novo, cadastre outra vez."
)


class AgentError(Exception):
    """Something the operator can act on; the message is shown as is (pt-BR)."""

    exit_code = EXIT_ERROR


class NotEnrolled(AgentError):
    pass


class EnrollmentRefused(AgentError):
    pass


class MachineRevoked(AgentError):
    """The server said `machine_revoked`: stop for good."""

    exit_code = EXIT_REVOKED

    def __init__(self, message: str = REVOKED_MESSAGE) -> None:
        super().__init__(message)


class IdentityRejected(MachineRevoked):
    """The server no longer accepts this machine's key (revoked, or enrolled again elsewhere).

    The server answers the same way for every machine that cannot log in, so the agent cannot
    know which; either way it is over until the machine is registered again.
    """

    def __init__(self) -> None:
        super().__init__(REJECTED_MESSAGE)


class ServerUnavailable(AgentError):
    """Network trouble or a 5xx that did not go away after the retries. Worth trying again."""
