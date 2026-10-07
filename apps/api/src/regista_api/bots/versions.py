"""Signed versions of a bot, for the panel (design-system.md 7.6, ADR 0021).

Publishing is Artemisys-only and in three steps, so the file never passes through the API:

1. `POST .../versions/uploads` takes the `.rgsig`, checks the signature against the trusted keys
   and that it is for this client and this bot, and answers with a short pre-signed PUT;
2. the browser sends the `.rgpkg` straight to the bucket;
3. `POST .../versions/{id}/complete` makes the server read the object back, recompute its sha256
   and size, and open the zip to check its inner manifest against the signed one. Only then is the
   version `published`. A package that is not what was signed is deleted and never published.

The server holds no private key: it can verify a signature, never produce one.
"""

import asyncio
import json
import tempfile
import uuid
from pathlib import Path
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from regista_api.audit import service as audit
from regista_api.auth.deps import Auth, Require
from regista_api.auth.permissions import Permission
from regista_api.bots.version_schemas import (
    CompleteUploadRequest,
    SetCurrentVersionRequest,
    StartUploadRequest,
    UploadTicket,
    VersionItem,
    VersionList,
)
from regista_api.core.config import Settings
from regista_api.core.errors import api_error
from regista_api.storage.s3 import S3Storage
from regista_pkg import PackageError, SignedManifest, parse_signature_doc, verify
from regista_pkg import archive as arch

log = structlog.get_logger()
router = APIRouter(tags=["bots"])

_VIEW = Depends(Require(Permission.BOTS_VIEW))
_MANAGE = Depends(Require(Permission.VERSIONS_MANAGE))
PACKAGE_CONTENT_TYPE = "application/octet-stream"
PUBLISHED_BY = "Equipe Artemisys"

_LIST = text(
    "SELECT v.id, v.version, v.package_sha256, v.size_bytes, v.release_note, v.published_at,"
    " v.manifest, (b.current_version_id = v.id) AS is_current"
    " FROM bot_versions v JOIN bots b ON b.tenant_id = v.tenant_id AND b.id = v.bot_id"
    " WHERE v.bot_id = :b AND v.status = 'published'"
    " ORDER BY v.published_at DESC, v.id DESC"
)


def _storage(request: Request) -> S3Storage:
    storage: S3Storage = request.app.state.storage
    return storage


def _item(row: Any) -> VersionItem:
    manifest: dict[str, Any] = row.manifest
    return VersionItem(
        id=row.id,
        version=row.version,
        package_sha256=row.package_sha256,
        size_bytes=row.size_bytes,
        release_note=row.release_note,
        published_at=row.published_at,
        published_by=PUBLISHED_BY,
        is_current=bool(row.is_current),
        python=manifest["python"],
        playwright=manifest.get("playwright"),
        chromium_revision=manifest.get("chromium_revision"),
    )


async def _load_bot(db: AsyncSession, bot_id: uuid.UUID) -> Any:
    bot = (
        await db.execute(
            text(
                "SELECT b.id, b.tenant_id, b.package_name, b.current_version_id FROM bots b"
                " JOIN tenants t ON t.id = b.tenant_id WHERE b.id = :b AND NOT t.is_internal"
            ),
            {"b": bot_id},
        )
    ).first()
    if bot is None:
        raise api_error(404, "bot_not_found")
    return bot


def _check_signature(
    document: str, bot: Any, client_id: uuid.UUID, settings: Settings
) -> tuple[SignedManifest, bytes]:
    """The signature must be valid, for this client and this bot, and not too big."""
    try:
        signed, signature = parse_signature_doc(document.encode("utf-8"))
        verify(signed, signature, settings.package_trusted_keys())
    except PackageError as exc:
        code = (
            "signature_invalid"
            if exc.reason in ("signature_invalid", "unknown_key")
            else ("signature_malformed")
        )
        raise api_error(422, code) from None
    if signed.manifest.tenant_id != str(client_id):
        raise api_error(422, "package_wrong_client")
    if signed.manifest.package_name != bot.package_name:
        raise api_error(422, "package_wrong_bot", package_name=bot.package_name)
    if signed.size > settings.package_max_bytes:
        raise api_error(422, "package_too_large", max_bytes=settings.package_max_bytes)
    return signed, signature


# --- reading ----------------------------------------------------------------------------------


@router.get("/bots/{bot_id}/versions", response_model=VersionList)
async def list_versions(bot_id: uuid.UUID, auth: Annotated[Auth, _VIEW]) -> VersionList:
    async with auth.scoped() as db:
        bot = await _load_bot(db, bot_id)
        rows = (await db.execute(_LIST, {"b": bot_id})).all()
    return VersionList(items=[_item(r) for r in rows], current_version_id=bot.current_version_id)


