import json
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
