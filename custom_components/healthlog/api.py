"""Restricted-token transport: verified HTTPS by default, HTTP by explicit opt-in."""

import math
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from ipaddress import ip_address
from urllib.parse import unquote, urlsplit

import aiohttp
from yarl import URL


class APIError(Exception):
    """Only retain non-sensitive status and retry metadata."""

    def __init__(self, status=0, retry_after=0):
        self.status = status
        self.retry_after = retry_after
        super().__init__(f"HealthLog request failed ({status})")


class AuthError(APIError):
    """Invalid, expired or revoked token (401)."""


class PermissionError(APIError):
    """Insufficient scope or server API disabled (403)."""


def normalize_url(value: str, *, allow_insecure_http: bool = False) -> str:
    """Normalize a complete base URL without guessing insecure transport.

    Ports, LAN names, IP addresses, IDNs and installation path prefixes are
    supported. Validate before URL parsers can discard controls or repair input.
    """
    if not isinstance(value, str) or type(allow_insecure_http) is not bool:
        raise ValueError("invalid_url")
    if re.search(r"[\x00-\x1f\x7f]", value):
        raise ValueError("invalid_url")
    value = value.strip()
    if not value or re.search(r"\s", value) or "\\" in value or "?" in value or "#" in value:
        raise ValueError("invalid_url")
    if re.search(r"%(?![0-9a-fA-F]{2})", value) or re.search(r"[\x00-\x1f\x7f\\]", unquote(value)):
        raise ValueError("invalid_url")
    if not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", value):
        # A host:port is not a URI scheme. Refuse opaque/single-slash URI
        # spellings instead of reinterpreting them as a different hostname.
        if value.lower().startswith(("/", "https:", "http:")) or (
            re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value)
            and not re.match(r"^[^:/]+:[0-9]+(?:/|$)", value)
        ):
            raise ValueError("invalid_url")
        value = "https://" + value
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme not in ("http", "https")
        or (parsed.scheme == "http" and not allow_insecure_http)
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.netloc.endswith(":")
    ):
        raise ValueError("invalid_url")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("invalid_url")
    url = URL(value)
    host = url.raw_host
    if host is None:
        raise ValueError("invalid_url")
    if ":" in host or re.fullmatch(r"[0-9.]+", host):
        ip_address(host)
    elif len(host.rstrip(".")) > 253 or any(
        not re.fullmatch(r"[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?", label)
        for label in host.rstrip(".").split(".")
    ):
        raise ValueError("invalid_url")
    return str(url).rstrip("/")


def validate_token(value: str) -> str:
    """Syntax only: scope/owner cannot be introspected with a write-only token."""
    if not isinstance(value, str) or not re.fullmatch(r"hlk_[A-Za-z0-9_-]{20,256}", value):
        raise ValueError("invalid_token")
    return value


def retry_seconds(value) -> float:
    """Accept seconds and HTTP dates; never allow NaN to poison worker timing."""
    try:
        seconds = float(value)
    except ValueError, TypeError:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except ValueError, TypeError, OverflowError:
            return 0
    return max(0, seconds) if math.isfinite(seconds) else 0


class API:
    """Never follow redirects or include raw exceptions/response bodies in logs."""

    def __init__(self, session, url: str, token: str, *, allow_insecure_http: bool = False):
        self.session = session
        self.url = normalize_url(url, allow_insecure_http=allow_insecure_http)
        self._token = validate_token(token)

    async def post(self, path: str, payload: dict, key: str) -> dict:
        endpoint = self.url + path
        # HA owns this shared session. Never clear or replace its cookie jar:
        # blank request-local cookies instead. HealthLog resolves cookies BEFORE
        # Bearer, so a cached browser session must not redirect another token's
        # readings to the wrong account. Also suppress both known session names.
        cookies = {name: "" for name in self.session.cookie_jar.filter_cookies(URL(endpoint))}
        cookies.update({"__Host-healthlog_session": "", "healthlog_session": ""})
        try:
            async with self.session.post(
                endpoint,
                json=payload,
                cookies=cookies,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Idempotency-Key": key,
                    "User-Agent": "HealthLog-HomeAssistant/1.0",
                },
                timeout=aiohttp.ClientTimeout(total=30),
                allow_redirects=False,
            ) as response:
                if response.status not in (200, 201):
                    error = {401: AuthError, 403: PermissionError}.get(response.status, APIError)
                    raise error(response.status, retry_seconds(response.headers.get("Retry-After")))
                try:
                    body = await response.json()
                except ValueError, aiohttp.ContentTypeError:
                    raise APIError(response.status) from None
                if (
                    not isinstance(body, dict)
                    or body.get("error") is not None
                    or not isinstance(body.get("data"), dict)
                ):
                    raise APIError(response.status)
                return body["data"]
        except aiohttp.ClientError, TimeoutError:
            raise APIError() from None