# --- publishing -------------------------------------------------------------------------------


@router.post("/bots/{bot_id}/versions/uploads", response_model=UploadTicket, status_code=201)
async def start_upload(
    bot_id: uuid.UUID,
    body: StartUploadRequest,
    request: Request,
    auth: Annotated[Auth, _MANAGE],
) -> UploadTicket:
    settings = auth.state.settings
    note = (body.release_note or "").strip() or None
    version_label = ""
    try:
        async with auth.writing() as db:
            bot = await _load_bot(db, bot_id)
            signed, signature = _check_signature(body.signature, bot, auth.client_id, settings)
            version_label = signed.manifest.version
            # A new attempt replaces an earlier one that never finished (a failed upload, a closed
            # dialog): the abandoned one cannot be completed any more, and never holds the number.
            await db.execute(
                text(
                    "UPDATE bot_versions SET status = 'expired' WHERE bot_id = :b AND version = :v"
                    " AND status = 'uploading'"
                ),
                {"b": bot_id, "v": signed.manifest.version},
            )
            version_id: uuid.UUID = (await db.execute(text("SELECT uuidv7()"))).scalar_one()
            key = f"tenants/{auth.client_id}/bots/{bot_id}/versions/{version_id}.rgpkg"
            await db.execute(
                text(
                    "INSERT INTO bot_versions (id, tenant_id, bot_id, version, package_sha256,"
                    " size_bytes, signature, key_id, manifest, storage_key, upload_expires_at,"
                    " release_note, created_by) VALUES (:id, :t, :b, :v, :sha, :size, :sig, :kid,"
                    " CAST(:m AS jsonb), :key, now() + make_interval(secs => :ttl), :note, :u)"
                ),
                {
                    "id": version_id,
                    "t": auth.client_id,
                    "b": bot_id,
                    "v": signed.manifest.version,
                    "sha": signed.sha256,
                    "size": signed.size,
                    "sig": signature,
                    "kid": signed.key_id,
                    "m": _json(signed.to_dict()),
                    "key": key,
                    "ttl": settings.package_upload_seconds,
                    "note": note,
                    "u": auth.user.id,
                },
            )
    except IntegrityError as exc:
        if "uq_bot_versions_bot_version" in str(exc.orig):
            raise api_error(409, "version_exists", version=version_label) from None
        raise
    url = _storage(request).presign_put(
        key,
        content_type=PACKAGE_CONTENT_TYPE,
        size=signed.size,
        expires=settings.package_upload_seconds,
    )
    return UploadTicket(
        version_id=version_id,
        version=signed.manifest.version,
        upload_url=url,
        upload_headers={"Content-Type": PACKAGE_CONTENT_TYPE},
        expires_in=settings.package_upload_seconds,
    )


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True)


def _check_zip(path: Path, signed: SignedManifest) -> None:
    """The zip is safe to open and its own manifest is the one that was signed."""
    with arch.open_package(path) as zf:
        arch.validate_members(zf)
        arch.require_layout(zf)
        if arch.read_inner_manifest(zf) != signed.manifest:
            raise PackageError("malformed_package", "manifest.json differs from the signed one")


async def _reject(
    auth: Auth, storage: S3Storage, version_id: uuid.UUID, key: str, code: str
) -> HTTPException:
    """The upload is not what was signed: it never becomes a version, and the object goes."""
    async with auth.writing() as db:
        await db.execute(
            text(
                "UPDATE bot_versions SET status = 'expired' WHERE id = :v AND status = 'uploading'"
            ),
            {"v": version_id},
        )
    try:
        await storage.delete(key)
    except Exception as exc:
        log.warning("package_object_not_deleted", key=key, error=type(exc).__name__)
    return api_error(422, code)


