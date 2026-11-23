"""The saved master switch for automation: off by default, overridden by the environment."""

import pytest

from camoufox_pm.config import Settings
from camoufox_pm.core import app_settings
from camoufox_pm.core.app_settings import (
    AppSettings,
    automation_enabled,
    automation_locked,
    set_automation_enabled,
)
from camoufox_pm.core.database import StorageManager


@pytest.fixture
async def storage(tmp_path):
    storage = StorageManager(str(tmp_path / "settings.db"))
    await storage.initialize()
    yield storage
    await storage.close()


@pytest.fixture
def environment(monkeypatch):
    """Stand in for the process environment: `set(None)` leaves the switch unset."""

    def use(value: bool | None) -> None:
        chosen = (
            Settings(_env_file=None)
            if value is None
            else Settings(_env_file=None, automation_enabled=value)
        )
        monkeypatch.setattr(app_settings, "get_settings", lambda: chosen)

    use(None)
    return use


@pytest.mark.asyncio
async def test_automation_is_off_until_someone_turns_it_on(storage, environment):
    assert automation_locked() is False
    assert automation_enabled(storage) is False

    set_automation_enabled(storage, True)
    assert automation_enabled(storage) is True

    set_automation_enabled(storage, False)
    assert automation_enabled(storage) is False


@pytest.mark.asyncio
async def test_the_saved_switch_survives_a_restart(tmp_path, environment):
    path = str(tmp_path / "kept.db")
    first = StorageManager(path)
    await first.initialize()
    set_automation_enabled(first, True)
    await first.close()

    again = StorageManager(path)
    await again.initialize()
    try:
        assert automation_enabled(again) is True
    finally:
        await again.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("named,saved", [(True, False), (False, True)])
async def test_the_environment_decides_when_it_names_a_value(storage, environment, named, saved):
    set_automation_enabled(storage, saved)

    environment(named)

    assert automation_locked() is True
    assert automation_enabled(storage) is named
    # The screen cannot change what the environment decided, and nothing is saved.
    with pytest.raises(PermissionError, match="CPM_AUTOMATION_ENABLED"):
        set_automation_enabled(storage, not named)
    assert AppSettings(storage).get(app_settings.AUTOMATION) == ("1" if saved else "0")

    environment(None)
    assert automation_locked() is False and automation_enabled(storage) is saved


@pytest.mark.asyncio
async def test_only_the_value_the_switch_writes_turns_automation_on(storage, environment):
    store = AppSettings(storage)
    assert store.get(app_settings.AUTOMATION) is None
    for stray in ("true", "yes", "on", "", "0"):
        store.set(app_settings.AUTOMATION, stray)
        assert automation_enabled(storage) is False, stray
    store.set(app_settings.AUTOMATION, "1")
    assert automation_enabled(storage) is True


@pytest.mark.asyncio
async def test_a_database_from_before_the_switch_gains_its_table_and_stays_off(
    tmp_path, environment
):
    path = str(tmp_path / "older.db")
    storage = StorageManager(path)
    await storage.initialize()
    storage.db._connection.execute("DROP TABLE app_settings")
    storage.db._connection.commit()
    await storage.close()

    upgraded = StorageManager(path)
    await upgraded.initialize()
    try:
        assert automation_enabled(upgraded) is False
    finally:
        await upgraded.close()
