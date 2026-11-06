"""Session authentication and credential helpers for the local MVP."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import sqlite3
from pathlib import Path
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send


def normalize_email(email: str) -> str:
    return email.strip().lower()


def valid_email(email: str) -> bool:
    if len(email) > 254 or email.count("@") != 1:
        return False
    local, domain = email.split("@", 1)
    return bool(local and domain and "." in domain and not any(ch.isspace() for ch in email))


def hash_password(password: str) -> str:
    import secrets

    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${derived.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_hex, digest_hex = encoded.split("$")
        if algorithm != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(digest_hex)),
        )
        return hmac.compare_digest(derived, bytes.fromhex(digest_hex))
    except (ValueError, TypeError, MemoryError):
        return False


def invitation_digest(code: str) -> str:
    return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()


def client_is_loopback(scope: Scope) -> bool:
    client = scope.get("client")
    if not client:
        return False
    try:
        return ipaddress.ip_address(client[0]).is_loopback
    except ValueError:
        return False


class AuthGateMiddleware:
    """Reject anonymous requests before protected bodies such as uploads are parsed."""

    PUBLIC_PATHS = {"/health", "/login", "/register", "/setup"}

    def __init__(self, app: ASGIApp, database_path: Path) -> None:
        self.app = app
        self.database_path = database_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self.PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        session: dict[str, Any] = scope.get("session", {})
        user_id = session.get("user_id")
        authenticated = False
        if user_id and self.database_path.exists():
            try:
                with sqlite3.connect(self.database_path, timeout=5) as connection:
                    authenticated = connection.execute(
                        "SELECT 1 FROM users WHERE id = ? AND is_active = 1", (user_id,)
                    ).fetchone() is not None
            except sqlite3.Error:
                authenticated = False

        if authenticated:
            await self.app(scope, receive, send)
            return

        session.clear()
        if scope.get("path", "").startswith("/api/"):
            body = b'{"detail":"Authentication required."}'
            await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return

        await send({"type": "http.response.start", "status": 303, "headers": [(b"location", b"/login"), (b"content-length", b"0")]})
        await send({"type": "http.response.body", "body": b""})
