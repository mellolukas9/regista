import logging

import structlog


def configure_logging(level: str = "INFO", *, json: bool = False) -> None:
    """Configure structlog (and the stdlib root logger) once at startup."""
    logging.basicConfig(format="%(message)s", level=level.upper(), force=True)

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        cache_logger_on_first_use=True,
    )
