"""API security: fail-closed auth, bind guard, error redaction, headers.

Closes:
* A12 — dashboard mutations fail OPEN. Every mutation now requires a valid
  token; any failure path denies.
* A13 — raw exception disclosure. Clients get a generic message + correlation
  id; details go only to structured logs.
* A09 — arbitrary AI URL editing. There is no endpoint to change model or
  allowlist configuration; it is operator-controlled config only.
"""

from __future__ import annotations

import hmac
import ipaddress
import logging
import secrets
from dataclasses import dataclass

logger = logging.getLogger("tradebot.api")

MIN_TOKEN_LENGTH = 32
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'none'; object-src 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}


class InsecureBindError(RuntimeError):
    """Raised at startup when a remote bind has no strong token."""


@dataclass(frozen=True, slots=True)
class ApiSettings:
    host: str = "127.0.0.1"  # loopback by default
    port: int = 8787
    auth_token: str | None = None
    max_body_bytes: int = 1 * 1024 * 1024

    @property
    def is_loopback(self) -> bool:
        if self.host in LOOPBACK_HOSTS:
            return True
        try:
            return ipaddress.ip_address(self.host).is_loopback
        except ValueError:
            return False


def validate_startup(settings: ApiSettings) -> None:
    """Refuse to start when bound remotely without strong authentication."""

    if settings.is_loopback:
        return
    token = settings.auth_token
    if not token:
        raise InsecureBindError(
            f"refusing to bind {settings.host}: non-loopback bind requires "
            "TRADEBOT_API_TOKEN"
        )
    if len(token) < MIN_TOKEN_LENGTH:
        raise InsecureBindError(
            f"refusing to bind {settings.host}: token must be at least "
            f"{MIN_TOKEN_LENGTH} characters"
        )


def token_matches(expected: str | None, presented: str | None) -> bool:
    """Constant-time compare. Fails CLOSED on any missing value (A12)."""

    if not expected or not presented:
        return False
    return hmac.compare_digest(expected, presented)


def new_correlation_id() -> str:
    return secrets.token_hex(8)


def origin_allowed(origin: str | None, settings: ApiSettings) -> bool:
    """Validate Origin for browser traffic. Absent Origin is same-origin/curl.

    Fails closed for any origin that is not our own.
    """

    if origin is None:
        return True
    expected = {
        f"http://{settings.host}:{settings.port}",
        f"https://{settings.host}:{settings.port}",
    }
    if settings.is_loopback:
        expected |= {
            f"http://localhost:{settings.port}",
            f"https://localhost:{settings.port}",
            f"http://127.0.0.1:{settings.port}",
        }
    return origin in expected
