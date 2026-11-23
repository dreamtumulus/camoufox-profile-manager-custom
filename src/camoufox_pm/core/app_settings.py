"""Preferences kept in the database: what the Settings screen changes without a restart.

Environment variables configure an installation and need a restart. A packaged
desktop app is started by a double click and has no environment to edit, so the
few things a person must be able to switch from the screen are stored here.
"""

import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ..config import get_settings

if TYPE_CHECKING:
    from .database import StorageManager

# The master switch for what the program does inside a profile's browser on its
# own. Every automation is asked through `automation_enabled`, so there is one
# place that decides.
AUTOMATION = "automation_enabled"


def initialize_tables(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
    """)


class AppSettings:
    def __init__(self, storage: "StorageManager"):
        self.connection = storage.db._connection

    def get(self, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
        return None if row is None else str(row["value"])

    def set(self, key: str, value: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (key, value, datetime.now(timezone.utc).isoformat()),
            )


def automation_locked() -> bool:
    """Whether the environment names a value for the switch.

    `CPM_AUTOMATION_ENABLED`, set either way, is an explicit decision about this
    installation: it wins, and the screen shows the switch without offering it.
    """
    return AUTOMATION in get_settings().model_fields_set


def automation_enabled(storage: "StorageManager") -> bool:
    """Whether the program may act inside a profile's browser on its own.

    Off unless someone turned it on: in the environment, or else on the Settings
    screen. Read at the moment of use, so a change applies to the next browser
    opened, with no restart.
    """
    if automation_locked():
        return bool(get_settings().automation_enabled)
    return AppSettings(storage).get(AUTOMATION) == "1"


def set_automation_enabled(storage: "StorageManager", enabled: bool) -> None:
    """Save the switch. Refused while the environment decides it."""
    if automation_locked():
        raise PermissionError("Automation is set by CPM_AUTOMATION_ENABLED")
    AppSettings(storage).set(AUTOMATION, "1" if enabled else "0")
