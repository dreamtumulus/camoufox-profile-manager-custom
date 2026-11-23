"""Browser session lifecycle: launch, monitor, and close Camoufox instances.

Extracted from ``profile_manager`` so profile CRUD and browser control have
clear, independently testable responsibilities.

Cleanup is driven primarily by Playwright's ``close``/``disconnected`` events, so
a user closing the browser window is detected and the session is torn down. OS
process polling is only a best-effort fallback: with a persistent context the
resolvable pid is Playwright's driver process, not Firefox, so it is not a
reliable window-close signal on its own.
"""

import asyncio
import json
import os
import re
import sys
import tempfile
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

import psutil
from loguru import logger

from ..config import get_settings
from .proxy_chain import PipeRelay, front_dialer
from .socks_relay import SocksRelay

if TYPE_CHECKING:
    # Type-only: importing StorageManager at runtime would be a cycle, since
    # database.py has no need of this module at all.
    from .database import StorageManager

try:
    from camoufox.async_api import AsyncCamoufox

    CAMOUFOX_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without camoufox installed
    AsyncCamoufox = None  # type: ignore[assignment, misc]
    CAMOUFOX_AVAILABLE = False

ExitHandler = Callable[[str], Awaitable[None]]


class BrowserLaunchError(RuntimeError):
    """Raised when a Camoufox browser fails to launch."""


BROWSER_EXIT_UNCONFIRMED = (
    "Browser exit could not be confirmed. Close remaining browsers, then retry "
    "Stop or restart the manager."
)
BROWSER_EXITS_UNCONFIRMED = (
    "Some browser exits could not be confirmed. Retry Stop for active profiles."
)


class BrowserTerminationError(RuntimeError):
    """Exit could not be confirmed; retain the tracked session and lease."""


class _OwnedProcessTree:
    """Pin driver/descendant birth identities before SDK teardown loses ancestry."""

    def __init__(self, pid: int | None):
        self.root: psutil.Process | None = None
        self.children: set[psutil.Process] = set()
        self.uncertain = False
        if pid is not None:
            try:
                candidate = psutil.Process(pid)
                if candidate.pid == os.getpid() or not any(
                    parent.pid == os.getpid() for parent in candidate.parents()
                ):
                    self.uncertain = True
                else:
                    self.root = candidate
            except psutil.NoSuchProcess:
                pass
            except psutil.Error:
                self.uncertain = True
        self.refresh()

    def refresh(self) -> None:
        if self.root is None:
            return
        # The SDK driver can exit before a pinned browser child. That child may
        # have spawned more descendants during SDK shutdown; its birth identity
        # still proves ownership even after it loses the driver as an ancestor.
        for parent in [self.root, *self.children]:
            try:
                if parent.is_running():
                    self.children.update(parent.children(recursive=True))
            except psutil.NoSuchProcess:
                pass
            except psutil.Error:
                self.uncertain = True

    def _wait(self, processes: list[psutil.Process]) -> list[psutil.Process]:
        try:
            # Exited/reused identities must not make us wait on a new PID owner.
            current = [process for process in processes if process.is_running()]
            return psutil.wait_procs(current, timeout=5)[1]
        except psutil.Error:
            # Try every remaining owned signal even if one exit is unreadable.
            # No private process exception text leaves the lifecycle boundary.
            self.uncertain = True
            return processes

    def finish(self) -> None:
        # Give already-closing descendants their own grace period before any
        # Windows TerminateProcess/SIGTERM fallback. Never scan/kill by name.
        self.refresh()
        known = self.children.copy()
        alive = self._wait(list(known))
        # A still-closing parent can create a child during the same bounded
        # grace period. Adopt it from the pinned parent before signalling it.
        self.refresh()
        targets = (
            alive + list(self.children - known) + ([self.root] if self.root is not None else [])
        )
        for process in targets:
            try:
                process.terminate()  # psutil checks the pinned PID/birth identity.
            except psutil.NoSuchProcess:
                pass
            except psutil.Error:
                self.uncertain = True
        alive = self._wait(targets)
        for process in alive:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
            except psutil.Error:
                self.uncertain = True
        alive = self._wait(alive)
        if alive or self.uncertain:
            raise BrowserTerminationError(BROWSER_EXIT_UNCONFIRMED)


