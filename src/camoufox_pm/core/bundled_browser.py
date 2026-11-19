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


# Recorded beside the archive's stamp when its location data did not produce
# working lookups, so the archive is not decompressed again for nothing.
_GEOIP_GAVE_UP = "geoip-unusable"


def _source_name(text: str | bytes) -> str | None:
    """The source a location config.yml selects, or None when it names none."""
    import yaml

    try:
        saved = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    name = saved.get("name") if isinstance(saved, dict) else None
    return name if isinstance(name, str) else None


def _selected_source(geoip_root: Path) -> str | None:
    try:
        return _source_name((geoip_root / "config.yml").read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return None


def _replace_geoip(
    archive: tarfile.TarFile, members: list[tarfile.TarInfo], geoip_root: Path
) -> None:
    """Swap in the installer's location data as a whole.

    Its config and its databases belong together, so they are never mixed with
    what is already there. The copy is finished beside the folder and renamed
    into place, which leaves either the old data or the new and never half of
    each.
    """
    staging = geoip_root.with_name(".geoip.installing")
    retired = geoip_root.with_name(".geoip.replaced")
    for leftover in (staging, retired):
        shutil.rmtree(leftover, ignore_errors=True)
    archive.extractall(staging, members=members, filter="data")
    # The library reads a database's modification time as the moment it last
    # checked for a newer build. Copied here, that moment is now.
    for database in (staging / "geoip").rglob("*.mmdb"):
        os.utime(database)
    if geoip_root.exists():
        geoip_root.rename(retired)
    try:
        (staging / "geoip").rename(geoip_root)
    except OSError:
        if retired.exists() and not geoip_root.exists():
            retired.rename(geoip_root)
        raise
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(retired, ignore_errors=True)


def _provision_geoip(archive: tarfile.TarFile, members: list[tarfile.TarInfo]) -> bool:
    """Put the installer's location data in use where needed; say whether lookups work.

    Files already present are normally kept: a database refreshed since the
    install is newer than the bundled one. Two caches are replaced whole instead.
    One cannot place an address (a damaged file, or a database stored under a
    source that describes another layout). The other selects a source this
    installer does not ship: the installer carries the one source known to be
    published, and a cache left on a source that has since disappeared works only
    until its next refresh fails.
    """
    from camoufox import multiversion

    from .browser_install import geoip_lookup_works

    bundled = [member for member in members if member.isfile() and member.name.startswith("geoip/")]
    if not bundled:
        return geoip_lookup_works()
    geoip_root = multiversion.INSTALL_DIR / "geoip"
    bundled_source = None
    for member in bundled:
        if member.name == "geoip/config.yml":
            source = archive.extractfile(member)
            if source is not None:
                with source:
                    bundled_source = _source_name(source.read())
    outdated_source = bundled_source is not None and bundled_source != _selected_source(geoip_root)
    if outdated_source or not geoip_lookup_works():
        try:
            _replace_geoip(archive, bundled, geoip_root)
        except (OSError, tarfile.TarError):
            # Location data must not keep the app from starting. Settings
            # reports the browser as not ready and offers the download.
            pass
        return geoip_lookup_works()

    for member in bundled:
        destination = geoip_root / Path(member.name).relative_to("geoip")
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = archive.extractfile(member)
        if source is None:
            raise RuntimeError("A bundled location database could not be read")
        with source, destination.open("xb") as output:
            shutil.copyfileobj(source, output)
    return geoip_lookup_works()


def provision_bundled_browser() -> None:
    archive_path = Path(getattr(sys, "_MEIPASS", "")) / "camoufox-runtime.tar.gz"
    if not getattr(sys, "frozen", False) or not archive_path.is_file():
        return

    from camoufox import multiversion
    from camoufox.browser_pin import is_explicit_choice

    from .browser_install import geoip_lookup_works, installed_browser

    # Listing a gzip archive decompresses all of it, and the bundled runtime is
    # over a gigabyte: measured 47 seconds before the window appeared on every
    # start. Once this exact archive has been provisioned, the browser it
    # installed is still usable and addresses can be placed, there is nothing
    # left to do. A location database that stops working later (a refresh cut
    # short, a file removed) sends the next start back to the archive, once.
    stat = archive_path.stat()
    stamp = f"{stat.st_size}:{stat.st_mtime_ns}"
    marker = multiversion.INSTALL_DIR / ".bundled-runtime"
    try:
        recorded = marker.read_text(encoding="utf-8").splitlines()
    except OSError:
        recorded = []
    provisioned = recorded[:1] == [stamp]
    # An installer whose own location data does not work is not worth reading
    # again while its copy is still in place; a removed copy is put back.
    gave_up = _GEOIP_GAVE_UP in recorded[1:] and (multiversion.INSTALL_DIR / "geoip").is_dir()
    if provisioned and installed_browser()[0] and (gave_up or geoip_lookup_works()):
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

        located = _provision_geoip(archive, members)

    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(stamp if located else f"{stamp}\n{_GEOIP_GAVE_UP}", encoding="utf-8")
    except OSError:
        pass  # Only costs the next start its time.
