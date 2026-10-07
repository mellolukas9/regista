"""Publishing signed versions and putting one in use (M4, ADR 0021).

The server holds no private key, so every package here is signed by a throwaway test key that the
app trusts through the development-only override. What the tests prove: a package that is not
exactly what was signed never becomes a version, and only the Artemisys team can publish.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import httpx
import pytest
from botocore.config import Config
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regista_api.core.db import tenant_session
from regista_pkg import Manifest

from .conftest import S3Env
from .helpers import Panel, csrf
from .jobs_helpers import make_bot
from .package_helpers import SignedPackage, SigningKey, make_package

pytestmark = pytest.mark.s3

Factory = async_sessionmaker[AsyncSession]


async def _staff(panel: Panel, tenant_id: uuid.UUID | None = None) -> httpx.AsyncClient:
    return await panel.staff_in(tenant_id or panel.tenant_a)


async def _bot(panel: Panel) -> dict[str, Any]:
    return await make_bot(panel, panel.tenant_a, panel.admin_a)


async def _start(
    client: httpx.AsyncClient, bot_id: str, pkg: SignedPackage, note: str | None = None
) -> httpx.Response:
    return await client.post(
        f"/bots/{bot_id}/versions/uploads",
        json={"signature": pkg.signature, "release_note": note},
        headers=csrf(client),
    )


async def _put(ticket: dict[str, Any], data: bytes) -> None:
    async with httpx.AsyncClient() as plain:  # no cookie: the URL is the credential
        r = await plain.put(ticket["upload_url"], content=data, headers=ticket["upload_headers"])
    assert r.status_code == 200, r.text


async def _complete(
    client: httpx.AsyncClient, bot_id: str, version_id: str, *, activate: bool = False
) -> httpx.Response:
    return await client.post(
        f"/bots/{bot_id}/versions/{version_id}/complete",
        json={"activate": activate},
        headers=csrf(client),
    )


async def _publish(
    panel: Panel,
    signing: SigningKey,
    bot: dict[str, Any],
    *,
    version: str = "1.0.0",
    activate: bool = False,
    note: str | None = None,
) -> dict[str, Any]:
    """The whole happy path: start, send the file, complete."""
    staff = await _staff(panel)
    pkg = make_package(
        signing, tenant_id=panel.tenant_a, package_name=bot["package_name"], version=version
    )
    started = await _start(staff, bot["id"], pkg, note)
    assert started.status_code == 201, started.text
    ticket = started.json()
    await _put(ticket, pkg.package)
    done = await _complete(staff, bot["id"], ticket["version_id"], activate=activate)
    assert done.status_code == 200, done.text
    body: dict[str, Any] = done.json()
    return body


async def _audit(owner: Factory, tenant_id: uuid.UUID, action: str) -> list[dict[str, Any]]:
    async with tenant_session(owner, tenant_id=tenant_id) as db:
        rows = await db.execute(
            text("SELECT metadata FROM audit_log WHERE action = :a ORDER BY created_at"),
            {"a": action},
        )
        return [dict(r.metadata) for r in rows]


def _s3(s3: S3Env) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=s3.endpoint_url,
        aws_access_key_id=s3.access_key_id,
        aws_secret_access_key=s3.secret_access_key,
        region_name=s3.region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


# --- publishing -------------------------------------------------------------------------------


async def test_the_staff_publishes_a_version_and_it_is_listed(
    panel: Panel, signing: SigningKey, owner_factory: Factory
) -> None:
    bot = await _bot(panel)
    item = await _publish(panel, signing, bot, version="1.2.0", note="Corrige o login.")
    assert item["version"] == "1.2.0" and item["release_note"] == "Corrige o login."
    assert item["published_by"] == "Equipe Artemisys" and item["is_current"] is False
    assert item["python"] == "3.13.5" and len(item["package_sha256"]) == 64

    for client in (panel.admin_a, panel.operator_a, panel.viewer_a):  # every role may read
        listed = (await client.get(f"/bots/{bot['id']}/versions")).json()
        assert [v["version"] for v in listed["items"]] == ["1.2.0"]
        assert listed["current_version_id"] is None
    published = await _audit(owner_factory, panel.tenant_a, "bot.version_published")
    assert any(m["version"] == "1.2.0" and m["bot_id"] == bot["id"] for m in published)


async def test_the_ticket_does_not_reveal_where_the_object_lives(
    panel: Panel, signing: SigningKey
) -> None:
    bot = await _bot(panel)
    pkg = make_package(
        signing, tenant_id=panel.tenant_a, package_name=bot["package_name"], version="1.0.0"
    )
    started = await _start(await _staff(panel), bot["id"], pkg)
    ticket = started.json()
    assert set(ticket) == {"version_id", "version", "upload_url", "upload_headers", "expires_in"}
    assert "storage_key" not in started.text and ticket["expires_in"] == 300
    assert "X-Amz-Signature=" in ticket["upload_url"]


async def test_activating_on_completion_is_audited_apart_from_publishing(
    panel: Panel, signing: SigningKey, owner_factory: Factory
) -> None:
    bot = await _bot(panel)
    item = await _publish(panel, signing, bot, version="2.0.0", activate=True)
    assert item["is_current"] is True
    detail = (await panel.admin_a.get(f"/bots/{bot['id']}")).json()
    assert detail["current_version"] == {"id": item["id"], "version": "2.0.0"}
    assert any(
        m["version"] == "2.0.0"
        for m in await _audit(owner_factory, panel.tenant_a, "bot.version_published")
    )
    activated = await _audit(owner_factory, panel.tenant_a, "bot.version_activated")
    assert any(m["version_id"] == item["id"] for m in activated)


async def test_publishing_without_activating_leaves_the_version_in_use_alone(
    panel: Panel, signing: SigningKey
) -> None:
    bot = await _bot(panel)
    first = await _publish(panel, signing, bot, version="1.0.0", activate=True)
    second = await _publish(panel, signing, bot, version="1.1.0")
    assert second["is_current"] is False
    listed = (await panel.admin_a.get(f"/bots/{bot['id']}/versions")).json()
    assert listed["current_version_id"] == first["id"]
    assert [v["version"] for v in listed["items"]] == ["1.1.0", "1.0.0"]  # newest first


async def test_switching_the_version_in_use(
    panel: Panel, signing: SigningKey, owner_factory: Factory
) -> None:
    bot = await _bot(panel)
    one = await _publish(panel, signing, bot, version="1.0.0", activate=True)
    two = await _publish(panel, signing, bot, version="1.1.0")
    staff = await _staff(panel)
    r = await staff.put(
        f"/bots/{bot['id']}/current-version",
        json={"version_id": two["id"]},
        headers=csrf(staff),
    )
    assert r.status_code == 200 and r.json()["is_current"] is True
    detail = (await panel.admin_a.get(f"/bots/{bot['id']}")).json()
    assert detail["current_version"]["version"] == "1.1.0"
    switched = [
        m
        for m in await _audit(owner_factory, panel.tenant_a, "bot.version_activated")
        if m["version_id"] == two["id"]
    ]
    assert switched and switched[0]["previous_version_id"] == one["id"]


async def test_completing_twice_is_harmless(panel: Panel, signing: SigningKey) -> None:
    bot = await _bot(panel)
    item = await _publish(panel, signing, bot, version="1.0.0")
    again = await _complete(await _staff(panel), bot["id"], item["id"])
    assert again.status_code == 200 and again.json()["id"] == item["id"]


# --- who may do it ----------------------------------------------------------------------------


async def test_only_the_staff_publishes_or_switches(panel: Panel, signing: SigningKey) -> None:
    bot = await _bot(panel)
    item = await _publish(panel, signing, bot, version="1.0.0")
    pkg = make_package(
        signing, tenant_id=panel.tenant_a, package_name=bot["package_name"], version="9.0.0"
    )
    for client in (panel.admin_a, panel.operator_a, panel.viewer_a, panel.admin_b):
        assert (await _start(client, bot["id"], pkg)).status_code == 403
        assert (await _complete(client, bot["id"], item["id"])).status_code == 403
        r = await client.put(
            f"/bots/{bot['id']}/current-version",
            json={"version_id": item["id"]},
            headers=csrf(client),
        )
        assert r.status_code == 403


async def test_a_client_cannot_see_the_versions_of_another_client(
    panel: Panel, signing: SigningKey
) -> None:
    bot = await _bot(panel)
    await _publish(panel, signing, bot, version="1.0.0")
    assert (await panel.admin_b.get(f"/bots/{bot['id']}/versions")).status_code == 404
    staff = await _staff(panel, panel.tenant_b)  # the staff, but inside client B
    assert (await staff.get(f"/bots/{bot['id']}/versions")).status_code == 404


async def test_the_staff_needs_a_client_context_to_write(panel: Panel, signing: SigningKey) -> None:
    bot = await _bot(panel)
    pkg = make_package(
        signing, tenant_id=panel.tenant_a, package_name=bot["package_name"], version="1.0.0"
    )
    r = await panel.staff.put("/auth/context", json={"client_id": None}, headers=csrf(panel.staff))
    assert r.status_code == 200, r.text
    assert (await _start(panel.staff, bot["id"], pkg)).status_code == 409


async def test_a_version_of_another_bot_cannot_be_put_in_use(
    panel: Panel, signing: SigningKey
) -> None:
    one, other = await _bot(panel), await _bot(panel)
    foreign = await _publish(panel, signing, other, version="1.0.0")
    staff = await _staff(panel)
    r = await staff.put(
        f"/bots/{one['id']}/current-version",
        json={"version_id": foreign["id"]},
        headers=csrf(staff),
    )
    assert r.status_code == 404 and r.json()["detail"]["code"] == "version_not_found"
    assert (await panel.admin_a.get(f"/bots/{one['id']}")).json()["current_version"] is None


async def test_a_version_of_another_client_cannot_be_put_in_use(
    panel: Panel, signing: SigningKey
) -> None:
    mine = await _bot(panel)
    theirs = await make_bot(panel, panel.tenant_b, panel.admin_b)
    staff_b = await panel.staff_in(panel.tenant_b)
    pkg = make_package(
        signing, tenant_id=panel.tenant_b, package_name=theirs["package_name"], version="1.0.0"
    )
    ticket = (await _start(staff_b, theirs["id"], pkg)).json()
    await _put(ticket, pkg.package)
    published = (await _complete(staff_b, theirs["id"], ticket["version_id"])).json()
    staff_a = await panel.staff_in(panel.tenant_a)
    r = await staff_a.put(
        f"/bots/{mine['id']}/current-version",
        json={"version_id": published["id"]},
        headers=csrf(staff_a),
    )
    assert r.status_code == 404


# --- the signature ----------------------------------------------------------------------------


async def _code(response: httpx.Response) -> str:
    return str(response.json()["detail"]["code"])


async def test_a_signature_from_a_key_nobody_trusts_is_refused(
    panel: Panel, signing: SigningKey
) -> None:
    bot = await _bot(panel)
    stranger = Ed25519PrivateKey.generate()
    forged = make_package(
        SigningKey(stranger, "f" * 16, signing.keys_file),
        tenant_id=panel.tenant_a,
        package_name=bot["package_name"],
    )
    r = await _start(await _staff(panel), bot["id"], forged)
    assert r.status_code == 422 and await _code(r) == "signature_invalid"


async def test_a_forged_signature_under_a_trusted_key_id_is_refused(
    panel: Panel, signing: SigningKey
) -> None:
    bot = await _bot(panel)
    forged = make_package(
        signing,
        tenant_id=panel.tenant_a,
        package_name=bot["package_name"],
        signer=Ed25519PrivateKey.generate(),  # claims the trusted key's id
    )
    r = await _start(await _staff(panel), bot["id"], forged)
    assert r.status_code == 422 and await _code(r) == "signature_invalid"


async def test_a_changed_signature_document_is_refused(panel: Panel, signing: SigningKey) -> None:
    bot = await _bot(panel)
    pkg = make_package(signing, tenant_id=panel.tenant_a, package_name=bot["package_name"])
    doc = json.loads(pkg.signature)
    doc["manifest"]["version"] = "9.9.9"
    tampered = SignedPackage(pkg.package, json.dumps(doc), pkg.manifest, pkg.sha256)
    r = await _start(await _staff(panel), bot["id"], tampered)
    assert r.status_code == 422 and await _code(r) == "signature_invalid"


@pytest.mark.parametrize("document", ["{}", "not json at all", "[]", '{"format": "x"}'])
async def test_a_signature_document_that_is_not_one_is_malformed(
    panel: Panel, signing: SigningKey, document: str
) -> None:
    bot = await _bot(panel)
    staff = await _staff(panel)
    r = await staff.post(
        f"/bots/{bot['id']}/versions/uploads",
        json={"signature": document},
        headers=csrf(staff),
    )
    assert r.status_code == 422 and await _code(r) == "signature_malformed"


async def test_a_package_for_another_client_is_refused(panel: Panel, signing: SigningKey) -> None:
    bot = await _bot(panel)
    pkg = make_package(signing, tenant_id=panel.tenant_b, package_name=bot["package_name"])
    r = await _start(await _staff(panel), bot["id"], pkg)
    assert r.status_code == 422 and await _code(r) == "package_wrong_client"


async def test_a_package_for_another_bot_is_refused(panel: Panel, signing: SigningKey) -> None:
    bot = await _bot(panel)
    pkg = make_package(signing, tenant_id=panel.tenant_a, package_name="some_other_robot")
    r = await _start(await _staff(panel), bot["id"], pkg)
    assert r.status_code == 422 and await _code(r) == "package_wrong_bot"
    assert r.json()["detail"]["package_name"] == bot["package_name"]


async def test_a_package_over_the_size_limit_is_refused(panel: Panel, signing: SigningKey) -> None:
    bot = await _bot(panel)
    pkg = make_package(
        signing,
        tenant_id=panel.tenant_a,
        package_name=bot["package_name"],
        claimed_size=300 * 1024 * 1024,
    )
    r = await _start(await _staff(panel), bot["id"], pkg)
    assert r.status_code == 422 and await _code(r) == "package_too_large"


async def test_a_version_number_can_be_published_once(panel: Panel, signing: SigningKey) -> None:
    bot = await _bot(panel)
    await _publish(panel, signing, bot, version="1.0.0")
    pkg = make_package(
        signing, tenant_id=panel.tenant_a, package_name=bot["package_name"], version="1.0.0"
    )
    r = await _start(await _staff(panel), bot["id"], pkg)
    assert r.status_code == 409 and await _code(r) == "version_exists"


async def test_an_upload_that_expired_does_not_hold_the_number(
    panel: Panel, signing: SigningKey, owner_factory: Factory
) -> None:
    bot = await _bot(panel)
    staff = await _staff(panel)
    pkg = make_package(
        signing, tenant_id=panel.tenant_a, package_name=bot["package_name"], version="1.0.0"
    )
    first = (await _start(staff, bot["id"], pkg)).json()
    # Still open: a second start for the same number is refused.
    assert (await _start(staff, bot["id"], pkg)).status_code == 409
    async with tenant_session(owner_factory, tenant_id=panel.tenant_a) as db:
        await db.execute(
            text("UPDATE bot_versions SET upload_expires_at = :t WHERE id = :v"),
            {"t": datetime.now(UTC) - timedelta(minutes=1), "v": uuid.UUID(first["version_id"])},
        )
    assert (await _complete(staff, bot["id"], first["version_id"])).status_code == 409
    second = await _start(staff, bot["id"], pkg)
    assert second.status_code == 201 and second.json()["version_id"] != first["version_id"]


# --- what was uploaded ------------------------------------------------------------------------


async def test_completing_without_uploading_is_refused(panel: Panel, signing: SigningKey) -> None:
    bot = await _bot(panel)
    staff = await _staff(panel)
    pkg = make_package(signing, tenant_id=panel.tenant_a, package_name=bot["package_name"])
    ticket = (await _start(staff, bot["id"], pkg)).json()
    r = await _complete(staff, bot["id"], ticket["version_id"])
    assert r.status_code == 409 and await _code(r) == "upload_missing"
    listed = (await panel.admin_a.get(f"/bots/{bot['id']}/versions")).json()
    assert listed["items"] == []


async def test_a_file_that_is_not_what_was_signed_never_becomes_a_version(
    panel: Panel, signing: SigningKey, s3_env: S3Env, owner_factory: Factory
) -> None:
    """Same size, other bytes: the signature is fine, the hash is not."""
    bot = await _bot(panel)
    staff = await _staff(panel)
    pkg = make_package(signing, tenant_id=panel.tenant_a, package_name=bot["package_name"])
    ticket = (await _start(staff, bot["id"], pkg)).json()
    swapped = bytearray(pkg.package)
    swapped[len(swapped) // 2] ^= 0xFF
    await _put(ticket, bytes(swapped))

    r = await _complete(staff, bot["id"], ticket["version_id"])
    assert r.status_code == 422 and await _code(r) == "package_hash_mismatch"
    listed = (await panel.admin_a.get(f"/bots/{bot['id']}/versions")).json()
    assert listed["items"] == []
    async with tenant_session(owner_factory, tenant_id=panel.tenant_a) as db:
        status: str = (
            await db.execute(
                text("SELECT status FROM bot_versions WHERE id = :v"),
                {"v": uuid.UUID(ticket["version_id"])},
            )
        ).scalar_one()
        key: str = (
            await db.execute(
                text("SELECT storage_key FROM bot_versions WHERE id = :v"),
                {"v": uuid.UUID(ticket["version_id"])},
            )
        ).scalar_one()
    assert status == "expired"
    bucket = panel.env.app.state.settings.s3_bucket
    with pytest.raises(Exception, match=r"404|NoSuchKey|Not Found"):
        _s3(s3_env).head_object(Bucket=bucket, Key=key)  # the bad object was deleted
    # And the number is free again.
    assert (await _start(staff, bot["id"], pkg)).status_code == 201


async def test_an_object_of_another_size_is_refused(
    panel: Panel, signing: SigningKey, s3_env: S3Env, owner_factory: Factory
) -> None:
    bot = await _bot(panel)
    staff = await _staff(panel)
    pkg = make_package(signing, tenant_id=panel.tenant_a, package_name=bot["package_name"])
    ticket = (await _start(staff, bot["id"], pkg)).json()
    async with tenant_session(owner_factory, tenant_id=panel.tenant_a) as db:
        key: str = (
            await db.execute(
                text("SELECT storage_key FROM bot_versions WHERE id = :v"),
                {"v": uuid.UUID(ticket["version_id"])},
            )
        ).scalar_one()
    # Someone with bucket access (not the pre-signed URL, which pins the size) puts other bytes.
    _s3(s3_env).put_object(
        Bucket=panel.env.app.state.settings.s3_bucket,
        Key=key,
        Body=pkg.package + b"extra",
        ContentType="application/octet-stream",
    )
    r = await _complete(staff, bot["id"], ticket["version_id"])
    assert r.status_code == 422 and await _code(r) == "package_hash_mismatch"


async def test_a_signed_package_with_a_hostile_zip_is_refused(
    panel: Panel, signing: SigningKey
) -> None:
    """Right signature, right hash, but a member that would write outside its folder."""
    bot = await _bot(panel)
    staff = await _staff(panel)
    pkg = make_package(
        signing,
        tenant_id=panel.tenant_a,
        package_name=bot["package_name"],
        members={"../../evil.py": b"boom"},
    )
    ticket = (await _start(staff, bot["id"], pkg)).json()
    await _put(ticket, pkg.package)
    r = await _complete(staff, bot["id"], ticket["version_id"])
    assert r.status_code == 422 and await _code(r) == "package_invalid"
    assert (await panel.admin_a.get(f"/bots/{bot['id']}/versions")).json()["items"] == []


async def test_a_zip_whose_manifest_is_not_the_signed_one_is_refused(
    panel: Panel, signing: SigningKey
) -> None:
    bot = await _bot(panel)
    staff = await _staff(panel)
    lying = Manifest(
        tenant_id=str(panel.tenant_b),
        package_name=bot["package_name"],
        version="1.0.0",
        python="3.13.5",
    )
    pkg = make_package(
        signing,
        tenant_id=panel.tenant_a,
        package_name=bot["package_name"],
        version="1.0.0",
        inner=lying,
    )
    ticket = (await _start(staff, bot["id"], pkg)).json()
    await _put(ticket, pkg.package)
    r = await _complete(staff, bot["id"], ticket["version_id"])
    assert r.status_code == 422 and await _code(r) == "package_invalid"


async def test_a_version_of_a_missing_bot_or_unknown_id_is_not_found(
    panel: Panel, signing: SigningKey
) -> None:
    bot = await _bot(panel)
    staff = await _staff(panel)
    r = await _complete(staff, bot["id"], str(uuid.uuid4()))
    assert r.status_code == 404 and await _code(r) == "version_not_found"
    r = await _complete(staff, str(uuid.uuid4()), str(uuid.uuid4()))
    assert r.status_code == 404 and await _code(r) == "bot_not_found"


# --- the bucket and the browser ---------------------------------------------------------------


async def test_the_browser_of_the_panel_may_put_a_package_and_no_other_origin(
    panel: Panel, signing: SigningKey
) -> None:
    """CORS: the panel's origin gets its preflight answered for the pre-signed PUT."""
    bot = await _bot(panel)
    pkg = make_package(signing, tenant_id=panel.tenant_a, package_name=bot["package_name"])
    ticket = (await _start(await _staff(panel), bot["id"], pkg)).json()
    panel_origin = panel.env.app.state.settings.public_url.rstrip("/")

    def preflight(origin: str) -> httpx.Response:
        return httpx.options(
            ticket["upload_url"],
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "content-type",
            },
        )

    ok = preflight(panel_origin)
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == panel_origin
    assert "PUT" in ok.headers["access-control-allow-methods"]
    assert preflight("http://evil.example").status_code == 403


async def test_a_listing_is_a_json_object_with_nothing_internal(
    panel: Panel, signing: SigningKey
) -> None:
    bot = await _bot(panel)
    await _publish(panel, signing, bot, version="1.0.0")
    text_ = (await panel.admin_a.get(f"/bots/{bot['id']}/versions")).text
    assert "storage_key" not in text_ and "signature" not in text_ and "tenants/" not in text_
