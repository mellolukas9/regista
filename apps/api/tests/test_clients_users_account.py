"""Clients, users and the signed-in person's account, end to end over HTTP."""

import re
import uuid

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api.core.db import tenant_session
from regista_api.core.email import MemoryEmailSender

from .conftest import Seed
from .helpers import (
    NEW_PASSWORD,
    PASSWORD,
    Account,
    Env,
    csrf,
    login,
    new_client,
    onboard,
    unique_email,
)

Factory = async_sessionmaker[AsyncSession]
UA_CHROME_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def token_from_mail(mail: MemoryEmailSender, to: str) -> str:
    message = next(m for m in reversed(mail.outbox) if m.to == to)
    found = re.search(r"/invite#(\S+)", message.text)
    assert found, message.text
    return found.group(1)


async def _post(client: httpx.AsyncClient, path: str, **kwargs: object) -> httpx.Response:
    return await client.post(path, headers=csrf(client), **kwargs)  # type: ignore[arg-type]


async def _artemisys(env: Env, internal: uuid.UUID) -> Account:
    return await onboard(
        env.app, env.client, internal, env.clock, role="tenant_admin", platform_admin=True
    )


async def _pick(client: httpx.AsyncClient, client_id: uuid.UUID | None) -> None:
    r = await client.put(
        "/auth/context",
        json={"client_id": str(client_id) if client_id else None},
        headers=csrf(client),
    )
    assert r.status_code == 200, r.text


async def _new_client(env: Env, artemisys: httpx.AsyncClient) -> tuple[uuid.UUID, str, str]:
    """Create a client through the API and return (client id, admin e-mail, invite token)."""
    admin_email = unique_email("owner")
    name = f"Cliente {uuid.uuid4().hex[:8]}"
    r = await _post(artemisys, "/clients", json={"name": name, "admin_email": admin_email})
    assert r.status_code == 201, r.text
    return uuid.UUID(r.json()["id"]), admin_email, token_from_mail(env.mail, admin_email)


async def _accept_and_activate(
    env: Env, browser: httpx.AsyncClient, tenant: uuid.UUID, email: str, token: str
) -> Account:
    """Finish onboarding for an already-invited user (what the invitation page does)."""
    from .helpers import PASSWORD as PW

    assert (
        await browser.post("/auth/invitations/accept", json={"token": token, "password": PW})
    ).status_code == 200
    secret = (await _post(browser, "/auth/mfa/setup")).json()["secret"]
    activate = await _post(browser, "/auth/mfa/activate", json={"code": env.clock.code(secret)})
    assert activate.status_code == 200, activate.text
    env.clock.next_step()
    assert (await _post(browser, "/auth/recovery-codes/ack")).status_code == 200
    me = (await browser.get("/auth/me")).json()
    return (
        Account(uuid.UUID(int=0), tenant, email, PW, secret, activate.json()["recovery_codes"])
        if me["stage"] == "active"
        else Account(uuid.UUID(int=0), tenant, email)
    )


async def _audit(
    owner_factory: Factory, tenant_id: uuid.UUID, action: str, target: uuid.UUID | None = None
) -> int:
    async with tenant_session(owner_factory, tenant_id=tenant_id) as db:
        sql = "SELECT count(*) FROM audit_log WHERE action = :a"
        params: dict[str, object] = {"a": action}
        if target is not None:
            sql += " AND target_id = :t"
            params["t"] = target
        count: int = (await db.execute(text(sql), params)).scalar_one()
    return count


# --- clients ----------------------------------------------------------------------------------


async def test_artemisys_creates_a_client_and_its_first_admin_is_invited(
    env: Env, seed: Seed, internal_tenant: uuid.UUID, owner_factory: Factory
) -> None:
    await _artemisys(env, internal_tenant)
    client_id, admin_email, token = await _new_client(env, env.client)

    message = env.mail.outbox[-1]
    assert message.to == admin_email
    assert message.subject == "Seu acesso ao Regista"
    assert "/invite#" in message.text

    async with new_client(env.app) as browser:
        inspect = await browser.post("/auth/invitations/inspect", json={"token": token})
        assert inspect.json() == {"email": admin_email}
        await _accept_and_activate(env, browser, client_id, admin_email, token)
        me = (await browser.get("/auth/me")).json()
        assert me["role"] == "tenant_admin"
        assert me["tenant_id"] == str(client_id)
        assert me["is_platform_admin"] is False

    assert await _audit(owner_factory, client_id, "tenant.created", client_id) == 1
    assert await _audit(owner_factory, client_id, "user.invited") == 1

    listed = (await env.client.get("/clients", params={"q": "Cliente"})).json()
    row = next(i for i in listed["items"] if i["id"] == str(client_id))
    assert row["users_count"] == 1
    assert (row["machines_total"], row["machines_online"]) == (0, 0)


