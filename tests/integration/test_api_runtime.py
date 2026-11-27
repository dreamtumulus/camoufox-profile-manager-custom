"""Browser/update API safety: explicit installs and links only for a confirmed release."""

from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from camoufox_pm.api.dependencies import get_profile_manager
from camoufox_pm.api.routes import runtime
from camoufox_pm.config import get_settings
from camoufox_pm.core.browser_install import BrowserStatus
from camoufox_pm.core.updates import RELEASES, UpdateStatus


@pytest.fixture(autouse=True)
def isolate_auth(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CPM_API_KEY", raising=False)
    monkeypatch.delenv("CPM_SECRET_KEY", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def available_update(monkeypatch):
    check = AsyncMock(
        return_value=UpdateStatus(
            latest_version="999.0.0", available=True, release_url=f"{RELEASES}/tag/v999.0.0"
        )
    )
    monkeypatch.setattr(runtime, "check_update", check)
    return check


@pytest.mark.parametrize("prefix", ["/api/v1", "/api"])
async def test_readiness_get_never_starts_installation(client, monkeypatch, prefix):
    installer = Mock()
    installer.status.return_value = BrowserStatus(installed=False, message="Not installed")
    monkeypatch.setattr(runtime, "browser_installer", installer)
    response = await client.get(f"{prefix}/system/browser")
    assert response.status_code == 200
    assert response.json()["data"]["installed"] is False
    assert response.json()["data"]["state"] == "idle"
    installer.start.assert_not_called()


async def test_explicit_post_starts_download_and_returns_pollable_progress(client, monkeypatch):
    installer = Mock()
    installer.start.return_value = BrowserStatus(
        installed=False, state="downloading", progress=42, message="Downloading"
    )
    monkeypatch.setattr(runtime, "browser_installer", installer)
    response = await client.post("/api/v1/system/browser/install")
    assert response.status_code == 200
    assert response.json()["data"]["state"] == "downloading"
    assert response.json()["data"]["progress"] == 42
    installer.start.assert_called_once_with()


@pytest.mark.parametrize("prefix", ["/api", "/api/v1"])
async def test_browser_download_token_is_one_request_only(client, monkeypatch, prefix):
    token = "github_pat_test_value"
    installer = Mock()
    installer.start.return_value = BrowserStatus(installed=False, state="downloading")
    installer.status.return_value = BrowserStatus(installed=False, state="downloading")
    monkeypatch.setattr(runtime, "browser_installer", installer)
    response = await client.post(f"{prefix}/system/browser/install", json={"github_token": token})
    assert response.status_code == 200
    assert token not in response.text
    installer.start.assert_called_once()
    assert installer.start.call_args.kwargs["github_token"].get_secret_value() == token
    assert token not in (await client.get(f"{prefix}/system/browser")).text
    installer.start.reset_mock()
    assert (await client.post(f"{prefix}/system/browser/install")).status_code == 200
    installer.start.assert_called_once_with()


@pytest.mark.parametrize("token", ["secret " * 50, "secret\nvalue", "secret\x00value", 123])
@pytest.mark.parametrize("prefix", ["/api", "/api/v1"])
async def test_invalid_download_token_does_not_leak_through_validation(
    client, monkeypatch, prefix, token
):
    installer = Mock()
    monkeypatch.setattr(runtime, "browser_installer", installer)
    response = await client.post(f"{prefix}/system/browser/install", json={"github_token": token})
    assert response.status_code == 422
    assert '"input"' not in response.text and '"ctx"' not in response.text
    assert "secret" not in response.text
    installer.start.assert_not_called()


async def test_browser_download_token_requires_admin_session(client, monkeypatch):
    from camoufox_pm.api.dependencies import get_storage_manager
    from camoufox_pm.core import auth

    storage = get_storage_manager()
    password = "test-password-149"
    await storage.create_user(auth.new_user_id(), "admin", auth.hash_password(password))
    await storage.create_user(
        auth.new_user_id(), "operator", auth.hash_password(password), role="operator"
    )
    assert (
        await client.post("/api/v1/auth/login", json={"username": "operator", "password": password})
    ).status_code == 200
    installer = Mock()
    monkeypatch.setattr(runtime, "browser_installer", installer)
    response = await client.post(
        "/api/v1/system/browser/install", json={"github_token": "github_pat_test_value"}
    )
    assert response.status_code == 403
    installer.start.assert_not_called()


async def test_runtime_routes_use_existing_api_auth_guard(client, monkeypatch):
    monkeypatch.setenv("CPM_API_KEY", "test-runtime-key")
    get_settings.cache_clear()
    installer = Mock()
    monkeypatch.setattr(runtime, "browser_installer", installer)
    for path, method in [
        ("/system/browser", "GET"),
        ("/system/browser/install", "POST"),
        ("/system/updates", "GET"),
        ("/system/updates/prepare", "POST"),
    ]:
        response = await client.request(method, f"/api/v1{path}")
        assert response.status_code == 401
    installer.start.assert_not_called()
    installer.status.assert_not_called()


async def test_update_check_failure_is_a_status_result_not_an_available_update(client, monkeypatch):
    monkeypatch.setattr(
        runtime, "check_update", AsyncMock(return_value=UpdateStatus(error="Network unavailable"))
    )
    response = await client.get("/api/v1/system/updates")
    assert response.status_code == 200
    body = response.json()["data"]
    assert not body["available"]
    assert body["error"] == "Network unavailable"


@pytest.mark.parametrize(
    ("update", "status"),
    [(UpdateStatus(error="Network unavailable"), 503), (UpdateStatus(available=False), 409)],
)
async def test_prepare_requires_a_successful_available_release(client, monkeypatch, update, status):
    monkeypatch.setattr(runtime, "check_update", AsyncMock(return_value=update))
    response = await client.post("/api/v1/system/updates/prepare")
    assert response.status_code == status
    assert "release_url" not in response.text


async def create_profile(client, name):
    response = await client.post(
        "/api/v1/profiles", json={"name": name, "generate_fingerprint": False}
    )
    assert response.status_code == 201
    profile = response.json()
    Path(profile["storage_path"], "cookies.sqlite").write_bytes(f"cookies for {name}".encode())
    return profile


async def test_prepare_returns_the_official_link_without_copying_any_profile(
    client, available_update
):
    """Profile backups were retired: preparing an update must not write archives.

    A profile another instance holds no longer stands in the way either, since
    nothing reads its data.
    """
    active = await create_profile(client, "Active profile")
    trashed = await create_profile(client, "Trashed profile")
    leased = await create_profile(client, "Leased profile")
    assert (await client.delete(f"/api/v1/profiles/{trashed['id']}")).status_code == 200
    manager = get_profile_manager()
    assert await manager.storage.acquire_lease(leased["id"], "another-instance", 300)
    response = await client.post("/api/v1/system/updates/prepare")
    assert response.status_code == 200
    assert response.json()["data"] == {
        "release_url": f"{RELEASES}/tag/v999.0.0",
        "message": "Close the application before installing the update.",
    }
    assert not (manager.data_dir / "backups").exists()
    for profile in (active, trashed, leased):
        assert (
            Path(profile["storage_path"], "cookies.sqlite").read_bytes()
            == f"cookies for {profile['name']}".encode()
        )
    assert len((await client.get("/api/v1/trash/profiles")).json()["data"]["profiles"]) == 1
