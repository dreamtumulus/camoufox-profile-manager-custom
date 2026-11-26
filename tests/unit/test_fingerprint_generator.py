"""Tests for direct Scrapfly fpgen fingerprint generation."""

import fpgen
import pytest

from camoufox_pm.core.fingerprint_generator import FingerprintGenerator
from camoufox_pm.core.models import BrowserSettings

SAMPLE = {
    "os": "Windows",
    "browser": "Firefox",
    "navigator": {
        "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) Firefox/146.0",
        "hardwareConcurrency": 12,
        "deviceMemory": 16,
        "maxTouchPoints": 0,
    },
    "screen": {"width": 2560, "height": 1440},
    "gpu": {"renderer": "GPU A"},
}


@pytest.mark.asyncio
async def test_draws_a_firefox_fingerprint_directly_from_fpgen(monkeypatch):
    calls = []

    def generate(conditions):
        calls.append(conditions)
        return SAMPLE

    monkeypatch.setattr(fpgen, "generate", generate)
    settings = await FingerprintGenerator().generate_fingerprint({"os": "windows", "region": "us"})

    assert calls == [{"browser": "Firefox", "os": "Windows"}]
    assert settings.os == "windows"
    assert settings.screen == "2560x1440"
    assert settings.hardware_concurrency == 12
    assert settings.device_memory == 16
    assert settings.fingerprint_source == "fpgen"
    assert settings.fingerprint_preset_id is None
    assert settings.languages[0].startswith("en")
    assert settings.timezone == "America/New_York"
    assert settings.user_agent is None


@pytest.mark.asyncio
async def test_geolocation_is_set_only_for_an_explicit_region(monkeypatch):
    monkeypatch.setattr(fpgen, "generate", lambda _conditions: SAMPLE)
    settings = await FingerprintGenerator().generate_fingerprint(
        {"os": "windows", "region": "germany"}
    )

    assert settings.geolocation is not None
    assert "lat" in settings.geolocation and "lon" in settings.geolocation


@pytest.mark.asyncio
async def test_a_profile_with_no_region_has_no_geography(monkeypatch):
    monkeypatch.setattr(fpgen, "generate", lambda _conditions: SAMPLE)
    settings = await FingerprintGenerator().generate_fingerprint({"os": "windows"})

    assert settings.timezone is None
    assert settings.geolocation is None
    assert settings.languages == ["en-US", "en"]
    assert settings.hardware_concurrency == 12


@pytest.mark.asyncio
async def test_non_numeric_fpgen_values_do_not_become_fake_hardware(monkeypatch):
    sample = {
        **SAMPLE,
        "navigator": {**SAMPLE["navigator"], "deviceMemory": "undefined"},
    }
    monkeypatch.setattr(fpgen, "generate", lambda _conditions: sample)

    settings = await FingerprintGenerator().generate_fingerprint({"os": "windows"})

    assert settings.device_memory is None


@pytest.mark.asyncio
async def test_rotating_keeps_the_os_and_does_not_move_the_profile(monkeypatch):
    sample = {**SAMPLE, "os": "MacOS"}
    monkeypatch.setattr(fpgen, "generate", lambda _conditions: sample)
    current = BrowserSettings(os="macos", timezone="Europe/Berlin")

    rotated = await FingerprintGenerator().rotate_fingerprint(current)

    assert rotated.os == "macos"
    assert rotated.timezone is None
    assert rotated.geolocation is None


@pytest.mark.asyncio
async def test_consecutive_devices_do_not_repeat_the_gpu(monkeypatch):
    samples = iter(
        (
            SAMPLE,
            {**SAMPLE, "screen": {"width": 1920, "height": 1080}},
            {**SAMPLE, "gpu": {"renderer": "GPU B"}},
        )
    )
    monkeypatch.setattr(fpgen, "generate", lambda _conditions: next(samples))
    generator = FingerprintGenerator()

    _, first = await generator.generate_device({"os": "windows"})
    _, second = await generator.generate_device({"os": "windows"})

    assert first["gpu"]["renderer"] == "GPU A"
    assert second["gpu"]["renderer"] == "GPU B"
