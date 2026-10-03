"""Server-side pagination and sorting for lists (docs/specs/design-system.md, rule 9)."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query

from regista_api.core.errors import api_error

PER_PAGE_CHOICES = (10, 25, 50)


@dataclass(frozen=True)
class PageParams:
    page: int
    per_page: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.per_page


def _page_params(
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    per_page: Annotated[int, Query()] = 25,
) -> PageParams:
    if per_page not in PER_PAGE_CHOICES:
        raise api_error(422, "invalid_per_page", allowed=list(PER_PAGE_CHOICES))
    return PageParams(page=page, per_page=per_page)


Pagination = Annotated[PageParams, Depends(_page_params)]


def like_pattern(q: str | None) -> str | None:
    """`%q%` for ILIKE ... ESCAPE '\\', with the user's own wildcards neutralised."""
    if not q or not q.strip():
        return None
    escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def order_by(sort: str | None, allowed: dict[str, str], default: str) -> str:
    """ORDER BY fragment from a `?sort=` value such as `-created_at`.

    Only keys of `allowed` (mapped to fixed SQL expressions) are ever used, so user input never
    reaches the SQL text. `id` is appended to keep the order stable between pages.
    """
    value = sort or default
    descending = value.startswith("-")
    key = value.lstrip("-")
    if key not in allowed:
        raise api_error(422, "invalid_sort", allowed=sorted(allowed))
    return f"{allowed[key]} {'DESC' if descending else 'ASC'} NULLS LAST, id ASC"
