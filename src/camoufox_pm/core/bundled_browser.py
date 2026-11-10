"""Provision the browser archived into a frozen desktop installer."""

import json
import os
import shutil
import sys
import tarfile
from pathlib import Path


def configure_browser_cache() -> None:
    configured = os.environ.get("CPM_CAMOUFOX_CACHE")
    if not configured:
        return
    from camoufox import geolocation, multiversion, pkgman

    root = Path(configured).expanduser().resolve()
    pkgman.INSTALL_DIR = root
    multiversion.INSTALL_DIR = root
    multiversion.BROWSERS_DIR = root / "browsers"
    multiversion.CONFIG_FILE = root / "config.json"
    multiversion.REPO_CACHE_FILE = root / "repo_cache.json"
    multiversion.COMPAT_FLAG = root / ".0.5_FLAG"
    geolocation.GEOIP_DIR = root / "geoip"
    geolocation.MMDB_DIR = root / "geoip" / "mmdb"
    geolocation.GEOIP_CONFIG = root / "geoip" / "config.yml"


def provision_bundled_browser() -> None:
    archive_path = Path(getattr(sys, "_MEIPASS", "")) / "camoufox-runtime.tar.gz"
    if not getattr(sys, "frozen", False) or not archive_path.is_file():
        return

    from camoufox import multiversion
    from camoufox.browser_pin import is_explicit_choice

    from .browser_install import installed_browser

    # Listing a gzip archive decompresses all of it, and the bundled runtime is
    # over a gigabyte: measured 47 seconds before the window appeared on every
    # start. Once this exact archive has been provisioned and the browser it
    # installed is still usable, there is nothing left to do.
    stat = archive_path.stat()
    stamp = f"{stat.st_size}:{stat.st_mtime_ns}"
    marker = multiversion.INSTALL_DIR / ".bundled-runtime"
    try:
        provisioned = marker.read_text(encoding="utf-8").strip() == stamp
    except OSError:
        provisioned = False
    if provisioned and installed_browser()[0] and (multiversion.INSTALL_DIR / "geoip").is_dir():
        return

    with tarfile.open(archive_path, "r:gz") as archive:
        config_file = archive.extractfile("config.json")
        if config_file is None:
            raise RuntimeError("The bundled Camoufox configuration is missing")
        source_config = json.load(config_file)
        relative = Path(source_config["active_version"])
        relative_name = relative.as_posix()
        if relative.is_absolute() or ".." in relative.parts or relative.parts[0] != "browsers":
            raise RuntimeError("The bundled Camoufox configuration is invalid")
        members = archive.getmembers()
        version_name = f"{relative_name}/version.json"
        if not any(member.name == version_name and member.isfile() for member in members):
            raise RuntimeError("The bundled Camoufox browser is incomplete")

        if not installed_browser()[0]:
            existing = multiversion.load_config()
            if not (is_explicit_choice(existing) and existing.get("active_version")):
                target = multiversion.INSTALL_DIR / relative
                if not target.exists():
                    temporary = target.parent / f".{target.name}.installing"
                    if temporary.exists():
                        raise RuntimeError("A previous bundled-browser copy needs manual inspection")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    browser_members = [
                        member for member in members if member.name.startswith(relative_name + "/")
                    ]
                    archive.extractall(temporary, members=browser_members, filter="data")
                    (temporary / relative).rename(target)
                    shutil.rmtree(temporary)
                if not (target / "version.json").is_file():
                    raise RuntimeError("The existing Camoufox cache is incomplete; repair it in Settings")
                existing["active_version"] = relative_name
                multiversion.save_config(existing)
                multiversion.COMPAT_FLAG.touch()

        geoip_root = multiversion.INSTALL_DIR / "geoip"
        for member in members:
            if not member.isfile() or not member.name.startswith("geoip/"):
                continue
            destination = geoip_root / Path(member.name).relative_to("geoip")
            if destination.exists():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise RuntimeError("A bundled location database could not be read")
            with source, destination.open("xb") as output:
                shutil.copyfileobj(source, output)

    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(stamp, encoding="utf-8")
    except OSError:
        pass  # Only costs the next start its time.
