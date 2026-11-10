"""Session-owned loopback relays that carry a proxy connection over a front proxy.

``PipeRelay`` is protocol-blind: the browser keeps speaking HTTP-proxy or
SOCKS5 to a loopback port (and keeps answering the proxy's own authentication),
while every accepted connection is piped to the real proxy through the dialer
it was given. Authenticated SOCKS5 and SOCKS4 keep using ``SocksRelay``, which
accepts the same dialer. Neither ever falls back to a direct connection.
"""

from __future__ import annotations

import asyncio
import ssl
from urllib.parse import quote, urlsplit

from .socks_relay import SocksRelay
from .system_proxy import Dialer, FrontProxy, dialer_for


class PipeRelay:
    """One ephemeral loopback listener piping bytes to one upstream proxy."""

    def __init__(
        self,
        server: str,
        dial: Dialer,
        username: str | None = None,
        password: str | None = None,
        *,
        max_connections: int = 256,
    ) -> None:
        parsed = urlsplit(server)
        if parsed.scheme not in {"http", "https", "socks5"} or not parsed.hostname or not parsed.port:
            raise ValueError("Proxy requires a scheme, host and port")
        self._host = parsed.hostname
        self._upstream_port = parsed.port
        # TLS to the proxy ends here: the browser cannot verify a certificate
        # for the real proxy name on a loopback address.
        self._tls = parsed.scheme == "https"
        self._scheme = "http" if self._tls else parsed.scheme
        self._dial = dial
        self._username = username
        self._password = password
        self._max_connections = max_connections
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._writers: set[asyncio.StreamWriter] = set()
        self._closed = False
        self.port: int | None = None

    @property
    def browser_proxy(self) -> dict[str, str]:
        if self.port is None or self._closed:
            raise RuntimeError("Proxy relay is not running")
        proxy = {"server": f"{self._scheme}://127.0.0.1:{self.port}"}
        if self._username:
            proxy["username"] = self._username
        if self._password:
            proxy["password"] = self._password
        return proxy

    @property
    def url(self) -> str:
        """The relay as a client URL, with percent-encoded credentials."""
        proxy = self.browser_proxy
        credentials = ""
        if self._username:
            credentials = quote(self._username, safe="")
            if self._password:
                credentials += f":{quote(self._password, safe='')}"
            credentials += "@"
        scheme, _, rest = proxy["server"].partition("://")
        return f"{scheme}://{credentials}{rest}"

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("Proxy relay is closed")
        if self._server is None:
            self._server = await asyncio.start_server(self._accept, "127.0.0.1", 0)
            self.port = self._server.sockets[0].getsockname()[1]

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._closed or len(self._tasks) >= self._max_connections:
            writer.close()
            return
        self._writers.add(writer)
        task = asyncio.create_task(self._serve(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream_writer: asyncio.StreamWriter | None = None
        try:
            upstream_reader, upstream_writer = await self._dial(self._host, self._upstream_port)
            if self._tls:
                start_tls = getattr(upstream_writer, "start_tls", None)
                if start_tls is None:  # Python 3.10
                    raise ConnectionError("HTTPS proxies need Python 3.11+ behind a front proxy")
                await start_tls(ssl.create_default_context(), server_hostname=self._host)
            pumps = [
                asyncio.create_task(SocksRelay._pump(reader, upstream_writer)),
                asyncio.create_task(SocksRelay._pump(upstream_reader, writer)),
            ]
            try:
                await asyncio.gather(*pumps)
            finally:
                for pump in pumps:
                    pump.cancel()
                await asyncio.gather(*pumps, return_exceptions=True)
        except asyncio.CancelledError:
            pass
        except Exception:
            # No error text is logged: proxy exceptions must not expose secrets.
            pass
        finally:
            # A failed tunnel resets the browser's connection; it never retries
            # the destination or the proxy directly.
            for stream in (writer, upstream_writer):
                if stream is not None:
                    stream.transport.abort()
            self._writers.discard(writer)

    async def close(self) -> None:
        self._closed = True
        server, self._server = self._server, None
        if server is not None:
            server.close()
        for writer in list(self._writers):
            writer.transport.abort()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if server is not None:
            await server.wait_closed()
        self._tasks.clear()
        self._writers.clear()
        self._username = self._password = None


async def front_dialer(server: str) -> tuple[Dialer | None, FrontProxy | None]:
    """The front-proxy dialer for a ``scheme://host:port`` proxy, if one applies."""
    try:
        host = urlsplit(server if "://" in server else f"//{server}").hostname
    except ValueError:
        host = None
    if not host:
        return None, None
    return await dialer_for(host)
