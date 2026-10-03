"""Request body size limit for the routes that take input from outside (the agent routes).

Pydantic bounds every field, but only after the whole body was read. This ASGI middleware
refuses an oversized body while it streams in, whether or not a Content-Length was declared.
"""

from fastapi import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from regista_api.core.errors import api_error


def _too_large() -> HTTPException:
    return api_error(413, "payload_too_large")


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, prefix: str, max_bytes: int) -> None:
        self.app = app
        self.prefix = prefix
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(self.prefix):
            await self.app(scope, receive, send)
            return

        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.max_bytes:
            await self._refuse(send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # An HTTPException, not a bare error: FastAPI turns any other exception
                    # raised while it reads the body into a 400.
                    raise _too_large()
            return message

        await self.app(scope, limited_receive, send)

    @staticmethod
    async def _refuse(send: Send) -> None:
        body = b'{"detail":{"code":"payload_too_large"}}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
