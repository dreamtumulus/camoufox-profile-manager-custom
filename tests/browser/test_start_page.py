"""The start page in a real browser: which tab it lands in, and what it leaves alone.

uv run pytest -m browser tests/browser/test_start_page.py
"""

import asyncio

import pytest

from camoufox_pm.core import browser_session
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


ADDRESS = "miyu.ogura@sso.example"


async def form_state(page) -> tuple[str, str]:
    """What the field holds, and which buttons were pressed, as the page recorded them."""
    return (
        await page.locator("#address").input_value(),
        (await page.locator("#pressed").text_content()).strip(),
    )


@pytest.mark.browser
@pytest.mark.asyncio
async def test_start_page_gets_the_address_and_only_its_sso_button_is_pressed(
    tmp_path, local_sites
):
    manager = BrowserSessionManager()
    session = await manager.launch("p", launch_options(tmp_path / "profile"))
    try:
        manager.open_start_page("p", local_sites.first + "login", email=ADDRESS)
        await asyncio.wait_for(session.start_page_task, 120)
        (page,) = session.context.pages
        # Entered, and SSO pressed once. "Continue with email" would have sent mail.
        assert await form_state(page) == (ADDRESS, "sso")
    finally:
        await manager.close("p")


@pytest.mark.browser
@pytest.mark.asyncio
async def test_sign_in_form_variants_in_a_real_page(tmp_path, local_sites, monkeypatch):
    monkeypatch.setattr(browser_session, "EMAIL_FIELD_WAIT", 3.0)
    monkeypatch.setattr(browser_session, "SSO_WAIT_AFTER_FILL", 3.0)
    monkeypatch.setattr(browser_session, "SSO_WAIT_AFTER_TYPING", 3.0)
    start = local_sites.first + "login"
    manager = BrowserSessionManager()
    session = await manager.launch("p", launch_options(tmp_path / "profile"))
    try:
        # A profile without an address: the page opens and nothing on it is touched.
        manager.open_start_page("p", start)
        await asyncio.wait_for(session.start_page_task, 120)
        (page,) = session.context.pages
        assert await form_state(page) == ("", "")

        plain = "miyu.ogura@plain.example"
        other = "someone@sso.example"
        cases = [
            # A form that only reads keys gets the address again as keys.
            (f"{start}?offer=keyup", ADDRESS, (ADDRESS, "sso")),
            # The site takes its time to offer SSO: waited for, then pressed.
            (f"{start}?delay=1500", ADDRESS, (ADDRESS, "sso")),
            # Shown greyed out first: pressed once it is enabled, not before.
            (f"{start}?disabled=1500", ADDRESS, (ADDRESS, "sso")),
            # The page grows under the pointer on its way to the button.
            (f"{start}?shift=400", ADDRESS, (ADDRESS, "sso")),
            # The form sits inside an element that is itself a button and holds the
            # same letters: the SSO button is pressed, not that element's middle.
            (f"{start}?wrap=1", ADDRESS, (ADDRESS, "sso")),
            # No single sign-on for this domain: entered, nothing pressed.
            (start, plain, (plain, "")),
            # A field that already holds something is left alone.
            (f"{start}?held={other}", ADDRESS, (other, "")),
            # Edited after it was entered: SSO is offered, but not for our address.
            (f"{start}?offer=never&edited={other}", ADDRESS, (other, "")),
            # The same form on another site than the start page's.
            (local_sites.second + "login", ADDRESS, ("", "")),
        ]
        for address, email, expected in cases:
            await page.goto(address)
            await browser_session._sign_in_with_sso(page, start, email)
            assert await form_state(page) == expected, address

        # A page with no form at all: the wait ends quietly.
        await page.goto(local_sites.first)
        await browser_session._sign_in_with_sso(page, start, ADDRESS)
        assert page.url == local_sites.first
    finally:
        await manager.close("p")
