"""The optional administrator declared by the deployment, reconciled at startup.

Setting ``CPM_ADMIN_USERNAME`` and ``CPM_ADMIN_PASSWORD`` makes the account part
of the server's configuration instead of something that only exists inside the
database. Every start compares the stored account with that configuration:

* missing account — created as an administrator;
* different password — the stored argon2id hash is replaced and the account's
  sessions are invalidated, so a reset really logs stale browsers out;
* different role — promoted back to administrator;
* already in step — nothing is written and no session is touched.

That is the recovery path for a forgotten password: change the environment on
the host and restart. The plaintext password never enters the database, the
activity log or the process arguments; it lives in the deployment's environment,
which is where the operator already keeps ``CPM_SECRET_KEY`` and ``CPM_API_KEY``.
Unset both variables to hand account management back to ``camoufox-pm user``.
"""

from loguru import logger

from camoufox_pm.api.principal import Principal
from camoufox_pm.config import Settings
from camoufox_pm.core import auth


async def apply_configured_admin(storage, settings: Settings) -> str | None:
    """Reconcile ``settings.admin_username`` with the users table.

    Returns ``"created"``, ``"updated"``, ``"unchanged"`` or ``None`` when no
    administrator is configured. Passwords and hashes never appear in the log.
    """
    username = settings.admin_username
    password = settings.admin_password
    if not username or not password:
        return None

    existing = await storage.get_user_by_username(username)
    if existing is None:
        await storage.create_user(
            auth.new_user_id(),
            username,
            auth.hash_password(password),
            role="administrator",
            actor=Principal("system"),
        )
        logger.info(f"Configured administrator '{username}' created")
        return "created"

    password_matches = auth.verify_password(existing["password_hash"], password)
    if password_matches and existing["role"] == "administrator":
        return "unchanged"

    # One transaction: a changed hash and a changed role both revoke sessions,
    # which is the point — the old browser must not keep the access the reset
    # was meant to take back.
    await storage.update_user_access(
        username,
        role="administrator",
        password_hash=None if password_matches else auth.hash_password(password),
        actor=Principal("system"),
    )
    if password_matches:
        logger.info(f"Configured administrator '{username}' restored to administrator")
    else:
        logger.info(f"Configured administrator '{username}' password reset from environment")
    return "updated"