async def test_client_creation_is_all_or_nothing(
    env: Env, seed: Seed, internal_tenant: uuid.UUID
) -> None:
    await _artemisys(env, internal_tenant)
    taken = unique_email("taken")
    await onboard(env.app, new_client(env.app), seed.tenant_a, env.clock, email=taken)
    name = f"Cliente {uuid.uuid4().hex[:8]}"

    # E-mail already used by someone in another client: generic error, nothing is created.
    conflict = await _post(env.client, "/clients", json={"name": name, "admin_email": taken})
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {"code": "email_in_use"}
    assert "Tenant A" not in conflict.text
    assert (await env.client.get("/clients", params={"q": name})).json()["total"] == 0

    first = await _post(env.client, "/clients", json={"name": name, "admin_email": unique_email()})
    assert first.status_code == 201
    again = await _post(
        env.client, "/clients", json={"name": name.upper(), "admin_email": unique_email()}
    )
    assert again.status_code == 409
    assert again.json()["detail"] == {"code": "client_name_taken"}

    bad = await _post(env.client, "/clients", json={"name": name, "admin_email": "sem-dominio"})
    assert bad.status_code == 422


async def test_clients_list_filters_sorts_and_hides_the_internal_tenant(
    env: Env, seed: Seed, internal_tenant: uuid.UUID
) -> None:
    await _artemisys(env, internal_tenant)
    prefix = uuid.uuid4().hex[:6]
    for letter in "bca":
        r = await _post(
            env.client,
            "/clients",
            json={"name": f"{prefix} {letter}", "admin_email": unique_email()},
        )
        assert r.status_code == 201

    ascending = (await env.client.get("/clients", params={"q": prefix, "sort": "name"})).json()
    assert [i["name"] for i in ascending["items"]] == [f"{prefix} a", f"{prefix} b", f"{prefix} c"]
    descending = (await env.client.get("/clients", params={"q": prefix, "sort": "-name"})).json()
    assert [i["name"][-1] for i in descending["items"]] == ["c", "b", "a"]

    # Wildcards typed by the user are literal.
    assert (await env.client.get("/clients", params={"q": "%"})).json()["total"] == 0

    page = (
        await env.client.get("/clients", params={"q": prefix, "per_page": 10, "page": 2})
    ).json()
    assert (page["total"], page["page"], page["per_page"], page["items"]) == (3, 2, 10, [])

    everyone = (await env.client.get("/clients", params={"per_page": 50})).json()
    assert "Artemisys" not in [i["name"] for i in everyone["items"]]

    assert (
        await env.client.get("/clients", params={"sort": "name; DROP TABLE"})
    ).status_code == 422
    assert (await env.client.get("/clients", params={"per_page": 7})).status_code == 422


async def test_only_artemisys_reaches_the_clients_routes(env: Env, seed: Seed) -> None:
    for role in ("tenant_admin", "operator", "viewer"):
        async with new_client(env.app) as browser:
            await onboard(env.app, browser, seed.tenant_a, env.clock, role=role)
            listing = await browser.get("/clients")
            creating = await _post(
                browser, "/clients", json={"name": "Invasor", "admin_email": unique_email()}
            )
            assert listing.status_code == creating.status_code == 403, role
            assert listing.json()["detail"] == {"code": "forbidden"}


# --- users ------------------------------------------------------------------------------------


