"""A stand-in for the server, for the job runner tests."""

import threading
from pathlib import Path
from typing import Any

from regista_agent.config import AgentSettings
from regista_agent.errors import MachineRevoked, ServerUnavailable
from regista_agent.jobapi import Ack, Assignment, JobGone, LogsResult, PackageOffer, RuntimeNeed

FIXTURE_BOTS = Path(__file__).parent / "fixtures" / "bots"


class FakeApi:
    """Implements `JobApi` in memory and records what the agent said."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.queue: list[Assignment | Exception | None] = []
        self.started: list[str] = []
        self.completed: list[str] = []
        self.released: list[str] = []
        self.failed: list[tuple[str, str, str]] = []
        self.failed_reasons: dict[str, str | None] = {}
        self.offers: dict[str, PackageOffer] = {}
        self.package_files: dict[str, bytes] = {}
        self.offer_error: Exception | None = None
        self.runtime_needs: list[RuntimeNeed] = []
        self.downloads: list[str] = []
        self.log_batches: list[list[dict[str, Any]]] = []
        self.uploads: list[tuple[str, dict[str, str], bytes]] = []
        self.presigned: list[tuple[str, str, int]] = []
        self.confirmed: list[str] = []
        self.cancel_on_start = False
        self.start_error: Exception | None = None
        self.log_errors: list[Exception] = []
        self.log_truncate_after: int | None = None
        self.upload_error: Exception | None = None

    # --- JobApi -------------------------------------------------------------------------------

    def next_job(self, wait: int) -> Assignment | None:
        with self.lock:
            item = self.queue.pop(0) if self.queue else None
        if isinstance(item, Exception):
            raise item
        return item

    def start(self, job_id: str) -> Ack:
        if self.start_error is not None:
            raise self.start_error
        self.started.append(job_id)
        return Ack(status="running", cancel_requested=self.cancel_on_start)

    def complete(self, job_id: str) -> None:
        self.completed.append(job_id)

    def release(self, job_id: str) -> None:
        self.released.append(job_id)

    def fail(self, job_id: str, error_code: str, message: str, reason: str | None = None) -> None:
        self.failed.append((job_id, error_code, message))
        self.failed_reasons[job_id] = reason

    def package_offer(self, version_id: str) -> PackageOffer:
        if self.offer_error is not None:
            raise self.offer_error
        return self.offers[version_id]

    def download_package(self, url: str, destination: Path, max_bytes: int) -> None:
        self.downloads.append(url)
        data = self.package_files[url]
        if len(data) > max_bytes:
            from regista_pkg import PackageError

            raise PackageError("too_large")
        destination.write_bytes(data)

    def runtimes(self) -> list[RuntimeNeed]:
        return list(self.runtime_needs)

    def send_logs(self, job_id: str, lines: list[dict[str, Any]]) -> LogsResult:
        with self.lock:
            if self.log_errors:
                raise self.log_errors.pop(0)
            self.log_batches.append(list(lines))
            total = sum(len(b) for b in self.log_batches)
        truncated = self.log_truncate_after is not None and total >= self.log_truncate_after
        return LogsResult(accepted=len(lines), truncated=truncated)

    def presign(self, job_id: str, content_type: str, size: int) -> tuple[str, str, dict[str, str]]:
        self.presigned.append((job_id, content_type, size))
        n = len(self.presigned)
        return f"art-{n}", f"http://storage.test/{n}", {"Content-Type": content_type}

    def upload(self, url: str, headers: dict[str, str], data: bytes) -> None:
        if self.upload_error is not None:
            raise self.upload_error
        self.uploads.append((url, headers, data))

    def confirm_upload(self, artifact_id: str) -> None:
        self.confirmed.append(artifact_id)

    # --- helpers ------------------------------------------------------------------------------

    @property
    def lines(self) -> list[dict[str, Any]]:
        return [line for batch in self.log_batches for line in batch]

    @property
    def messages(self) -> list[str]:
        return [line["message"] for line in self.lines]


def job(
    mode: str = "ok", *, package: str = "fake_bot", timeout: int = 30, **params: Any
) -> Assignment:
    return Assignment(
        job_id="00000000-0000-4000-8000-0000000000aa",
        short_code="exec-abc123",
        package_name=package,
        params={"mode": mode, **params},
        timeout_seconds=timeout,
    )


def dev_settings(**overrides: Any) -> AgentSettings:
    values: dict[str, Any] = {
        "environment": "dev",
        "dev_unsigned": True,
        "dev_bots_dir": FIXTURE_BOTS,
        "cancel_grace_seconds": 1,
        "log_flush_seconds": 0.1,
        "poll_wait_seconds": 0,
        "job_priority": "normal",
        **overrides,
    }
    return AgentSettings(**values)


__all__ = ["FakeApi", "JobGone", "MachineRevoked", "ServerUnavailable", "dev_settings", "job"]
