#!/usr/bin/env python3
"""Install the pinned Camoufox build from documented mirrors when upstream is gone.

Upstream publishes every browser as a GitHub release asset of `daijro/camoufox`.
That repository answered 404 for the whole account in November 2026, so release
discovery -- and the location database source that lived beside it -- stopped
working. Builds still have to be reproducible, so this module carries the exact
asset digests for the build the installed library pins, and refuses anything
that does not match them.

Only the desktop build uses this: a shipped installer carries the browser, so
the mirror is a build-time dependency, not something an installed app needs.
"""

import json
import os
import platform
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

# The mirror republished the upstream release byte for byte and recorded the
# source release (daijro/camoufox v158.0.1-beta.41) in its manifest.json.
MIRROR_REPOSITORY = "jo-inc/camofox-browser"
MIRROR_TAG = "camoufox-backup-428447379"
MIRROR_BASE = f"https://github.com/{MIRROR_REPOSITORY}/releases/download/{MIRROR_TAG}"

# Digests published by the mirror, matching the pinned release's asset sizes.
BROWSER_ASSETS = {
    "lin.arm64": (
        "60e25f6eb95e13d9ec655bc02913d51057e669cb31542e6ace9d2190ab5380af",
        1297503606,
    ),
    "lin.x86_64": (
        "3f2eb1d0451fa58e45e43e55f5e6181565069791ae60c0574f0ceb1f01341c33",
        1303577033,
    ),
    "mac.arm64": (
        "9038f86d666c9780b6fff199d488737b8596c01f24e3e517845a0ecfb0cf8add",
        1297694189,
    ),
    "mac.x86_64": (
        "1afd6caa1e82caa41a4f56d8519f673e36b0835700b1c9c904e78a84c5711f3a",
        1305712546,
    ),
    "win.x86_64": (
        "2644bd14f1cc345c768f6a70b576bcc87eb3d0bf0df54856d6fe0bbc3027515f",
        1320075455,
    ),
}

# The default source (daijro/geoip-all-in-one) went away with the repository.
# GeoLite2 City carries every field the profile pinning and the proxy check read,
# with the same dotted paths the library's MaxMind source expects. One database
# covers both address families, so it is stored under each name the library asks
# for.
GEOIP_SOURCE_NAME = "MaxMind GeoLite2"
GEOIP_URL = "https://github.com/P3TERX/GeoLite.mmdb/releases/latest/download/GeoLite2-City.mmdb"
GEOIP_ASSETS = {"ipv4": GEOIP_URL, "ipv6": GEOIP_URL}


def platform_key() -> str:
    """The archive suffix this interpreter's browser would come from."""
    if sys.platform.startswith("linux"):
        name = "lin"
    elif sys.platform == "darwin":
        name = "mac"
    elif sys.platform == "win32":
        name = "win"
    else:
        raise SystemExit(f"No mirrored browser for {sys.platform}")
    arch = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}.get(
        platform.machine().lower(), platform.machine().lower()
    )
    return f"{name}.{arch}"


def _download(url: str, destination: Path) -> None:
    print(f"Downloading {url}", file=sys.stderr)
    with urllib.request.urlopen(url, timeout=120) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def seed_browser() -> Path:
    """Install the pinned browser build into the SDK cache and mark it active."""
    from camoufox import multiversion
    from camoufox.browser_pin import load_pin

    pin = load_pin()
    if pin is None:
        raise SystemExit("This library carries no pinned browser; nothing to mirror")

    key = platform_key()
    if key not in BROWSER_ASSETS:
        raise SystemExit(f"No mirrored browser asset for {key}")
    expected, size = BROWSER_ASSETS[key]

    folder = f"{pin.version}-{pin.build}"
    repo_dir = multiversion.BROWSERS_DIR / pin.repo_name
    target = repo_dir / folder
    if (target / "version.json").is_file():
        print(f"The mirrored browser is already installed: {target}", file=sys.stderr)
        _activate(target, pin)
        return target

    name = f"camoufox-{pin.version}-{pin.build}-{key}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".cpm-mirror-") as workspace:
        archive = Path(workspace) / name
        _download(f"{MIRROR_BASE}/{name}", archive)
        downloaded = archive.stat().st_size
        if downloaded != size:
            raise SystemExit(f"{name} is {downloaded} bytes; the pinned size is {size}")
        digest = _sha256(archive)
        if digest != expected:
            raise SystemExit(f"{name} does not match the recorded digest: {digest}")
        staging = Path(workspace) / "browser"
        with zipfile.ZipFile(archive) as source:
            source.extractall(staging)
        (staging / "version.json").write_text(
            json.dumps(
                {
                    "version": pin.version,
                    "build": pin.build,
                    "prerelease": True,
                    "sha256": expected,
                    "interface": 1,
                }
            ),
            encoding="utf-8",
        )
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(staging), str(target))

    if sys.platform != "win32":
        for path in target.rglob("*"):
            path.chmod(0o755 if path.is_dir() else 0o755)
    _activate(target, pin)
    print(f"Installed the mirrored browser at {target}", file=sys.stderr)
    return target


def _activate(target: Path, pin) -> None:
    from camoufox import multiversion

    multiversion.COMPAT_FLAG.touch()
    config = multiversion.load_config()
    config["active_version"] = target.relative_to(multiversion.INSTALL_DIR).as_posix()
    multiversion.save_config(config)


def seed_geoip() -> None:
    """Install the location database the pinned source can no longer supply."""
    import maxminddb
    from camoufox import geolocation

    geoip_dir = geolocation.GEOIP_DIR
    mmdb_dir = geoip_dir / "mmdb"
    mmdb_dir.mkdir(parents=True, exist_ok=True)
    keys = sorted(GEOIP_ASSETS)
    name = GEOIP_SOURCE_NAME.lower()
    if all((mmdb_dir / f"{name}-{key}.mmdb").is_file() for key in keys):
        print("The location database is already installed", file=sys.stderr)
    else:
        with tempfile.TemporaryDirectory(prefix=".cpm-geoip-") as workspace:
            downloaded = Path(workspace) / "city.mmdb"
            _download(GEOIP_URL, downloaded)
            with maxminddb.open_database(str(downloaded)) as database:
                metadata = database.metadata()
                if "GeoLite2-City" not in str(metadata.database_type):
                    raise SystemExit(
                        f"The location database is {metadata.database_type}, not a city database"
                    )
            for key in keys:
                shutil.copyfile(downloaded, mmdb_dir / f"{name}-{key}.mmdb")
                os.utime(mmdb_dir / f"{name}-{key}.mmdb")

    # Explicit: the built-in default source is gone, and a cache that keeps
    # naming it would fall back to it on every launch and fail the download.
    geolocation.save_geoip_config({"name": GEOIP_SOURCE_NAME}, explicit=True)
    print(f"Selected location database: {GEOIP_SOURCE_NAME}", file=sys.stderr)
