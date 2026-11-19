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
# The library's other source is still published, and it is taken whole: the
# name, the download addresses and the field paths all come from its one entry
# in the library's repos.yml, so the file on disk always has the layout its
# source describes and later refreshes fetch the same thing.
#
# Do not store another publisher's database under this name. The source reads a
# flat record (`country_code`); MaxMind's own GeoLite2-City nests the same value
# (`country.iso_code`), opens without complaint and then places every address in
# the country "NONE".
GEOIP_SOURCE_NAME = "MaxMind GeoLite2"


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
    """Select the location source that is still published and install its database."""
    from camoufox import geolocation

    from camoufox_pm.core.browser_install import geoip_lookup_works

    selected = geolocation.load_geoip_config().get("name") == GEOIP_SOURCE_NAME
    if selected and geoip_lookup_works():
        print("The location database is already installed", file=sys.stderr)
    else:
        # Naming the source makes it the explicit choice. That matters: the
        # built-in default is gone, and a cache that still names it would go
        # back to it on every launch and fail the download.
        geolocation.download_mmdb(GEOIP_SOURCE_NAME)
    if not geoip_lookup_works():
        raise SystemExit(
            f"The location database installed as {GEOIP_SOURCE_NAME} cannot place a known "
            "address through that source's field paths; refusing to build with it"
        )
    print(f"Selected location database: {GEOIP_SOURCE_NAME}", file=sys.stderr)
