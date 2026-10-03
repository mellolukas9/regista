"""Local log of the agent: rotating file plus the console, with secrets scrubbed.

The agent is careful not to log secrets in the first place; this is the net under it, the same
rule the server applies (CLAUDE.md, rule 5): an enrollment key, an access token, a nonce or a
signature never reaches a log, even if some message ends up carrying one.
"""

import logging
import logging.handlers
import re
import sys

from regista_agent.config import AgentSettings

_SECRETS = [
    (re.compile(r"rgk_[A-Za-z0-9_\-]+"), "rgk_[redacted]"),
    (re.compile(r"rga1\.[A-Za-z0-9_\-.]+"), "rga1.[redacted]"),
    (re.compile(r"(?i)(bearer\s+)\S+"), r"\1[redacted]"),
    (
        re.compile(r"(?i)\b(key|token|nonce|signature|proof|secret|password)=\S+"),
        r"\1=[redacted]",
    ),
]
_MAX_BYTES = 1_000_000
_BACKUPS = 5


def scrub(text: str) -> str:
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text)
    return text


class _ScrubFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = scrub(record.getMessage())
        record.args = ()
        return True


def configure(settings: AgentSettings) -> None:
    root = logging.getLogger("regista_agent")
    root.setLevel(settings.log_level.upper())
    root.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    try:
        settings.log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                settings.log_path, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8"
            )
        )
    except OSError as exc:
        # No log folder (not enrolled yet, or no permission): the console still works.
        sys.stderr.write(f"Aviso: sem arquivo de log em {settings.log_path.parent}: {exc}\n")
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(_ScrubFilter())
        root.addHandler(handler)
    root.propagate = False