@router.post("/bots/{bot_id}/versions/{version_id}/complete", response_model=VersionItem)
async def complete_upload(
    bot_id: uuid.UUID,
    version_id: uuid.UUID,
    body: CompleteUploadRequest,
    request: Request,
    auth: Annotated[Auth, _MANAGE],
) -> VersionItem:
    storage = _storage(request)
    async with auth.writing() as db:
        await _load_bot(db, bot_id)
        row = (
            await db.execute(
                text(
                    "SELECT status, storage_key, package_sha256, size_bytes, manifest,"
                    " upload_expires_at < now() AS expired FROM bot_versions"
                    " WHERE id = :v AND bot_id = :b"
                ),
                {"v": version_id, "b": bot_id},
            )
        ).first()
        if row is None:
            raise api_error(404, "version_not_found")
        if row.status == "published":
            # Completing twice is harmless; asking to activate on the second call still works.
            if body.activate:
                await _activate(db, auth, bot_id, version_id)
            return await _load_item(db, bot_id, version_id)
        if row.status != "uploading":
            raise api_error(409, "upload_not_open")
        if row.expired:
            raise api_error(409, "upload_expired")

    stored = await storage.head(row.storage_key)
    if stored is None:
        raise api_error(409, "upload_missing")
    if stored.size != row.size_bytes:
        raise await _reject(auth, storage, version_id, row.storage_key, "package_hash_mismatch")

    signed = SignedManifest.from_dict(row.manifest)
    with tempfile.TemporaryDirectory(prefix="regista-version-") as folder:
        path = Path(folder) / "package.rgpkg"
        try:
            digest, size = await storage.download(row.storage_key, path, max_bytes=row.size_bytes)
        except ValueError:
            raise await _reject(
                auth, storage, version_id, row.storage_key, "package_hash_mismatch"
            ) from None
        if digest != row.package_sha256 or size != row.size_bytes:
            raise await _reject(auth, storage, version_id, row.storage_key, "package_hash_mismatch")
        try:
            await asyncio.to_thread(_check_zip, path, signed)
        except PackageError:
            raise await _reject(
                auth, storage, version_id, row.storage_key, "package_invalid"
            ) from None

    async with auth.writing() as db:
        published = (
            await db.execute(
                text(
                    "UPDATE bot_versions SET status = 'published', published_at = now()"
                    " WHERE id = :v AND bot_id = :b AND status = 'uploading' RETURNING version"
                ),
                {"v": version_id, "b": bot_id},
            )
        ).first()
        if published is None:
            raise api_error(409, "upload_not_open")
        await audit.record(
            db,
            tenant_id=auth.client_id,
            actor_type="user",
            actor_id=auth.user.id,
            action="bot.version_published",
            target_type="bot_version",
            target_id=version_id,
            metadata={
                "bot_id": str(bot_id),
                "version": published.version,
                "sha256": row.package_sha256,
                "key_id": signed.key_id,
            },
            ip=auth.ip,
        )
        if body.activate:
            await _activate(db, auth, bot_id, version_id)
        return await _load_item(db, bot_id, version_id)


async def _load_item(db: AsyncSession, bot_id: uuid.UUID, version_id: uuid.UUID) -> VersionItem:
    for row in (await db.execute(_LIST, {"b": bot_id})).all():
        if row.id == version_id:
            return _item(row)
    raise api_error(404, "version_not_found")


# --- the version in use -----------------------------------------------------------------------


async def _activate(db: AsyncSession, auth: Auth, bot_id: uuid.UUID, version_id: uuid.UUID) -> None:
    """Put a published version of this bot in use. The conditions are repeated in the write, and
    the database says no again (a composite foreign key and a trigger) if they are ever wrong."""
    previous = (
        await db.execute(text("SELECT current_version_id FROM bots WHERE id = :b"), {"b": bot_id})
    ).scalar_one()
    changed = (
        await db.execute(
            text(
                "UPDATE bots SET current_version_id = :v, updated_at = now()"
                " WHERE id = :b AND EXISTS (SELECT 1 FROM bot_versions v WHERE v.id = :v"
                " AND v.tenant_id = bots.tenant_id AND v.bot_id = bots.id"
                " AND v.status = 'published') RETURNING id"
            ),
            {"v": version_id, "b": bot_id},
        )
    ).first()
    if changed is None:
        raise api_error(404, "version_not_found")
    await audit.record(
        db,
        tenant_id=auth.client_id,
        actor_type="user",
        actor_id=auth.user.id,
        action="bot.version_activated",
        target_type="bot",
        target_id=bot_id,
        metadata={
            "version_id": str(version_id),
            "previous_version_id": None if previous is None else str(previous),
        },
        ip=auth.ip,
    )


@router.put("/bots/{bot_id}/current-version", response_model=VersionItem)
async def set_current_version(
    bot_id: uuid.UUID, body: SetCurrentVersionRequest, auth: Annotated[Auth, _MANAGE]
) -> VersionItem:
    async with auth.writing() as db:
        await _load_bot(db, bot_id)
        await _activate(db, auth, bot_id, body.version_id)
        return await _load_item(db, bot_id, body.version_id)
