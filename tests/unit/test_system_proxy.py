"""The profile's proxy is reached through this computer's proxy, never around it."""

import asyncio
import base64
from contextlib import asynccontextmanager

import pytest

from camoufox_pm.config import get_settings
from camoufox_pm.core import proxy_check, system_proxy
from camoufox_pm.core.models import ProxyConfig
from camoufox_pm.core.proxy_chain import PipeRelay
from camoufox_pm.core.socks_relay import SocksRelay
from camoufox_pm.core.system_proxy import FrontProxy, FrontProxyError
from tests.support.proxy_protocols import certificates, proxy_fixture


@pytest.fixture
def certs(tmp_path):
    return certificates(tmp_path)


@pytest.fixture
def front_setting(monkeypatch):
    def apply(value):
        monkeypatch.setenv("CPM_FRONT_PROXY", value)
        get_settings.cache_clear()

    yield apply
    monkeypatch.setenv("CPM_FRONT_PROXY", "direct")
    get_settings.cache_clear()


@asynccontextmanager
async def front_proxy(kind, *, refuse=False, username=None, password=None):
    """A local stand-in for Clash/v2rayN: HTTP CONNECT or SOCKS5, recording targets."""
    targets = []
    tasks = set()

    async def pipe(reader, writer, host, port):
        upstream_reader, upstream_writer = await asyncio.open_connection(host, port)

        async def pump(source, target):
            try:
                while data := await source.read(65536):
                    target.write(data)
                    await target.drain()
            finally:
                target.close()

        await asyncio.gather(
            pump(reader, upstream_writer), pump(upstream_reader, writer), return_exceptions=True
        )

    async def http(reader, writer):
        head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
        method, target, _ = head.split("\r\n", 1)[0].split(" ")
        assert method == "CONNECT"
        if username:
            token = base64.b64encode(f"{username}:{password}".encode()).decode()
            assert f"Proxy-Authorization: Basic {token}" in head
        host, _, port = target.rpartition(":")
        targets.append((host, int(port)))
        if refuse:
            writer.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            await writer.drain()
            return
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        await pipe(reader, writer, host, int(port))

    async def socks5(reader, writer):
        _, count = await reader.readexactly(2)
        methods = await reader.readexactly(count)
        if username:
            assert 2 in methods
            writer.write(b"\x05\x02")
            await writer.drain()
            assert await reader.readexactly(1) == b"\x01"
            user = await reader.readexactly((await reader.readexactly(1))[0])
            secret = await reader.readexactly((await reader.readexactly(1))[0])
            assert (user, secret) == (username.encode(), password.encode())
            writer.write(b"\x01\x00")
        else:
            writer.write(b"\x05\x00")
        await writer.drain()
        head = await reader.readexactly(4)
        if head[3] == 1:
            host = ".".join(str(part) for part in await reader.readexactly(4))
        else:
            assert head[3] == 3
            host = (await reader.readexactly((await reader.readexactly(1))[0])).decode()
        port = int.from_bytes(await reader.readexactly(2), "big")
        targets.append((host, port))
        if refuse:
            writer.write(b"\x05\x02\x00\x01" + bytes(6))
            await writer.drain()
            return
        writer.write(b"\x05\x00\x00\x01" + bytes(6))
        await writer.drain()
        await pipe(reader, writer, host, port)

    def accept(reader, writer):
        async def run():
            try:
                await (http if kind == "http" else socks5)(reader, writer)
            except (asyncio.IncompleteReadError, ConnectionError, OSError):
                pass
            finally:
                writer.close()

        task = asyncio.create_task(run())
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield FrontProxy(kind, "127.0.0.1", port, username, password), targets
    finally:
        server.close()
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await server.wait_closed()


def config(fixture):
    return ProxyConfig(
        type=fixture.protocol,
        server=f"127.0.0.1:{fixture.proxy_port}",
        username=fixture.username if fixture.auth else None,
        password=fixture.password if fixture.auth else None,
    )