def _browser_launch_options(launch_options: dict[str, Any]) -> dict[str, Any]:
    """Preserve declared screens and keep Linux desktop input methods usable.

    Playwright's focus test mode prevents native IME composition from committing
    in headed Firefox. PyInstaller also prepends its bundled libraries to
    LD_LIBRARY_PATH; those GTK/GLib versions can be incompatible with the host's
    input-method module. Restore the pre-bundle path for the browser child only.
    Explicit preferences and environments remain under the caller's control.
    """
    options = launch_options.copy()
    config = options.get("config")
    if (
        not options.get("headless", False)
        and "screen" not in options
        and isinstance(config, dict)
        and all(
            type(config.get(key)) is int and config[key] > 0
            for key in ("screen.width", "screen.height")
        )
    ):
        from camoufox.fingerprints import Screen

        # This profile already declares its screen. Do not add unrelated host
        # monitor bounds to fpgen's intermediate sample: some display/model
        # combinations raise RestrictiveConstraints before the saved config is
        # applied. The public empty Screen disables only that implicit bound;
        # it preserves the pin and any explicitly supplied Screen constraint.
        options["screen"] = Screen()
    if sys.platform != "linux":
        return options

    if not options.get("headless", False):
        prefs = dict(options.get("firefox_user_prefs") or {})
        prefs.setdefault("focusmanager.testmode", False)
        options["firefox_user_prefs"] = prefs

    if getattr(sys, "frozen", False) and options.get("env") is None:
        env = os.environ.copy()
        original_path = env.get("LD_LIBRARY_PATH_ORIG")
        if original_path:
            env["LD_LIBRARY_PATH"] = original_path
        else:
            env.pop("LD_LIBRARY_PATH", None)
        options["env"] = env
    return options


def _replace_preferences(path: Path, content: bytes) -> None:
    """Replace a small preferences file without leaving a partial write."""
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(content)
            handle.close()
            if path.exists():
                temporary.chmod(path.stat().st_mode)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


@contextmanager
def _linux_ime_startup_preferences(options: dict[str, Any]) -> Iterator[None]:
    """Apply focus preferences before GTK creates its input context.

    Playwright 1.60 sends firefox_user_prefs over Browser.enable after startup.
    That is too late for native IME focus: candidates appear but do not commit.
    Temporarily append the preference to this profile's user.js so Firefox
    reads it at startup, then restore the original file after launch or failure.
    """
    directory = options.get("user_data_dir")
    if (
        sys.platform != "linux"
        or options.get("headless")
        or not options.get("persistent_context")
        or not directory
    ):
        yield
        return

    path = Path(directory) / "user.js"
    path.parent.mkdir(parents=True, exist_ok=True)
    original = path.read_bytes() if path.exists() else None
    preference = options["firefox_user_prefs"]["focusmanager.testmode"]
    content = (original or b"") + (
        f'\nuser_pref("focusmanager.testmode", {json.dumps(preference)});\n'
    ).encode()
    _replace_preferences(path, content)
    try:
        yield
    finally:
        try:
            # Preserve a concurrent edit, or a preferences file rewritten by an
            # older Playwright release, instead of silently overwriting it.
            if path.exists() and path.read_bytes() == content:
                if original is None:
                    path.unlink()
                else:
                    _replace_preferences(path, original)
        except OSError as exc:
            # Cleanup must not orphan a browser which has already started.
            logger.warning(f"Could not restore browser startup preferences: {exc}")


# What a tab shows before anyone has used it.
_BLANK_PAGES = frozenset({"", "about:blank", "about:newtab", "about:home"})
START_PAGE_TIMEOUT = 60.0
# Asks the browser for an empty tab beside the page this runs in. It runs in the
# driver's own world, apart from the page's scripts, and neither reads nor
# changes the page; the tab it opens knows nothing of the page that asked.
_BLANK_TAB = "() => { window.open('about:blank', '_blank', 'noopener,noreferrer'); }"
# How long a page is given to bring an empty tab up, and how many pages are asked.
# A tab arrives within a second; a page the driver cannot script never answers.
NEW_TAB_TIMEOUT = 3.0
_NEW_TAB_OPENERS = 2


def _site(url: str) -> str:
    """The host a page is on, without a leading "www."; empty for internal pages."""
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


