"""What the agent says to the server about runs (docs/specs/agent.md, protocol).

Every call is an outgoing HTTPS request. The answers are small and fixed; anything unexpected is
treated as "the server is not available" and retried by the caller, never as a reason to run or
keep running something.
"""

import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from regista_agent.errors import ServerUnavailable
from regista_agent.transport import AgentSession, error_code

# A little more than the longest the server holds a request (30 s).
POLL_TIMEOUT_MARGIN = 20.0


@dataclass(frozen=True)
class Assignment:
    job_id: str
    short_code: str
    package_name: str
    params: dict[str, Any]
    timeout_seconds: int


@dataclass(frozen=True)
class Ack:
    status: str
    cancel_requested: bool


@dataclass(frozen=True)
class LogsResult:
    accepted: int
    truncated: bool


class JobGone(Exception):
    """The server no longer considers this run ours (404, or 409 once it ended). Stop working on
    it; there is nothing more to report."""


class JobApi(Protocol):
    """All the job runner needs from the server, so tests can stand in for it."""

    def next_job(self, wait: int) -> Assignment | None: ...

    def start(self, job_id: str) -> Ack: ...

    def complete(self, job_id: str) -> None: ...

    def fail(self, job_id: str, error_code: str, message: str) -> None: ...

    def send_logs(self, job_id: str, lines: list[dict[str, Any]]) -> LogsResult: ...

    def presign(self, job_id: str, content_type: str, size: int) -> tuple[str, str, dict[str, str]]:
        """`(artifact_id, url, headers)`."""
        ...

    def upload(self, url: str, headers: dict[str, str], data: bytes) -> None: ...

    def confirm_upload(self, artifact_id: str) -> None: ...


class HttpJobApi:
    def __init__(self, session: AgentSession) -> None:
        self._session = session

    # --- runs ---------------------------------------------------------------------------------

    def next_job(self, wait: int) -> Assignment | None:
        response = self._session.request(
            "GET", f"/agent/jobs/next?wait={wait}", timeout=wait + POLL_TIMEOUT_MARGIN, attempts=1
        )
        if response.status_code == 204:
            return None
        if response.status_code != 200:
            raise ServerUnavailable(
                f"O servidor recusou o pedido de trabalho ({response.status_code})."
            )
        body = response.json()
        return Assignment(
            job_id=str(body["job_id"]),
            short_code=str(body["short_code"]),
            package_name=str(body["package_name"]),
            params=dict(body.get("params") or {}),
            timeout_seconds=int(body["timeout_seconds"]),
        )

    def _post(self, job_id: str, action: str, body: dict[str, Any]) -> httpx.Response:
        response = self._session.request("POST", f"/agent/jobs/{job_id}/{action}", json=body)
        if response.status_code in (404, 409):
            raise JobGone(f"{action}: {error_code(response)}")
        if response.status_code != 200:
            raise ServerUnavailable(f"O servidor recusou '{action}' ({response.status_code}).")
        return response

    def start(self, job_id: str) -> Ack:
        body = self._post(job_id, "start", {}).json()
        return Ack(status=str(body["status"]), cancel_requested=bool(body["cancel_requested"]))

    def complete(self, job_id: str) -> None:
        self._post(job_id, "complete", {})

    def fail(self, job_id: str, error_code: str, message: str) -> None:
        self._post(job_id, "fail", {"error_code": error_code, "message": message})

    # --- logs ---------------------------------------------------------------------------------

    def send_logs(self, job_id: str, lines: list[dict[str, Any]]) -> LogsResult:
        response = self._session.request(
            "POST", "/agent/logs", json={"job_id": job_id, "lines": lines}, attempts=3
        )
        if response.status_code in (404, 409):
            raise JobGone(f"logs: {error_code(response)}")
        if response.status_code != 200:
            # 503 `log_partition_missing` and friends: the batch is kept and sent again.
            raise ServerUnavailable(f"O servidor recusou os logs ({response.status_code}).")
        body = response.json()
        return LogsResult(accepted=int(body["accepted"]), truncated=bool(body["truncated"]))

    # --- screenshots --------------------------------------------------------------------------

    def presign(self, job_id: str, content_type: str, size: int) -> tuple[str, str, dict[str, str]]:
        response = self._session.request(
            "POST",
            "/agent/artifacts/presign",
            json={
                "job_id": job_id,
                "kind": "screenshot",
                "content_type": content_type,
                "size_bytes": size,
            },
        )
        if response.status_code != 200:
            raise ServerUnavailable(
                f"O servidor recusou a captura ({response.status_code}, {error_code(response)})."
            )
        body = response.json()
        return str(body["artifact_id"]), str(body["url"]), dict(body["headers"])

    def upload(self, url: str, headers: dict[str, str], data: bytes) -> None:
        """PUT straight to the storage. The URL is the credential, so no token goes with it."""
        response = self._session.http.client.put(url, content=data, headers=headers, timeout=60)
        if response.status_code not in (200, 204):
            raise ServerUnavailable(f"O armazenamento recusou a captura ({response.status_code}).")

    def confirm_upload(self, artifact_id: str) -> None:
        response = self._session.request(
            "POST", f"/agent/artifacts/{artifact_id}/uploaded", json={}
        )
        if response.status_code != 200:
            raise ServerUnavailable(
                f"O servidor não confirmou a captura ({response.status_code}, "
                f"{error_code(response)})."
            )


def dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)
