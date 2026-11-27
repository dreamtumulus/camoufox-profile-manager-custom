"""The archives of the retired backup feature are removed, and nothing else is."""

import os
from pathlib import Path

import pytest

from camoufox_pm.core.legacy_backups import remove_legacy_backups

FIRST = "0123456789abcdef0123456789abcdef"
SECOND = "fedcba9876543210fedcba9876543210"


def write(path: Path, size: int) -> Path:
    path.write_bytes(b"x" * size)
    return path


def test_removes_everything_the_feature_wrote_and_then_the_folder(tmp_path):
    backups = tmp_path / "backups"
    backups.mkdir()
    write(backups / f"{FIRST}.zip", 1000)
    write(backups / f"{FIRST}.json", 10)
    write(backups / f"{SECOND}.zip", 2000)
    write(backups / f"{SECOND}.json", 20)
    # An interrupted write left these two names.
    write(backups / f"{SECOND}.partial", 300)
    write(backups / f"{SECOND}.pending", 3)
    profile = write(tmp_path / "profiles.db", 5)

    assert remove_legacy_backups(tmp_path) == (2, 3333)
    assert not backups.exists()
    assert profile.read_bytes() == b"xxxxx"
    # Nothing is left to do on the next start.
    assert remove_legacy_backups(tmp_path) == (0, 0)


def test_keeps_files_the_feature_did_not_write_and_the_folder_holding_them(tmp_path):
    backups = tmp_path / "backups"
    (backups / "nested").mkdir(parents=True)
    write(backups / f"{FIRST}.zip", 100)
    kept = [
        write(backups / "my-own-copy.zip", 7),
        write(backups / f"{FIRST}.zip.bak", 7),
        write(backups / f"{FIRST[:-1]}.zip", 7),
        write(backups / "nested" / f"{SECOND}.zip", 7),
    ]
    # A folder that merely has an archive's name is not an archive.
    (backups / f"{SECOND}.zip").mkdir()

    assert remove_legacy_backups(tmp_path) == (1, 100)
    assert all(path.read_bytes() == b"x" * 7 for path in kept)
    assert (backups / f"{SECOND}.zip").is_dir()


@pytest.mark.skipif(os.name == "nt", reason="Creating a symbolic link needs a privilege on Windows")
def test_never_follows_a_link(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    target = write(outside / f"{FIRST}.zip", 50)
    backups = tmp_path / "backups"
    backups.mkdir()
    link = backups / f"{SECOND}.zip"
    link.symlink_to(target)

    assert remove_legacy_backups(tmp_path) == (0, 0)
    assert link.is_symlink() and target.read_bytes() == b"x" * 50

    # Nor a backups folder that is itself a link to somewhere else.
    link.unlink()
    backups.rmdir()
    backups.symlink_to(outside, target_is_directory=True)
    assert remove_legacy_backups(tmp_path) == (0, 0)
    assert target.exists()


def test_a_missing_folder_or_a_file_in_its_place_is_left_alone(tmp_path):
    assert remove_legacy_backups(tmp_path) == (0, 0)
    write(tmp_path / "backups", 4)
    assert remove_legacy_backups(tmp_path) == (0, 0)
    assert (tmp_path / "backups").read_bytes() == b"xxxx"


def test_a_file_that_cannot_be_removed_does_not_stop_the_rest(tmp_path, monkeypatch):
    backups = tmp_path / "backups"
    backups.mkdir()
    stuck = write(backups / f"{FIRST}.zip", 10)
    write(backups / f"{SECOND}.zip", 20)
    unlink = os.unlink

    def refuse(path, *args, **kwargs):
        if Path(path) == stuck:
            raise PermissionError("in use")
        unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", refuse)
    assert remove_legacy_backups(tmp_path) == (1, 20)
    assert stuck.exists() and not (backups / f"{SECOND}.zip").exists()
