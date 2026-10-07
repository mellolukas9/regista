"""A small HTTP server that speaks the agent's side of the protocol, for tests where the agent runs
as a real process or a real Windows service and cannot be handed a stand-in object.

It does not check signatures or tokens (the real server has its own tests); it hands out the
runs it is given, serves their packages and records everything the agent says."""

import json
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse


class FakeRegistaServer:
    def __init__(self, *, tenant_id: uuid.UUID, mode: str = "service") -> None:
        self.tenant_id = tenant_id
        self.mode = mode
        self.machine_id = uuid.uuid4()
        self.lock = threading.Lock()
        self.queue: list[dict[str, Any]] = []
        self.offers: dict[str, dict[str, Any]] = {}
        self.files: dict[str, bytes] = {}
        self.runtimes: list[dict[str, Any]] = []
        self.cancellations: set[str] = set()
        self.events: list[tuple[str, str, dict[str, Any]]] = []
        self.logs: dict[str, list[str]] = {}
        self.uploads: list[bytes] = []
        self.heartbeats: list[dict[str, Any]] = []
        self.enrolled_keys: list[str] = []
        self._art = 0
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: object) -> None:  # silence
                pass

            def _body(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    return dict(json.loads(raw)) if raw else {}
                except ValueError:
                    return {}

            def _json(self, body: Any, status: int = 200) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _empty(self, status: int) -> None:
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self) -> None:
                server.handle("GET", self, urlparse(self.path), {})

            def do_POST(self) -> None:
                server.handle("POST", self, urlparse(self.path), self._body())

            def do_PUT(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                data = self.rfile.read(length) if length else b""
                with server.lock:
                    server.uploads.append(data)
                self._empty(200)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    # --- what the test does -------------------------------------------------------------------

    def publish(self, version_id: str, offer: dict[str, Any], package: bytes) -> None:
        with self.lock:
            offer = {**offer, "download_url": f"{self.url}/storage/pkg/{version_id}"}
            self.offers[version_id] = offer
            self.files[version_id] = package

    def submit(self, version_id: str, package_name: str, version: str, **params: Any) -> str:
        job_id = str(uuid.uuid4())
        timeout = int(params.pop("timeout_seconds", 120))
        with self.lock:
            self.queue.append(
                {
                    "job_id": job_id,
                    "short_code": "exec-" + job_id[:6],
                    "package_name": package_name,
                    "params": params,
                    "timeout_seconds": timeout,
                    "bot_version_id": version_id,
                    "version": version,
                }
            )
        return job_id

    def result(self, job_id: str, timeout: float = 240.0) -> tuple[str, dict[str, Any]]:
        """('complete' | 'fail' | 'release', body) once the agent reports the end of the run."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                for action, job, body in self.events:
                    if job == job_id and action in ("complete", "fail", "release"):
                        return action, body
            time.sleep(0.2)
        raise TimeoutError(f"a execução {job_id} não terminou em {timeout:g} s")

    def wait_log(self, job_id: str, text: str, timeout: float = 120.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(text in line for line in self.log_lines(job_id)):
                return True
            time.sleep(0.2)
        return False

    def log_lines(self, job_id: str) -> list[str]:
        with self.lock:
            return list(self.logs.get(job_id, []))

    def wait_heartbeat(self, timeout: float = 90.0) -> bool:
        deadline = time.monotonic() + timeout
        count = len(self.heartbeats)
        while time.monotonic() < deadline:
            if len(self.heartbeats) > count:
                return True
            time.sleep(0.2)
        return False

    # --- the protocol -------------------------------------------------------------------------

    def handle(self, method: str, h: Any, url: Any, body: dict[str, Any]) -> Any:
        path = url.path
        if path == "/agent/enroll":
            with self.lock:
                self.enrolled_keys.append(str(body.get("public_key")))
            return h._json(
                {
                    "machine_id": str(self.machine_id),
                    "tenant_id": str(self.tenant_id),
                    "mode": self.mode,
                    "heartbeat_seconds": 2,
                }
            )
        if path == "/agent/challenge":
            return h._json({"nonce": "nonce-" + uuid.uuid4().hex})
        if path == "/agent/token":
            return h._json({"access_token": "rga1.test.token", "expires_in": 900})
        if path == "/agent/heartbeat":
            with self.lock:
                self.heartbeats.append(body)
                cancel = sorted(self.cancellations)
            return h._json({"heartbeat_seconds": 2, "mode": self.mode, "cancellations": cancel})
        if path == "/agent/jobs/next":
            wait = min(int(parse_qs(url.query).get("wait", ["0"])[0]), 2)
            deadline = time.monotonic() + wait
            while True:
                with self.lock:
                    job = self.queue.pop(0) if self.queue else None
                if job is not None:
                    return h._json(job)
                if time.monotonic() >= deadline:
                    return h._empty(204)
                time.sleep(0.2)
        match = re.fullmatch(r"/agent/jobs/([0-9a-f-]+)/(start|complete|fail|release)", path)
        if match:
            job_id, action = match.groups()
            with self.lock:
                self.events.append((action, job_id, body))
            if action == "start":
                return h._json({"status": "running", "cancel_requested": False})
            return h._json({"status": action})
        if path == "/agent/logs":
            with self.lock:
                lines = self.logs.setdefault(str(body.get("job_id")), [])
                lines.extend(str(line.get("message", "")) for line in body.get("lines", []))
            return h._json({"accepted": len(body.get("lines", [])), "truncated": False})
        match = re.fullmatch(r"/agent/packages/([\w-]+)", path)
        if match:
            offer = self.offers.get(match.group(1))
            return h._json(offer) if offer else h._json({"detail": {"code": "not_found"}}, 404)
        match = re.fullmatch(r"/storage/pkg/([\w-]+)", path)
        if match:
            data = self.files.get(match.group(1), b"")
            h.send_response(200)
            h.send_header("Content-Length", str(len(data)))
            h.end_headers()
            h.wfile.write(data)
            return None
        if path == "/agent/runtimes":
            return h._json({"runtimes": self.runtimes})
        if path == "/agent/artifacts/presign":
            with self.lock:
                self._art += 1
                n = self._art
            return h._json(
                {
                    "artifact_id": f"art-{n}",
                    "url": f"{self.url}/storage/art/{n}",
                    "headers": {"Content-Type": str(body.get("content_type", "image/png"))},
                }
            )
        if re.fullmatch(r"/agent/artifacts/[\w-]+/uploaded", path):
            return h._json({"status": "uploaded"})
        return h._json({"detail": {"code": "not_found", "path": path}}, 404)
