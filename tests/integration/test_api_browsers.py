"""Integration tests for the browser-control endpoints.

No browser is started: a stand-in session is registered so the endpoints that
report and close them can be checked without a Camoufox binary.
"""

import pytest

from camoufox_pm.api.dependencies import get_profile_manager
from camoufox_pm.core.browser_session import BrowserSession


class FakeCamoufox:
    """Stands in for a running AsyncCamoufox and records that it was closed."""

    def __init__(self):
        self.exits = 0

    async def __aexit__(self, *_exc):
        self.exits += 1


def register(profile_id: str) -> FakeCamoufox:
    """Register a running browser for a profile."""
    camoufox = FakeCamoufox()
    session = BrowserSession(profile_id, camoufox=camoufox)
    session.process_id = 4242  # Summary-only fixture; never adopt a real PID.
    get_profile_manager().browser_sessions.active_sessions[profile_id] = session
    return camoufox


@pytest.mark.asyncio
async def test_no_browsers_are_reported_when_none_are_running(client):
    body = (await client.get("/api/browsers/active")).json()

    assert body == {"active_browsers": [], "count": 0}


@pytest.mark.asyncio
async def test_a_running_browser_is_listed_with_its_process(client):
    register("p1")

    body = (await client.get("/api/browsers/active")).json()

    assert body["count"] == 1
    assert body["active_browsers"][0]["profile_id"] == "p1"
    assert body["active_browsers"][0]["process_id"] == 4242
    assert body["active_browsers"][0]["started_at"]


@pytest.mark.asyncio
async def test_closing_one_browser_leaves_the_others_running(client):
    first = register("p1")
    second = register("p2")

    result = (await client.post("/api/profiles/p1/close")).json()

    assert result["status"] == "closed"
    assert first.exits == 1
    assert second.exits == 0
    assert (await client.get("/api/browsers/active")).json()["count"] == 1


@pytest.mark.asyncio
async def test_closing_a_browser_that_is_not_running_is_not_an_error(client):
    """The window may already have been closed by hand; saying so beats a 500."""
    response = await client.post("/api/profiles/nope/close")

    assert response.status_code == 200
    assert response.json()["status"] == "not_running"


@pytest.mark.asyncio
async def test_close_all_closes_every_browser_and_counts_them(client):
    browsers = [register("p1"), register("p2"), register("p3")]

    body = (await client.post("/api/browsers/close-all")).json()

    assert body["closed_count"] == 3
    assert [b.exits for b in browsers] == [1, 1, 1]
    assert (await client.get("/api/browsers/active")).json()["count"] == 0


@pytest.mark.asyncio
async def test_launching_an_unknown_profile_is_a_404(client):
    response = await client.post("/api/profiles/nope/launch", json={"headless": True})

    assert response.status_code == 404


@pytest.fixture
def launches(monkeypatch):
    """Answer launches without a browser and record which ones got a start page."""
    from unittest.mock import AsyncMock, Mock

    manager = get_profile_manager()
    launch = AsyncMock(return_value={"status": "launched", "process_id": 4242})
    opened = Mock()
    monkeypatch.setattr(manager, "_launch_browser", launch)
    monkeypatch.setattr(manager.browser_sessions, "open_start_page", opened)
    return launch, opened


@pytest.mark.asyncio
async def test_a_window_opened_by_hand_starts_on_the_configured_page(client, launches):
    from camoufox_pm.config import get_settings

    _, opened = launches
    assert get_settings().start_url == "https://claude.ai"
    created = (await client.post("/api/profiles", json={"name": "by hand"})).json()

    response = await client.post(f"/api/profiles/{created['id']}/launch", json={"headless": False})

    assert response.status_code == 200 and response.json()["status"] == "launched"
    opened.assert_called_once_with(created["id"], "https://claude.ai", email=None)


