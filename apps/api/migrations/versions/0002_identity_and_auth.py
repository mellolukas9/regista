"""identity and auth: users, invitations, recovery codes, sessions, audit log

Revision ID: 0002
Revises: 0001

Also adds the internal (Artemisys) tenant flag, the lookup functions used before a tenant
exists in the session (ADR 0017) and the rate limit table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from regista_api.core.rls import tenant_rls_statements

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
NOW = sa.text("now()")

# SECURITY DEFINER functions: owned by regista_owner (the migration role) with a fixed
# search_path. FORCE RLS also binds the owner, so each function raises the narrowest session
# flag it needs with set_config(..., true) and restores the caller's value before returning;
# the flag never outlives the call. (PostgreSQL 18 does not let a non-superuser attach a
# custom GUC with a function-level SET clause, hence the in-body approach.)
# regista_app keeps no BYPASSRLS and never connects as the owner.
DEFINER = "SECURITY DEFINER SET search_path = pg_catalog, public"
FLAG_ON = "PERFORM set_config('app.platform_admin', 'on', true);"
FLAG_RESTORE = "PERFORM set_config('app.platform_admin', coalesce(prev, ''), true);"

FUNCTIONS = [
    "app.tenants_guard_internal()",
    "app.users_guard_platform_admin()",
    "app.lookup_login(citext)",
    "app.lookup_invitation(bytea)",
    "app.lookup_session(bytea)",
    "app.rate_limit_hit(bytea, integer, integer)",
]
CALLABLE_BY_APP = FUNCTIONS[2:]


def _id() -> sa.Column[object]:
    return sa.Column("id", UUID, server_default=sa.text("uuidv7()"), nullable=False)


def _tenant_id(table: str) -> sa.Column[object]:
    return sa.Column(
        "tenant_id",
        UUID,
        sa.ForeignKey("tenants.id", name=f"fk_{table}_tenant_id_tenants"),
        nullable=False,
    )


def _ts(name: str, *, default: bool = False) -> sa.Column[object]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=NOW if default else None,
        nullable=not default,
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    # --- tenants: the single hidden internal tenant for the Artemisys team -----------------
    op.add_column(
        "tenants",
        sa.Column("is_internal", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_tenants_single_internal ON tenants ((true)) WHERE is_internal"
    )
    op.execute(
        "ALTER TABLE tenants ADD CONSTRAINT ck_tenants_internal_active "
        "CHECK (NOT is_internal OR is_active)"
    )
    op.execute(
        """
        CREATE FUNCTION app.tenants_guard_internal() RETURNS trigger
          LANGUAGE plpgsql AS
        $$ BEGIN
          IF NEW.is_internal IS DISTINCT FROM OLD.is_internal THEN
            RAISE EXCEPTION 'tenants.is_internal is immutable';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER tenants_guard_internal BEFORE UPDATE ON tenants "
        "FOR EACH ROW EXECUTE FUNCTION app.tenants_guard_internal()"
    )

    # --- users -----------------------------------------------------------------------------
    op.create_table(
        "users",
        _id(),
        _tenant_id("users"),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("password_hash", sa.Text()),
        sa.Column("display_name", sa.Text()),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column(
            "is_platform_admin", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("mfa_secret_enc", postgresql.BYTEA()),
        sa.Column("mfa_key_id", sa.Text()),
        sa.Column("mfa_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("mfa_enabled_at", sa.DateTime(timezone=True)),
        sa.Column("mfa_last_step", sa.BigInteger()),
        sa.Column("status", sa.Text(), server_default="invited", nullable=False),
        sa.Column("failed_logins", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        _ts("created_at", default=True),
        _ts("updated_at", default=True),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        # Global uniqueness: the login form has no client field (ADR 0017).
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.CheckConstraint("role IN ('tenant_admin', 'operator', 'viewer')", name="role"),
        sa.CheckConstraint("status IN ('invited', 'active', 'disabled')", name="status"),
        sa.CheckConstraint(
            "NOT mfa_enabled OR (mfa_secret_enc IS NOT NULL AND mfa_key_id IS NOT NULL)",
            name="mfa_secret",
        ),
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id", "status"])

    # is_platform_admin <=> the user belongs to the internal tenant. Enforced here, not only
    # in the application.
    op.execute(
        f"""
        CREATE FUNCTION app.users_guard_platform_admin() RETURNS trigger
          LANGUAGE plpgsql {DEFINER} AS
        $$
        DECLARE
          internal boolean;
          prev text := current_setting('app.platform_admin', true);
        BEGIN
          {FLAG_ON}
          SELECT t.is_internal INTO internal FROM public.tenants t WHERE t.id = NEW.tenant_id;
          {FLAG_RESTORE}
          IF internal IS NULL THEN
            RAISE EXCEPTION 'unknown tenant';
          END IF;
          IF NEW.is_platform_admin IS DISTINCT FROM internal THEN
            RAISE EXCEPTION 'is_platform_admin must match tenants.is_internal';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER users_guard_platform_admin "
        "BEFORE INSERT OR UPDATE OF tenant_id, is_platform_admin ON users "
        "FOR EACH ROW EXECUTE FUNCTION app.users_guard_platform_admin()"
    )

    # --- invitations -----------------------------------------------------------------------
    op.create_table(
        "invitations",
        _id(),
        _tenant_id("invitations"),
        sa.Column(
            "user_id",
            UUID,
            sa.ForeignKey("users.id", name="fk_invitations_user_id_users"),
            nullable=False,
        ),
        sa.Column("token_hash", postgresql.BYTEA(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _ts("used_at"),
        _ts("revoked_at"),
        sa.Column(
            "created_by", UUID, sa.ForeignKey("users.id", name="fk_invitations_created_by_users")
        ),
        _ts("created_at", default=True),
        sa.PrimaryKeyConstraint("id", name="pk_invitations"),
        sa.UniqueConstraint("token_hash", name="uq_invitations_token_hash"),
    )
    op.create_index("ix_invitations_tenant_id", "invitations", ["tenant_id", "user_id"])

    # --- recovery_codes --------------------------------------------------------------------
    op.create_table(
        "recovery_codes",
        _id(),
        _tenant_id("recovery_codes"),
        sa.Column(
            "user_id",
            UUID,
            sa.ForeignKey("users.id", name="fk_recovery_codes_user_id_users"),
            nullable=False,
        ),
        sa.Column("code_hash", sa.Text(), nullable=False),
        _ts("used_at"),
        _ts("created_at", default=True),
        sa.PrimaryKeyConstraint("id", name="pk_recovery_codes"),
    )
    op.create_index("ix_recovery_codes_tenant_id", "recovery_codes", ["tenant_id", "user_id"])

    # --- sessions --------------------------------------------------------------------------
    op.create_table(
        "sessions",
        _id(),
        _tenant_id("sessions"),
        sa.Column(
            "user_id",
            UUID,
            sa.ForeignKey("users.id", name="fk_sessions_user_id_users"),
            nullable=False,
        ),
        sa.Column("token_hash", postgresql.BYTEA(), nullable=False),
        sa.Column("csrf_token_hash", postgresql.BYTEA(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        _ts("created_at", default=True),
        _ts("last_seen_at", default=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _ts("revoked_at"),
        sa.Column("ip", postgresql.INET()),
        sa.Column("user_agent", sa.Text()),
        sa.PrimaryKeyConstraint("id", name="pk_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
        sa.CheckConstraint(
            "stage IN ('mfa_required', 'mfa_setup', 'recovery_codes', 'active')",
            name="stage",
        ),
    )
    op.create_index("ix_sessions_tenant_id", "sessions", ["tenant_id", "user_id"])

    # --- audit_log -------------------------------------------------------------------------
    op.create_table(
        "audit_log",
        _id(),
        _tenant_id("audit_log"),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_id", UUID),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text()),
        sa.Column("target_id", UUID),
        sa.Column(
            "metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("ip", postgresql.INET()),
        _ts("created_at", default=True),
        sa.PrimaryKeyConstraint("id", name="pk_audit_log"),
        sa.CheckConstraint("actor_type IN ('user', 'machine', 'system')", name="actor_type"),
    )
    op.execute("CREATE INDEX ix_audit_log_tenant_id ON audit_log (tenant_id, created_at DESC)")

    # --- auth_rate_limits: platform data, reachable only through app.rate_limit_hit ---------
    op.create_table(
        "auth_rate_limits",
        sa.Column("key_hash", postgresql.BYTEA(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("key_hash", "window_start", name="pk_auth_rate_limits"),
    )
    op.execute("ALTER TABLE auth_rate_limits ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE auth_rate_limits FORCE ROW LEVEL SECURITY")
    gate = "current_setting('app.rate_limit', true) = 'on'"
    op.execute(f"CREATE POLICY tenant_select ON auth_rate_limits FOR SELECT USING ({gate})")
    op.execute(f"CREATE POLICY tenant_insert ON auth_rate_limits FOR INSERT WITH CHECK ({gate})")
    op.execute(
        "CREATE POLICY tenant_update ON auth_rate_limits FOR UPDATE "
        f"USING ({gate}) WITH CHECK ({gate})"
    )
    op.execute(f"CREATE POLICY tenant_delete ON auth_rate_limits FOR DELETE USING ({gate})")

    # --- RLS and grants (explicit, no DEFAULT PRIVILEGES) -----------------------------------
    for table in ("users", "invitations", "recovery_codes", "sessions", "audit_log"):
        for statement in tenant_rls_statements(table):
            op.execute(statement)
    op.execute("GRANT SELECT, INSERT, UPDATE ON users, invitations, sessions TO regista_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON recovery_codes TO regista_app")
    # Append-only: the app role can insert audit rows and nothing else.
    op.execute("GRANT INSERT ON audit_log TO regista_app")

    # --- lookup functions used before a tenant is known -------------------------------------
    op.execute(
        f"""
        CREATE FUNCTION app.lookup_login(p_email citext)
          RETURNS TABLE (
            id uuid, tenant_id uuid, password_hash text, status text, mfa_enabled boolean,
            failed_logins integer, locked_until timestamptz, is_platform_admin boolean,
            tenant_active boolean)
          LANGUAGE plpgsql {DEFINER} AS
        $$
        DECLARE prev text := current_setting('app.platform_admin', true);
        BEGIN
          {FLAG_ON}
          RETURN QUERY
            SELECT u.id, u.tenant_id, u.password_hash, u.status, u.mfa_enabled,
                   u.failed_logins, u.locked_until, u.is_platform_admin, t.is_active
            FROM public.users u JOIN public.tenants t ON t.id = u.tenant_id
            WHERE u.email = p_email;
          {FLAG_RESTORE}
        END $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION app.lookup_invitation(p_token_hash bytea)
          RETURNS TABLE (
            invitation_id uuid, tenant_id uuid, user_id uuid, email text,
            expires_at timestamptz, used_at timestamptz, revoked_at timestamptz)
          LANGUAGE plpgsql {DEFINER} AS
        $$
        DECLARE prev text := current_setting('app.platform_admin', true);
        BEGIN
          {FLAG_ON}
          RETURN QUERY
            SELECT i.id, i.tenant_id, i.user_id, u.email::text, i.expires_at, i.used_at,
                   i.revoked_at
            FROM public.invitations i JOIN public.users u ON u.id = i.user_id
            WHERE i.token_hash = p_token_hash;
          {FLAG_RESTORE}
        END $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION app.lookup_session(p_token_hash bytea)
          RETURNS TABLE (
            session_id uuid, tenant_id uuid, user_id uuid, stage text, csrf_token_hash bytea,
            expires_at timestamptz, last_seen_at timestamptz, revoked_at timestamptz)
          LANGUAGE plpgsql {DEFINER} AS
        $$
        DECLARE prev text := current_setting('app.platform_admin', true);
        BEGIN
          {FLAG_ON}
          RETURN QUERY
            SELECT s.id, s.tenant_id, s.user_id, s.stage, s.csrf_token_hash,
                   s.expires_at, s.last_seen_at, s.revoked_at
            FROM public.sessions s
            WHERE s.token_hash = p_token_hash;
          {FLAG_RESTORE}
        END $$
        """
    )
    # Fixed-window counter. Returns true when the key went over `p_max_hits` in the window.
    op.execute(
        f"""
        CREATE FUNCTION app.rate_limit_hit(
          p_key bytea, p_window_seconds integer, p_max_hits integer)
          RETURNS boolean
          LANGUAGE plpgsql {DEFINER} AS
        $$
        DECLARE
          w timestamptz;
          c integer;
          prev text := current_setting('app.rate_limit', true);
        BEGIN
          PERFORM set_config('app.rate_limit', 'on', true);
          w := to_timestamp(floor(extract(epoch FROM now()) / p_window_seconds) * p_window_seconds);
          DELETE FROM public.auth_rate_limits r WHERE r.key_hash = p_key AND r.window_start < w;
          INSERT INTO public.auth_rate_limits AS r (key_hash, window_start, count)
            VALUES (p_key, w, 1)
            ON CONFLICT (key_hash, window_start) DO UPDATE SET count = r.count + 1
            RETURNING r.count INTO c;
          PERFORM set_config('app.rate_limit', coalesce(prev, ''), true);
          RETURN c > p_max_hits;
        END $$
        """
    )
    for signature in FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    for signature in CALLABLE_BY_APP:
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO regista_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION app.rate_limit_hit(bytea, integer, integer)")
    op.execute("DROP FUNCTION app.lookup_session(bytea)")
    op.execute("DROP FUNCTION app.lookup_invitation(bytea)")
    op.execute("DROP FUNCTION app.lookup_login(citext)")
    for table in ("auth_rate_limits", "audit_log", "sessions", "recovery_codes", "invitations"):
        op.drop_table(table)
    op.execute("DROP TRIGGER users_guard_platform_admin ON users")
    op.drop_table("users")
    op.execute("DROP FUNCTION app.users_guard_platform_admin()")
    op.execute("DROP TRIGGER tenants_guard_internal ON tenants")
    op.execute("DROP FUNCTION app.tenants_guard_internal()")
    op.execute("ALTER TABLE tenants DROP CONSTRAINT ck_tenants_internal_active")
    op.execute("DROP INDEX uq_tenants_single_internal")
    op.drop_column("tenants", "is_internal")