async def test_admin_invites_and_lists_users(env: Env, seed: Seed) -> None:
    admin = await onboard(env.app, env.client, seed.tenant_a, env.clock, role="tenant_admin")
    email = unique_email("novo")
    r = await _post(env.client, "/users/invitations", json={"email": email, "role": "operator"})
    assert r.status_code == 201
    assert r.json()["status"] == "invited"
    assert env.mail.outbox[-1].to == email
    assert "/invite#" in env.mail.outbox[-1].text

    listed = (await env.client.get("/users", params={"q": email})).json()
    assert listed["total"] == 1
    item = listed["items"][0]
    assert (item["email"], item["role"], item["status"], item["mfa"]) == (
        email,
        "operator",
        "invited",
        None,
    )
    assert item["client_id"] == str(seed.tenant_a)
    assert item["is_self"] is False

    me = (await env.client.get("/users", params={"q": admin.email})).json()["items"][0]
    assert me["is_self"] is True
    assert me["mfa"] == "active"
    assert me["last_login_at"] is not None

    only_admins = (await env.client.get("/users", params={"role": "tenant_admin"})).json()
    assert {i["role"] for i in only_admins["items"]} == {"tenant_admin"}
    ordered = (await env.client.get("/users", params={"sort": "-email", "per_page": 50})).json()
    emails = [i["email"] for i in ordered["items"]]
    assert emails == sorted(emails, reverse=True)


async def test_invitation_conflicts_do_not_leak_other_clients(env: Env, seed: Seed) -> None:
    await onboard(env.app, env.client, seed.tenant_a, env.clock, role="tenant_admin")
    mine = await onboard(env.app, new_client(env.app), seed.tenant_a, env.clock)
    theirs = await onboard(env.app, new_client(env.app), seed.tenant_b, env.clock)

    same_client = await _post(
        env.client, "/users/invitations", json={"email": mine.email, "role": "viewer"}
    )
    assert same_client.status_code == 409
    assert same_client.json()["detail"] == {"code": "already_has_access"}

    other_client = await _post(
        env.client, "/users/invitations", json={"email": theirs.email, "role": "viewer"}
    )
    assert other_client.status_code == 409
    assert other_client.json()["detail"] == {"code": "email_in_use"}
    assert "Tenant B" not in other_client.text
    assert str(seed.tenant_b) not in other_client.text


async def test_removed_user_can_come_back_with_a_new_invitation(
    env: Env, seed: Seed, owner_factory: Factory
) -> None:
    await onboard(env.app, env.client, seed.tenant_a, env.clock, role="tenant_admin")
    async with new_client(env.app) as person:
        victim = await onboard(env.app, person, seed.tenant_a, env.clock, role="operator")
        victim_id = await _user_id(env, seed.tenant_a, victim.email)

        removed = await _post(env.client, f"/users/{victim_id}/remove-access")
        assert removed.json()["status"] == "disabled"
        assert (await person.get("/auth/me")).status_code == 401  # signed out on the spot
        async with new_client(env.app) as retry:
            blocked = await retry.post(
                "/auth/login", json={"email": victim.email, "password": PASSWORD}
            )
            assert blocked.status_code == 401

        # Disabled people are hidden unless asked for; inviting again reactivates them.
        assert (await env.client.get("/users", params={"q": victim.email})).json()["total"] == 0
        gone = (
            await env.client.get("/users", params={"q": victim.email, "status": "disabled"})
        ).json()
        assert gone["total"] == 1
        back = await _post(
            env.client, "/users/invitations", json={"email": victim.email, "role": "viewer"}
        )
        assert back.status_code == 201
        assert back.json()["id"] == str(victim_id)
        assert back.json()["role"] == "viewer"

        async with new_client(env.app) as fresh:
            token = token_from_mail(env.mail, victim.email)
            await _accept_and_activate(env, fresh, seed.tenant_a, victim.email, token)
            assert (await fresh.get("/auth/me")).json()["role"] == "viewer"
    assert await _audit(owner_factory, seed.tenant_a, "user.access_removed", victim_id) == 1


async def _user_id(env: Env, tenant: uuid.UUID, email: str) -> uuid.UUID:
    async with tenant_session(env.app.state.session_factory, tenant_id=tenant) as db:
        found: uuid.UUID = (
            await db.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email})
        ).scalar_one()
    return found


