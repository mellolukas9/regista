"""What the agent asks of the server about packages (docs/specs/agent.md, ADR 0021).

The server never sends a command or a file of its own making: it names the package of a run the
machine already holds, and hands out a short pre-signed address to download it. Whether that
package is acceptable is for the agent to decide, with the keys it carries (hash, signature,
client, package, version). A machine can only ask for a version that is on one of its own active
runs: any other id, including one of another client, is simply not found.
"""

import base64
import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import text

from regista_api.auth.machine import MachineAuth, MachineRoute
from regista_api.core.errors import api_error
from regista_api.storage.s3 import S3Storage
from regista_pkg import SIGNATURE_FORMAT

router = APIRouter(prefix="/agent", tags=["agent"])


class PackageInfo(BaseModel):
    version_id: uuid.UUID
    version: str
    package_name: str
    sha256: str
    size_bytes: int
    key_id: str
    # The `.rgsig` document, exactly as `regista-pack` wrote it: the agent verifies it itself.
    signature_doc: str
    download_url: str
    expires_in: int


class Runtime(BaseModel):
    package_name: str
    version: str
    python: str
    playwright: str | None
    chromium_revision: str | None


class RuntimeList(BaseModel):
    runtimes: list[Runtime]


_PACKAGE = text(
    "SELECT v.id, v.version, v.package_sha256, v.size_bytes, v.key_id, v.signature, v.manifest,"
    " v.storage_key, b.package_name FROM bot_versions v"
    " JOIN bots b ON b.tenant_id = v.tenant_id AND b.id = v.bot_id"
    " WHERE v.id = :v AND v.status = 'published' AND EXISTS ("
    "  SELECT 1 FROM jobs j WHERE j.tenant_id = v.tenant_id AND j.machine_id = :m"
    "  AND j.bot_version_id = v.id AND j.status IN ('assigned', 'running'))"
)
_RUNTIMES = text(
    "SELECT b.package_name, v.version, v.manifest FROM bots b"
    " JOIN bot_versions v ON v.tenant_id = b.tenant_id AND v.bot_id = b.id"
    "  AND v.id = b.current_version_id"
    " JOIN machines m ON m.tenant_id = b.tenant_id AND m.pool_id = b.pool_id"
    " WHERE m.id = :m AND b.is_active ORDER BY b.package_name"
)


@router.get("/packages/{bot_version_id}", response_model=PackageInfo)
async def get_package(
    bot_version_id: uuid.UUID,
    request: Request,
    machine: Annotated[MachineAuth, Depends(MachineRoute())],
) -> PackageInfo:
    settings = machine.state.settings
    async with machine.session() as db:
        row = (await db.execute(_PACKAGE, {"v": bot_version_id, "m": machine.machine_id})).first()
    if row is None:
        raise api_error(404, "package_not_found")
    storage: S3Storage = request.app.state.storage
    document = json.dumps(
        {
            "format": SIGNATURE_FORMAT,
            "manifest": row.manifest,
            "signature": base64.b64encode(bytes(row.signature)).decode("ascii"),
        },
        sort_keys=True,
    )
    return PackageInfo(
        version_id=row.id,
        version=row.version,
        package_name=row.package_name,
        sha256=row.package_sha256,
        size_bytes=row.size_bytes,
        key_id=row.key_id,
        signature_doc=document,
        download_url=storage.presign_get_download(
            row.storage_key, expires=settings.package_download_seconds
        ),
        expires_in=settings.package_download_seconds,
    )


@router.get("/runtimes", response_model=RuntimeList)
async def get_runtimes(
    machine: Annotated[MachineAuth, Depends(MachineRoute())],
) -> RuntimeList:
    """The runtime (Python, Playwright, Chromium) of the version in use of each bot of this
    machine's pool: what `regista-agent setup --from-server` prepares and `diagnose` checks."""
    async with machine.session() as db:
        rows = (await db.execute(_RUNTIMES, {"m": machine.machine_id})).all()
    runtimes: list[Runtime] = []
    for r in rows:
        manifest: dict[str, Any] = r.manifest
        runtimes.append(
            Runtime(
                package_name=r.package_name,
                version=r.version,
                python=manifest["python"],
                playwright=manifest.get("playwright"),
                chromium_revision=manifest.get("chromium_revision"),
            )
        )
    return RuntimeList(runtimes=runtimes)
