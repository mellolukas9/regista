"""Secrets never reach a log or an error message the panel shows (CLAUDE.md, rules 5 and 6).

What an agent or a robot sends is untrusted data. The agent already scrubs it before sending; this
is the same net on the server, so a secret that got past (or an agent that skipped the step) is
still cut out before anything is stored. Keep it in step with `regista_agent/logs.py`.
"""

import re

_SECRETS = [
    (re.compile(r"rgk_[A-Za-z0-9_\-]+"), "rgk_[redacted]"),
    (re.compile(r"rga1\.[A-Za-z0-9_\-.]+"), "rga1.[redacted]"),
    (re.compile(r"(?i)(bearer\s+)\S+"), r"\1[redacted]"),
    (
        re.compile(r"(?i)\b(key|token|nonce|signature|proof|secret|password)=\S+"),
        r"\1=[redacted]",
    ),
]
# ANSI escape sequences and control characters other than tab and newline.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def scrub(text: str) -> str:
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text)
    return text


def clean_line(text: str, *, max_bytes: int) -> str:
    """Scrub, drop terminal control sequences and cut to `max_bytes` (never in the middle of a
    character)."""
    text = _CONTROL.sub("", _ANSI.sub("", scrub(text)))
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    marker = "…[cortado]"
    keep = max_bytes - len(marker.encode("utf-8"))
    return raw[: max(keep, 0)].decode("utf-8", errors="ignore") + marker
