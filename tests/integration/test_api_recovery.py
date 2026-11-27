"""Recovery API actions are explicit, guarded and never overwrite a profile."""

import pytest

from camoufox_pm.api.dependencies import get_profile_manager


async def create(client):
    result = await client.post(
        "/api/v1/profiles", json={"name": "Recoverable", "generate_fingerprint": False}
    )
    assert result.status_code == 201
    return result.json()


@pytest.mark.asyncio
async def test_trash_restore_and_explicit_permanent_delete(client):
    profile = await create(client)
    profile_id = profile["id"]
    assert (await client.delete(f"/api/v1/profiles/{profile_id}")).status_code == 200
    assert (await client.get(f"/api/v1/profiles/{profile_id}")).status_code == 404
    trash = (await client.get("/api/v1/trash/profiles")).json()["data"]["profiles"]
    assert trash[0]["id"] == profile_id and trash[0]["deleted_at"]
    denied = await client.delete(f"/api/v1/trash/profiles/{profile_id}")
    assert denied.status_code == 400
    restored = await client.post(f"/api/v1/trash/profiles/{profile_id}/restore")
    assert restored.status_code == 200
    assert restored.json()["data"]["id"] == profile_id
    await client.delete(f"/api/v1/profiles/{profile_id}")
    deleted = await client.delete(f"/api/v1/trash/profiles/{profile_id}?confirm=true")
    assert deleted.status_code == 200
    assert (await client.get("/api/v1/trash/profiles")).json()["data"]["profiles"] == []


@pytest.mark.asyncio
async def test_lease_conflicts_are_409(client):
    profile = await create(client)
    profile_id = profile["id"]
    storage = get_profile_manager().storage
    await storage.acquire_lease(profile_id, "remote", 300)
    assert (await client.delete(f"/api/v1/profiles/{profile_id}")).status_code == 409
    assert (
        await client.post(f"/api/v1/profiles/{profile_id}/clone", json={"new_name": "copy"})
    ).status_code == 409
    await storage.release_lease(profile_id, "remote")
    await client.delete(f"/api/v1/profiles/{profile_id}")
    await storage.acquire_lease(profile_id, "remote", 300, include_deleted=True)
    assert (await client.post(f"/api/v1/trash/profiles/{profile_id}/restore")).status_code == 409
    assert (
        await client.delete(f"/api/v1/trash/profiles/{profile_id}?confirm=true")
    ).status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/system/backups"),
        ("POST", "/system/backups"),
        ("DELETE", "/system/backups/0123456789abcdef0123456789abcdef?confirm=true"),
        ("POST", "/system/backups/0123456789abcdef0123456789abcdef/restore"),
    ],
)
async def test_retired_backup_endpoints_are_gone_and_write_nothing(client, method, path):
    await create(client)
    response = await client.request(method, f"/api/v1{path}", json={})
    assert response.status_code in (404, 405)
    assert not (get_profile_manager().data_dir / "backups").exists()
