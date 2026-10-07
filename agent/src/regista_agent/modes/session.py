"""Session mode: runs on a logged-in user's desktop, for robots that need a visible window."""

from regista_agent.errors import AgentError
from regista_agent.modes import is_interactive_session


def preflight() -> None:
    if not is_interactive_session():
        raise AgentError(
            "O modo Sessão precisa rodar no desktop de um usuário logado, e este processo está "
            "na sessão 0 do Windows (a dos serviços), que não tem desktop. O hospedeiro do robô "
            "deve rodar no logon do usuário dedicado (`regista-agent service install --mode "
            'session` cria a tarefa "ao fazer logon").'
        )
