"""Screenshots of a run (docs/specs/agent.md, docs/adr/0019).

The agent asks for a pre-signed PUT, uploads straight to the bucket and then confirms; the server
looks at the object itself before it trusts the confirmation. The panel never gets a permanent
address: `GET /artifacts/{id}/content` checks the session and the client, then redirects to a GET
that is valid for about a minute.

Everything the agent says is untrusted: a fixed schema, a closed list of types, a size limit, and
the object key is built by the server from ids it already knows.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from regista_api.audit import service as audit
from regista_api.auth.deps import Auth, Require
from regista_api.auth.machine import MachineAuth, MachineRoute
from regista_api.auth.permissions import Permission
from regista_api.core.errors import api_error
from regista_api.jobs.logs import GRACE
from regista_api.storage.s3 import S3Storage, object_key

agent_router = APIRouter(prefix="/agent", tags=["agent"])
router = APIRouter(tags=["jobs"])

ContentType = Literal["image/png", "image/jpeg"]
_VIEW = Depends(Require(Permission.JOBS_VIEW))


def _storage(request: Request) -> S3Storage:
    storage: S3Storage = request.app.state.storage
    return storage


class PresignRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "job_id": "00000000-0000-4000-8000-000000000000",
                    "kind": "screenshot",
                    "content_type": "image/png",
                    "size_bytes": 1024,
                }
            ]
        },
    )
    job_id: uuid.UUID
    kind: Literal["screenshot"]
    content_type: ContentType
    size_bytes: Annotated[int, Field(ge=1, le=50_000_000)]


class PresignResponse(BaseModel):
    artifact_id: uuid.UUID
    url: str
    # Headers the PUT must carry exactly as given (they are part of the signature).
    headers: dict[str, str]
    expires_in: int


class UploadedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{}]})


class UploadedResponse(BaseModel):
    artifact_id: uuid.UUID
    size_bytes: int


class ArtifactItem(BaseModel):
    id: uuid.UUID
    kind: str
    content_type: str
    size_bytes: int
    created_at: datetime
    uploaded_at: datetime


class ArtifactList(BaseModel):
    items: list[ArtifactItem]


# --- the agent --------------------------------------------------------------------------------


@agent_router.post("/artifacts/presign", response_model=PresignResponse)
async def presign(
    body: PresignRequest,
    request: Request,
    machine: Annotated[MachineAuth, Depends(MachineRoute())],
) -> PresignResponse:
    settings = machine.state.settings
    if body.size_bytes > settings.artifact_max_bytes:
        raise api_error(422, "artifact_too_large", max_bytes=settings.artifact_max_bytes)
    async with machine.session() as db:
        job = (
            await db.execute(
                text("SELECT finished_at FROM jobs WHERE id = :j AND machine_id = :m FOR SHARE"),
                {"j": body.job_id, "m": machine.machine_id},
            )
        ).first()
        if job is None:
            raise api_error(404, "job_not_found")
        if job.finished_at is not None and datetime.now(UTC) - job.finished_at > GRACE:
            raise api_error(409, "job_closed")
        count: int = (
            await db.execute(
                text("SELECT count(*) FROM artifacts WHERE job_id = :j"), {"j": body.job_id}
            )
        ).scalar_one()
        if count >= settings.artifact_max_per_job:
            raise api_error(409, "artifact_limit", max=settings.artifact_max_per_job)
        # The id is needed for the key before the row exists.
        artifact_id: uuid.UUID = (await db.execute(text("SELECT uuidv7()"))).scalar_one()
        key = object_key(machine.tenant_id, body.job_id, artifact_id, body.content_type)
        await db.execute(
            text(
                "INSERT INTO artifacts (id, tenant_id, job_id, kind, storage_key, content_type,"
                " size_bytes) VALUES (:id, :t, :j, :k, :key, :ct, :size)"
            ),
            {
                "id": artifact_id,
                "t": machine.tenant_id,
                "j": body.job_id,
                "k": body.kind,
                "key": key,
                "ct": body.content_type,
                "size": body.size_bytes,
            },
        )
    url = _storage(request).presign_put(
        key,
        content_type=body.content_type,
        size=body.size_bytes,
        expires=settings.artifact_upload_seconds,
    )
    return PresignResponse(
        artifact_id=artifact_id,
        url=url,
        headers={"Content-Type": body.content_type},
        expires_in=settings.artifact_upload_seconds,
    )


@agent_router.post("/artifacts/{artifact_id}/uploaded", response_model=UploadedResponse)
async def uploaded(
    artifact_id: uuid.UUID,
    body: UploadedRequest,
    request: Request,
    machine: Annotated[MachineAuth, Depends(MachineRoute())],
) -> UploadedResponse:
    """The agent says the PUT is done. The server does not take its word: it looks at the object
    and only then marks the artifact as uploaded."""
    async with machine.session() as db:
        art = (
            await db.execute(
                text(
                    "SELECT a.storage_key, a.content_type, a.size_bytes, a.uploaded_at,"
                    " j.finished_at FROM artifacts a JOIN jobs j ON j.tenant_id = a.tenant_id"
                    " AND j.id = a.job_id WHERE a.id = :a AND j.machine_id = :m"
                ),
                {"a": artifact_id, "m": machine.machine_id},
            )
        ).first()
        if art is None:
            raise api_error(404, "artifact_not_found")
        if art.uploaded_at is not None:
            return UploadedResponse(artifact_id=artifact_id, size_bytes=art.size_bytes)
        if art.finished_at is not None and datetime.now(UTC) - art.finished_at > GRACE:
            raise api_error(409, "job_closed")

        stored = await _storage(request).head(art.storage_key)
        if stored is None:
            raise api_error(409, "upload_not_found")
        if stored.size != art.size_bytes or stored.content_type != art.content_type:
            # Not what was announced: it stays unusable (never marked), and goes away.
            await _storage(request).delete(art.storage_key)
            raise api_error(409, "upload_mismatch")
        await db.execute(
            text("UPDATE artifacts SET uploaded_at = now() WHERE id = :a"), {"a": artifact_id}
        )
        await audit.record(
            db,
            tenant_id=machine.tenant_id,
            actor_type="machine",
            actor_id=machine.machine_id,
            action="artifact.uploaded",
            target_type="artifact",
            target_id=artifact_id,
            metadata={"size_bytes": art.size_bytes},
            ip=machine.ip,
        )
    return UploadedResponse(artifact_id=artifact_id, size_bytes=art.size_bytes)


# --- the panel --------------------------------------------------------------------------------


@router.get("/jobs/{job_id}/artifacts", response_model=ArtifactList)
async def list_artifacts(job_id: uuid.UUID, auth: Annotated[Auth, _VIEW]) -> ArtifactList:
    async with auth.scoped() as db:
        job = (
            await db.execute(
                text(
                    "SELECT j.tenant_id FROM jobs j JOIN tenants t ON t.id = j.tenant_id"
                    " WHERE j.id = :j AND NOT t.is_internal"
                ),
                {"j": job_id},
            )
        ).first()
        if job is None:
            raise api_error(404, "job_not_found")
        rows = (
            await db.execute(
                text(
                    "SELECT id, kind, content_type, size_bytes, created_at, uploaded_at"
                    " FROM artifacts WHERE tenant_id = :t AND job_id = :j"
                    " AND uploaded_at IS NOT NULL ORDER BY created_at, id"
                ),
                {"t": job.tenant_id, "j": job_id},
            )
        ).all()
    return ArtifactList(
        items=[
            ArtifactItem(
                id=r.id,
                kind=r.kind,
                content_type=r.content_type,
                size_bytes=r.size_bytes,
                created_at=r.created_at,
                uploaded_at=r.uploaded_at,
            )
            for r in rows
        ]
    )


@router.get("/artifacts/{artifact_id}/content", response_class=RedirectResponse, status_code=302)
async def artifact_content(
    artifact_id: uuid.UUID, request: Request, auth: Annotated[Auth, _VIEW]
) -> RedirectResponse:
    """Checks who is asking, then sends the browser to a GET valid for about a minute. The bucket
    is private and no permanent address is ever shown."""
    async with auth.scoped() as db:
        art = (
            await db.execute(
                text(
                    "SELECT a.storage_key, a.content_type FROM artifacts a"
                    " JOIN tenants t ON t.id = a.tenant_id"
                    " WHERE a.id = :a AND a.uploaded_at IS NOT NULL AND NOT t.is_internal"
                ),
                {"a": artifact_id},
            )
        ).first()
    if art is None:
        raise api_error(404, "artifact_not_found")
    url = _storage(request).presign_get(
        art.storage_key,
        content_type=art.content_type,
        expires=auth.state.settings.artifact_view_seconds,
    )
    # The redirect target is a secret for a minute: never cached, never kept by a proxy.
    return RedirectResponse(url, status_code=302, headers={"Cache-Control": "no-store"})
