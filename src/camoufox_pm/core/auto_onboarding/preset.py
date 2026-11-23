"""What is chosen on the access preset card, and whether those choices would do.

The choices are saved as they are made, so a saved preset may be unfinished: a
proxy not picked yet, a domain not chosen yet. `Preset` therefore only holds
each field to its shape, and `issues` says what is still missing or no longer
on offer, against what the onboarder token may use right now.
"""

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# The three ways a batch's accounts get their proxy on the platform:
#   row  - every row through the pool entry that has its own browser exit;
#   auto - the platform picks, inside `proxy_group` when one is named;
#   pool - one entry of the node's pool, `proxy_id`, for every row.
ProxyMode = Literal["row", "auto", "pool"]

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
# The platform's own bound for both limits.
LIMIT_MAX = 100000


class Preset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str | None = Field(default=None, max_length=120)
    proxy_mode: ProxyMode = "row"
    proxy_group: str | None = Field(default=None, max_length=60)
    proxy_id: str | None = Field(default=None, max_length=60)
    # An organization profile of the token, and one of that profile's domains.
    org_id: str | None = Field(default=None, max_length=60)
    domain: str | None = Field(default=None, max_length=253)
    # Only for a token without organization profiles: leave the organization to
    # the platform, or name it. `org_uuid` is kept as typed; `issues` judges it.
    org_manual: bool = False
    org_uuid: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=500)
    concurrency_limit: int | None = Field(default=None, ge=0, le=LIMIT_MAX)
    rpm_limit: int | None = Field(default=None, ge=0, le=LIMIT_MAX)
    # The local profile group whose profiles are to be onboarded.
    scope_group: str | None = Field(default=None, max_length=120)

    @field_validator(
        "node_id",
        "proxy_group",
        "proxy_id",
        "org_id",
        "domain",
        "org_uuid",
        "note",
        "scope_group",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    def for_another_token(self) -> "Preset":
        """The choices that survive a change of token: those the platform did not offer.

        Another token may reach other nodes and other organizations, so what was
        chosen among them is asked again rather than carried over.
        """
        return self.model_copy(
            update={
                "node_id": None,
                "proxy_group": None,
                "proxy_id": None,
                "org_id": None,
                "domain": None,
                "org_manual": False,
                "org_uuid": None,
            }
        )


class Issue(BaseModel):
    """One reason the saved choices would not do. `code` is what the screen translates."""

    code: str
    field: str


def issues(preset: Preset, options: dict[str, Any]) -> list[Issue]:
    """What is missing from the choices, or no longer among what the token may use.

    `options` is the answer of `platform.read_options` for the preset's node.
    """
    found: list[Issue] = []

    def add(code: str, field: str) -> None:
        found.append(Issue(code=code, field=field))

    nodes = {node["id"]: node for node in options["nodes"]}
    node = nodes.get(preset.node_id or "")
    if preset.node_id is None:
        add("node_missing", "node_id")
    elif node is None:
        add("node_unavailable", "node_id")
    elif not node["online"]:
        add("node_offline", "node_id")

    pool = options.get("pool") if node is not None else None
    if pool is not None:
        if (
            preset.proxy_mode == "auto"
            and preset.proxy_group
            and preset.proxy_group not in {group["name"] for group in pool["groups"]}
        ):
            add("proxy_group_unavailable", "proxy_group")
        if preset.proxy_mode == "pool":
            if not preset.proxy_id:
                add("proxy_missing", "proxy_id")
            elif preset.proxy_id not in {proxy["id"] for proxy in pool["proxies"]}:
                add("proxy_unavailable", "proxy_id")

    orgs = {org["id"]: org for org in options["orgs"]}
    if orgs:
        org = orgs.get(preset.org_id or "")
        if preset.org_id is None:
            add("org_missing", "org_id")
        elif org is None:
            add("org_unavailable", "org_id")
        elif org["domains"]:
            if not preset.domain:
                add("domain_missing", "domain")
            elif preset.domain.lower() not in {domain.lower() for domain in org["domains"]}:
                add("domain_unavailable", "domain")
    elif preset.org_manual:
        if not preset.org_uuid:
            add("org_uuid_missing", "org_uuid")
        elif not _UUID.fullmatch(preset.org_uuid):
            add("org_uuid_invalid", "org_uuid")
    return found


def expected_domain(preset: Preset, options: dict[str, Any]) -> str | None:
    """The domain every account's address must be under, when the organization names one."""
    for org in options["orgs"]:
        if org["id"] == preset.org_id and org["domains"] and preset.domain:
            return preset.domain.lower()
    return None
