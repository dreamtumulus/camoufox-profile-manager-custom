"""The build's location data: selected with its own source, checked before it ships."""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import Mock

import maxminddb
import pytest
from camoufox import geolocation

from camoufox_pm.core import browser_install
from tests.support.geoip import (
    FLAT,
    FLAT_PATHS,
    FLAT_SOURCE,
    NESTED,
    NESTED_PATHS,
    NESTED_SOURCE,
    install_source,
    point_library_at,
    write_mmdb,
)

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def load_script(name):
    # The build scripts import one another by bare name, as they do when run.
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(f"cpm_script_{name}", SCRIPTS / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS))


mirror = load_script("mirror_browser")
bundle = load_script("bundle_browser")


@pytest.fixture
def geoip(tmp_path, monkeypatch):
    return point_library_at(monkeypatch, tmp_path / "cache")


def published(record, monkeypatch):
    """Stand in for the source's download: every file it lists arrives holding `record`."""

    def download(source=None, progress_callback=None, activate=True):
        config = geolocation._get_geoip_config_by_name(source)
        for family in config["urls"]:
            write_mmdb(geolocation.MMDB_DIR / f"{config['name'].lower()}-{family}.mmdb", record)
        geolocation.save_geoip_config(config, explicit=True)

    fake = Mock(side_effect=download)
    monkeypatch.setattr(geolocation, "download_mmdb", fake)
    return fake


def country(record, paths):
    for part in paths["iso_code"].split("."):
        record = record.get(part) if isinstance(record, dict) else None
    return record


def test_each_layout_is_read_by_its_own_paths_and_not_by_the_other(tmp_path):
    """Why a database must be stored under the source that describes it."""
    readings = {}
    for layout, record in (("flat", FLAT), ("nested", NESTED)):
        with maxminddb.open_database(str(write_mmdb(tmp_path / f"{layout}.mmdb", record))) as db:
            answer = db.get("8.8.8.8")
        readings[layout] = (country(answer, FLAT_PATHS), country(answer, NESTED_PATHS))
    assert readings == {"flat": ("US", None), "nested": (None, "US")}


def test_the_mirrored_source_is_the_published_one_with_the_flat_layout():
    config = geolocation._get_geoip_config_by_name(mirror.GEOIP_SOURCE_NAME)
    assert config["paths"] == FLAT_PATHS
    assert sorted(config["urls"]) == ["ipv4", "ipv6"]
    # The library's default went away with its repository; the build must not rely on it.
    assert geolocation._get_geoip_config_by_name(None)["name"] != mirror.GEOIP_SOURCE_NAME


@pytest.mark.filterwarnings("ignore::FutureWarning")
def test_seed_installs_the_source_by_name_and_the_launch_lookup_reads_it(geoip, monkeypatch):
    download = published(FLAT, monkeypatch)
    mirror.seed_geoip()
    download.assert_called_once_with(mirror.GEOIP_SOURCE_NAME)
    assert geolocation.load_geoip_config()["name"] == FLAT_SOURCE
    assert browser_install.geoip_lookup_works()
    # Explicit, so the library does not move a deprecated source back to its default.
    assert "explicit: true" in (geoip / "config.yml").read_text()
    # The lookup a launch makes, through the paths of the source that was registered.
    location = geolocation.get_geolocation("8.8.8.8")
    assert (location.locale.region, location.timezone) == ("US", "America/Chicago")
    assert geolocation.get_geolocation("2001:4860:4860::8888").locale.region == "US"


def test_seed_refuses_a_database_its_source_cannot_read(geoip, monkeypatch):
    """What shipped: a nested database registered under the flat source."""
    published(NESTED, monkeypatch)
    with pytest.raises(SystemExit, match="cannot place a known address"):
        mirror.seed_geoip()


def test_seed_keeps_a_working_install_and_replaces_everything_else(geoip, monkeypatch):
    download = published(FLAT, monkeypatch)
    install_source(geoip, FLAT_SOURCE, FLAT)
    mirror.seed_geoip()
    download.assert_not_called()

    # A build machine still holding the broken files under the right name.
    install_source(geoip, FLAT_SOURCE, NESTED)
    monkeypatch.setattr(browser_install, "_geoip_check", None)
    mirror.seed_geoip()
    assert download.call_count == 1 and browser_install.geoip_lookup_works()

    # A cache that works today on the source that is no longer published.
    install_source(geoip, NESTED_SOURCE, NESTED)
    mirror.seed_geoip()
    assert download.call_count == 2
    assert geolocation.load_geoip_config()["name"] == FLAT_SOURCE


def test_bundle_carries_only_the_selected_source_that_was_checked(geoip, tmp_path):
    install_source(geoip, FLAT_SOURCE, FLAT)
    write_mmdb(geoip / "mmdb" / "geoip aio by daijro-combined.mmdb", NESTED)
    (geoip / "config.yml.bak").write_text("name: something older\n")
    stage = tmp_path / "stage" / "geoip"
    bundle.stage_geoip(stage)
    assert sorted(path.relative_to(stage).as_posix() for path in stage.rglob("*")) == [
        "config.yml",
        "mmdb",
        "mmdb/maxmind geolite2-ipv4.mmdb",
        "mmdb/maxmind geolite2-ipv6.mmdb",
    ]
    assert (stage / "config.yml").read_text() == (geoip / "config.yml").read_text()


@pytest.mark.parametrize("record", [NESTED, None])
def test_bundle_refuses_location_data_that_cannot_place_an_address(geoip, tmp_path, record):
    if record is not None:
        install_source(geoip, FLAT_SOURCE, record)
    stage = tmp_path / "stage" / "geoip"
    with pytest.raises(RuntimeError, match="refusing to bundle"):
        bundle.stage_geoip(stage)
    assert not stage.exists()