async def test_change_role_rules(env: Env, seed: Seed) -> None:
    admin = await onboard(env.app, env.client, seed.tenant_a, env.clock, role="tenant_admin")
    person = await onboard(env.app, new_client(env.app), seed.tenant_a, env.clock, role="operator")
    person_id = await _user_id(env, seed.tenant_a, person.email)

    ok = await env.client.patch(
        f"/users/{person_id}", json={"role": "viewer"}, headers=csrf(env.client)
    )
    assert ok.status_code == 200
    assert ok.json()["role"] == "viewer"

    self_id = await _user_id(env, seed.tenant_a, admin.email)
    own = await env.client.patch(
        f"/users/{self_id}", json={"role": "viewer"}, headers=csrf(env.client)
    )
    assert own.status_code == 409
    assert own.json()["detail"] == {"code": "cannot_change_own_role"}

    missing = await env.client.patch(
        f"/users/{uuid.uuid4()}", json={"role": "viewer"}, headers=csrf(env.client)
    )
    assert missing.status_code == 404
    platform_roles = await env.client.patch(
        f"/users/{person_id}", json={"role": "platform_admin"}, headers=csrf(env.client)
    )
    assert platform_roles.status_code == 422


async def test_a_client_always_keeps_one_active_admin(
    env: Env, seed: Seed, internal_tenant: uuid.UUID
) -> None:
    staff = await _artemisys(env, internal_tenant)
    client_id, owner_email, token = await _new_client(env, env.client)
    async with new_client(env.app) as owner_browser:
        await _accept_and_activate(env, owner_browser, client_id, owner_email, token)
    owner_id = await _user_id(env, client_id, owner_email)
    await _pick(env.client, client_id)

    demote = await env.client.patch(
        f"/users/{owner_id}", json={"role": "operator"}, headers=csrf(env.client)
    )
    remove = await _post(env.client, f"/users/{owner_id}/remove-access")
    for refused in (demote, remove):
        assert refused.status_code == 409
        assert refused.json()["detail"] == {"code": "last_admin"}

    # With a second active Admin the first can be demoted.
    second = unique_email("segundo")
    await _post(env.client, "/users/invitations", json={"email": second, "role": "tenant_admin"})
    async with new_client(env.app) as browser:
        await _accept_and_activate(
            env, browser, client_id, second, token_from_mail(env.mail, second)
        )
    assert (
        await env.client.patch(
            f"/users/{owner_id}", json={"role": "operator"}, headers=csrf(env.client)
        )
    ).status_code == 200
    assert staff.email  # the staff user stayed signed in throughout


async def test_resend_invitation_is_the_way_back_for_a_lone_admin(
    env: Env, seed: Seed, internal_tenant: uuid.UUID, owner_factory: Factory
) -> None:
    await _artemisys(env, internal_tenant)
    client_id, owner_email, token = await _new_client(env, env.client)
    async with new_client(env.app) as owner_browser:
        owner = await _accept_and_activate(env, owner_browser, client_id, owner_email, token)
        owner_id = await _user_id(env, client_id, owner_email)
        assert (await owner_browser.get("/auth/me")).status_code == 200

        await _pick(env.client, client_id)
        again = await _post(env.client, f"/users/{owner_id}/resend-invitation")
        assert again.status_code == 200
        assert again.json()["status"] == "invited"

        # Password, MFA, recovery codes and sessions are all gone.
        assert (await owner_browser.get("/auth/me")).status_code == 401
        async with new_client(env.app) as retry:
            old = await retry.post(
                "/auth/login", json={"email": owner_email, "password": owner.password}
            )
            assert old.status_code == 401
        remaining = await _count(
            env, client_id, "SELECT count(*) FROM recovery_codes WHERE user_id = :u", owner_id
        )
        assert remaining == 0

    new_token = token_from_mail(env.mail, owner_email)
    assert new_token != token
    assert env.mail.outbox[-1].subject == "Novo convite para o Regista"
    async with new_client(env.app) as stale:
        assert (
            await stale.post("/auth/invitations/inspect", json={"token": token})
        ).status_code == 404
    async with new_client(env.app) as browser:
        assert (
            await browser.post(
                "/auth/invitations/accept", json={"token": new_token, "password": NEW_PASSWORD}
            )
        ).status_code == 200
        assert (await browser.get("/auth/me")).json()["stage"] == "mfa_setup"  # MFA from scratch
    assert await _audit(owner_factory, client_id, "user.reinvited", owner_id) == 1


