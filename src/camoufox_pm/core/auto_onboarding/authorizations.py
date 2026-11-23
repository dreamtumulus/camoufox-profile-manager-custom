"""Authorizing one row's account by hand, and keeping track of what came of it.

The platform makes a link; the account's owner opens it in the row's own
browser and consents; the page then shows a code, which is pasted back and
handed to the platform. Nothing here signs in, consents or reads a page: the
only thing done in a browser is opening the link in a new tab.

Only a job the platform reports as done, for the row's own account, makes the
row onboarded. A link made, a code accepted and a job being worked on are not
success.
"""

import asyncio
import re
from typing import Any

from ..onboarding import OnboardingError
from . import platform
from .preset import Preset, expected_domain, issues
from .scopes import check_rows
from .store import Authorization, OnboardingStore

# One at a time for a profile: two requests arriving together make one job.
_locks: dict[str, asyncio.Lock] = {}

# How the platform starts an error it reports for a job: its code, then a colon.
_ERROR_CODE = re.compile(r"^([a-z][a-z0-9_]{0,63}):")
# The pool refusals share one code and say which one at the start of the message.
_POOL_REASON = re.compile(r"^(proxy_[a-z_]{1,40}):")


def lock_for(profile_id: str) -> asyncio.Lock:
    return _locks.setdefault(profile_id, asyncio.Lock())


def refusal(code: str, message: str, status: int = 409) -> OnboardingError:
    return OnboardingError(message, status, code)


def _platform_refusal(exc: OnboardingError) -> OnboardingError:
    """A refusal by the platform, under a code the screen can explain.

    The platform's own code is kept, behind `platform_`; for a pool refusal the
    more telling reason at the start of its message is used instead.
    """
    reason = exc.upstream_code or "refused"
    told = _POOL_REASON.match(str(exc))
    if reason == "proxy_pool_empty" and told:
        reason = told.group(1)
    return OnboardingError(str(exc), 409, f"platform_{reason}")


def _relayed(exc: OnboardingError) -> OnboardingError:
    """Tell the platform's own refusals from its being unreachable or broken."""
    status = exc.upstream_status
    if status is not None and 400 <= status < 500:
        return _platform_refusal(exc)
    return exc


def job_request(
    preset: Preset, options: dict[str, Any], email: str, proxy_id: str | None
) -> dict[str, Any]:
    """What the platform is asked to make a link for, from the saved choices.

    `proxy_id` is the pool entry matched to the row's own exit, used only when
    every row goes through its own exit.
    """
    request: dict[str, Any] = {"nodeId": preset.node_id, "name": email}
    if preset.proxy_mode == "auto":
        request["proxyId"] = "auto"
        if preset.proxy_group:
            request["proxyGroup"] = preset.proxy_group
    else:
        request["proxyId"] = preset.proxy_id if preset.proxy_mode == "pool" else proxy_id
    if options["orgs"]:
        request["orgId"] = preset.org_id
        domain = expected_domain(preset, options)
        if domain:
            request["domain"] = domain
    elif preset.org_manual and preset.org_uuid:
        request["orgUuid"] = preset.org_uuid
    if preset.note:
        request["note"] = preset.note
    if preset.concurrency_limit is not None:
        request["concurrencyLimit"] = preset.concurrency_limit
    if preset.rpm_limit is not None:
        request["rpmLimit"] = preset.rpm_limit
    return request


def _made_with(
    preset: Preset, options: dict[str, Any], proxy_id: str | None
) -> tuple[str | None, str | None]:
    """The node and the proxy an authorization is made with, as the row shows them.

    The proxy is where it comes out, or "auto" (with the group, when one is
    named) when the platform picks.
    """
    node = next((n["name"] for n in options["nodes"] if n["id"] == preset.node_id), preset.node_id)
    if preset.proxy_mode == "auto":
        return node, f"auto:{preset.proxy_group}" if preset.proxy_group else "auto"
    chosen = preset.proxy_id if preset.proxy_mode == "pool" else proxy_id
    pool = (options.get("pool") or {}).get("proxies", [])
    exit_ip = next((p["exit_ip"] for p in pool if p["id"] == chosen), None)
    return node, exit_ip or chosen


