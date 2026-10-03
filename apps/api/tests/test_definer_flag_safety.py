"""The session flags raised inside the SECURITY DEFINER functions must never outlive a failure.

Each function turns a flag on with `set_config(..., true)` (transaction-local), runs its query and
turns it back to the caller's value. This file proves the failure paths: an exception in the middle
of the function, with and without a savepoint, and a caller that already had the flag on.
"""

import re
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from .conftest import Seed

PLATFORM_FLAG = "SELECT coalesce(current_setting('app.platform_admin', true), '')"
RATE_FLAG = "SELECT coalesce(current_setting('app.rate_limit', true), '')"
DEFINER_FUNCTIONS = (
    "lookup_login",
    "lookup_invitation",
    "lookup_session",
    "rate_limit_hit",
    "users_guard_platform_admin",
)


async def _value(conn: AsyncConnection, sql: str) -> str:
    value: str = (await conn.execute(text(sql))).scalar_one()
    return value


async def test_every_set_config_in_the_definer_functions_is_transaction_local(
    app_engine: AsyncEngine, seed: Seed
) -> None:
    async with app_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT p.proname, p.prosrc FROM pg_proc p"
                    " JOIN pg_namespace n ON n.oid = p.pronamespace"
                    " WHERE n.nspname = 'app' AND p.proname = ANY(:names)"
                ),
                {"names": list(DEFINER_FUNCTIONS)},
            )
        ).all()
    assert {r.proname for r in rows} == set(DEFINER_FUNCTIONS)
    for row in rows:
        calls = re.findall(r"set_config\((.*?)\)\s*;", row.prosrc, flags=re.S)
        for call in calls:
            # The third argument is is_local; `false` would leak into the whole connection.
            assert call.rstrip().endswith("true"), f"{row.proname}: set_config({call})"


async def test_failure_inside_a_savepoint_leaves_no_flag_behind(
    app_engine: AsyncEngine, seed: Seed
) -> None:
    async with app_engine.connect() as conn:
        outer = await conn.begin()
        assert await _value(conn, RATE_FLAG) != "on"

        savepoint = await conn.begin_nested()
        with pytest.raises(DBAPIError, match="division by zero"):
            # window_seconds = 0 divides by zero *after* the function raised its flag.
            await conn.execute(
                text("SELECT app.rate_limit_hit(:k, 0, 3)"), {"k": uuid.uuid4().bytes}
            )
        await savepoint.rollback()

        assert await _value(conn, RATE_FLAG) != "on"
        # The transaction is still usable and the function works normally afterwards.
        assert (
            await conn.execute(
                text("SELECT app.rate_limit_hit(:k, 60, 3)"), {"k": uuid.uuid4().bytes}
            )
        ).scalar_one() is False
        assert await _value(conn, RATE_FLAG) != "on"
        await outer.rollback()


async def test_failure_without_a_savepoint_does_not_poison_the_pooled_connection(
    app_engine: AsyncEngine, seed: Seed
) -> None:
    # The app engine has a single pooled connection, so the next checkout is the same session.
    async with app_engine.connect() as conn:
        await conn.begin()
        with pytest.raises(DBAPIError, match="division by zero"):
            await conn.execute(
                text("SELECT app.rate_limit_hit(:k, 0, 3)"), {"k": uuid.uuid4().bytes}
            )
        await conn.rollback()
    async with app_engine.connect() as conn:
        assert await _value(conn, RATE_FLAG) != "on"
        assert await _value(conn, PLATFORM_FLAG) != "on"


async def test_platform_flag_is_restored_when_a_lookup_fails_midway(
    owner_engine: AsyncEngine, seed: Seed
) -> None:
    """The failure is forced by renaming the table inside a transaction that is rolled back, so
    `lookup_login` raises after it has already turned `app.platform_admin` on."""
    async with owner_engine.connect() as conn:
        outer = await conn.begin()
        await conn.execute(text("ALTER TABLE users RENAME TO users_renamed"))
        await conn.execute(text("ALTER TABLE sessions RENAME TO sessions_renamed"))

        # 1. Caller without the flag: it must not be left on after the rolled-back savepoint.
        savepoint = await conn.begin_nested()
        with pytest.raises(DBAPIError, match="users"):
            await conn.execute(text("SELECT * FROM app.lookup_login('nobody@example.com')"))
        await savepoint.rollback()
        assert await _value(conn, PLATFORM_FLAG) != "on"

        # 2. Caller that already had the flag keeps exactly its own value, not an empty one.
        await conn.execute(text("SELECT set_config('app.platform_admin', 'on', true)"))
        savepoint = await conn.begin_nested()
        with pytest.raises(DBAPIError, match="users"):
            await conn.execute(text("SELECT * FROM app.lookup_login('nobody@example.com')"))
        await savepoint.rollback()
        assert await _value(conn, PLATFORM_FLAG) == "on"

        # 3. Same failure in a savepoint of a caller without the flag, other lookup functions too.
        await conn.execute(text("SELECT set_config('app.platform_admin', '', true)"))
        for call in (
            "SELECT * FROM app.lookup_invitation(:h)",
            "SELECT * FROM app.lookup_session(:h)",
        ):
            savepoint = await conn.begin_nested()
            with pytest.raises(DBAPIError):
                await conn.execute(text(call), {"h": uuid.uuid4().bytes})
            await savepoint.rollback()
            assert await _value(conn, PLATFORM_FLAG) != "on", call

        await outer.rollback()

    # The rename was rolled back with everything else.
    async with owner_engine.connect() as conn:
        exists: str = (await conn.execute(text("SELECT to_regclass('public.users')"))).scalar_one()
        assert exists == "users"
        assert await _value(conn, PLATFORM_FLAG) != "on"
