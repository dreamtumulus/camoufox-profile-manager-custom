#!/usr/bin/env python3
"""Exercise the manager's normal installation on a fresh CI browser cache.

CI supplies a read-only GITHUB_TOKEN for release discovery. The normal installer
verifies readiness and the exact upstream browser pair before acceptance starts.

Upstream's release repository disappeared in November 2026, so a build that
cannot reach it installs the same pinned build from a mirror that records its
source release and published digests (see mirror_browser.py).
"""

import sys


def install_browser() -> str:
    from camoufox.browser_pin import load_pin

    from camoufox_pm.core.browser_install import BrowserInstaller

    installer = BrowserInstaller()
    installer._run()
    status = installer.status()
    if not status.installed:
        import mirror_browser

        print(
            "Upstream browser releases are unreachable; installing the pinned build from the mirror.",
            file=sys.stderr,
        )
        mirror_browser.seed_browser()
        mirror_browser.seed_geoip()
        installer = BrowserInstaller()
        installer._run()
        status = installer.status()

    pin = load_pin()
    if pin is None or status.state != "ready" or not status.installed or status.version != pin.spec:
        raise RuntimeError("CI browser setup did not install the wrapper's paired build")
    return pin.spec


def main() -> int:
    try:
        version = install_browser()
    except Exception as exc:
        # Do not render arbitrary exception data/request headers into CI logs.
        print(f"Camoufox CI setup failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(f"Camoufox CI browser and resources are ready: {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