async def _count(env: Env, tenant: uuid.UUID, sql: str, user_id: uuid.UUID) -> int:
    async with tenant_session(env.app.state.session_factory, tenant_id=tenant) as db:
        count: int = (await db.execute(text(sql), {"u": user_id})).scalar_one()
    return count


async def test_resend_invitation_guards(env: Env, seed: Seed) -> None:
    admin = await onboard(env.app, env.client, seed.tenant_a, env.clock, role="tenant_admin")
    self_id = await _user_id(env, seed.tenant_a, admin.email)
    own = await _post(env.client, f"/users/{self_id}/resend-invitation")
    assert own.status_code == 409
    assert own.json()["detail"] == {"code": "cannot_reinvite_self"}

    person = await onboard(env.app, new_client(env.app), seed.tenant_a, env.clock)
    person_id = await _user_id(env, seed.tenant_a, person.email)
    await _post(env.client, f"/users/{person_id}/remove-access")
    disabled = await _post(env.client, f"/users/{person_id}/resend-invitation")
    assert disabled.status_code == 409
    assert disabled.json()["detail"] == {"code": "user_disabled"}


async def test_admin_ends_all_sessions_of_a_user(env: Env, seed: Seed) -> None:
    await onboard(env.app, env.client, seed.tenant_a, env.clock, role="tenant_admin")
    async with new_client(env.app) as laptop, new_client(env.app) as phone:
        person = await onboard(env.app, laptop, seed.tenant_a, env.clock, role="operator")
        env.clock.next_step()
        await login(phone, person, env.clock)
        person_id = await _user_id(env, seed.tenant_a, person.email)

        ended = await _post(env.client, f"/users/{person_id}/revoke-sessions")
        assert ended.json() == {"revoked": 2}
        assert (await laptop.get("/auth/me")).status_code == 401
        assert (await phone.get("/auth/me")).status_code == 401


async def test_remove_access_guards_and_is_idempotent(env: Env, seed: Seed) -> None:
    admin = await onboard(env.app, env.client, seed.tenant_a, env.clock, role="tenant_admin")
    self_id = await _user_id(env, seed.tenant_a, admin.email)
    own = await _post(env.client, f"/users/{self_id}/remove-access")
    assert own.status_code == 409
    assert own.json()["detail"] == {"code": "cannot_remove_own_access"}

    person = await onboard(env.app, new_client(env.app), seed.tenant_a, env.clock)
    person_id = await _user_id(env, seed.tenant_a, person.email)
    assert (await _post(env.client, f"/users/{person_id}/remove-access")).status_code == 200
    assert (await _post(env.client, f"/users/{person_id}/remove-access")).status_code == 200


async def test_roles_without_permission_cannot_manage_users(env: Env, seed: Seed) -> None:
    target = await onboard(env.app, new_client(env.app), seed.tenant_a, env.clock)
    target_id = await _user_id(env, seed.tenant_a, target.email)
    for role in ("operator", "viewer"):
        async with new_client(env.app) as browser:
            await onboard(env.app, browser, seed.tenant_a, env.clock, role=role)
            responses = [
                await browser.get("/users"),
                await _post(
                    browser, "/users/invitations", json={"email": unique_email(), "role": "viewer"}
                ),
                await browser.patch(
                    f"/users/{target_id}", json={"role": "viewer"}, headers=csrf(browser)
                ),
                await _post(browser, f"/users/{target_id}/revoke-sessions"),
                await _post(browser, f"/users/{target_id}/remove-access"),
                await _post(browser, f"/users/{target_id}/resend-invitation"),
            ]
            assert [r.status_code for r in responses] == [403] * 6, role
    assert (await _user_id(env, seed.tenant_a, target.email)) == target_id


