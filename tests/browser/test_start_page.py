"""The start page in a real browser: which tab it lands in, and what it leaves alone.

uv run pytest -m browser tests/browser/test_start_page.py
"""

import asyncio

import pytest

from camoufox_pm.core.browser_session import BrowserSessionManager
from camoufox_pm.core.models import BrowserSettings, Profile
from tests.browser.support import offline_launch


def launch_options(directory) -> dict:
    profile = Profile(name="start page", browser_settings=BrowserSettings(os="windows"))
    options = offline_launch(profile.to_camoufox_launch_options())
    options["headless"] = True
    options["user_data_dir"] = str(directory)
    return options


@pytest.mark.browser
@pytest.mark.asyncio
async def test_start_page_replaces_the_blank_tab_of_a_fresh_window(tmp_path, local_sites):
    manager = BrowserSessionManager()
    session = await manager.launch("p", launch_options(tmp_path / "profile"))
    try:
        before = [page.url for page in session.context.pages]
        manager.open_start_page("p", local_sites.first)
        await asyncio.wait_for(session.start_page_task, 60)
        # One tab, on the start page: the blank tab was used, not joined by another.
        assert [page.url for page in session.context.pages] == [local_sites.first], before
    finally:
        await manager.close("p")


@pytest.mark.browser
@pytest.mark.asyncio
async def test_start_page_leaves_a_tab_in_use_alone_and_is_not_opened_twice(tmp_path, local_sites):
    manager = BrowserSessionManager()
    session = await manager.launch("p", launch_options(tmp_path / "profile"))
    try:
        for page in session.context.pages:
            await page.goto(local_sites.second)  # every tab now shows somebody's page
        manager.open_start_page("p", local_sites.first)
        await asyncio.wait_for(session.start_page_task, 60)
        assert sorted(page.url for page in session.context.pages) == sorted(
            [local_sites.first, local_sites.second]
        )

        # Asked again while a tab is on the site: nothing new.
        manager.open_start_page("p", local_sites.first)
        await asyncio.wait_for(session.start_page_task, 60)
        assert len(session.context.pages) == 2
    finally:
        await manager.close("p")
