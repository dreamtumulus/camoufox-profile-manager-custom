"""Keep what the browser can rebuild from taking over a profile's folder.

A profile that has been used is mostly cache: measured on a real installation,
`cache2` (pages and their resources) and `startupCache` (compiled browser code)
were over half of every profile, and `cache2` has no ceiling of its own short of
the browser's default of up to a gigabyte. Neither holds anything of the account:
cookies, logins, site storage and history live elsewhere in the folder.

Three things keep them small. The browser is told its limit at launch and evicts
down to it by itself. `startupCache` is removed when a browser closes, since the
next start rebuilds it. And profiles that were filled before there was a limit
are trimmed once, because a closed browser cannot evict anything.
"""

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any

from loguru import logger

from .app_settings import AppSettings
from .leases import ProfileLocked
from .profile_operations import profile_data_path, profile_operation, run_file_operation

HTTP_CACHE = "cache2"
STARTUP_CACHE = "startupCache"

# Recorded once every profile that existed before the limit has been looked at.
_TRIMMED = "profile_caches_trimmed"


def limit_disk_cache(options: dict[str, Any], megabytes: int) -> None:
    """Add the browser's cache ceiling to launch options; zero leaves it alone.

    Verified on Camoufox 158: with these two preferences the browser brings
    `cache2` back under the limit within about a minute of exceeding it, and
    keeps it there across restarts. Without the first one the browser sizes the
    cache from free disk space and ignores the second.
    """
    if megabytes <= 0:
        return
    prefs = dict(options.get("firefox_user_prefs") or {})
    # A value given for this launch, or by the profile, wins over the default.
    prefs.setdefault("browser.cache.disk.smart_size.enabled", False)
    prefs.setdefault("browser.cache.disk.capacity", megabytes * 1024)
    options["firefox_user_prefs"] = prefs


def _is_real_directory(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink()


def _size(path: Path, stop_above: int) -> int:
    """Bytes under ``path``, giving up once the answer is known to exceed a bound."""
    total = 0
    pending = [path]
    while pending:
        try:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                        if total > stop_above:
                            return total
        except OSError:
            continue
    return total


def _remove(path: Path) -> bool:
    # A link in place of the folder is left alone: it may lead anywhere.
    if not _is_real_directory(path):
        return False
    shutil.rmtree(path, ignore_errors=True)
    return not path.exists()


def remove_startup_cache(profile_dir: Path) -> bool:
    """Delete a closed profile's compiled-code cache. Never raises."""
    try:
        return _remove(profile_dir / STARTUP_CACHE)
    except OSError as exc:
        logger.warning(f"Could not remove the startup cache in {profile_dir.name}: {exc}")
        return False


def trim_closed_profile(profile_dir: Path, megabytes: int) -> int:
    """Trim one closed profile; return the bytes freed. Never raises.

    The page cache is removed whole when it is over the limit: its index and its
    entries only make sense together, and the browser starts a new one.
    """
    freed = 0
    try:
        startup = profile_dir / STARTUP_CACHE
        if _is_real_directory(startup):
            size = _size(startup, stop_above=1 << 62)
            if _remove(startup):
                freed += size
        pages = profile_dir / HTTP_CACHE
        if megabytes > 0 and _is_real_directory(pages):
            limit = megabytes * 1024 * 1024
            if _size(pages, stop_above=limit) > limit:
                size = _size(pages, stop_above=1 << 62)
                if _remove(pages):
                    freed += size
    except OSError as exc:
        logger.warning(f"Could not trim the caches in {profile_dir.name}: {exc}")
    return freed


async def trim_existing_profiles(manager, megabytes: int, gate=None) -> tuple[int, int] | None:
    """Trim every closed profile once; return (profiles trimmed, bytes freed).

    Returns None when an earlier start already did it. Each profile is reserved
    while its folder is touched, exactly as an export reserves it, so a browser
    cannot start in a folder that is being trimmed.

    A profile that is running, held by another instance or fenced by
    synchronization is skipped and not revisited: it is in use, and its next
    launch applies the limit and its next close clears the startup cache. A run
    that is interrupted records nothing and starts over at the next start.
    """
    settings = AppSettings(manager.storage)
    if settings.get(_TRIMMED) is not None:
        return None
    trimmed = freed = skipped = 0
    for listed in await manager.storage.list_profiles({"include_deleted": True}):
        try:
            if gate is not None:
                with gate.operation():
                    released = await _trim_reserved(manager, listed.id, megabytes)
            else:
                released = await _trim_reserved(manager, listed.id, megabytes)
        except asyncio.CancelledError:
            raise
        except ProfileLocked:
            skipped += 1
            continue
        except Exception as exc:  # noqa: BLE001 - one profile must not stop the others
            skipped += 1
            logger.warning(f"Skipped trimming the caches of {listed.id}: {type(exc).__name__}")
            continue
        if released:
            trimmed += 1
            freed += released
        # Let requests in between profiles; this is housekeeping, not a queue.
        await asyncio.sleep(0)
    if skipped:
        logger.info(f"Left the caches of {skipped} profiles in use for their next launch")
    settings.set(_TRIMMED, "1")
    return trimmed, freed


async def _trim_reserved(manager, profile_id: str, megabytes: int) -> int:
    async with profile_operation(manager, profile_id, include_deleted=True) as profile:
        directory = profile_data_path(manager, profile)
        if not directory.is_dir():
            return 0
        return await run_file_operation(trim_closed_profile, directory, megabytes)
