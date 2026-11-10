"""Reach an onboarding proxy through this computer's own proxy.

An operator behind a local client (Clash, v2rayN, a corporate gateway) cannot
open a TCP connection to the onboarding proxy directly. The browser and the
health check therefore dial the onboarding proxy *through* the proxy the
operating system is configured with:

    browser -> loopback relay -> system proxy -> onboarding proxy -> site

The exit address is still the onboarding proxy's; the system proxy only carries
the connection to it. ``CPM_FRONT_PROXY`` selects the behaviour:

* ``system`` (default) — follow the operating system (Windows Internet
  settings, macOS network settings, ``*_proxy`` variables elsewhere);
* ``direct`` — never use a front proxy;
* a URL (``http://host:port``, ``socks5://user:pass@host:port``) — use exactly
  that one, for PAC-only setups the system lookup cannot see.

A front proxy that is configured but unusable is an error, never a reason to
connect directly: a silent fallback would show the operator's real address to
the proxy provider.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from camoufox_pm.config import get_settings

Streams = tuple[asyncio.StreamReader, asyncio.StreamWriter]
Dialer = Callable[[str, int], Awaitable[Streams]]

_SCHEMES = {
    "http": "http",
    # Windows reports the one configured server under an "https://" key too;
    # a local client listening there still speaks plain HTTP CONNECT.
    "https": "http",
    "socks": "socks5",
    "socks5": "socks5",
    "socks5h": "socks5",
}
# CONNECT-capable entries first: the tunnel targets an arbitrary proxy port.
_SYSTEM_KEYS = ("https", "http", "all", "socks")
DIAL_TIMEOUT = 10.0


class FrontProxyError(ConnectionError):
    """The front proxy is configured but cannot be used. Carries no secrets."""


@dataclass(frozen=True)
class FrontProxy:
    scheme: str  # "http" (CONNECT) or "socks5"
    host: str
    port: int
    username: str | None = None
    password: str | None = None

    @property
    def label(self) -> str:
        """Safe to log: never includes credentials."""
        return f"{self.scheme}://{_hostport(self.host, self.port)}"


def _hostport(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def _is_loopback(host: str) -> bool:
    name = host.strip("[]").lower().rstrip(".")
    if name == "localhost" or name.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def parse(url: str) -> FrontProxy | None:
    """A front proxy from a URL, or ``None`` when the scheme cannot tunnel."""
    value = url.strip()
    if "://" not in value:
        value = f"http://{value}"
    try:
        parsed = urlsplit(value)
        scheme = _SCHEMES.get(parsed.scheme.lower())
        host, port = parsed.hostname, parsed.port
    except ValueError:
        return None
    if scheme is None or not host:
        return None
    if port is None:
        port = 1080 if scheme == "socks5" else 8080 if parsed.scheme.lower() == "http" else 443
    return FrontProxy(
        scheme,
        host,
        port,
        unquote(parsed.username) if parsed.username else None,
        unquote(parsed.password) if parsed.password else None,
    )


def front_proxy_for(host: str) -> FrontProxy | None:
    """The proxy to dial ``host`` through, or ``None`` to connect directly.

    Blocking on Windows (registry and name lookups); call via :func:`dialer_for`.
    """
    mode = (get_settings().front_proxy or "system").strip()
    if mode.lower() == "direct" or _is_loopback(host):
        return None
    if mode.lower() != "system":
        front = parse(mode)
        if front is None:
            raise FrontProxyError("CPM_FRONT_PROXY is not a usable http:// or socks5:// address")
        return front
    proxies = urllib.request.getproxies()
    candidates = [proxies[key] for key in _SYSTEM_KEYS if proxies.get(key)]
    if not candidates:
        return None
    try:
        if urllib.request.proxy_bypass(host):
            return None
    except OSError:
        pass
    for candidate in candidates:
        front = parse(candidate)
        if front is not None:
            return front
    raise FrontProxyError(
        "The system proxy uses an unsupported protocol. Set CPM_FRONT_PROXY explicitly."
    )


async def _connect_http(reader, writer, front: FrontProxy, host: str, port: int) -> None:
    target = _hostport(host, port)
    request = f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\nProxy-Connection: keep-alive\r\n"
    if front.username:
        token = base64.b64encode(
            f"{front.username}:{front.password or ''}".encode()
        ).decode("ascii")
        request += f"Proxy-Authorization: Basic {token}\r\n"
    writer.write((request + "\r\n").encode("latin-1"))
    await writer.drain()
    head = await reader.readuntil(b"\r\n\r\n")
    parts = head.split(b"\r\n", 1)[0].split(b" ", 2)
    if len(parts) < 2 or not parts[0].startswith(b"HTTP/") or parts[1] != b"200":
        raise FrontProxyError("The system proxy refused the tunnel to the onboarding proxy")


async def _connect_socks5(reader, writer, front: FrontProxy, host: str, port: int) -> None:
    writer.write(b"\x05\x02\x00\x02" if front.username else b"\x05\x01\x00")
    await writer.drain()
    version, method = await reader.readexactly(2)
    if version != 5 or method not in ((0, 2) if front.username else (0,)):
        raise FrontProxyError("The system proxy rejected the SOCKS5 handshake")
    if method == 2:
        user = (front.username or "").encode()
        secret = (front.password or "").encode()
        if not 1 <= len(user) <= 255 or len(secret) > 255:
            raise FrontProxyError("The system proxy credentials are not valid for SOCKS5")
        writer.write(b"\x01" + bytes([len(user)]) + user + bytes([len(secret)]) + secret)
        await writer.drain()
        if await reader.readexactly(2) != b"\x01\x00":
            raise FrontProxyError("The system proxy rejected its credentials")
    name = host.strip("[]")
    try:
        address = ipaddress.ip_address(name)
        encoded = (b"\x01" if address.version == 4 else b"\x04") + address.packed
    except ValueError:
        raw = name.encode("idna")
        if not 1 <= len(raw) <= 255:
            raise FrontProxyError("The onboarding proxy hostname is not valid") from None
        encoded = b"\x03" + bytes([len(raw)]) + raw
    writer.write(b"\x05\x01\x00" + encoded + port.to_bytes(2, "big"))
    await writer.drain()
    reply = await reader.readexactly(4)
    if reply[0] != 5 or reply[1] != 0:
        raise FrontProxyError("The system proxy refused the tunnel to the onboarding proxy")
    if reply[3] == 1:
        await reader.readexactly(4 + 2)
    elif reply[3] == 4:
        await reader.readexactly(16 + 2)
    elif reply[3] == 3:
        await reader.readexactly((await reader.readexactly(1))[0] + 2)
    else:
        raise FrontProxyError("The system proxy sent an invalid SOCKS5 reply")


async def open_tunnel(
    front: FrontProxy, host: str, port: int, timeout: float = DIAL_TIMEOUT
) -> Streams:
    """A byte stream to ``host:port`` carried by ``front``. Never dials directly."""
    writer: asyncio.StreamWriter | None = None

    async def establish() -> Streams:
        nonlocal writer
        reader, writer = await asyncio.open_connection(front.host, front.port)
        handshake = _connect_http if front.scheme == "http" else _connect_socks5
        await handshake(reader, writer, front, host, port)
        return reader, writer

    try:
        return await asyncio.wait_for(establish(), timeout)
    except BaseException as exc:
        if writer is not None:
            writer.transport.abort()
        if isinstance(exc, (FrontProxyError, asyncio.CancelledError)):
            raise
        if isinstance(exc, (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError,
                            asyncio.LimitOverrunError, ValueError)):
            raise FrontProxyError(
                f"Could not reach the onboarding proxy through the system proxy {front.label}"
            ) from None
        raise


async def dialer_for(host: str) -> tuple[Dialer | None, FrontProxy | None]:
    """How to open connections to the proxy at ``host``.

    ``(None, None)`` means "connect directly, exactly as before". Resolved once
    per browser session or health check so a session keeps one consistent path.
    """
    front = await asyncio.to_thread(front_proxy_for, host)
    if front is None:
        return None, None

    async def dial(target_host: str, target_port: int) -> Streams:
        return await open_tunnel(front, target_host, target_port)

    return dial, front
