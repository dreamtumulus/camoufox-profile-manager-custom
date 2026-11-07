"""Provision the browser bundled into a frozen desktop installer."""

import json
import os
import shutil
import sys
from pathlib import Path


def _copy_missing(source: Path, target: Path) -> None:
    if not source.is_dir():
        return
    for item in source.rglob("*"):
        if not item.is_file():
            continue
        destination = target / item.relative_to(source)
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, destination)


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
    bundle = Path(getattr(sys, "_MEIPASS", "")) / "camoufox-runtime"
    if not getattr(sys, "frozen", False) or not bundle.is_dir():
        return

    from camoufox import multiversion
    from camoufox.browser_pin import is_explicit_choice

    from .browser_install import installed_browser

    source_config = json.loads((bundle / "config.json").read_text(encoding="utf-8"))
    relative = Path(source_config["active_version"])
    if relative.is_absolute() or ".." in relative.parts or relative.parts[0] != "browsers":
        raise RuntimeError("The bundled Camoufox configuration is invalid")
    source = bundle / relative
    if not (source / "version.json").is_file():
        raise RuntimeError("The bundled Camoufox browser is incomplete")

    if not installed_browser()[0]:
        existing = multiversion.load_config()
        if not (is_explicit_choice(existing) and existing.get("active_version")):
            target = multiversion.INSTALL_DIR / relative
            if not target.exists():
                temporary = target.with_name(f".{target.name}.installing")
                if temporary.exists():
                    raise RuntimeError("A previous bundled-browser copy needs manual inspection")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, temporary, symlinks=True)
                temporary.rename(target)
            if not (target / "version.json").is_file():
                raise RuntimeError("The existing Camoufox cache is incomplete; repair it in Settings")
            existing["active_version"] = relative.as_posix()
            multiversion.save_config(existing)
            multiversion.COMPAT_FLAG.touch()

    _copy_missing(bundle / "geoip", multiversion.INSTALL_DIR / "geoip")
