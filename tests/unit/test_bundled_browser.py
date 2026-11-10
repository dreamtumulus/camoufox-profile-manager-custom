import json
import tarfile
from pathlib import Path

from camoufox import multiversion

from camoufox_pm.core import browser_install, bundled_browser


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
