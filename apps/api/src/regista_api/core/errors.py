from typing import Any

from fastapi import HTTPException


def api_error(status_code: int, code: str, **extra: Any) -> HTTPException:
    """Errors carry a stable machine code; the panel maps it to the design-system texts."""
    return HTTPException(status_code=status_code, detail={"code": code, **extra})
