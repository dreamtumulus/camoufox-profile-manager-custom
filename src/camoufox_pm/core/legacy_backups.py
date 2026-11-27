"""Remove the archives left behind by the retired profile-backup feature.

Earlier versions copied every profile into ``<data dir>/backups`` once a day and
kept seven copies of each. That feature is gone, and with it everything that
listed, restored or pruned those archives, so they would otherwise stay on disk
for good. They are unencrypted copies of signed-in sessions and proxy passwords.
"""

import os
import re
from pathlib import Path

from loguru import logger

# What the feature wrote: an archive and its record, named by a UUID, and the two
# temporary names a write used. Anything else in the folder is not ours to remove.
_WRITTEN = re.compile(r"[0-9a-f]{32}\.(zip|json|partial|pending)")


def remove_legacy_backups(data_dir: Path) -> tuple[int, int]:
    """Delete the old archives; return how many were removed and the bytes freed."""
    directory = data_dir / "backups"
    if directory.is_symlink() or not directory.is_dir():
        return 0, 0
    archives = freed = 0
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                # A link is skipped rather than followed: it may point anywhere.
                if not _WRITTEN.fullmatch(entry.name) or not entry.is_file(follow_symlinks=False):
                    continue
                try:
                    size = entry.stat(follow_symlinks=False).st_size
                    os.unlink(entry.path)
                except OSError as exc:
                    logger.warning(f"Could not remove old profile backup {entry.name}: {exc}")
                    continue
                freed += size
                archives += entry.name.endswith(".zip")
    except OSError as exc:
        # Leftover archives cost disk space; they must not keep the app from starting.
        logger.warning(f"Could not read the old profile backups folder: {exc}")
        return archives, freed
    try:
        directory.rmdir()  # Succeeds only once nothing is left in it.
    except OSError:
        pass
    return archives, freed
