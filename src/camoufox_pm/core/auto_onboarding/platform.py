"""1DayAPI as an onboarder token sees it: what it may use, and its authorization jobs.

Only routes the platform already has for such a token. Three read what it may
use: who it is and which nodes and organization profiles it reaches, and a
node's proxy groups and assignable proxies. The others are the life of one
authorization job: make it (the platform answers with a link), hand over the
code the account's owner got by consenting, ask how it stands, cancel it.
"""

import asyncio
from typing import Any
from urllib.parse import quote

from ..onboarding import ONBOARDING_ORIGIN, OnboardingError, OneDayClient

PLATFORM = ONBOARDING_ORIGIN
TOKEN_PREFIX = "1dk_onb_"


class TokenRefused(OnboardingError):
    """The platform does not accept the token: wrong, or revoked since.

    Answered as 409 rather than 401: in this app 401 and 403 mean its own
    sign-in, and the web UI acts on them.
    """

    def __init__(self) -> None:
        super().__init__(
            "The platform refused the onboarder token: it is invalid or was revoked.",
            409,
            "onboard_token_refused",
        )


async def _ask(token: str, method: str, path: str, body: Any = None) -> Any:
    try:
        return await OneDayClient(token).request(method, path, body)
    except OnboardingError as exc:
        if getattr(exc, "upstream_status", None) == 401:
            raise TokenRefused() from exc
        raise


async def _get(token: str, path: str) -> Any:
    return await _ask(token, "GET", path)


async def create_job(token: str, request: dict[str, Any]) -> dict[str, Any]:
    """Make an authorization job. The answer carries its id and the authorize link."""
    return await _ask(token, "POST", "/api/onboard/jobs", {"method": "auth_url", **request})


async def hand_over(token: str, job_id: str, code: str) -> dict[str, Any]:
    """Give the platform what the authorize page showed after consent.

    The platform calls the field a callback URL; it takes the `code#state` the
    page shows as well as the whole address the browser was sent to.
    """
    return await _ask(
        token,
        "POST",
        "/api/onboard/jobs",
        {"method": "auth_url", "jobId": job_id, "callbackUrl": code},
    )


async def read_job(token: str, job_id: str) -> dict[str, Any]:
    return await _ask(token, "GET", f"/api/onboard/jobs/{quote(job_id, safe='')}")


async def cancel_job(token: str, job_id: str) -> dict[str, Any]:
    """Cancel a job; the answer is the job as it stands, which may be done already."""
    return await _ask(token, "POST", f"/api/onboard/jobs/{quote(job_id, safe='')}/cancel", {})


def _node(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": node["nodeId"],
        "name": node.get("name") or node["nodeId"],
        "online": bool(node.get("online")),
        "used": int(node.get("used") or 0),
        "capacity": int(node.get("capacity") or 0),
    }


def _org(org: dict[str, Any]) -> dict[str, Any]:
    return {"id": org["id"], "name": org["name"], "domains": list(org.get("domains") or [])}


def _proxy(proxy: dict[str, Any]) -> dict[str, Any]:
    """A pool entry as the screen needs it: where it comes out, never how to reach it."""
    return {
        "id": proxy["id"],
        "exit_ip": proxy.get("exitIp"),
        "group": proxy.get("group"),
        # How many more accounts this exit can take before the platform's hard limit.
        "slots": int(proxy.get("overflowSlots") or 0),
    }


async def read_identity(token: str) -> dict[str, Any]:
    """The token's label, and the nodes and organization profiles it reaches."""
    me = await _get(token, "/api/onboard/me")
    nodes = me.get("nodes") or ([me["node"]] if me.get("node") else [])
    return {
        "label": str(me.get("label") or ""),
        "nodes": [_node(node) for node in nodes],
        "orgs": [_org(org) for org in me.get("orgs") or []],
    }


async def read_pool(token: str, node_id: str) -> dict[str, Any]:
    """A node's proxy groups, and its proxies that can still take an account."""
    node = quote(node_id, safe="")
    groups, proxies = await asyncio.gather(
        _get(token, f"/api/onboard/node-groups?nodeId={node}"),
        _get(token, f"/api/onboard/proxies?nodeId={node}"),
    )
    return {
        "node_id": node_id,
        # A pool's proxies without a group are counted under a nameless entry;
        # only named groups can be asked for.
        "groups": [
            {"name": group["group"], "assignable": int(group.get("assignable") or 0)}
            for group in groups
            if group.get("group")
        ],
        "proxies": [_proxy(proxy) for proxy in proxies],
    }


async def read_options(token: str, node_id: str | None) -> dict[str, Any]:
    """Everything the card offers, with the proxy pool of one node.

    The pool is that of `node_id` when the token reaches it, and of the first
    node otherwise; `pool["node_id"]` says which. It is None only when the
    token reaches no node at all.
    """
    identity = await read_identity(token)
    known = [node["id"] for node in identity["nodes"]]
    chosen = node_id if node_id in known else (known[0] if known else None)
    pool = await read_pool(token, chosen) if chosen else None
    return {**identity, "pool": pool}