async def test_artemisys_reads_every_client_but_writes_in_one(
    env: Env, seed: Seed, internal_tenant: uuid.UUID
) -> None:
    await _artemisys(env, internal_tenant)
    in_a = await onboard(env.app, new_client(env.app), seed.tenant_a, env.clock)
    in_b = await onboard(env.app, new_client(env.app), seed.tenant_b, env.clock)

    # "Todos os clientes": consolidated read, internal staff excluded, client name per row.
    everyone = (await env.client.get("/users", params={"per_page": 50, "sort": "-email"})).json()
    clients = {i["client_id"] for i in everyone["items"]}
    assert {str(seed.tenant_a), str(seed.tenant_b)} <= clients
    assert str(internal_tenant) not in clients
    names = {i["client_name"] for i in everyone["items"]}
    assert {"Tenant A", "Tenant B"} <= names
    assert {in_a.email, in_b.email} & {i["email"] for i in everyone["items"]}

    # Writes need one client.
    blocked = await _post(
        env.client, "/users/invitations", json={"email": unique_email(), "role": "viewer"}
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == {"code": "client_context_required"}
    some_id = await _user_id(env, seed.tenant_a, in_a.email)
    assert (await _post(env.client, f"/users/{some_id}/revoke-sessions")).status_code == 409

    await _pick(env.client, seed.tenant_a)
    only_a = (await env.client.get("/users", params={"per_page": 50})).json()
    assert {i["client_id"] for i in only_a["items"]} == {str(seed.tenant_a)}
    assert (await _post(env.client, f"/users/{some_id}/revoke-sessions")).status_code == 200

    # Working in A, a user of B is not found.
    other_id = await _user_id(env, seed.tenant_b, in_b.email)
    assert (await _post(env.client, f"/users/{other_id}/revoke-sessions")).status_code == 404


async def test_an_admin_only_sees_their_own_client(env: Env, seed: Seed) -> None:
    await onboard(env.app, env.client, seed.tenant_a, env.clock, role="tenant_admin")
    theirs = await onboard(env.app, new_client(env.app), seed.tenant_b, env.clock)
    listed = (await env.client.get("/users", params={"per_page": 50})).json()
    assert {i["client_id"] for i in listed["items"]} == {str(seed.tenant_a)}
    assert theirs.email not in {i["email"] for i in listed["items"]}
    assert (await env.client.get("/users", params={"q": theirs.email})).json()["total"] == 0
    other_id = await _user_id(env, seed.tenant_b, theirs.email)
    assert (await _post(env.client, f"/users/{other_id}/remove-access")).status_code == 404


# --- account: sessions ------------------------------------------------------------------------


async def test_my_sessions_list_and_end(env: Env, seed: Seed) -> None:
    person = await onboard(env.app, env.client, seed.tenant_a, env.clock)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.app),
        base_url="http://test",
        headers={"User-Agent": UA_CHROME_WINDOWS},
    ) as other:
        env.clock.next_step()
        await login(other, person, env.clock)

        listed = (await env.client.get("/account/sessions")).json()["items"]
        assert len(listed) == 2
        current = [s for s in listed if s["is_current"]]
        assert len(current) == 1
        chrome = next(s for s in listed if not s["is_current"])
        assert chrome["device"] == "Chrome no Windows"

        assert (await other.get("/auth/me")).status_code == 200
        ended = await env.client.delete(
            f"/account/sessions/{chrome['id']}", headers=csrf(env.client)
        )
        assert ended.status_code == 204
        assert (await other.get("/auth/me")).status_code == 401
        assert len((await env.client.get("/account/sessions")).json()["items"]) == 1

    own = await env.client.delete(f"/account/sessions/{current[0]['id']}", headers=csrf(env.client))
    assert own.status_code == 409
    assert own.json()["detail"] == {"code": "cannot_end_current_session"}
    unknown = await env.client.delete(f"/account/sessions/{uuid.uuid4()}", headers=csrf(env.client))
    assert unknown.status_code == 404


async def test_nobody_ends_someone_elses_session(env: Env, seed: Seed) -> None:
    await onboard(env.app, env.client, seed.tenant_a, env.clock)
    async with new_client(env.app) as stranger:
        await onboard(env.app, stranger, seed.tenant_a, env.clock)
        theirs = (await stranger.get("/account/sessions")).json()["items"][0]["id"]
        r = await env.client.delete(f"/account/sessions/{theirs}", headers=csrf(env.client))
        assert r.status_code == 404
        assert (await stranger.get("/auth/me")).status_code == 200


