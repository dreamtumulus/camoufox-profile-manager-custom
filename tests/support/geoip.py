"""Tiny location databases for tests, in both layouts the sources publish.

A real city database is tens of megabytes and has to be downloaded. These hold a
single record that answers for every address, which is all a test of *which
fields are read* needs. The writer covers only the MaxMind DB types used below.
"""

import struct
import time
from pathlib import Path

# GeoLite2 City as MaxMind publishes it: values nested by subject.
NESTED = {
    "country": {"iso_code": "US"},
    "location": {"latitude": 37.751, "longitude": -97.822, "time_zone": "America/Chicago"},
}
NESTED_PATHS = {
    "iso_code": "country.iso_code",
    "longitude": "location.longitude",
    "latitude": "location.latitude",
    "timezone": "location.time_zone",
}
# The same data as sapics/ip-location-db republishes it: one flat record.
FLAT = {
    "country_code": "US",
    "latitude": 37.751,
    "longitude": -97.822,
    "timezone": "America/Chicago",
}
FLAT_PATHS = {
    "iso_code": "country_code",
    "longitude": "longitude",
    "latitude": "latitude",
    "timezone": "timezone",
}

_STRING, _DOUBLE, _UINT16, _UINT32, _MAP, _UINT64, _ARRAY = 2, 3, 5, 6, 7, 9, 11


def _control(kind: int, size: int) -> bytes:
    if size >= 29:
        raise ValueError("Test databases keep every value short")
    return bytes([kind << 5 | size]) if kind < 8 else bytes([size, kind - 7])


def _unsigned(kind: int, value: int) -> bytes:
    data = value.to_bytes(8, "big").lstrip(b"\0")
    return _control(kind, len(data)) + data


def _encode(value) -> bytes:
    if isinstance(value, dict):
        return _control(_MAP, len(value)) + b"".join(
            _encode(key) + _encode(item) for key, item in value.items()
        )
    if isinstance(value, list):
        return _control(_ARRAY, len(value)) + b"".join(_encode(item) for item in value)
    if isinstance(value, str):
        data = value.encode()
        return _control(_STRING, len(data)) + data
    if isinstance(value, float):
        return _control(_DOUBLE, 8) + struct.pack(">d", value)
    raise TypeError(f"Unsupported test value: {type(value).__name__}")


def write_mmdb(path: Path, record: dict, build_epoch: int | None = None) -> Path:
    """Write a database in which every IPv4 and IPv6 address answers `record`."""
    # One node whose two branches both point at the first data record. The
    # reader stops at the first branch, so the address itself never matters.
    node_count = 1
    pointer = (node_count + 16).to_bytes(3, "big")
    metadata = (
        _control(_MAP, 9)
        + _encode("binary_format_major_version")
        + _unsigned(_UINT16, 2)
        + _encode("binary_format_minor_version")
        + _unsigned(_UINT16, 0)
        + _encode("build_epoch")
        + _unsigned(_UINT64, int(time.time()) if build_epoch is None else build_epoch)
        + _encode("database_type")
        + _encode("Test-City")
        + _encode("description")
        + _encode({"en": "test"})
        + _encode("ip_version")
        + _unsigned(_UINT16, 6)
        + _encode("languages")
        + _encode(["en"])
        + _encode("node_count")
        + _unsigned(_UINT32, node_count)
        + _encode("record_size")
        + _unsigned(_UINT16, 24)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        pointer + pointer + bytes(16) + _encode(record) + b"\xab\xcd\xefMaxMind.com" + metadata
    )
    return path


# The two sources the pinned library lists, by the layout each one reads.
FLAT_SOURCE = "MaxMind GeoLite2"
NESTED_SOURCE = "GeoIP AIO by daijro"


def point_library_at(monkeypatch, root: Path) -> Path:
    """Make the library read and write location data under `root`/geoip only."""
    from camoufox import geolocation

    from camoufox_pm.core import browser_install

    geoip = root / "geoip"
    monkeypatch.setattr(geolocation, "GEOIP_DIR", geoip)
    monkeypatch.setattr(geolocation, "MMDB_DIR", geoip / "mmdb")
    monkeypatch.setattr(geolocation, "GEOIP_CONFIG", geoip / "config.yml")
    monkeypatch.setattr(browser_install, "_geoip_check", None)
    return geoip


def install_source(geoip: Path, name: str, record: dict) -> list[Path]:
    """Select the library source `name` and store `record` in each file it reads."""
    from camoufox import geolocation

    config = geolocation._get_geoip_config_by_name(name)
    files = [
        write_mmdb(geoip / "mmdb" / f"{name.lower()}-{family}.mmdb", record)
        for family in config["urls"]
    ]
    (geoip / "config.yml").write_text(f"explicit: true\nname: {name}\n", encoding="utf-8")
    return files
