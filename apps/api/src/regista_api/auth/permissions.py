"""Role permissions (docs/specs/design-system.md, section 4), enforced on the server.

Only the rows of the matrix that exist in M1 are here; later milestones add theirs. A
platform admin (Artemisys team) holds every permission, tenant roles hold their own set.
"""

import enum


class Permission(enum.StrEnum):
    CLIENTS_VIEW_ALL = "clients.view_all"  # client list, client selector, "Todos os clientes"
    CLIENTS_CREATE = "clients.create"
    USERS_MANAGE = "users.manage"  # list, invite, change role, end sessions, remove, re-invite


PLATFORM_ONLY: frozenset[Permission] = frozenset(
    {Permission.CLIENTS_VIEW_ALL, Permission.CLIENTS_CREATE}
)

ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    "tenant_admin": frozenset({Permission.USERS_MANAGE}),
    "operator": frozenset(),
    "viewer": frozenset(),
}


def permissions_for(*, role: str, is_platform_admin: bool) -> frozenset[Permission]:
    if is_platform_admin:
        return frozenset(Permission)
    return ROLE_PERMISSIONS.get(role, frozenset())


def has_permission(*, role: str, is_platform_admin: bool, permission: Permission) -> bool:
    return permission in permissions_for(role=role, is_platform_admin=is_platform_admin)
