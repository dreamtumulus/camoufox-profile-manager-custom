"""Shared machinery for the browser tests: two local origins, and offline launches.

Kept out of conftest.py because these are imported by name; a conftest can be
loaded more than once depending on the rootdir, and importing one directly is a
pytest pitfall rather than a convention.
"""

import errno
import http.server
import socket
import socketserver
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, NamedTuple
from urllib.parse import urlsplit

# In the MaxMind database Camoufox ships, which not every address is — 1.1.1.1
# is not.
GEOIP_ADDRESS = "8.8.8.8"

PAGE = b"<!doctype html><meta charset=utf-8><title>test page</title><p>test page"


def offline_launch(options: dict[str, Any]) -> dict[str, Any]:
    """These launch options with the one thing that reaches the internet replaced.

    A real launch passes `geoip=True`, and Camoufox then asks the internet what
    this machine's public address is. The answer is cached per process, so it is
    one lookup per run rather than one per launch — but a *failed* lookup is not
    cached, so with no internet every launch retries and fails, and the answer
    differs by machine anyway.

    Naming an address keeps the GeoIP database, timezone and WebRTC code in
    the test. The browser test fixture supplies a synthetic IPv4-only host
    for the SDK's separate public IPv6 check.

    Leave explicit geoip settings alone so profiles with coordinates can test
    that automatic location is disabled. This also substitutes the IPv4 answer
    for the lookup that would normally go through a configured proxy.
    """
    launch = dict(options)
    if launch.get("geoip") is True:
        launch["geoip"] = GEOIP_ADDRESS
    return launch


# `/login`: a sign-in form shaped like the start page's. An address field, a
# button that would send mail, and an SSO button offered once the form has looked
# at an address on sso.example; a hidden link with the same letters stands before
# it. The query string picks the variant, and every press is written into
# #pressed, where a test can read it back.
#   offer=input|keyup|never  the event that makes the form look at the address:
#                            "input" sees a value set in one step, "keyup" only keys
#   held=ADDRESS             the field already holds something
#   edited=ADDRESS           someone replaces the address just after it is entered
#   delay=MS                 the SSO button shows this long after the form looked
#   shift=MS                 this long after the SSO button shows, the page grows
#                            above it, so the mail button sits where SSO was
#   disabled=MS              the SSO button shows disabled, and is enabled this much later
#   wrap=1                   the whole form sits in one element that is itself a button
LOGIN_PAGE = b"""<!doctype html><meta charset=utf-8><title>sign in</title>
<style>
button, input, a { display: block; box-sizing: border-box; width: 320px; height: 40px; margin: 12px 0 }
[hidden] { display: none !important }
</style>
<button id=google>Continue with Google</button>
<input type=email id=address placeholder="Enter your email">
<button id=mail>Continue with email</button>
<a id=decoy href="#" hidden>SSO help</a>
<button id=sso hidden>Continue with SSO</button>
<output id=pressed></output>
<script>
const options = new URLSearchParams(location.search)
const field = document.getElementById('address'), sso = document.getElementById('sso')
if (options.has('held')) field.value = options.get('held')
const after = (name, action) => setTimeout(action, Number(options.get(name) || 0))
let shown = false
function show() {
  const offered = field.value.endsWith('@sso.example')
  sso.hidden = !offered
  if (!offered || shown) return
  shown = true
  if (options.has('disabled')) {
    sso.disabled = true
    after('disabled', () => { sso.disabled = false })
  }
  if (options.has('shift')) after('shift', () => {
    const mail = document.getElementById('mail'), spacer = document.createElement('div')
    spacer.style.height = (sso.getBoundingClientRect().top - mail.getBoundingClientRect().top) + 'px'
    mail.before(spacer)
  })
}
field.addEventListener(options.get('offer') || 'input', () => {
  if (options.has('delay')) after('delay', show)
  else show()
})
if (options.has('edited')) field.addEventListener('input', () => setTimeout(() => {
  field.value = options.get('edited')
  sso.hidden = false
}, 50), { once: true })
for (const button of document.querySelectorAll('button')) button.addEventListener('click', () => {
  document.getElementById('pressed').textContent += button.id + ' '
})
if (options.has('wrap')) {
  const box = document.createElement('div')
  box.setAttribute('role', 'button')
  document.body.prepend(box)
  box.append(...document.querySelectorAll('button, input, a'))
}
</script>"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = LOGIN_PAGE if urlsplit(self.path).path == "/login" else PAGE
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        """Silence the per-request logging; pytest output is not a web log."""


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class LocalSites(NamedTuple):
    """Two URLs serving the same page that the browser treats as different sites."""

    first: str
    second: str


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _serve(family: int, host: str, port: int) -> _Server:
    class Bound(_Server):
        address_family = family

    server = Bound((host, port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@contextmanager
def serve_local_sites() -> Iterator[LocalSites]:
    """Serve one page under both loopback names, on one port.

    Both families, so `localhost` reaches a server whichever way this machine
    resolves it.
    """
    port = _free_port()
    servers = [_serve(socket.AF_INET, "127.0.0.1", port)]
    try:
        servers.append(_serve(socket.AF_INET6, "::1", port))
    except OSError as error:
        # No IPv6 here, so `localhost` resolves to 127.0.0.1 and the first
        # server answers both names. Anything else — the port taken on ::1
        # between the probe and the bind — must not be swallowed: `localhost`
        # may resolve to ::1, and the tests would then fail on a 45-second
        # navigation timeout with nothing pointing back at this fixture.
        if error.errno not in (errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL):
            raise

    try:
        yield LocalSites(f"http://127.0.0.1:{port}/", f"http://localhost:{port}/")
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


# What the page itself computes, written where automation can read it back.
# Automation must not evaluate this directly: since 152.0.4-beta.29 Camoufox runs
# `page.evaluate` in an isolated world, and a timezone override belongs to the
# realm that received it. The page's realm is the one a site actually gets, so
# that is the only observer worth asserting on.
_TIMEZONE_PROBE = (
    "<script>document.title = Intl.DateTimeFormat().resolvedOptions().timeZone</script>"
)


async def timezone_a_page_sees(page) -> str:
    """The timezone a script on the page resolves, not the one automation sees."""
    await page.set_content(_TIMEZONE_PROBE)
    return await page.title()
