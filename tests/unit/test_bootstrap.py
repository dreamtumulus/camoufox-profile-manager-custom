"""The administrator declared by the deployment, and the recovery it exists for.

A forgotten password is not a database problem: the operator sets the account on
the server's environment and restarts. These tests pin that contract — created
when missing, reset when it drifted, untouched when it already matches — and that
a reset really revokes whatever session the old password left open.
"""

from loguru import logger

from camoufox_pm.config import Settings
from camoufox_pm.core import auth
from camoufox_pm.core.bootstrap import apply_configured_admin

PASSWORD = "server-side-password"


def _settings(username: str = "root", password: str = PASSWORD) -> Settings:
    return Settings(_env_file=None, admin_username=username, admin_password=password)


async def test_no_configured_admin_changes_nothing(storage):
    assert await apply_configured_admin(storage, Settings(_env_file=None)) is None
    assert await storage.count_users() == 0


async def test_a_missing_account_is_created_as_administrator(storage):
    assert await apply_configured_admin(storage, _settings()) == "created"

    user = await storage.get_user_by_username("root")
    assert user["role"] == "administrator"
    assert auth.verify_password(user["password_hash"], PASSWORD)
    assert PASSWORD not in user["password_hash"]


async def test_an_account_already_in_step_is_left_alone(storage):
    await apply_configured_admin(storage, _settings())
    before = await storage.get_user_by_username("root")
    token = await auth.create_session(storage, before["id"])

    assert await apply_configured_admin(storage, _settings()) == "unchanged"

    after = await storage.get_user_by_username("root")
    assert after["password_hash"] == before["password_hash"]
    # No write means no revocation: restarting with the same configuration must
    # not log everyone out.
    assert await auth.validate_session(storage, token) == "root"


async def test_a_changed_password_resets_the_hash_and_revokes_sessions(storage):
    await storage.create_user(
        auth.new_user_id(), "root", auth.hash_password("forgotten-password")
    )
    user = await storage.get_user_by_username("root")
    token = await auth.create_session(storage, user["id"])

    assert await apply_configured_admin(storage, _settings()) == "updated"

    updated = await storage.get_user_by_username("root")
    assert auth.verify_password(updated["password_hash"], PASSWORD)
    assert not auth.verify_password(updated["password_hash"], "forgotten-password")
    assert await auth.validate_session(storage, token) is None


async def test_a_demoted_account_is_promoted_back(storage):
    await storage.create_user(
        auth.new_user_id(), "root", auth.hash_password(PASSWORD), role="viewer"
    )

    assert await apply_configured_admin(storage, _settings()) == "updated"

    user = await storage.get_user_by_username("root")
    assert user["role"] == "administrator"


async def test_the_password_never_reaches_the_log(storage):
    messages: list[str] = []
    handler = logger.add(lambda message: messages.append(str(message)), level="INFO")
    try:
        await apply_configured_admin(storage, _settings(password="never-log-this-value"))
    finally:
        logger.remove(handler)

    assert messages, "the reconciliation should say what it did"
    assert all("never-log-this-value" not in message for message in messages)
