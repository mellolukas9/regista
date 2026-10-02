import logging
import re
from typing import Any

import structlog

REDACTED = "[redacted]"

# Keys whose values must never reach a log (passwords, TOTP secrets, tokens, codes, cookies).
_SENSITIVE_KEY = re.compile(
    r"pass(word|wd)|secret|token|recovery|otpauth|authorization|cookie|csrf|credential|"
    r"^(code|otp|totp|api_key|private_key|master_key)$",
    re.IGNORECASE,
)


def redact_value(key: str, value: Any) -> Any:
    if _SENSITIVE_KEY.search(key):
        return REDACTED
    if isinstance(value, dict):
        return {k: redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact_value(key, v) for v in value]
    return value


def redact_sensitive(
    _logger: Any, _method: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """structlog processor: mask the value of every sensitive key, recursively."""
    return {k: redact_value(k, v) for k, v in event_dict.items()}


def configure_logging(level: str = "INFO", *, json: bool = False) -> None:
    """Configure structlog (and the stdlib root logger) once at startup."""
    logging.basicConfig(format="%(message)s", level=level.upper(), force=True)

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            redact_sensitive,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        cache_logger_on_first_use=False,
    )
