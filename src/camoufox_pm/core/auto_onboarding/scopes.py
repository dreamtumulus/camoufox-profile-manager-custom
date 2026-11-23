"""Which profiles can be onboarded: a group's profiles that have an email address.

A profile without an address has no account to sign in as, so it is neither
counted nor listed. Profiles outside every group cannot be chosen at all.

A profile is onboarded once the platform has reported its account done, and it
is told by profile and address together: given another address, it is another
account and may be onboarded again.
"""

from typing import Any

from ..models import Profile
from .preset import Preset, expected_domain

# Every (profile id, address in lower case) whose account is on the platform.
Onboarded = frozenset[tuple[str, str]] | set[tuple[str, str]]
NONE: Onboarded = frozenset()


def _onboarded(profile: Profile, onboarded: Onboarded) -> bool:
    """Whether this profile's account is already on the platform."""
    return (profile.id, str(profile.email or "").strip().lower()) in onboarded


def _row(profile: Profile, onboarded: Onboarded) -> dict[str, Any]:
    check = profile.proxy_check
    return {
        "profile_id": profile.id,
        "name": profile.name,
        "email": profile.email,
        # Where this profile's own proxy came out when it was last checked. Only
        # a check that passed says so; a failed or missing one leaves it unknown.
        "exit_ip": check.ip if check is not None and check.reachable else None,
        "exit_checked": check is not None,
        "exit_reachable": bool(check is not None and check.reachable),
        "onboarded": _onboarded(profile, onboarded),
    }


def _with_email(profiles: list[Profile], allowed: set[str] | None) -> list[Profile]:
    return [
        profile
        for profile in profiles
        if profile.email and profile.group and (allowed is None or profile.id in allowed)
    ]


async def list_groups(
    manager, allowed: set[str] | None, onboarded: Onboarded = NONE
) -> list[dict[str, Any]]:
    """Groups that still have a profile to onboard, newest group first.

    `allowed` is the set of profile ids the caller may see, or None for all.
    """
    counts: dict[str, dict[str, int]] = {}
    for profile in _with_email(await manager.list_profiles(), allowed):
        count = counts.setdefault(str(profile.group), {"pending": 0, "onboarded": 0})
        count["onboarded" if _onboarded(profile, onboarded) else "pending"] += 1
    return [
        {"id": group["id"], "name": group["name"], **counts[group["id"]]}
        for group in await manager.list_groups()
        if counts.get(group["id"], {}).get("pending")
    ]


async def list_rows(
    manager, group_id: str, allowed: set[str] | None, onboarded: Onboarded = NONE
) -> list[dict[str, Any]]:
    """A group's profiles that have an email address, newest first, all at once."""
    profiles = await manager.list_profiles(filters={"group": group_id})
    return [_row(profile, onboarded) for profile in _with_email(profiles, allowed)]


async def row_of(manager, profile_id: str, onboarded: Onboarded = NONE) -> dict[str, Any] | None:
    """One profile as a row, or None when it is gone, has no address or is in no group."""
    profile = await manager.get_profile(profile_id)
    if profile is None or profile.deleted_at is not None or not _with_email([profile], None):
        return None
    return _row(profile, onboarded)


def check_rows(
    rows: list[dict[str, Any]], preset: Preset, options: dict[str, Any]
) -> list[dict[str, Any]]:
    """Say of every row whether the saved choices would onboard it, and if not, why.

    `status` is "ready", or the first thing that stands in the way:
    "onboarded", "domain_mismatch", and, when every row goes through its own
    exit, "exit_unknown", "exit_failed", "exit_not_in_pool" or "exit_ambiguous".
    For a ready row that goes through its own exit, `proxy_id` names the pool
    entry it matched.
    """
    domain = expected_domain(preset, options)
    pool = options.get("pool") or {"proxies": []}
    by_exit: dict[str, list[str]] = {}
    for proxy in pool["proxies"]:
        if proxy.get("exit_ip"):
            by_exit.setdefault(str(proxy["exit_ip"]).lower(), []).append(proxy["id"])

    checked = []
    for row in rows:
        status, proxy_id = "ready", None
        if row["onboarded"]:
            status = "onboarded"
        elif domain and not str(row["email"]).lower().endswith("@" + domain):
            status = "domain_mismatch"
        elif preset.proxy_mode == "row":
            matches = by_exit.get(str(row["exit_ip"] or "").lower(), [])
            if not row["exit_checked"]:
                status = "exit_unknown"
            elif not row["exit_reachable"] or not row["exit_ip"]:
                status = "exit_failed"
            elif not matches:
                status = "exit_not_in_pool"
            elif len(matches) > 1:
                status = "exit_ambiguous"
            else:
                proxy_id = matches[0]
        checked.append({**row, "status": status, "proxy_id": proxy_id})
    return checked
