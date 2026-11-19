import json
import shutil
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import maxminddb
import pytest
from camoufox import geolocation, multiversion

from camoufox_pm.core import browser_install, bundled_browser
from tests.support.geoip import (
    FLAT,
    FLAT_SOURCE,
    NESTED,
    NESTED_SOURCE,
    install_source,
    point_library_at,
)


def test_frozen_install_provisions_browser_without_replacing_existing_files(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle" / "camoufox-runtime"
    source = bundle / "browsers" / "official" / "test-build"
    source.mkdir(parents=True)
    (source / "version.json").write_text('{"version":"1","build":"test"}')
    (source / "camoufox.exe").write_bytes(b"browser")
    (bundle / "config.json").write_text('{"active_version":"browsers/official/test-build"}')
    (bundle / "geoip" / "mmdb").mkdir(parents=True)
    (bundle / "geoip" / "mmdb" / "test.mmdb").write_bytes(b"bundled")
    with tarfile.open(tmp_path / "bundle" / "camoufox-runtime.tar.gz", "w:gz") as archive:
        for item in bundle.iterdir():
            archive.add(item, arcname=item.name)
    cache = tmp_path / "cache"
    monkeypatch.setattr(bundled_browser.sys, "frozen", True, raising=False)
    monkeypatch.setattr(bundled_browser.sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.setattr(multiversion, "INSTALL_DIR", cache)
    monkeypatch.setattr(multiversion, "BROWSERS_DIR", cache / "browsers")
    monkeypatch.setattr(multiversion, "CONFIG_FILE", cache / "config.json")
    monkeypatch.setattr(multiversion, "COMPAT_FLAG", cache / ".0.5_FLAG")
    point_library_at(monkeypatch, cache)
    monkeypatch.setattr(browser_install, "installed_browser", lambda: (False, None))

    bundled_browser.provision_bundled_browser()

    assert (cache / "browsers" / "official" / "test-build" / "camoufox.exe").read_bytes() == b"browser"
    assert json.loads((cache / "config.json").read_text())["active_version"] == "browsers/official/test-build"
    assert (cache / "geoip" / "mmdb" / "test.mmdb").read_bytes() == b"bundled"
    assert (cache / ".0.5_FLAG").exists()


def test_source_run_does_not_touch_browser_cache(monkeypatch):
    monkeypatch.delattr(bundled_browser.sys, "frozen", raising=False)
    monkeypatch.setattr(Path, "mkdir", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("write")))
    bundled_browser.provision_bundled_browser()


def test_explicit_browser_cache_override_is_applied_to_all_camoufox_paths(tmp_path, monkeypatch):
    from camoufox import geolocation, pkgman

    monkeypatch.setenv("CPM_CAMOUFOX_CACHE", str(tmp_path / "isolated"))
    bundled_browser.configure_browser_cache()
    assert pkgman.INSTALL_DIR == tmp_path / "isolated"
    assert multiversion.CONFIG_FILE == tmp_path / "isolated" / "config.json"
    assert multiversion.BROWSERS_DIR == tmp_path / "isolated" / "browsers"
    assert geolocation.MMDB_DIR == tmp_path / "isolated" / "geoip" / "mmdb"


def test_later_starts_skip_the_archive_once_it_has_been_provisioned(tmp_path, monkeypatch):
    """Listing the gigabyte archive on every start kept the window away for 47 s."""
    bundle = tmp_path / "bundle" / "camoufox-runtime"
    source = bundle / "browsers" / "official" / "test-build"
    source.mkdir(parents=True)
    (source / "version.json").write_text('{"version":"1","build":"test"}')
    (bundle / "config.json").write_text('{"active_version":"browsers/official/test-build"}')
    (bundle / "geoip" / "mmdb").mkdir(parents=True)
    (bundle / "geoip" / "mmdb" / "test.mmdb").write_bytes(b"bundled")
    archive_path = tmp_path / "bundle" / "camoufox-runtime.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        for item in bundle.iterdir():
            archive.add(item, arcname=item.name)
    cache = tmp_path / "cache"
    installed = [False]
    monkeypatch.setattr(bundled_browser.sys, "frozen", True, raising=False)
    monkeypatch.setattr(bundled_browser.sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.setattr(multiversion, "INSTALL_DIR", cache)
    monkeypatch.setattr(multiversion, "BROWSERS_DIR", cache / "browsers")
    monkeypatch.setattr(multiversion, "CONFIG_FILE", cache / "config.json")
    monkeypatch.setattr(multiversion, "COMPAT_FLAG", cache / ".0.5_FLAG")
    point_library_at(monkeypatch, cache)
    monkeypatch.setattr(browser_install, "installed_browser", lambda: (installed[0], None))
    opened = []
    real_open = tarfile.open

    def counting_open(*args, **kwargs):
        opened.append(args[0])
        return real_open(*args, **kwargs)

    monkeypatch.setattr(bundled_browser.tarfile, "open", counting_open)

    bundled_browser.provision_bundled_browser()
    assert len(opened) == 1
    installed[0] = True
    bundled_browser.provision_bundled_browser()
    assert len(opened) == 1  # same archive, browser usable: nothing to read

    # A broken browser, a wiped location database or a new installer is provisioned again.
    installed[0] = False
    bundled_browser.provision_bundled_browser()
    assert len(opened) == 2
    installed[0] = True
    (cache / "geoip" / "mmdb" / "test.mmdb").unlink()
    (cache / "geoip" / "mmdb").rmdir()
    (cache / "geoip").rmdir()
    bundled_browser.provision_bundled_browser()
    assert len(opened) == 3 and (cache / "geoip" / "mmdb" / "test.mmdb").exists()
    with tarfile.open(archive_path, "w:gz") as archive:
        for item in bundle.iterdir():
            archive.add(item, arcname=item.name)
        archive.add(bundle / "config.json", arcname="extra.json")
    bundled_browser.provision_bundled_browser()
    assert len(opened) == 5  # the rewrite above, then the new archive


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    """A frozen app whose installer archive and browser cache are both temporary."""
    cache = tmp_path / "cache"
    monkeypatch.setattr(bundled_browser.sys, "frozen", True, raising=False)
    monkeypatch.setattr(bundled_browser.sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.setattr(multiversion, "INSTALL_DIR", cache)
    monkeypatch.setattr(multiversion, "BROWSERS_DIR", cache / "browsers")
    monkeypatch.setattr(multiversion, "CONFIG_FILE", cache / "config.json")
    monkeypatch.setattr(multiversion, "COMPAT_FLAG", cache / ".0.5_FLAG")
    point_library_at(monkeypatch, cache)
    # Nothing here may reach for a newer database.
    monkeypatch.setattr(geolocation, "download_mmdb", Mock(side_effect=AssertionError("download")))
    installed = [False]
    monkeypatch.setattr(browser_install, "installed_browser", lambda: (installed[0], None))
    opened = []
    real_open = tarfile.open

    def counting_open(*args, **kwargs):
        opened.append(args[0])
        return real_open(*args, **kwargs)

    def write_archive(source: str, record: dict) -> str:
        """Build the installer archive with `record` stored under `source`; return its stamp."""
        bundle = tmp_path / "bundle" / "camoufox-runtime"
        shutil.rmtree(bundle, ignore_errors=True)
        build = bundle / "browsers" / "official" / "test-build"
        build.mkdir(parents=True)
        (build / "version.json").write_text('{"version":"1","build":"test"}')
        (bundle / "config.json").write_text('{"active_version":"browsers/official/test-build"}')
        install_source(bundle / "geoip", source, record)
        archive_path = tmp_path / "bundle" / "camoufox-runtime.tar.gz"
        with real_open(archive_path, "w:gz") as archive:
            for item in bundle.iterdir():
                archive.add(item, arcname=item.name)
        stat = archive_path.stat()
        return f"{stat.st_size}:{stat.st_mtime_ns}"

    monkeypatch.setattr(bundled_browser.tarfile, "open", counting_open)
    return SimpleNamespace(
        cache=cache,
        geoip=cache / "geoip",
        installed=installed,
        opened=opened,
        write_archive=write_archive,
        marker=lambda: (cache / ".bundled-runtime").read_text().splitlines(),
    )


def read(database: Path, field: str):
    with maxminddb.open_database(str(database)) as reader:
        return reader.get("8.8.8.8").get(field)


def test_location_data_stored_under_the_wrong_source_is_replaced_whole(frozen):
    """What the first mirrored installers left behind: nested files under the flat source."""
    stamp = frozen.write_archive(FLAT_SOURCE, FLAT)
    local = install_source(frozen.geoip, FLAT_SOURCE, NESTED)
    (frozen.geoip / "mmdb" / "left-over.mmdb").write_bytes(b"from the broken installer")
    assert not browser_install.geoip_lookup_works()

    bundled_browser.provision_bundled_browser()

    assert browser_install.geoip_lookup_works()
    assert [read(database, "country_code") for database in local] == ["US", "US"]
    assert not (frozen.geoip / "mmdb" / "left-over.mmdb").exists()
    assert not [path.name for path in frozen.cache.iterdir() if path.name.startswith(".geoip")]
    assert frozen.marker() == [stamp]
    # The library's own lookup, the one a launch makes, now places the address.
    with pytest.warns(FutureWarning):
        assert geolocation.get_geolocation("8.8.8.8").locale.region == "US"


def test_cache_on_a_source_the_installer_does_not_ship_is_replaced(frozen):
    """A hand-repaired cache works only until its unpublished source is next refreshed."""
    frozen.write_archive(FLAT_SOURCE, FLAT)
    install_source(frozen.geoip, NESTED_SOURCE, NESTED)
    assert browser_install.geoip_lookup_works()

    bundled_browser.provision_bundled_browser()

    assert geolocation.load_geoip_config()["name"] == FLAT_SOURCE
    assert sorted(path.name for path in (frozen.geoip / "mmdb").iterdir()) == [
        "maxmind geolite2-ipv4.mmdb",
        "maxmind geolite2-ipv6.mmdb",
    ]
    assert browser_install.geoip_lookup_works()


def test_working_location_data_of_the_shipped_source_is_kept(frozen):
    """A database refreshed since the install is newer than the installer's copy."""
    stamp = frozen.write_archive(FLAT_SOURCE, FLAT)
    local = install_source(frozen.geoip, FLAT_SOURCE, {**FLAT, "timezone": "America/New_York"})

    bundled_browser.provision_bundled_browser()

    assert [read(database, "timezone") for database in local] == ["America/New_York"] * 2
    assert frozen.marker() == [stamp]


def test_location_data_that_stops_working_is_repaired_on_the_next_start(frozen):
    frozen.write_archive(FLAT_SOURCE, FLAT)
    bundled_browser.provision_bundled_browser()
    frozen.installed[0] = True
    bundled_browser.provision_bundled_browser()
    assert len(frozen.opened) == 1  # usable browser, working lookups: the archive stays shut

    (frozen.geoip / "mmdb" / "maxmind geolite2-ipv4.mmdb").write_bytes(b"a refresh cut short")
    bundled_browser.provision_bundled_browser()
    assert len(frozen.opened) == 2 and browser_install.geoip_lookup_works()


def test_installer_with_unusable_location_data_is_not_reread_on_every_start(frozen):
    stamp = frozen.write_archive(FLAT_SOURCE, NESTED)
    bundled_browser.provision_bundled_browser()
    assert not browser_install.geoip_lookup_works()
    assert frozen.marker() == [stamp, "geoip-unusable"]
    frozen.installed[0] = True
    bundled_browser.provision_bundled_browser()
    assert len(frozen.opened) == 1


def test_a_replacement_that_fails_leaves_the_old_data_and_still_starts(frozen, monkeypatch):
    frozen.write_archive(FLAT_SOURCE, FLAT)
    local = install_source(frozen.geoip, FLAT_SOURCE, NESTED)
    real_rename = Path.rename

    def busy(self, target):
        if self.parent.name == ".geoip.installing":
            raise OSError("in use")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", busy)
    bundled_browser.provision_bundled_browser()  # Settings reports it; the app still opens.

    assert [read(database, "country") for database in local] == [{"iso_code": "US"}] * 2
    assert (frozen.geoip / "config.yml").is_file()
    assert not browser_install.geoip_lookup_works()
