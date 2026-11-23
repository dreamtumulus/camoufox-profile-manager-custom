"""A link opened in a running browser lands in a tab of its window, not in another window.

uv run pytest -m browser tests/browser/test_link_tab.py

The driver's `new_page()` always opens a window; whether a tab was had instead
can only be told by counting the browser's windows. That is done with macOS's
window list, so these run on macOS only, and with a window on screen.
"""

import asyncio
import sys

import psutil
import pytest

from camoufox_pm.core import browser_session
from camoufox_pm.core.browser_session import BrowserSessionManager
from camoufox_pm.core.models import BrowserSettings, Profile
from tests.browser.support import offline_launch

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(sys.platform != "darwin", reason="windows are counted with macOS's list"),
]


def launch_options(directory) -> dict:
    profile = Profile(name="link tab", browser_settings=BrowserSettings(os="macos"))
    options = offline_launch(profile.to_camoufox_launch_options())
    options["headless"] = False
    options["user_data_dir"] = str(directory)
    return options


def windows_of(directory) -> int:
    """How many ordinary windows the browser on this data directory has on screen."""
    quartz = pytest.importorskip("Quartz")
    pids = set()
    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            if any(str(directory) in part for part in process.info["cmdline"] or []):
                pids.add(process.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    listed = quartz.CGWindowListCopyWindowInfo(
        quartz.kCGWindowListOptionOnScreenOnly | quartz.kCGWindowListExcludeDesktopElements,
        quartz.kCGNullWindowID,
    )
    return sum(
        1
        for window in listed
        if window.get("kCGWindowOwnerPID") in pids
        and window.get("kCGWindowLayer") == 0
        and window.get("kCGWindowBounds", {}).get("Width", 0) > 300
        and window.get("kCGWindowBounds", {}).get("Height", 0) > 200
    )


async def settled(directory) -> int:
    await asyncio.sleep(1.5)  # a window takes a moment to be listed, or to go
    return windows_of(directory)


@pytest.mark.asyncio
async def test_the_drivers_own_new_page_is_another_window(tmp_path, local_sites):
    """What the tab is had instead of. If this stops holding, `_new_tab` is no longer needed."""
    directory = tmp_path / "profile"
    manager = BrowserSessionManager()
    session = await manager.launch("p", launch_options(directory))
    try:
        await session.context.pages[0].goto(local_sites.first)
        assert await settled(directory) == 1
        page = await session.context.new_page()
        assert await settled(directory) == 2
        await page.close()
    finally:
        await manager.close("p")


@pytest.mark.asyncio
async def test_a_tab_is_had_in_the_window_that_is_there(tmp_path, local_sites):
    directory = tmp_path / "profile"
    manager = BrowserSessionManager()
    session = await manager.launch("p", launch_options(directory))
    try:
        there = session.context.pages[0]
        await there.goto(local_sites.first)

        tab = await browser_session._new_tab(session.context)

        assert tab is not None and tab is not there
        await tab.goto(local_sites.second)
        assert await settled(directory) == 1, "a tab, not another window"
        assert [page.url for page in session.context.pages] == [
            local_sites.first,
            local_sites.second,
        ]
        # The tab knows nothing of the page that asked for it.
        assert await tab.evaluate("[String(window.opener), document.referrer]") == ["null", ""]
    finally:
        await manager.close("p")


@pytest.mark.asyncio
async def test_a_link_is_opened_in_a_tab_whatever_the_page_there_shows(tmp_path, local_sites):
    directory = tmp_path / "profile"
    manager = BrowserSessionManager()
    session = await manager.launch("p", launch_options(directory))
    try:
        # A fresh window's blank tab, as after Run with no start page.
        # Nothing answers at the address: the tab is what is looked at, not the page.
        assert await manager.open_page("p", "https://127.0.0.1:9/authorize") == "tab"
        await asyncio.gather(*list(manager._page_tasks))
        assert await settled(directory) == 1
        assert len(session.context.pages) == 2

        # And beside a page on a site.
        await session.context.pages[0].goto(local_sites.first)
        assert await manager.open_page("p", "https://127.0.0.1:9/again") == "tab"
        await asyncio.gather(*list(manager._page_tasks))
        assert await settled(directory) == 1
        assert len(session.context.pages) == 3
        assert session.context.pages[0].url == local_sites.first, "the page there is left alone"
    finally:
        await manager.close("p")
