"""API models for the auto-onboarding parameters and the authorization of a row."""

from typing import Literal

from pydantic import BaseModel, Field

from camoufox_pm.core.auto_onboarding.preset import Issue, Preset


class ConnectionData(BaseModel):
    """The saved onboarder token as it may be shown. The token itself never leaves the server."""

    saved: bool = Field(..., description="Whether a token is saved on this installation")
    preview: str | None = Field(default=None, description="First twelve characters and last four")
    label: str | None = Field(default=None, description="What the platform calls this token")
    read_at: str | None = Field(default=None, description="When the platform last accepted it")
    refused: bool = Field(default=False, description="The platform refused it when last asked")
    refused_at: str | None = None
    encrypted: bool | None = Field(
        default=None, description="False when it is kept unencrypted for want of CPM_SECRET_KEY"
    )
    platform: str = Field(..., description="The only address the token is sent to")


class TokenRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=512)


class TokenSavedData(BaseModel):
    connection: ConnectionData
    replaced: bool = Field(
        ..., description="Another token was saved before; choices made with it were cleared"
    )


class NodeOption(BaseModel):
    id: str
    name: str
    online: bool
    used: int
    capacity: int


class OrgOption(BaseModel):
    id: str
    name: str
    domains: list[str]


class ProxyGroupOption(BaseModel):
    name: str
    assignable: int


class ProxyOption(BaseModel):
    id: str
    exit_ip: str | None
    group: str | None
    slots: int = Field(..., description="Accounts this exit can still take")


class PoolData(BaseModel):
    node_id: str
    groups: list[ProxyGroupOption]
    proxies: list[ProxyOption]


class OptionsData(BaseModel):
    label: str
    nodes: list[NodeOption]
    orgs: list[OrgOption]
    pool: PoolData | None


class PresetData(BaseModel):
    preset: Preset
    version: int = Field(..., description="How many times the choices have been saved")


class PresetsData(PresetData):
    connection: ConnectionData
    options: OptionsData | None = Field(
        default=None,
        description="What the token may use; absent without a usable token or platform",
    )
    options_error: str | None = Field(
        default=None, description="Why the options are absent although a token is saved"
    )


class ScopeGroup(BaseModel):
    id: str
    name: str
    pending: int = Field(..., description="Profiles with an email address not onboarded yet")
    onboarded: int


class AuthorizationData(BaseModel):
    """An authorization asked of the platform for a row. Never its link, code or state."""

    status: str = Field(
        ...,
        description=(
            "'pending' (the link waits for its code), 'working' (the platform has the code), "
            "'done', 'failed', or 'lost' (it can no longer be asked about)"
        ),
    )
    outcome: str | None = Field(
        default=None,
        description=(
            "Of a finished one: 'onboarded', 'reused' (an account already there was "
            "authorized again), 'mismatch' (another account than the row's), 'failed'"
        ),
    )
    error_code: str | None = Field(default=None, description="The platform's code for a failure")
    account_email: str | None = Field(default=None, description="The account that was onboarded")
    node: str | None = Field(default=None, description="The node it was made with")
    proxy: str | None = Field(
        default=None,
        description="Where its proxy comes out, or 'auto' / 'auto:<group>' when the platform picks",
    )
    expires_at: str | None = Field(default=None, description="When the platform lets it lapse")
    cooldown_until: str | None = None
    created_at: str


class ScopeRow(BaseModel):
    profile_id: str
    name: str
    email: str
    exit_ip: str | None
    exit_checked: bool
    exit_reachable: bool
    onboarded: bool
    can_launch: bool = Field(
        default=True, description="Whether the caller may run this profile's browser"
    )
    authorization: AuthorizationData | None = Field(
        default=None, description="The last authorization asked for this row's address"
    )


class RowJobData(BaseModel):
    """How a row's authorization stands, as the platform has just said."""

    profile_id: str
    onboarded: bool
    authorization: AuthorizationData | None
    authorize_url: str | None = Field(
        default=None, description="The authorize link, only while it waits for its code"
    )
    opened: bool | None = Field(
        default=None,
        description="Only when a link was just made: whether it was opened in the row's browser",
    )
    opened_in: Literal["tab", "window"] | None = Field(
        default=None,
        description=(
            "Where it was opened: 'tab', a new tab of a window the browser already had, or "
            "'window', a new window of the same browser when no tab could be had"
        ),
    )


class CodeRequest(BaseModel):
    code: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="What the authorize page showed after consent, pasted whole",
    )


class ScopesData(BaseModel):
    groups: list[ScopeGroup]
    rows: list[ScopeRow] | None = Field(default=None, description="The asked group's profiles")


class CheckedRow(ScopeRow):
    status: str = Field(..., description="'ready', or what stands in this row's way")
    proxy_id: str | None = None


class PreflightData(BaseModel):
    ok: bool
    version: int
    issues: list[Issue]
    rows: list[CheckedRow]
    ready: int
    blocked: int
