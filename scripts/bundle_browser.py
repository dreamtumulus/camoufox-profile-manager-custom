"""Install and stage the exact Camoufox build paired with this package."""

import json
import shutil
import tarfile
from pathlib import Path

from install_ci_browser import install_browser

ROOT = Path(__file__).resolve().parent.parent


def stage_geoip(destination: Path) -> None:
    """Copy the selected location source, and only once it places an address.

    An installer carries this to every machine and nothing downstream looks at it
    again, so a database stored under a source that describes another layout has
    to stop the build here. Only the selected source is copied: whatever else a
    build machine's cache holds is not part of what was checked.
    """
    from camoufox import geolocation

    from camoufox_pm.core.browser_install import geoip_lookup_works

    if not geoip_lookup_works():
        raise RuntimeError(
            "The location database cannot place a known address through its selected source; "
            "refusing to bundle it"
        )
    config = geolocation.load_geoip_config()
    (destination / "mmdb").mkdir(parents=True)
    if geolocation.GEOIP_CONFIG.is_file():
        shutil.copyfile(geolocation.GEOIP_CONFIG, destination / "config.yml")
    for database in {geolocation.get_mmdb_path(family, config) for family in ("ipv4", "ipv6")}:
        shutil.copyfile(database, destination / "mmdb" / database.name)


def stage_browser() -> Path:
    install_browser()
    from camoufox import multiversion

    active = multiversion.get_active_path()
    if active is None:
        raise RuntimeError("Camoufox installation did not select a browser")
    relative = active.relative_to(multiversion.INSTALL_DIR)
    if relative.parts[0] != "browsers":
        raise RuntimeError("Expected a versioned Camoufox browser")

    stage = ROOT / "build" / "bundled-camoufox"
    shutil.rmtree(stage, ignore_errors=True)
    shutil.copytree(active, stage / relative, symlinks=True)
    stage_geoip(stage / "geoip")
    (stage / "config.json").write_text(
        json.dumps({"active_version": relative.as_posix()}, indent=2) + "\n", encoding="utf-8"
    )
    (stage / ".0.5_FLAG").touch()
    archive = ROOT / "build" / "camoufox-runtime.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        for item in stage.iterdir():
            output.add(item, arcname=item.name)
    return archive


if __name__ == "__main__":
    print(stage_browser())
