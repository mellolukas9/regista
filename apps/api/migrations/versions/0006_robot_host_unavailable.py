"""jobs.error_code accepts `robot_host_unavailable` (ADR 0022)

Revision ID: 0006
Revises: 0005

The robot no longer runs as the agent: a separate robot host starts it. When that host is not there,
does not answer or is not who it should be, the run fails with this code and the panel shows a fixed
text (docs/specs/design-system.md, section 16). Like the other codes of M4, no free text from the
agent is kept for it.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_ERROR_CODES = (
    "machine_lost",
    "machine_revoked",
    "timeout",
    "robot_failed",
    "robot_not_found",
    "cancelled",
    "internal",
    "package_invalid",
    "robot_not_allowed",
    "runtime_missing",
    "environment_failed",
)
NEW_ERROR_CODES = (*OLD_ERROR_CODES, "robot_host_unavailable")


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    # The naming convention prefixes check names (`ck_<table>_<name>`), so the short name is used.
    op.drop_constraint("error_code", "jobs", type_="check")
    op.create_check_constraint(
        "error_code",
        "jobs",
        f"error_code IS NULL OR error_code IN ({_in(NEW_ERROR_CODES)})",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE jobs SET error_code = 'internal' WHERE error_code = 'robot_host_unavailable'"
    )
    op.drop_constraint("error_code", "jobs", type_="check")
    op.create_check_constraint(
        "error_code",
        "jobs",
        f"error_code IS NULL OR error_code IN ({_in(OLD_ERROR_CODES)})",
    )