# The sign-in form of the start page. The site offers single sign-on only once
# it has recognised the domain of the address in the field, and through a slow
# proxy that answer can take a while.
EMAIL_FIELD = 'input[type="email"], input[name="email"]'
# A control with nothing pressable inside it. An element that merely wraps the
# form's buttons contains the same letters, and pressing its middle would press
# whichever button happens to sit there.
SSO_BUTTON = (
    ':is(button, [role="button"], a[href]):not(:has(button, [role="button"], a[href], input))'
)
# The acronym, whatever language the rest of the label is in.
_SSO_LABEL = re.compile(r"\bSSO\b")
# Never the button that sends a message to the mailbox.
_MAIL_LABEL = re.compile(r"e-?mail", re.IGNORECASE)
EMAIL_FIELD_WAIT = 60.0
SSO_WAIT_AFTER_FILL = 20.0
SSO_WAIT_AFTER_TYPING = 25.0
# How long the button must stay shown and enabled before it is pressed.
SSO_SETTLE = 0.5
_POLL = 0.25


async def _visible_within(locator: Any, seconds: float) -> bool:
    """Whether the element shows up in time. Polled, so a wait ends without an error."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while True:
        if await locator.is_visible():
            return True
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(_POLL)


async def _pressable(locator: Any) -> bool:
    if not await locator.is_visible():
        return False
    try:
        return bool(await locator.is_enabled(timeout=1000))
    except Exception:  # noqa: BLE001 - it went away between the two questions
        return False


async def _pressable_within(locator: Any, seconds: float) -> bool:
    """Whether the control is shown and enabled in time, and still is a moment later.

    A button that flashes up while the page is still making up its mind, or one
    that is shown greyed out, is not one to press yet.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    seen = False
    while True:
        ready = await _pressable(locator)
        if ready and seen:
            return True
        if not ready and loop.time() >= deadline:
            return False
        seen = ready
        await asyncio.sleep(SSO_SETTLE if ready else _POLL)


def _same_address(value: str, email: str) -> bool:
    return value.strip().lower() == email.strip().lower()


async def _sign_in_with_sso(page: Any, url: str, email: str) -> str:
    """Enter the profile's address on the start page and press its SSO button, once.

    Only what the person would otherwise do by hand on the page that was just
    opened for them. A field that already holds something is theirs and is left
    alone, as is a page that has gone to another site. The button is pressed
    only once it is shown and enabled, while the field still holds this address,
    and nothing follows the press: the identity provider's page is for the
    person. Returns what was done, in words fit for the log (no address).
    """
    site = _site(url)
    field = page.locator(EMAIL_FIELD).first
    if not await _visible_within(field, EMAIL_FIELD_WAIT):
        return "no sign-in form appeared"  # signed in already, or it never loaded
    if _site(page.url) != site:
        return "the page went to another site; nothing entered"
    if await field.input_value():
        return "the email field already held something; left alone"
    await field.fill(email)
    button = (
        page.locator(SSO_BUTTON)
        .filter(has_text=_SSO_LABEL, has_not_text=_MAIL_LABEL, visible=True)
        .first
    )
    how = "address entered"
    if not await _pressable_within(button, SSO_WAIT_AFTER_FILL):
        # Filling sets the value in one step. A form that listens for keys has
        # seen none, so give it the keys before concluding there is no SSO.
        if _site(page.url) != site or not _same_address(await field.input_value(), email):
            return f"{how}; the page or the field changed, nothing pressed"
        await field.fill("")
        await field.press_sequentially(email)
        how = "address entered, then typed again as keys"
        if not await _pressable_within(button, SSO_WAIT_AFTER_TYPING):
            return f"{how}; no SSO button was offered, nothing pressed"
    if _site(page.url) != site or not _same_address(await field.input_value(), email):
        return f"{how}; the page or the field changed, nothing pressed"
    # What is about to be pressed, read from the control itself.
    label = " ".join((await button.inner_text()).split())
    if not _SSO_LABEL.search(label) or _MAIL_LABEL.search(label):
        return f"{how}; the SSO button changed, nothing pressed"
    await button.click(timeout=10000)
    shown = label[:40] if "@" not in label else "the SSO button"
    return f'{how}; pressed "{shown}"'


async def _open_start_page(context: Any, url: str, email: str | None, profile_id: str) -> None:
    """The start page, and on it the profile's address when it has one."""
    page = await _show_start_page(context, url)
    if page is None or not email:
        return
    began = asyncio.get_running_loop().time()
    try:
        outcome = await _sign_in_with_sso(page, url, email)
    except Exception as exc:  # noqa: BLE001 - the window stays usable by hand
        # The type only: a driver error can quote the address it was typing.
        logger.info(
            f"Email not entered on the start page for profile {profile_id}: {type(exc).__name__}"
        )
        return
    waited = asyncio.get_running_loop().time() - began
    logger.info(f"Start page sign-in for profile {profile_id} after {waited:.0f}s: {outcome}")