@pytest.fixture
def environment(monkeypatch):
    """Set or unset CPM_AUTOMATION_ENABLED for the settings the app reads."""
    from camoufox_pm.config import get_settings

    def use(value: str | None) -> None:
        if value is None:
            monkeypatch.delenv("CPM_AUTOMATION_ENABLED", raising=False)
        else:
            monkeypatch.setenv("CPM_AUTOMATION_ENABLED", value)
        get_settings.cache_clear()

    use(None)
    yield use
    use(None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "named,switched,entered",
    [
        (None, None, None),  # nobody turned it on: nothing is done in the browser
        (None, True, "miyu@sso.example"),  # turned on from the Settings screen
        (None, False, None),
        ("true", None, "miyu@sso.example"),  # turned on in the environment
        ("false", None, None),
    ],
)
async def test_a_window_opened_by_hand_is_handed_the_address_only_with_automation_on(
    client, launches, environment, named, switched, entered
):
    _, opened = launches
    if switched is not None:
        saved = await client.put("/api/v1/system/automation", json={"enabled": switched})
        assert saved.status_code == 200
    environment(named)
    created = (await client.post("/api/profiles", json={"name": "with address"})).json()
    await get_profile_manager().storage.db.set_profile_email(
        created["id"], "miyu@sso.example", created["row_version"]
    )

    response = await client.post(f"/api/profiles/{created['id']}/launch", json={"headless": False})

    assert response.status_code == 200
    opened.assert_called_once_with(created["id"], "https://claude.ai", email=entered)
    # The address goes to the browser, not back to whoever asked for the launch.
    assert "sso.example" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "start_url,headless,status",
    [
        ("https://claude.ai", True, "launched"),  # nobody is looking at a headless browser
        ("https://claude.ai", False, "already_running"),  # its tabs are in use
        ("", False, "launched"),  # the start page is switched off
        ("   ", False, "launched"),
    ],
)
async def test_no_start_page_where_it_would_not_be_wanted(
    client, launches, monkeypatch, start_url, headless, status
):
    from camoufox_pm.config import get_settings

    launch, opened = launches
    launch.return_value = {"status": status, "process_id": 4242}
    monkeypatch.setattr(get_settings(), "start_url", start_url)
    created = (await client.post("/api/profiles", json={"name": "quiet"})).json()

    response = await client.post(
        f"/api/profiles/{created['id']}/launch", json={"headless": headless}
    )

    assert response.status_code == 200
    opened.assert_not_called()


@pytest.mark.asyncio
async def test_a_clone_is_created_as_a_new_profile(client):
    created = (await client.post("/api/profiles", json={"name": "source"})).json()

    cloned = await client.post(f"/api/profiles/{created['id']}/clone", json={"new_name": "copy"})

    assert cloned.status_code == 201
    assert cloned.json()["name"] == "copy"
    assert cloned.json()["id"] != created["id"]
    assert (await client.get(f"/api/profiles/{created['id']}")).status_code == 200


@pytest.mark.asyncio
async def test_cloning_an_unknown_profile_is_a_404(client):
    response = await client.post("/api/profiles/nope/clone", json={"new_name": "copy"})

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_launching_a_profile_leased_elsewhere_is_a_conflict(client):
    """409, not 500: the client can retry once the other holder closes it."""
    created = (await client.post("/api/profiles", json={"name": "leased"})).json()
    manager = get_profile_manager()
    await manager.storage.acquire_lease(created["id"], "other-host:999:c0ffee", 120)

    response = await client.post(f"/api/profiles/{created['id']}/launch", json={"headless": True})

    assert response.status_code == 409
    assert "leased by another holder" in response.json()["detail"]


@pytest.mark.asyncio
async def test_exporting_a_profile_leased_elsewhere_is_a_conflict(client):
    created = (await client.post("/api/profiles", json={"name": "leased"})).json()
    manager = get_profile_manager()
    await manager.storage.acquire_lease(created["id"], "other-host:999:c0ffee", 120)

    response = await client.get(f"/api/profiles/{created['id']}/export")

    assert response.status_code == 409
