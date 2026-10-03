"""What the agent tells the server about itself. The server limits and filters all of it: it is
data from the customer's machine, not to be trusted."""

import platform
import socket
from importlib import metadata

_MAX = 100


def agent_version() -> str:
    try:
        return metadata.version("regista-agent")
    except metadata.PackageNotFoundError:
        return "0.0.0"


def collect() -> dict[str, str]:
    values = {
        "system": platform.system(),
        "release": platform.release(),
        "hostname": socket.gethostname(),
        "python": platform.python_version(),
    }
    return {key: value[:_MAX] for key, value in values.items() if value}
