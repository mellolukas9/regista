"""SQL for the tenant isolation pattern (docs/specs/security.md).

Policies are split by command: platform admins may *read* across tenants, but every
write (INSERT/UPDATE/DELETE) is limited to the tenant set on the transaction.
"""

import re

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def tenant_rls_statements(table: str) -> list[str]:
    """Statements that enable and force RLS on a table that has a `tenant_id` column."""
    if not _IDENT.match(table):
        raise ValueError(f"invalid table name: {table!r}")
    cur = "tenant_id = app.current_tenant_id()"
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY tenant_select ON {table} FOR SELECT "
        f"USING ({cur} OR app.is_platform_admin())",
        f"CREATE POLICY tenant_insert ON {table} FOR INSERT WITH CHECK ({cur})",
        f"CREATE POLICY tenant_update ON {table} FOR UPDATE USING ({cur}) WITH CHECK ({cur})",
        f"CREATE POLICY tenant_delete ON {table} FOR DELETE USING ({cur})",
    ]