def settle(
    store: OnboardingStore, authorization: Authorization, job: dict[str, Any]
) -> Authorization:
    """Write down what the platform says of a job, and what that means for the row."""
    status = job.get("status")
    expires = job.get("expiresAt") or authorization.expires_at
    if status in ("pending_callback", "working"):
        return store.update(
            authorization,
            status="pending" if status == "pending_callback" else "working",
            expires_at=expires,
        )
    if status == "done":
        account_email = job.get("accountEmail")
        # The platform holds the account to the chosen domain, not to the row's
        # address: another account of the same domain can have been signed in.
        if account_email and account_email.strip().lower() != authorization.email:
            outcome = "mismatch"
        elif job.get("reused"):
            outcome = "reused"
        else:
            outcome = "onboarded"
        settled = store.update(
            authorization,
            status="done",
            outcome=outcome,
            account_id=job.get("accountId"),
            account_email=account_email,
            cooldown_until=job.get("cooldownUntil"),
        )
        if outcome != "mismatch":
            store.mark_onboarded(
                authorization.profile_id, authorization.email, job.get("accountId")
            )
        return settled
    if status == "failed":
        told = _ERROR_CODE.match(str(job.get("error") or ""))
        return store.update(
            authorization,
            status="failed",
            outcome="failed",
            error_code=told.group(1) if told else "authorization_failed",
        )
    return authorization


async def sync(
    store: OnboardingStore, token: str, authorization: Authorization
) -> tuple[Authorization, str | None]:
    """Ask the platform how an open authorization stands; also its link, while it waits.

    A job the platform no longer knows under this token is lost: it can neither
    be asked about nor cancelled, and the platform lets it lapse by itself.
    """
    if not authorization.open:
        return authorization, None
    try:
        job = await platform.read_job(token, authorization.job_id)
    except OnboardingError as exc:
        if exc.upstream_status == 404:
            return store.update(authorization, status="lost"), None
        raise
    settled = settle(store, authorization, job)
    link = job.get("authorizeUrl") if settled.status == "pending" else None
    return settled, link if isinstance(link, str) and link.startswith("https://") else None


async def create(
    store: OnboardingStore, token: str, row: dict[str, Any]
) -> tuple[Authorization, str | None]:
    """Have the platform make an authorize link for a row, with the saved choices.

    Refused, before the platform is asked for a job, when the choices or the
    row would not do. `row` is the profile as `scopes.list_rows` gives it.
    """
    preset, _ = store.preset()
    options = await platform.read_options(token, preset.node_id)
    store.accepted(options["label"])
    found = issues(preset, options)
    if found:
        raise refusal(found[0].code, "The onboarding parameters are not complete")
    checked = check_rows([row], preset, options)[0]
    if checked["status"] != "ready":
        raise refusal(checked["status"], "This profile cannot be onboarded with these parameters")
    try:
        job = await platform.create_job(
            token, job_request(preset, options, row["email"], checked["proxy_id"])
        )
    except platform.TokenRefused:
        raise
    except OnboardingError as exc:
        raise _relayed(exc) from exc
    link = job.get("authorizeUrl")
    if not isinstance(job.get("jobId"), str) or not isinstance(link, str):
        raise OnboardingError("1dayApi 返回格式不正确", 502, "oneday_response_invalid")
    node, proxy = _made_with(preset, options, checked["proxy_id"])
    authorization = store.begin(row["profile_id"], row["email"], job["jobId"], node, proxy, None)
    # The time it has left is the platform's to say, so it is asked once more; a
    # link whose expiry could not be read is still a link.
    try:
        authorization, _ = await sync(store, token, authorization)
    except OnboardingError:
        pass
    return authorization, link if link.startswith("https://") else None


async def hand_over(
    store: OnboardingStore, token: str, authorization: Authorization, code: str
) -> Authorization:
    """Hand the pasted code to the platform. What comes back is not success yet."""
    try:
        await platform.hand_over(token, authorization.job_id, code)
    except platform.TokenRefused:
        raise
    except OnboardingError as exc:
        if exc.upstream_code == "callback_invalid":
            raise refusal(
                "callback_invalid",
                "The platform refused the code: it is incomplete or belongs to another row",
                422,
            ) from exc
        # The answer was lost, or the job was no longer waiting: the job itself
        # says which. Never sent a second time unseen.
        settled, _ = await sync(store, token, authorization)
        if settled.status == "pending":
            raise _relayed(exc) from exc
        return settled
    return store.update(authorization, status="working")


async def cancel(store: OnboardingStore, token: str, authorization: Authorization) -> Authorization:
    """Cancel an open authorization. One that finished meanwhile stays finished."""
    try:
        job = await platform.cancel_job(token, authorization.job_id)
    except platform.TokenRefused:
        raise
    except OnboardingError as exc:
        if exc.upstream_status == 404:
            return store.update(authorization, status="lost")
        raise _relayed(exc) from exc
    return settle(store, authorization, job)
