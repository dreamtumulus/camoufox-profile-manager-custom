"""Recovery must preserve existing identities and refuse live browser data."""

import asyncio
import threading
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from camoufox_pm.core import profile_archive
from camoufox_pm.core.cleanup import ProfileCleanupManager
from camoufox_pm.core.database import StorageManager
from camoufox_pm.core.leases import ProfileLocked
from camoufox_pm.core.models import Schedule, ScheduleAction, ScheduleKind
from camoufox_pm.core.profile_operations import profile_operation
from camoufox_pm.core.scheduler import TaskScheduler


async def seeded(manager):
    profile = await manager.create_profile(
        "account",
        generate_fingerprint=False,
        proxy_config={
            "type": "http",
            "server": "localhost:8080",
            "username": "fixture-user",
            "password": "secret",
        },
    )
    path = Path(profile.storage_path)
    (path / "cookies.sqlite").write_bytes(b"identity")
    profile.fingerprint = {"screen.width": 1920}
    await manager.storage.save_profile(profile)
    return profile, path


@pytest.mark.asyncio
async def test_trash_is_hidden_but_retains_data_and_pauses_schedules(profile_manager):
    profile, path = await seeded(profile_manager)
    schedule = Schedule(
        profile_id=profile.id,
        action=ScheduleAction.LAUNCH,
        kind=ScheduleKind.INTERVAL,
        interval_minutes=5,
    )
    await profile_manager.storage.save_schedule(schedule)
    assert await profile_manager.delete_profile(profile.id)
    assert await profile_manager.list_profiles() == []
    assert await profile_manager.storage.count_profiles() == 0
    assert await profile_manager.storage.get_profile(profile.id) is None
    assert (path / "cookies.sqlite").read_bytes() == b"identity"
    assert not (await profile_manager.storage.get_schedule(schedule.id)).enabled
    scheduler = TaskScheduler(profile_manager.storage, profile_manager)
    profile_manager.launch_browser = AsyncMock()
    await scheduler.run_now(schedule)
    profile_manager.launch_browser.assert_not_called()
    restored = await profile_manager.restore_trashed_profile(profile.id)
    assert restored.id == profile.id and restored.deleted_at is None
    assert not (await profile_manager.storage.get_schedule(schedule.id)).enabled


@pytest.mark.asyncio
async def test_trash_survives_cleanup_even_with_a_stale_orphan_list(profile_manager):
    profile, path = await seeded(profile_manager)
    await profile_manager.delete_profile(profile.id)
    cleanup = ProfileCleanupManager(
        str(profile_manager.data_dir), str(profile_manager.storage.db.db_path)
    )
    await cleanup.initialize()
    try:
        assert await cleanup.find_orphaned_profile_directories() == []
        assert (
            await cleanup.cleanup_orphaned_directories(
                [{"path": path, "profile_id": profile.id, "size_mb": 0}], confirm=False
            )
            == 0
        )
        assert path.exists()
    finally:
        await cleanup.close()


@pytest.mark.asyncio
async def test_permanent_delete_requires_trash_and_rejects_remote_lease(profile_manager):
    profile, path = await seeded(profile_manager)
    assert not await profile_manager.permanently_delete_profile(profile.id)
    await profile_manager.delete_profile(profile.id)
    assert await profile_manager.storage.acquire_lease(
        profile.id, "other", 300, include_deleted=True
    )
    with pytest.raises(ProfileLocked):
        await profile_manager.permanently_delete_profile(profile.id)
    assert path.exists()
    await profile_manager.storage.release_lease(profile.id, "other")
    assert await profile_manager.permanently_delete_profile(profile.id)
    assert not path.exists()
    assert await profile_manager.storage.get_profile(profile.id, include_deleted=True) is None


