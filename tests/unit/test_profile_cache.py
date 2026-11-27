"""A profile's rebuildable caches are bounded, and nothing of the account is touched."""

import asyncio
import os
from pathlib import Path

import pytest

from camoufox_pm.config import get_settings
from camoufox_pm.core import profile_cache
from camoufox_pm.core import profile_manager as pm_module
from camoufox_pm.core.app_settings import AppSettings

MB = 1024 * 1024


def fill(profile_dir: Path, *, pages: int, startup: int = 3 * MB) -> Path:
    """Give a profile folder caches of the given sizes and some real data."""
    profile_dir = Path(profile_dir)
    (profile_dir / "cache2" / "entries").mkdir(parents=True, exist_ok=True)
    (profile_dir / "cache2" / "index").write_bytes(b"i" * 1024)
    (profile_dir / "cache2" / "entries" / "A1").write_bytes(b"p" * pages)
    (profile_dir / "startupCache").mkdir(exist_ok=True)
    (profile_dir / "startupCache" / "startupCache.8.little").write_bytes(b"s" * startup)
    (profile_dir / "cookies.sqlite").write_bytes(b"signed-in")
    (profile_dir / "storage" / "default").mkdir(parents=True, exist_ok=True)
    (profile_dir / "storage" / "default" / "site.sqlite").write_bytes(b"site data")
    return profile_dir


def account_data_is_intact(profile_dir: Path) -> bool:
    return (profile_dir / "cookies.sqlite").read_bytes() == b"signed-in" and (
        profile_dir / "storage" / "default" / "site.sqlite"
    ).read_bytes() == b"site data"


# -- The limit handed to the browser ---------------------------------------


def test_the_limit_is_given_in_kilobytes_with_automatic_sizing_off():
    options = {"firefox_user_prefs": {"privacy.fingerprintingProtection": True}}
    profile_cache.limit_disk_cache(options, 50)
    assert options["firefox_user_prefs"] == {
        "privacy.fingerprintingProtection": True,
        "browser.cache.disk.smart_size.enabled": False,
        "browser.cache.disk.capacity": 50 * 1024,
    }


def test_a_value_chosen_for_the_launch_wins_and_the_callers_dict_is_not_changed():
    given = {"browser.cache.disk.capacity": 1024}
    options = {"firefox_user_prefs": given}
    profile_cache.limit_disk_cache(options, 50)
    assert options["firefox_user_prefs"]["browser.cache.disk.capacity"] == 1024
    assert given == {"browser.cache.disk.capacity": 1024}


def test_zero_leaves_the_browsers_own_default():
    options: dict = {}
    profile_cache.limit_disk_cache(options, 0)
    assert options == {}


@pytest.fixture
def launches(profile_manager, monkeypatch):
    recorded: list[dict] = []

    class LaunchedSession:
        process_id = 4242

    async def record_launch(profile_id, options, on_exit=None):
        recorded.append(options)
        profile_manager.browser_sessions.active_sessions[profile_id] = LaunchedSession()
        return LaunchedSession()

    monkeypatch.setattr(profile_manager.browser_sessions, "launch", record_launch)
    monkeypatch.setattr(pm_module.fingerprint_store, "resolve", lambda *_args, **_kw: {})
    return recorded


@pytest.mark.asyncio
async def test_every_launch_carries_the_limit_beside_the_profiles_own_preferences(
    profile_manager, launches
):
    profile = await profile_manager.create_profile(name="p")
    await profile_manager.launch_browser(profile.id)
    prefs = launches[0]["firefox_user_prefs"]
    assert prefs["browser.cache.disk.capacity"] == 50 * 1024
    assert prefs["browser.cache.disk.smart_size.enabled"] is False
    # What the profile itself asks of the browser is still there.
    assert any(name.startswith("privacy.") for name in prefs)


@pytest.mark.asyncio
async def test_the_limit_follows_the_setting_and_zero_turns_it_off(
    profile_manager, launches, monkeypatch
):
    first = await profile_manager.create_profile(name="first")
    second = await profile_manager.create_profile(name="second")
    try:
        monkeypatch.setenv("CPM_BROWSER_CACHE_MB", "20")
        get_settings.cache_clear()
        await profile_manager.launch_browser(first.id)
        monkeypatch.setenv("CPM_BROWSER_CACHE_MB", "0")
        get_settings.cache_clear()
        await profile_manager.launch_browser(second.id)
    finally:
        get_settings.cache_clear()
    assert launches[0]["firefox_user_prefs"]["browser.cache.disk.capacity"] == 20 * 1024
    assert "browser.cache.disk.capacity" not in launches[1]["firefox_user_prefs"]


# -- When a browser closes --------------------------------------------------


@pytest.mark.asyncio
async def test_closing_a_browser_removes_only_its_startup_cache(profile_manager):
    profile = await profile_manager.create_profile(name="p", generate_fingerprint=False)
    directory = fill(profile.storage_path, pages=2 * MB)
    await profile_manager._on_browser_stopped(profile.id)
    assert not (directory / "startupCache").exists()
    # The page cache is the browser's to evict; it is still valid for the next start.
    assert (directory / "cache2" / "entries" / "A1").stat().st_size == 2 * MB
    assert account_data_is_intact(directory)


