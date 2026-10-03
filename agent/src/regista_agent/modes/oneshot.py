"""Oneshot mode: takes one job, runs it and exits (containers, elastic execution).

Outside the MVP. The module exists so the other modes are written with it in mind: nothing in
the core assumes the agent runs until someone stops it.
"""

from regista_agent.errors import AgentError


def preflight() -> None:
    raise AgentError("O modo oneshot ainda não está disponível.")