@pytest.mark.asyncio
async def test_leased_profile_cannot_be_deleted_cloned_or_exported(profile_manager):
    profile, path = await seeded(profile_manager)
    await profile_manager.storage.acquire_lease(profile.id, "remote", 300)
    with pytest.raises(ProfileLocked):
        await profile_manager.delete_profile(profile.id)
    with pytest.raises(ProfileLocked):
        await profile_manager.clone_profile(profile.id, "clone")
    with pytest.raises(ProfileLocked):
        await profile_manager.export_profile(profile.id, profile_manager.data_dir / "export.zip")
    assert path.exists()


@pytest.mark.asyncio
async def test_operation_lock_blocks_other_instance_launch_and_releases(profile_manager):
    profile, _ = await seeded(profile_manager)
    other = StorageManager(str(profile_manager.storage.db.db_path))
    await other.initialize()
    try:
        async with profile_operation(profile_manager, profile.id):
            assert not await other.acquire_lease(profile.id, "another-instance", 300)
        assert await other.acquire_lease(profile.id, "another-instance", 300)
    finally:
        await other.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancellations", [1, 3])
async def test_cancelled_export_keeps_lease_until_io_finishes(
    profile_manager, monkeypatch, tmp_path, cancellations
):
    profile, _ = await seeded(profile_manager)
    started, finish = threading.Event(), threading.Event()
    original = profile_archive.export_profile

    def slow(*args, **kwargs):
        started.set()
        assert finish.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(profile_archive, "export_profile", slow)
    task = asyncio.create_task(
        profile_manager.export_profile(profile.id, tmp_path / "cancelled.zip")
    )
    while not started.is_set():
        await asyncio.sleep(0.005)
    for _ in range(cancellations):
        task.cancel()
        await asyncio.sleep(0.01)
        assert not await profile_manager.storage.acquire_lease(profile.id, "racing-launch", 300)
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await profile_manager.storage.get_lease(profile.id))[0] is None


@pytest.mark.asyncio
async def test_legacy_relative_profile_path_survives_changed_working_directory(
    profile_manager, monkeypatch, tmp_path
):
    profile, path = await seeded(profile_manager)
    profile.storage_path = f"data/profiles/profile_{profile.id}"
    await profile_manager.storage.save_profile(profile)
    new_cwd = tmp_path / "desktop-shortcut-working-directory"
    new_cwd.mkdir()
    monkeypatch.chdir(new_cwd)
    loaded = await profile_manager.get_profile(profile.id)
    assert Path(loaded.storage_path) == path.resolve()
    exported = await profile_manager.export_profile(profile.id, tmp_path / "relative.zip")
    fresh = await profile_manager.import_profile(exported)
    assert Path(fresh.storage_path, "cookies.sqlite").read_bytes() == b"identity"
    await profile_manager.delete_profile(profile.id)
    assert await profile_manager.permanently_delete_profile(profile.id)
    assert not path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_kind", ["password_without_username", "socks4_credentials"])
async def test_legacy_unsupported_proxy_still_exports_and_imports(
    profile_manager, tmp_path, legacy_kind
):
    from camoufox_pm.core.models import ProxyType

    profile, path = await seeded(profile_manager)
    # Model loading/storage deliberately stay permissive for pre-existing data;
    # only new explicit configuration and launches enforce authentication policy.
    if legacy_kind == "password_without_username":
        profile.proxy.username = None
    else:
        profile.proxy.type = ProxyType.SOCKS4
    await profile_manager.storage.save_profile(profile)
    exported = await profile_manager.export_profile(profile.id, tmp_path / "legacy.camoufox.zip")
    assert exported.is_file() and exported.stat().st_size > 0
    restored = await profile_manager.import_profile(exported)
    assert restored.id != profile.id and restored.proxy == profile.proxy
    assert Path(restored.storage_path, "cookies.sqlite").read_bytes() == b"identity"
    assert (path / "cookies.sqlite").read_bytes() == b"identity"
    with pytest.raises(ValueError):
        restored.to_camoufox_launch_options()