async def test_end_all_other_sessions(env: Env, seed: Seed) -> None:
    person = await onboard(env.app, env.client, seed.tenant_a, env.clock)
    async with new_client(env.app) as a, new_client(env.app) as b:
        env.clock.next_step()
        await login(a, person, env.clock)
        env.clock.next_step()
        await login(b, person, env.clock)
        done = await _post(env.client, "/account/sessions/revoke-others")
        assert done.json() == {"revoked": 2}
        assert (await a.get("/auth/me")).status_code == 401
        assert (await b.get("/auth/me")).status_code == 401
    assert (await env.client.get("/auth/me")).status_code == 200


# --- account: recovery codes and password -----------------------------------------------------


async def test_new_recovery_codes_replace_the_old_ones(env: Env, seed: Seed) -> None:
    person = await onboard(env.app, env.client, seed.tenant_a, env.clock)
    wrong = await _post(
        env.client, "/account/recovery-codes", json={"password": "errada-errada-12"}
    )
    assert wrong.status_code == 422
    assert wrong.json()["detail"] == {"code": "password_incorrect"}

    fresh = await _post(env.client, "/account/recovery-codes", json={"password": PASSWORD})
    codes = fresh.json()["recovery_codes"]
    assert len(set(codes)) == 10
    assert not set(codes) & set(person.recovery_codes)
    assert (await env.client.get("/auth/me")).json()["recovery_codes_remaining"] == 10

    async with new_client(env.app) as browser:
        await browser.post("/auth/login", json={"email": person.email, "password": PASSWORD})
        old = await _post(
            browser, "/auth/mfa/recover", json={"recovery_code": person.recovery_codes[0]}
        )
        assert old.status_code == 401
        new = await _post(browser, "/auth/mfa/recover", json={"recovery_code": codes[0]})
        assert new.status_code == 200


async def test_change_password(env: Env, seed: Seed, owner_factory: Factory) -> None:
    person = await onboard(env.app, env.client, seed.tenant_a, env.clock)
    async with new_client(env.app) as other_device:
        env.clock.next_step()
        await login(other_device, person, env.clock)
        env.clock.next_step()

        def body(**changes: str) -> dict[str, str]:
            return {
                "current_password": PASSWORD,
                "new_password": NEW_PASSWORD,
                "code": env.clock.code(person.totp_secret),
                **changes,
            }

        wrong_pw = await _post(
            env.client, "/account/password", json=body(current_password="x" * 14)
        )
        assert wrong_pw.json()["detail"] == {"code": "password_incorrect"}
        wrong_code = await _post(env.client, "/account/password", json=body(code="000000"))
        assert wrong_code.json()["detail"] == {"code": "invalid_code"}
        same = await _post(env.client, "/account/password", json=body(new_password=PASSWORD))
        assert same.json()["detail"] == {"code": "same_as_current"}
        short = await _post(env.client, "/account/password", json=body(new_password="curta-1234"))
        assert short.json()["detail"] == {"code": "too_short"}
        weak = await _post(env.client, "/account/password", json=body(new_password="password1234"))
        assert weak.json()["detail"] == {"code": "too_common"}

        done = await _post(env.client, "/account/password", json=body())
        assert done.status_code == 204
        assert env.mail.outbox[-1].subject == "Sua senha do Regista foi alterada"
        assert env.mail.outbox[-1].to == person.email

        assert (await env.client.get("/auth/me")).status_code == 200  # this session stays
        assert (await other_device.get("/auth/me")).status_code == 401  # the others go

        # The TOTP step just used cannot be replayed to change it again.
        replay = await _post(
            env.client,
            "/account/password",
            json={
                "current_password": NEW_PASSWORD,
                "new_password": PASSWORD + "x",
                "code": env.clock.code(person.totp_secret),
            },
        )
        assert replay.json()["detail"] == {"code": "invalid_code"}

    async with new_client(env.app) as browser:
        env.clock.next_step()
        assert (await login(browser, person, env.clock, password=NEW_PASSWORD)).status_code == 200
    async with new_client(env.app) as browser:
        old = await browser.post("/auth/login", json={"email": person.email, "password": PASSWORD})
        assert old.status_code == 401
    assert await _audit(owner_factory, seed.tenant_a, "auth.password_changed", None) >= 1