def use_front(monkeypatch, front_setting, front):
    """Point the app at ``front`` even though the test proxies live on loopback."""
    monkeypatch.setattr(system_proxy, "_is_loopback", lambda host: False)
    front_setting(f"{front.scheme}://127.0.0.1:{front.port}")


def test_direct_mode_and_loopback_never_use_a_front_proxy(monkeypatch, front_setting):
    monkeypatch.setattr(
        system_proxy.urllib.request, "getproxies", lambda: {"http": "http://127.0.0.1:10808"}
    )
    monkeypatch.setattr(system_proxy.urllib.request, "proxy_bypass", lambda host: False)
    front_setting("system")
    assert system_proxy.front_proxy_for("127.0.0.1") is None
    assert system_proxy.front_proxy_for("localhost") is None
    assert system_proxy.front_proxy_for("proxy.example").label == "http://127.0.0.1:10808"
    front_setting("direct")
    assert system_proxy.front_proxy_for("proxy.example") is None


def test_system_mode_reads_the_operating_system_settings(monkeypatch, front_setting):
    front_setting("system")
    request = system_proxy.urllib.request
    monkeypatch.setattr(request, "proxy_bypass", lambda host: host == "intranet.example")
    # Windows reports one "host:port" under every scheme, https:// included.
    monkeypatch.setattr(
        request,
        "getproxies",
        lambda: {"http": "http://127.0.0.1:7890", "https": "https://127.0.0.1:7890"},
    )
    assert system_proxy.front_proxy_for("proxy.example") == FrontProxy("http", "127.0.0.1", 7890)
    assert system_proxy.front_proxy_for("intranet.example") is None
    monkeypatch.setattr(request, "getproxies", lambda: {"socks": "socks://127.0.0.1:7891"})
    assert system_proxy.front_proxy_for("proxy.example") == FrontProxy("socks5", "127.0.0.1", 7891)
    monkeypatch.setattr(request, "getproxies", lambda: {})
    assert system_proxy.front_proxy_for("proxy.example") is None
    # Present but unusable is an error, not permission to connect directly.
    monkeypatch.setattr(request, "getproxies", lambda: {"all": "socks4://127.0.0.1:7891"})
    with pytest.raises(FrontProxyError):
        system_proxy.front_proxy_for("proxy.example")


def test_an_explicit_front_proxy_wins_and_keeps_its_credentials(monkeypatch, front_setting):
    monkeypatch.setattr(
        system_proxy.urllib.request, "getproxies", lambda: {"http": "http://127.0.0.1:1"}
    )
    front_setting("socks5://us%40er:pa%3Ass@gateway.example:1081")
    front = system_proxy.front_proxy_for("proxy.example")
    assert front == FrontProxy("socks5", "gateway.example", 1081, "us@er", "pa:ss")
    assert "pa:ss" not in front.label and "us@er" not in front.label
    front_setting("ftp://gateway.example:21")
    with pytest.raises(FrontProxyError):
        system_proxy.front_proxy_for("proxy.example")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["http", "socks5"])
@pytest.mark.parametrize(
    "protocol,auth",
    [
        ("http", False),
        ("http", True),
        ("https", True),
        ("socks4", False),
        ("socks5", False),
        ("socks5", True),
    ],
)
async def test_the_health_check_reaches_every_proxy_type_through_the_front_proxy(
    certs, monkeypatch, front_setting, kind, protocol, auth
):
    monkeypatch.setenv("SSL_CERT_FILE", str(certs[0]))
    async with proxy_fixture(protocol, certs, auth=auth) as fixture:
        async with front_proxy(kind) as (front, targets):
            use_front(monkeypatch, front_setting, front)
            monkeypatch.setattr(
                proxy_check,
                "IP_ENDPOINTS",
                (f"https://cpm-origin.invalid:{fixture.https_port}/ip",),
            )
            ip, _ = await proxy_check.resolve_exit_ip(config(fixture), timeout=5)
            assert ip == "203.0.113.19"
            # The only thing the front proxy was asked for is the proxy itself.
            assert targets and set(targets) == {("127.0.0.1", fixture.proxy_port)}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["http", "socks5"])