async def _show_start_page(context: Any, url: str) -> Any:
    """Bring the start page up in a tab nobody is using, and return that tab.

    This runs after the launch on purpose. A URL handed to the browser as a
    startup argument is requested before the driver's handlers exist, and the
    proxy's credentials are among them. Tabs that already show something are
    left alone, and nothing is opened (and nothing returned) when a tab is
    already on the site.
    """
    pages = [page for page in context.pages if not page.is_closed()]
    if not pages:
        # The window's first tab can attach a moment after the launch returns.
        try:
            pages = [await context.wait_for_event("page", timeout=3000)]
        except Exception:  # noqa: BLE001 - no tab arrived; open one below
            pages = []
    site = _site(url)
    if any(_site(page.url) == site for page in pages):
        return None
    blank = next((page for page in pages if page.url in _BLANK_PAGES), None)
    page = blank if blank is not None else await context.new_page()
    # "commit": the address is on its way. Waiting for the whole page would only
    # keep this task alive while the person is already using the window.
    await page.goto(url, wait_until="commit", timeout=START_PAGE_TIMEOUT * 1000)
    return page


async def _new_tab(context: Any) -> Any:
    """An empty tab in a window that is already open, or None when none could be had.

    The driver's own `new_page()` always opens another window; it has no way to
    ask for a tab. A page that is already there can: the browser puts what such
    a page opens in a tab beside it. So a page of the window is asked for an
    empty tab, and the caller navigates that tab as it would any page of its
    own, which is the same request as from `new_page()`.

    Pages on a site or blank are asked, newest first; the browser's own
    settings pages cannot be scripted and are not. None is returned when there
    is no such page, or none answers in time, and the caller falls back.
    """
    pages = [page for page in context.pages if not page.is_closed()]
    askable = [
        page
        for page in reversed(pages)
        if page.url in _BLANK_PAGES or page.url.startswith(("https://", "http://"))
    ]
    for opener in askable[:_NEW_TAB_OPENERS]:
        try:
            # Only a blank tab is taken: a tab the person opens on a site in the
            # same moment is theirs.
            async with context.expect_page(
                predicate=lambda page: page.url in _BLANK_PAGES, timeout=NEW_TAB_TIMEOUT * 1000
            ) as arriving:
                await asyncio.wait_for(opener.evaluate(_BLANK_TAB), NEW_TAB_TIMEOUT)
            return await arriving.value
        except Exception:  # noqa: BLE001 - this page could not; another may, or a window will do
            continue
    return None


def _resolve_process_id(obj: Any) -> int | None:
    """Best-effort resolution of a driver/browser OS process id.

    Playwright does not expose this publicly, so this walks known internal
    attributes and returns ``None`` if none are available. It never fabricates a
    placeholder pid. Note: for a persistent context this is the Playwright driver
    process, used only as a forceful-kill fallback.
    """
    candidates = (
        lambda b: b._browser_process.pid,  # noqa: SLF001
        lambda b: b.browser._impl_obj._connection._transport._proc.pid,  # noqa: SLF001
        lambda b: b._impl_obj._connection._transport._proc.pid,  # noqa: SLF001
        lambda b: b._connection._transport._proc.pid,  # noqa: SLF001
        lambda b: b.browser._impl._connection._transport._proc.pid,  # noqa: SLF001
        lambda b: b._impl._connection._transport._proc.pid,  # noqa: SLF001
    )
    for getter in candidates:
        try:
            pid = getter(obj)
            if pid:
                return int(pid)
        except Exception:  # noqa: BLE001 - private attributes vary across versions
            continue
    return None


