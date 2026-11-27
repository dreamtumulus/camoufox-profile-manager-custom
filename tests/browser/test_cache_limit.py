"""The real browser honours the cache limit, and closing it clears the startup cache."""

import asyncio
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from camoufox_pm.config import get_settings

MB = 1024 * 1024
BLOBS, BLOB_SIZE = 100, 300 * 1024  # About 30 MB of cacheable responses.
BODY = os.urandom(BLOB_SIZE)


class CacheableSite(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            body = (
                "<!doctype html><title>loading</title><script>"
                f"Promise.all([...Array({BLOBS}).keys()].map(i=>"
                "fetch('/blob/'+i).then(r=>r.arrayBuffer())))"
                ".then(()=>document.title='done')</script>"
            ).encode()
            kind = "text/html"
        else:
            body, kind = BODY, "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def folder_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


@pytest.mark.browser
@pytest.mark.asyncio
async def test_page_cache_settles_under_the_limit_and_close_clears_the_startup_cache(
    profile_manager, monkeypatch
):
    manager = profile_manager
    monkeypatch.setenv("CPM_BROWSER_CACHE_MB", "5")
    get_settings.cache_clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), CacheableSite)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    profile = await manager.create_profile("Cache limit", generate_fingerprint=False)
    directory = Path(profile.storage_path)
    try:
        await manager.launch_browser(profile.id, headless=True, geoip="8.8.8.8")
        try:
            context = manager.browser_sessions.active_sessions[profile.id].camoufox.browser
            page = await context.new_page()
            await page.goto(f"http://127.0.0.1:{server.server_port}/", timeout=20000)
            await page.wait_for_function("document.title === 'done'", timeout=60000)
            # The browser evicts in the background, a little after the limit is passed.
            deadline = asyncio.get_running_loop().time() + 120
            while folder_size(directory / "cache2") > 8 * MB:
                assert asyncio.get_running_loop().time() < deadline, (
                    f"cache2 stayed at {folder_size(directory / 'cache2') // MB} MB"
                )
                await asyncio.sleep(2)
            assert (directory / "startupCache").is_dir()
        finally:
            await manager.close_browser(profile.id)
        assert not (directory / "startupCache").exists()
        assert folder_size(directory / "cache2") <= 8 * MB
        # What the account consists of is still there, and the profile opens again.
        assert (directory / "cookies.sqlite").is_file()
        await manager.launch_browser(profile.id, headless=True, geoip="8.8.8.8")
        await manager.close_browser(profile.id)
        assert not (directory / "startupCache").exists()
    finally:
        get_settings.cache_clear()
        server.shutdown()
        server.server_close()
        thread.join()