@pytest.mark.asyncio
async def test_a_failed_cleanup_never_stops_the_teardown_that_releases_the_lease(
    profile_manager, monkeypatch
):
    profile = await profile_manager.create_profile(name="p", generate_fingerprint=False)

    def broken(_directory):
        raise PermissionError("in use")

    monkeypatch.setattr(profile_cache, "remove_startup_cache", broken)
    await profile_manager._on_browser_stopped(profile.id)
    await profile_manager._on_browser_stopped("no-such-profile")


@pytest.mark.skipif(os.name == "nt", reason="Creating a symbolic link needs a privilege on Windows")
def test_a_link_in_place_of_a_cache_folder_is_never_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.bin").write_bytes(b"k" * (2 * MB))
    directory = tmp_path / "profile"
    directory.mkdir()
    (directory / "startupCache").symlink_to(outside, target_is_directory=True)
    (directory / "cache2").symlink_to(outside, target_is_directory=True)
    assert profile_cache.remove_startup_cache(directory) is False
    assert profile_cache.trim_closed_profile(directory, 1) == 0
    assert (outside / "keep.bin").stat().st_size == 2 * MB
    assert (directory / "cache2").is_symlink()


# -- Profiles filled before there was a limit -------------------------------


def test_a_page_cache_over_the_limit_is_removed_whole_and_one_under_it_is_kept(tmp_path):
    over = fill(tmp_path / "over", pages=3 * MB)
    under = fill(tmp_path / "under", pages=1 * MB)
    assert profile_cache.trim_closed_profile(over, 2) == 3 * MB + 1024 + 3 * MB
    assert not (over / "cache2").exists() and not (over / "startupCache").exists()
    assert profile_cache.trim_closed_profile(under, 2) == 3 * MB
    assert (under / "cache2" / "index").exists() and not (under / "startupCache").exists()
    assert account_data_is_intact(over) and account_data_is_intact(under)
    # Nothing is left to do a second time.
    assert profile_cache.trim_closed_profile(over, 2) == 0


def test_without_a_limit_the_page_cache_is_left_alone(tmp_path):
    directory = fill(tmp_path / "profile", pages=3 * MB)
    assert profile_cache.trim_closed_profile(directory, 0) == 3 * MB
    assert (directory / "cache2" / "entries" / "A1").exists()


@pytest.mark.asyncio
async def test_existing_profiles_are_trimmed_once_including_trash_but_not_one_in_use(
    profile_manager,
):
    names = ("large", "small", "trashed", "leased", "never opened")
    profiles = {
        name: await profile_manager.create_profile(name=name, generate_fingerprint=False)
        for name in names
    }
    folders = {
        name: fill(profiles[name].storage_path, pages=(1 if name == "small" else 3) * MB)
        for name in names
        if name != "never opened"
    }
    await profile_manager.delete_profile(profiles["trashed"].id)
    assert await profile_manager.storage.acquire_lease(profiles["leased"].id, "elsewhere", 300)

    trimmed, freed = await profile_cache.trim_existing_profiles(profile_manager, 2)

    assert trimmed == 3
    assert freed == 2 * (3 * MB + 1024 + 3 * MB) + 3 * MB
    for name in ("large", "trashed"):
        assert not (folders[name] / "cache2").exists()
        assert not (folders[name] / "startupCache").exists()
    assert (folders["small"] / "cache2" / "index").exists()
    # Another instance holds this one: its folder is not touched at all.
    assert (folders["leased"] / "startupCache").exists()
    assert (folders["leased"] / "cache2" / "entries" / "A1").stat().st_size == 3 * MB
    assert all(account_data_is_intact(folder) for folder in folders.values())
    # Every reservation was handed back, and the other instance still has its own.
    for name in names:
        lease = await profile_manager.storage.get_lease(profiles[name].id)
        assert (lease[0] if lease else None) == ("elsewhere" if name == "leased" else None)

    # Done once: a cache that grows back is the browser's to evict, not ours.
    fill(folders["large"], pages=3 * MB)
    assert await profile_cache.trim_existing_profiles(profile_manager, 2) is None
    assert (folders["large"] / "cache2" / "entries" / "A1").exists()


@pytest.mark.asyncio
async def test_an_interrupted_run_records_nothing_and_starts_over(profile_manager, monkeypatch):
    profile = await profile_manager.create_profile(name="p", generate_fingerprint=False)
    directory = fill(profile.storage_path, pages=3 * MB)

    async def interrupted(*_args):
        raise asyncio.CancelledError

    with monkeypatch.context() as patched:
        patched.setattr(profile_cache, "_trim_reserved", interrupted)
        with pytest.raises(asyncio.CancelledError):
            await profile_cache.trim_existing_profiles(profile_manager, 2)
    assert AppSettings(profile_manager.storage).get("profile_caches_trimmed") is None
    assert (directory / "cache2").exists()

    assert await profile_cache.trim_existing_profiles(profile_manager, 2) == (
        1,
        3 * MB + 1024 + 3 * MB,
    )
    assert not (directory / "cache2").exists()