async def _drain_cleanup(task: asyncio.Task) -> Any:
    """Let owned teardown finish even when its request is cancelled repeatedly."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


class BrowserSession:
    """A single running Camoufox browser tied to a profile."""

    def __init__(
        self,
        profile_id: str,
        camoufox: Any,
        process_id: int | None = None,
        socks_relay: SocksRelay | PipeRelay | None = None,
    ):
        self.profile_id = profile_id
        self.camoufox = camoufox  # AsyncCamoufox context manager instance
        self.process_id = process_id
        self._owned_processes = _OwnedProcessTree(process_id)
        self.started_at = datetime.now()
        self.monitor_task: asyncio.Task | None = None
        self.start_page_task: asyncio.Task | None = None
        self.on_exit: ExitHandler | None = None
        self.on_stopped: ExitHandler | None = None
        self._terminated = False
        self.socks_relay = socks_relay
        self._termination_task: asyncio.Task[None] | None = None
        self.context: Any = None

    async def terminate(self) -> None:
        """Drain one strongly owned teardown, including after caller cancellation."""
        if self._terminated:
            return
        if self._termination_task is None or self._termination_task.done():
            self._termination_task = asyncio.create_task(self._terminate())
        await _drain_cleanup(self._termination_task)

    async def _terminate(self) -> None:
        logger.info(f"Terminating browser session for profile {self.profile_id}")
        # Do not await the monitor: it may itself be waiting for this teardown.
        if self.monitor_task and not self.monitor_task.done():
            self.monitor_task.cancel()
        if self.start_page_task and not self.start_page_task.done():
            self.start_page_task.cancel()

        self._owned_processes.refresh()
        cancelled = False
        sdk_close_failed = False
        if self.socks_relay is not None:
            try:
                await self.socks_relay.close()
            except asyncio.CancelledError:
                cancelled = True
            except Exception as exc:
                logger.warning(f"Proxy relay close failed: {type(exc).__name__}")

        if self.camoufox is not None:
            try:
                await self.camoufox.__aexit__(None, None, None)
            except asyncio.CancelledError:
                cancelled = True
                sdk_close_failed = True
            except Exception as exc:
                sdk_close_failed = True
                logger.warning(f"Browser context close failed: {type(exc).__name__}")

        # Blocking process waits must not freeze API/event-loop admission. The
        # strongly owned task drains this worker even after caller cancellation.
        await asyncio.to_thread(self._owned_processes.finish)
        if sdk_close_failed and self._owned_processes.root is None:
            # Neither SDK acknowledgement nor a pinned process identity proves
            # exit. Retain the session/lease; an explicit SDK retry may recover.
            raise BrowserTerminationError(BROWSER_EXIT_UNCONFIRMED)
        self._terminated = True
        if cancelled:
            raise asyncio.CancelledError

    def info(self) -> dict[str, Any]:
        """Return a serializable summary of this session."""
        return {
            "profile_id": self.profile_id,
            "process_id": self.process_id,
            "started_at": self.started_at.isoformat(),
        }


class BrowserSessionManager:
    """Track and control the browsers currently running."""

    def __init__(self, storage: "StorageManager | None" = None, holder: str | None = None) -> None:
        self.active_sessions: dict[str, BrowserSession] = {}
        # Profile id -> how many launches are currently inside camoufox.start().
        # A count rather than a set, because two concurrent launches of one
        # profile both mark it and the first to leave would otherwise clear the
        # mark while the other is still starting.
        self._starting: dict[str, int] = {}
        # The event loop only holds weak references to tasks, so a teardown
        # suspended inside camoufox.__aexit__ could be garbage-collected and take
        # the primary cleanup path with it. Hold a strong reference until done.
        self._exit_tasks: set[asyncio.Task[None]] = set()
        # Pages still loading in tabs opened by `open_page`; held so they are not collected.
        self._page_tasks: set[asyncio.Task[None]] = set()
        self._closing: dict[str, asyncio.Task[bool]] = {}
        self._release_after_close: set[str] = set()
        # Lease bookkeeping. Without a storage and a holder id (unit tests, and
        # anything that only watches processes) every lease call below is a
        # no-op and this class behaves exactly as it did before leases existed.
        self._storage = storage
        self._holder = holder
        self.on_stopped: ExitHandler | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None

    def start_heartbeat(self, interval: float = 30.0) -> None:
        """Begin renewing this process's leases in the background.

        The heartbeat is the difference between "this profile is running" and
        "this profile was running when someone last crashed": while it beats
        the lease stays alive, and when it stops — SIGKILL, a power cut — the
        lease outlives the browser by at most its TTL and then frees itself.
        """
        if self._storage is None or self._holder is None:
            return
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(interval))

    async def stop_heartbeat(self) -> None:
        """Stop the renewal loop, and wait for the beat in flight to finish."""
        if self._heartbeat_task is None:
            return
        self._heartbeat_task.cancel()
        try:
            await self._heartbeat_task
        except asyncio.CancelledError:
            pass
        self._heartbeat_task = None

    async def _heartbeat_loop(self, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await self._renew_leases()
            except Exception as exc:  # noqa: BLE001 - one bad beat must not stop the loop
                logger.warning(f"Lease heartbeat failed: {exc}")

    async def _renew_leases(self) -> None:
        """Renew every lease we hold, and close any browser whose lease is gone."""
        if self._storage is None or self._holder is None or not self.active_sessions:
            return
        profile_ids = list(self.active_sessions)
        # The configured TTL, not a constant: a hardcoded value here would
        # shorten a deliberately long lease on every beat and let it expire
        # under a browser that is still running.
        ttl_seconds = get_settings().lease_ttl
        renewed = await self._storage.renew_lease(profile_ids, self._holder, ttl_seconds)
        if renewed == len(profile_ids):
            return
        # Something was taken from us. The bulk update already renewed whatever
        # survived; ask one at a time only to learn which ones those were.
        for profile_id in profile_ids:
            if await self._storage.renew_lease([profile_id], self._holder, ttl_seconds) > 0:
                continue
            logger.warning(
                f"Lost the lease on profile {profile_id} — another instance may be "
                "driving this identity; closing the browser."
            )
            # No attempt to take it back: whoever holds it now is already
            # running the profile, and racing them is the corruption itself.
            await self.close(profile_id)

    def is_running(self, profile_id: str) -> bool:
        """Return whether a browser is currently tracked for the profile."""
        return profile_id in self.active_sessions

    def is_live(self, profile_id: str) -> bool:
        """Whether a browser is running *or* still starting for the profile.

        ``is_running`` is false for the whole time ``camoufox.start()`` is
        being awaited, so a lease released on that answer can be taken away
        from a browser that is coming up. Lease decisions use this; the
        user-facing "is it open" answers stay on ``is_running``.
        """
        return profile_id in self.active_sessions or profile_id in self._starting

    def list_active(self) -> list[dict[str, Any]]:
        """Return summaries of the active sessions.

        A pure read: it used to drop sessions whose driver process had gone,
        which skipped ``terminate()`` and the exit handler and so leaked the
        Camoufox context. Teardown belongs to the close event and, failing that,
        to :meth:`_monitor`; both route through ``_handle_exit``.
        """
        return [session.info() for session in self.active_sessions.values()]

    async def launch(
        self,
        profile_id: str,
        launch_options: dict[str, Any],
        on_exit: ExitHandler | None = None,
    ) -> BrowserSession:
        """Launch a Camoufox browser and register a monitored session."""
        if not CAMOUFOX_AVAILABLE:
            raise BrowserLaunchError(
                "Camoufox is not installed. Reinstall Camoufox Profile Manager with its dependencies."
            )
        if profile_id in self.active_sessions:
            return self.active_sessions[profile_id]

        # Counted before the first await: from here until the session is
        # registered, is_live() says this profile is coming up, and nothing
        # may hand its lease away.
        self._starting[profile_id] = self._starting.get(profile_id, 0) + 1
        relay: SocksRelay | PipeRelay | None = None
        camoufox = None
        cookie_directory: Path | None = None
        pending_cookies = None
        try:
            try:
                options = _browser_launch_options(launch_options)
                proxy = options.get("proxy") or {}
                server = proxy.get("server", "")
                # A system proxy on this computer carries the connection to the
                # profile's proxy; without one the paths below are unchanged.
                dial, front = await front_dialer(server) if server else (None, None)
                if front is not None:
                    logger.info(f"Profile {profile_id} reaches its proxy through {front.label}")
                if server.startswith("socks4://") or (
                    server.startswith("socks5://")
                    and (proxy.get("username") or proxy.get("password"))
                ):
                    relay = SocksRelay(
                        proxy["server"],
                        proxy.get("username"),
                        proxy.get("password"),
                        **({"connect": dial} if dial else {}),
                    )
                    await relay.start()
                    options["proxy"] = relay.browser_proxy
                elif dial is not None:
                    relay = PipeRelay(
                        server, dial, proxy.get("username"), proxy.get("password")
                    )
                    await relay.start()
                    options["proxy"] = relay.browser_proxy
                if server.startswith(("socks4://", "socks5://")):
                    prefs = dict(options.get("firefox_user_prefs") or {})
                    prefs["network.proxy.socks_remote_dns"] = True
                    options["firefox_user_prefs"] = prefs
                if options.get("persistent_context") and options.get("user_data_dir"):
                    from .cookie_transfer import take_pending_cookies

                    cookie_directory = Path(options["user_data_dir"])
                    pending_cookies = take_pending_cookies(cookie_directory)
                with _linux_ime_startup_preferences(options):
                    camoufox = AsyncCamoufox(**options)
                    browser = await camoufox.start()
                    if pending_cookies is not None:
                        from .cookie_transfer import apply_pending_cookies

                        assert cookie_directory is not None
                        await apply_pending_cookies(browser, pending_cookies, cookie_directory)
            except Exception as exc:  # noqa: BLE001
                raise BrowserLaunchError(f"Failed to launch browser: {exc}") from exc

            process_id = _resolve_process_id(browser) or _resolve_process_id(camoufox)
            session = BrowserSession(profile_id, camoufox, process_id, socks_relay=relay)
            # Expose the ready Playwright context; automation never reaches into
            # AsyncCamoufox internals or passes destination URLs at startup.
            session.context = browser
            session.on_exit = on_exit
            session.on_stopped = self.on_stopped
            self.active_sessions[profile_id] = session
        except (Exception, asyncio.CancelledError):
            # Keep the starting marker until both browser and relay teardown
            # finish. Cancelling the request must not release a live profile.
            failed_session = BrowserSession(
                profile_id, camoufox, _resolve_process_id(camoufox), socks_relay=relay
            )
            try:
                await failed_session.terminate()
            except BrowserTerminationError:
                # Startup may have produced a browser before the SDK failed.
                # Keep the lease fence and an explicit Close path until exit is
                # confirmed; never turn an unconfirmed child into "not running".
                failed_session.on_stopped = self.on_stopped
                self.active_sessions.setdefault(profile_id, failed_session)
                raise
            finally:
                if cookie_directory is not None and pending_cookies is not None:
                    from .cookie_transfer import discard_pending_ack
                    from .profile_operations import run_file_operation

                    await run_file_operation(
                        discard_pending_ack, cookie_directory, pending_cookies["nonce"]
                    )
            raise
        finally:
            remaining = self._starting.get(profile_id, 1) - 1
            if remaining > 0:
                self._starting[profile_id] = remaining
            else:
                self._starting.pop(profile_id, None)

        # Primary signal: the browser/context closing (e.g. the user closes the window).
        self._register_close_handler(browser, profile_id)
        # Fallback: poll the driver pid, only if we could resolve one.
        if process_id:
            session.monitor_task = asyncio.create_task(self._monitor(profile_id, process_id))

        logger.info(f"Browser launched for profile {profile_id} (pid={process_id})")
        return session

    def open_start_page(self, profile_id: str, url: str, email: str | None = None) -> None:
        """Show `url` in the browser just launched for this profile, in the background.

        With `email`, the address is entered in the page's sign-in form and its
        SSO button is pressed, when the page has them.

        The launch has already succeeded and stays that way: a page that cannot
        be loaded is the browser's to report in its own window, and a window
        closed meanwhile is nothing to report at all.
        """
        session = self.active_sessions.get(profile_id)
        if session is None or session.context is None:
            return
        if not url.startswith(("https://", "http://")):
            logger.warning("Ignoring the start page: it is not an http(s) address")
            return

        def finished(task: asyncio.Task[None]) -> None:
            if not task.cancelled() and task.exception() is not None:
                logger.info(
                    f"Start page not shown for profile {profile_id}: "
                    f"{type(task.exception()).__name__}"
                )

        task = asyncio.create_task(_open_start_page(session.context, url, email, profile_id))
        task.add_done_callback(finished)
        session.start_page_task = task

    async def open_page(self, profile_id: str, url: str) -> Literal["tab", "window"] | None:
        """Open `url` in the browser running for this profile, in a new tab of its window.

        Returns "tab" when it was opened in a tab of a window that was already
        there, and "window" when only a new window of the same browser could be
        had: no page to ask for a tab, or none that answered. None when nothing
        was opened: no browser runs for the profile, or it went away meanwhile.

        Opening is all that is done: nothing is filled in or pressed on the
        page that is opened. The tab is brought up here and its page loads in
        the background, so a slow proxy does not hold the caller. The address
        is never logged: it may carry a secret.
        """
        session = self.active_sessions.get(profile_id)
        if session is None or session.context is None or not url.startswith("https://"):
            return None
        where: Literal["tab", "window"] = "tab"
        try:
            page = await _new_tab(session.context)
            if page is None:
                where = "window"
                page = await session.context.new_page()
        except Exception as exc:  # noqa: BLE001 - a browser closed meanwhile is not an error
            logger.info(f"No page opened for profile {profile_id}: {type(exc).__name__}")
            return None
        if where == "window":
            logger.info(f"No tab could be had for profile {profile_id}; a new window was opened")

        async def show() -> None:
            try:
                # A tab opened beside another is not always the one in front.
                await page.bring_to_front()
            except Exception:  # noqa: BLE001 - the page is there either way
                pass
            try:
                await page.goto(url)
            except Exception as exc:  # noqa: BLE001 - the browser reports it in its own window
                logger.info(f"Page not loaded for profile {profile_id}: {type(exc).__name__}")

        task = asyncio.create_task(show())
        self._page_tasks.add(task)
        task.add_done_callback(self._page_tasks.discard)
        return where

    def _register_close_handler(self, browser: Any, profile_id: str) -> None:
        """Wire Playwright close/disconnect events to session cleanup."""
        loop = asyncio.get_running_loop()

        def finished(task: asyncio.Task[None]) -> None:
            self._exit_tasks.discard(task)
            if not task.cancelled() and task.exception() is not None:
                # Observe the exception even when no request awaits a natural
                # close event. Failed sessions remain tracked for explicit Close.
                logger.warning("Browser close callback did not complete; session remains tracked")

        def _on_close(*_: Any) -> None:
            task = loop.create_task(self._handle_exit(profile_id))
            self._exit_tasks.add(task)
            task.add_done_callback(finished)

        for event in ("close", "disconnected"):
            try:
                browser.on(event, _on_close)
            except Exception:  # noqa: BLE001 - Browser vs BrowserContext expose different events
                continue

    async def _handle_exit(self, profile_id: str) -> None:
        """Tear down a session exactly once and notify the exit handler."""
        await self._close_session(profile_id, release_lease=True, notify=True)

    async def _close_session(
        self, profile_id: str, *, release_lease: bool, notify: bool = False
    ) -> bool:
        task = self._closing.get(profile_id)
        if release_lease:
            self._release_after_close.add(profile_id)
        if task is None:
            session = self.active_sessions.get(profile_id)
            if session is None:
                self._release_after_close.discard(profile_id)
                return False

            async def finish() -> bool:
                try:
                    await session.terminate()
                    stopped = getattr(session, "on_stopped", self.on_stopped)
                    if stopped is not None:
                        await stopped(profile_id)
                    if profile_id in self._release_after_close:
                        await self._release_lease(profile_id)
                    if self.active_sessions.get(profile_id) is session:
                        self.active_sessions.pop(profile_id)
                    if notify and session.on_exit is not None:
                        try:
                            await session.on_exit(profile_id)
                        except Exception as exc:
                            logger.warning(f"on_exit handler failed for {profile_id}: {exc}")
                    return True
                finally:
                    self._closing.pop(profile_id, None)
                    self._release_after_close.discard(profile_id)

            task = asyncio.create_task(finish())
            self._closing[profile_id] = task
        return await _drain_cleanup(task)

    async def close(self, profile_id: str) -> bool:
        """Close one browser, keeping it tracked until teardown completes."""
        return await self._close_session(profile_id, release_lease=False)

    async def close_and_release(self, profile_id: str) -> bool:
        """Drain browser teardown, then release its lease in the same owned task."""
        return await self._close_session(profile_id, release_lease=True)

    async def _release_lease(self, profile_id: str) -> None:
        """Best-effort lease release. Teardown must not fail on bookkeeping."""
        if self._storage is None or self._holder is None:
            return
        try:
            await self._storage.release_lease(profile_id, self._holder)
        except Exception as exc:  # noqa: BLE001 - a stuck release must not block cleanup
            logger.warning(f"Could not release the lease on {profile_id}: {exc}")

    async def close_all(self) -> int:
        """Close every active browser and return how many were closed."""
        count = 0
        failed = False
        for profile_id in list(self.active_sessions.keys()):
            try:
                if await self.close_and_release(profile_id):
                    count += 1
            except BrowserTerminationError:
                failed = True
        if failed:
            raise BrowserTerminationError(BROWSER_EXITS_UNCONFIRMED)
        return count

    async def _monitor(self, profile_id: str, process_id: int) -> None:
        """Fallback watchdog: clean up if the driver process disappears."""
        while psutil.pid_exists(process_id):
            await asyncio.sleep(10)
        await self._handle_exit(profile_id)
