"""Install and stage the exact Camoufox build paired with this package."""

import json
import shutil
from pathlib import Path

from install_ci_browser import install_browser

ROOT = Path(__file__).resolve().parent.parent


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
    geoip = multiversion.INSTALL_DIR / "geoip"
    if geoip.is_dir():
        shutil.copytree(geoip, stage / "geoip", symlinks=True)
    (stage / "config.json").write_text(
        json.dumps({"active_version": relative.as_posix()}, indent=2) + "\n", encoding="utf-8"
    )
    (stage / ".0.5_FLAG").touch()
    return stage


if __name__ == "__main__":
    print(stage_browser())