@pytest.mark.parametrize("protocol,auth", [("http", True), ("socks5", True), ("socks5", False)])
async def test_a_refusing_front_proxy_fails_the_check_without_a_direct_connection(
    certs, monkeypatch, front_setting, kind, protocol, auth
):
    monkeypatch.setenv("SSL_CERT_FILE", str(certs[0]))
    async with proxy_fixture(protocol, certs, auth=auth) as fixture:
        async with front_proxy(kind, refuse=True) as (front, targets):
            use_front(monkeypatch, front_setting, front)
            monkeypatch.setattr(
                proxy_check,
                "IP_ENDPOINTS",
                (f"https://cpm-origin.invalid:{fixture.https_port}/ip",),
            )
            with pytest.raises(ConnectionError):
                await proxy_check.resolve_exit_ip(config(fixture), timeout=3)
            assert targets
            assert fixture.events == []


@pytest.mark.asyncio
async def test_an_unreachable_front_proxy_is_an_error_not_a_fallback(certs):
    async with proxy_fixture("socks5", certs, auth=True) as fixture:
        server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
        dead_port = server.sockets[0].getsockname()[1]
        server.close()
        await server.wait_closed()
        front = FrontProxy("http", "127.0.0.1", dead_port)
        with pytest.raises(FrontProxyError) as caught:
            await system_proxy.open_tunnel(front, "127.0.0.1", fixture.proxy_port, timeout=2)
        assert "secret" not in str(caught.value)
        assert fixture.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["http", "socks5"])
async def test_front_proxy_credentials_are_sent_to_the_front_proxy_only(certs, kind):
    async with proxy_fixture("socks5", certs, auth=True) as fixture:
        async with front_proxy(kind, username="gate", password="keeper") as (front, targets):
            async def dial(host, port):
                return await system_proxy.open_tunnel(front, host, port)

            relay = SocksRelay(
                f"socks5://127.0.0.1:{fixture.proxy_port}",
                fixture.username,
                fixture.password,
                connect=dial,
            )
            await relay.start()
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", relay.port)
                writer.write(b"\x05\x01\x00")
                await writer.drain()
                assert await reader.readexactly(2) == b"\x05\x00"
                writer.write(
                    b"\x05\x01\x00\x01" + bytes([203, 0, 113, 10])
                    + fixture.http_port.to_bytes(2, "big")
                )
                await writer.drain()
                assert (await asyncio.wait_for(reader.readexactly(10), 2))[1] == 0
                writer.close()
                await writer.wait_closed()
            finally:
                await relay.close()
            assert targets == [("127.0.0.1", fixture.proxy_port)]


@pytest.mark.asyncio
async def test_pipe_relay_keeps_browser_side_authentication_and_closes_cleanly(certs):
    async with proxy_fixture("http", certs, auth=True) as fixture:
        async with front_proxy("http") as (front, targets):
            async def dial(host, port):
                return await system_proxy.open_tunnel(front, host, port)

            relay = PipeRelay(
                f"http://127.0.0.1:{fixture.proxy_port}", dial, fixture.username, fixture.password
            )
            await relay.start()
            proxy = relay.browser_proxy
            assert proxy["server"] == f"http://127.0.0.1:{relay.port}"
            assert proxy["username"] == fixture.username
            reader, writer = await asyncio.open_connection("127.0.0.1", relay.port)
            for _ in range(100):  # let the listener adopt the connection first
                if relay._writers:
                    break
                await asyncio.sleep(0.01)
            await relay.close()
            assert await asyncio.wait_for(reader.read(1), 2) == b""
            writer.close()
            with pytest.raises(RuntimeError):
                _ = relay.browser_proxy
