"""Generate Firefox device fingerprints with Scrapfly's Bayesian fpgen model."""

import asyncio
import random
from typing import Any

from .models import BrowserSettings

_FPGEN_OS = {"windows": "Windows", "macos": "MacOS", "linux": "Linux"}
_FPGEN_TO_CAMOUFOX_OS = {value.lower(): key for key, value in _FPGEN_OS.items()}


def generate_fpgen_fingerprint(os_name: str | list[str] | tuple[str, ...] | None = None) -> dict:
    """Draw one complete Firefox fingerprint, optionally constrained by OS."""
    import fpgen

    conditions: dict[str, Any] = {"browser": "Firefox"}
    if isinstance(os_name, str):
        conditions["os"] = _FPGEN_OS.get(os_name, os_name)
    elif os_name:
        conditions["os"] = tuple(_FPGEN_OS.get(name, name) for name in os_name)
    return fpgen.generate(conditions)


def _integer(value: Any) -> int | None:
    return value if type(value) is int else None


class FingerprintGenerator:
    """Generate realistic, Camoufox-friendly browser constraints with fpgen."""

    def __init__(self) -> None:
        self.language_sets = {
            "us": ["en-US", "en"],
            "uk": ["en-GB", "en"],
            "russia": ["ru-RU", "ru", "en-US"],
            "germany": ["de-DE", "de", "en"],
            "france": ["fr-FR", "fr", "en"],
            "spain": ["es-ES", "es", "en"],
            "italy": ["it-IT", "it", "en"],
            "china": ["zh-CN", "zh", "en"],
            "japan": ["ja-JP", "ja", "en"],
        }
        self.locale_map = {
            "us": "en_US",
            "uk": "en_GB",
            "russia": "ru_RU",
            "germany": "de_DE",
            "france": "fr_FR",
            "spain": "es_ES",
            "italy": "it_IT",
            "china": "zh_CN",
            "japan": "ja_JP",
        }
        self.timezone_map = {
            "us": "America/New_York",
            "uk": "Europe/London",
            "russia": "Europe/Moscow",
            "germany": "Europe/Berlin",
            "france": "Europe/Paris",
            "spain": "Europe/Madrid",
            "italy": "Europe/Rome",
            "china": "Asia/Shanghai",
            "japan": "Asia/Tokyo",
        }
        self.coords_map = {
            "us": {"lat": 40.7128, "lon": -74.0060},
            "uk": {"lat": 51.5074, "lon": -0.1278},
            "russia": {"lat": 55.7558, "lon": 37.6176},
            "germany": {"lat": 52.5200, "lon": 13.4050},
            "france": {"lat": 48.8566, "lon": 2.3522},
            "spain": {"lat": 40.4168, "lon": -3.7038},
            "italy": {"lat": 41.9028, "lon": 12.4964},
            "china": {"lat": 31.2304, "lon": 121.4737},
            "japan": {"lat": 35.6762, "lon": 139.6503},
        }

    async def generate_fingerprint(
        self, constraints: dict[str, Any] | BrowserSettings | None = None
    ) -> BrowserSettings:
        """Draw a coherent fpgen sample and retain its user-editable constraints."""
        settings, _ = await self.generate_device(constraints)
        return settings

    async def generate_device(
        self, constraints: dict[str, Any] | BrowserSettings | None = None
    ) -> tuple[BrowserSettings, dict]:
        """Return settings and the same raw sample to resolve on the first launch."""
        if isinstance(constraints, BrowserSettings):
            constraints = constraints.model_dump()

        requested = constraints or {}
        fingerprint = await asyncio.to_thread(generate_fpgen_fingerprint, requested.get("os"))
        navigator = fingerprint.get("navigator") or {}
        screen = fingerprint.get("screen") or {}
        width, height = _integer(screen.get("width")), _integer(screen.get("height"))
        os_name = _FPGEN_TO_CAMOUFOX_OS.get(str(fingerprint.get("os", "")).lower())
        if os_name is None:
            os_name = requested.get("os") or "windows"

        settings = BrowserSettings(
            os=os_name,
            screen=f"{width}x{height}" if width and height else "1920x1080",
            hardware_concurrency=_integer(navigator.get("hardwareConcurrency")),
            device_memory=_integer(navigator.get("deviceMemory")),
            max_touch_points=_integer(navigator.get("maxTouchPoints")) or 0,
            fingerprint_source="fpgen",
        )
        region = requested.get("region")
        if region:
            settings.languages = self.language_sets.get(region, ["en-US", "en"])
            settings.locale = self.locale_map.get(region, "en_US")
            settings.timezone = self.timezone_map.get(region, "UTC")
            settings.geolocation = self._geolocation_for_region(region)
        return settings, fingerprint

    async def reset_fingerprint(self, profile_id: str) -> BrowserSettings:
        """Regenerate a fingerprint from scratch."""
        return await self.generate_fingerprint()

    async def rotate_fingerprint(self, current: BrowserSettings) -> BrowserSettings:
        """Rotate a fingerprint, keeping the same operating system."""
        return await self.generate_fingerprint({"os": current.os})

    def _geolocation_for_region(self, region: str) -> dict[str, float]:
        """Return approximate coordinates for a region with a small random offset."""
        base = self.coords_map.get(region, {"lat": 0.0, "lon": 0.0})
        return {
            "lat": base["lat"] + random.uniform(-0.5, 0.5),
            "lon": base["lon"] + random.uniform(-0.5, 0.5),
        }
