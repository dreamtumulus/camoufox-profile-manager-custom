"""Exercise real lifespan startup/shutdown, which ASGITransport does not run."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from camoufox_pm import main
from camoufox_pm.api import dependencies
from camoufox_pm.core.database import StorageManager
from camoufox_pm.core.profile_manager import ProfileManager


@pytest.fixture
async def startup_database(tmp_path, monkeypatch):
    db_path = tmp_path / "profiles.db"
    storage = StorageManager(str(db_path))
    await storage.initialize()
    manager = ProfileManager(storage, str(tmp_path))
    for name in ("active account", "trashed account"):
        profile = await manager.create_profile(name, generate_fingerprint=False)
        Path(profile.storage_path, "cookies.sqlite").write_bytes(name.encode())
        if name.startswith("trashed"):
            await manager.delete_profile(profile.id)
    await storage.close()
    monkeypatch.setattr(
        main, "settings", main.settings.model_copy(update={"db_path": str(db_path)})
    )
    for variable in ("_storage_manager", "_profile_manager", "_scheduler"):
        monkeypatch.setattr(dependencies, variable, None)
    return db_path


@pytest.mark.asyncio
async def test_real_lifespan_removes_old_backups_and_makes_no_new_ones(startup_database):
    """Profile backups were retired; what an earlier version wrote is cleared at startup."""
    backups = startup_database.parent / "backups"
    backups.mkdir()
    old = "0123456789abcdef0123456789abcdef"
    (backups / f"{old}.zip").write_bytes(b"archive")
    (backups / f"{old}.json").write_text("{}", encoding="utf-8")
    tasks_before = asyncio.all_tasks()
    async with main.lifespan(main.app):
        manager = dependencies.get_profile_manager()
        scheduler = dependencies.get_scheduler()
        assert not backups.exists()
        # Long enough for the scan an earlier version ran right after startup.
        await asyncio.sleep(0.2)
        assert not backups.exists()
        profiles = await manager.list_profiles() + await manager.list_trashed_profiles()
        assert sorted(profile.name for profile in profiles) == ["active account", "trashed account"]
        for profile in profiles:
            assert (
                Path(profile.storage_path, "cookies.sqlite").read_bytes() == profile.name.encode()
            )
        created_tasks = asyncio.all_tasks() - tasks_before
        assert not any(task.get_name() == "profile-backups" for task in created_tasks)
    assert manager.storage.db._connection is None
    assert manager.browser_sessions._heartbeat_task is None
    assert scheduler._task is None
    assert all(task.done() for task in created_tasks)


@pytest.mark.asyncio
async def test_real_lifespan_trims_caches_that_predate_the_limit_once(startup_database):
    megabyte = 1024 * 1024
    limit = main.settings.browser_cache_mb * megabyte
    folders = sorted(startup_database.parent.glob("profiles/profile_*"))
    assert len(folders) == 2
    for folder in folders:
        (folder / "cache2" / "entries").mkdir(parents=True)
        (folder / "cache2" / "entries" / "A1").write_bytes(b"p" * (limit + megabyte))
        (folder / "startupCache").mkdir()
        (folder / "startupCache" / "startupCache.8.little").write_bytes(b"s" * megabyte)
    tasks_before = asyncio.all_tasks()
    async with main.lifespan(main.app):
        deadline = asyncio.get_running_loop().time() + 10
        while any((folder / "cache2").exists() for folder in folders):
            assert asyncio.get_running_loop().time() < deadline, "caches were not trimmed"
            await asyncio.sleep(0.01)
        manager = dependencies.get_profile_manager()
        created_tasks = asyncio.all_tasks() - tasks_before
    for folder in folders:
        assert not (folder / "startupCache").exists()
        assert (folder / "cookies.sqlite").read_bytes().endswith(b" account")
    assert all(task.done() for task in created_tasks)
    assert manager.storage.db._connection is None

    # The next start finds the work recorded as done and leaves a new cache alone.
    (folders[0] / "cache2").mkdir()
    (folders[0] / "cache2" / "A2").write_bytes(b"p" * (limit + megabyte))
    async with main.lifespan(main.app):
        await asyncio.sleep(0.3)
    assert (folders[0] / "cache2" / "A2").exists()


@pytest.mark.asyncio
async def test_failed_lifespan_startup_closes_database_and_background_tasks(
    startup_database, monkeypatch
):
    tasks_before = asyncio.all_tasks()
    monkeypatch.setattr(main.TaskScheduler, "start", AsyncMock(side_effect=RuntimeError("startup")))
    try:
        with pytest.raises(RuntimeError, match="startup"):
            async with main.lifespan(main.app):
                pytest.fail("failed startup yielded an app")
        manager = dependencies.get_profile_manager()
        tasks_after = asyncio.all_tasks() - tasks_before
        assert manager.storage.db._connection is None
        assert manager.browser_sessions._heartbeat_task is None
        assert not tasks_after
    finally:
        # Keep the suite clean even if the startup regression recurs.
        leaked = asyncio.all_tasks() - tasks_before
        for task in leaked:
            task.cancel()
        if leaked:
            await asyncio.gather(*leaked, return_exceptions=True)
        if dependencies._storage_manager is not None:
            await dependencies._storage_manager.close()