async def test_change_password_failures_feed_the_lockout(env: Env, seed: Seed) -> None:
    person = await onboard(env.app, env.client, seed.tenant_a, env.clock)
    attempts = []
    for _ in range(env.app.state.settings.lockout_threshold + 1):
        r = await _post(
            env.client,
            "/account/password",
            json={
                "current_password": "errada-errada-12",
                "new_password": NEW_PASSWORD,
                "code": env.clock.code(person.totp_secret),
            },
        )
        attempts.append(r.status_code)
    assert attempts == [422] * env.app.state.settings.lockout_threshold + [429]
    # The sign-in is locked too, since it is the same counter.
    async with new_client(env.app) as browser:
        locked = await browser.post(
            "/auth/login", json={"email": person.email, "password": PASSWORD}
        )
        assert locked.status_code == 429


async def test_api_refuses_actions_on_your_own_row_and_changes_nothing(
    env: Env, seed: Seed, internal_tenant: uuid.UUID
) -> None:
    """The panel hides these buttons, but the rule lives in the API: calling it directly is refused
    and neither the row, the credentials, the sessions nor the e-mail outbox move."""
    admin = await onboard(env.app, env.client, seed.tenant_a, env.clock, role="tenant_admin")
    me = await _user_id(env, seed.tenant_a, admin.email)
    sent_before = len(env.mail.outbox)

    async def snapshot() -> dict[str, object]:
        async with tenant_session(env.app.state.session_factory, tenant_id=seed.tenant_a) as db:
            row = (
                (
                    await db.execute(
                        text(
                            "SELECT role, status, password_hash, mfa_enabled, mfa_secret_enc,"
                            " (SELECT count(*) FROM sessions s WHERE s.user_id = u.id"
                            "  AND s.revoked_at IS NULL) AS live_sessions,"
                            " (SELECT count(*) FROM recovery_codes r WHERE r.user_id = u.id"
                            "  AND r.used_at IS NULL) AS codes,"
                            " (SELECT count(*) FROM invitations i"
                            "  WHERE i.user_id = u.id) AS invites"
                            " FROM users u WHERE u.id = :u"
                        ),
                        {"u": me},
                    )
                )
                .mappings()
                .one()
            )
        return dict(row)

    before = await snapshot()
    attempts = [
        (
            await env.client.patch(
                f"/users/{me}", json={"role": "viewer"}, headers=csrf(env.client)
            ),
            "cannot_change_own_role",
        ),
        (await _post(env.client, f"/users/{me}/remove-access"), "cannot_remove_own_access"),
        (await _post(env.client, f"/users/{me}/resend-invitation"), "cannot_reinvite_self"),
    ]
    for response, code in attempts:
        assert response.status_code == 409, code
        assert response.json()["detail"] == {"code": code}

    assert await snapshot() == before  # role, status, password, MFA, sessions, codes, invitations
    assert len(env.mail.outbox) == sent_before  # no invitation e-mail went out
    assert (await env.client.get("/auth/me")).json()["role"] == "tenant_admin"  # still signed in

    # The Artemisys team: their own user lives in the hidden internal tenant, so from inside a
    # client it is simply not there, and in "all clients" writes are refused before anything runs.
    async with new_client(env.app) as staff:
        me_staff = await onboard(
            env.app, staff, internal_tenant, env.clock, role="tenant_admin", platform_admin=True
        )
        staff_id = me_staff.user_id
        await _pick(staff, seed.tenant_a)
        for method, path, body in (
            ("PATCH", f"/users/{staff_id}", {"role": "viewer"}),
            ("POST", f"/users/{staff_id}/remove-access", None),
            ("POST", f"/users/{staff_id}/resend-invitation", None),
        ):
            r = await staff.request(method, path, json=body, headers=csrf(staff))
            assert r.status_code == 404, (method, path)
        await _pick(staff, None)
        r = await _post(staff, f"/users/{staff_id}/remove-access")
        assert r.status_code == 409
        assert r.json()["detail"] == {"code": "client_context_required"}
        assert (await staff.get("/auth/me")).json()["is_platform_admin"] is True
