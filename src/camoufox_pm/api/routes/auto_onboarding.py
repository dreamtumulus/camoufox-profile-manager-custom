"""Auto onboarding: the token and what is chosen, and the authorization of a row.

The onboarder token is accepted, kept and used on the server; no response
carries it. A row's account is authorized by hand: the platform makes a link,
which is opened in a new tab of the row's running browser, and the code its
owner gets by consenting is pasted back and handed on. No route here signs in,
consents, or reads a page, and none starts or stops a browser: that is the
profiles' own launch and close.
"""

from typing import Literal

from fastapi import APIRouter, Depends, Query

from camoufox_pm.api.access import (
    record_audit,
    require_onboarding,
    require_profile_access,
    visible_profile_ids,
)
from camoufox_pm.api.dependencies import get_profile_manager, get_storage_manager
from camoufox_pm.api.models.auto_onboarding import (
    AuthorizationData,
    CheckedRow,
    CodeRequest,
    ConnectionData,
    OptionsData,
    PreflightData,
    PresetData,
    PresetsData,
    RowJobData,
    ScopeGroup,
    ScopeRow,
    ScopesData,
    TokenRequest,
    TokenSavedData,
)
from camoufox_pm.core.auto_onboarding import authorizations, platform, scopes
from camoufox_pm.core.auto_onboarding.authorizations import refusal
from camoufox_pm.core.auto_onboarding.preset import Issue, Preset, issues
from camoufox_pm.core.auto_onboarding.store import Authorization, OnboardingStore
from camoufox_pm.core.onboarding import OnboardingError

# Administrators and operators; a viewer has no business with the token.
router = APIRouter(dependencies=[Depends(require_onboarding)])


def _store() -> OnboardingStore:
    return OnboardingStore(get_storage_manager())


def _connection(store: OnboardingStore) -> ConnectionData:
    saved = store.saved()
    if saved is None:
        return ConnectionData(saved=False, platform=platform.PLATFORM)
    return ConnectionData(
        saved=True,
        preview=saved.preview,
        label=saved.label,
        read_at=saved.read_at,
        refused=saved.refused_at is not None,
        refused_at=saved.refused_at,
        encrypted=saved.encrypted,
        platform=platform.PLATFORM,
    )


@router.get(
    "/auto-onboarding/connection",
    response_model=ConnectionData,
    operation_id="get_auto_onboarding_connection",
    summary="Report the saved onboarder token.",
    description="Whether an onboarder token is saved, and its masked preview. Never the token.",
)
async def get_connection():
    return _connection(_store())


@router.put(
    "/auto-onboarding/connection",
    response_model=TokenSavedData,
    operation_id="save_auto_onboarding_token",
    summary="Save or replace the onboarder token.",
    description=(
        "The token is first shown to the platform. Only one the platform accepts is saved, "
        "so a refused or mistyped token leaves the saved one as it was (409 or 422). "
        "Replacing a token clears the node, proxy and organization chosen with the old one. "
        "An authorization belongs to the token that made it, so the token is not replaced "
        "while one is open (409 `authorizations_open`), unless the platform no longer "
        "accepts the old token."
    ),
)
async def save_token(request: TokenRequest):
    token = request.token.strip()
    if not token.startswith(platform.TOKEN_PREFIX) or any(char.isspace() for char in token):
        raise OnboardingError(
            f"An onboarder token starts with {platform.TOKEN_PREFIX}",
            422,
            "onboard_token_malformed",
        )
    identity = await platform.read_identity(token)
    store = _store()
    previous = store.token()
    replaced = previous is not None and previous != token
    if replaced and previous is not None:
        await _leave_no_authorization_behind(store, previous)
    store.save_token(token, identity["label"])
    if replaced:
        store.save_preset(store.preset()[0].for_another_token())
    await record_audit("replace_onboard_token" if replaced else "save_onboard_token", None)
    return TokenSavedData(connection=_connection(store), replaced=replaced)


async def _leave_no_authorization_behind(store: OnboardingStore, previous: str) -> None:
    """Refuse to replace a token that still has an authorization open.

    Another token can neither ask about such an authorization nor cancel it.
    They are asked about once more with the old token first, so one that
    finished or lapsed while nobody looked does not stand in the way. A token
    the platform no longer accepts cannot reach its authorizations either:
    those are noted as lost and the replacement goes ahead.
    """
    saved = store.saved()
    reachable = saved is not None and saved.refused_at is None
    still_open = []
    for authorization in store.open_authorizations():
        if reachable:
            try:
                authorization, _ = await authorizations.sync(store, previous, authorization)
            except platform.TokenRefused:
                reachable = False
            except OnboardingError:
                pass  # Unreachable just now: it counts as open.
        if not reachable:
            store.update(authorization, status="lost")
        elif authorization.open:
            still_open.append(authorization.email)
    if still_open:
        raise refusal(
            "authorizations_open", "Authorizations are still open: " + ", ".join(still_open)
        )


@router.get(
    "/auto-onboarding/presets",
    response_model=PresetsData,
    operation_id="get_auto_onboarding_presets",
    summary="Read what the token may use, and what is chosen.",
    description=(
        "Asks the platform anew each time, so this is also the refresh. The proxy pool is "
        "that of `node_id`, or of the chosen node. A platform that cannot be reached, or "
        "that refuses the token, leaves `options` empty and says why; the saved choices "
        "are returned either way."
    ),
)
async def get_presets(
    node_id: str | None = Query(None, max_length=120, description="Read this node's proxy pool"),
):
    store = _store()
    preset, version = store.preset()
    token = store.token()
    options, problem = None, None
    if token is not None:
        try:
            options = await platform.read_options(token, node_id or preset.node_id)
            store.accepted(options["label"])
        except platform.TokenRefused:
            store.refused()
        except OnboardingError as exc:
            problem = exc.code
    return PresetsData(
        connection=_connection(store),
        options=None if options is None else OptionsData.model_validate(options),
        options_error=problem,
        preset=preset,
        version=version,
    )


@router.put(
    "/auto-onboarding/presets",
    response_model=PresetData,
    operation_id="save_auto_onboarding_preset",
    summary="Save what is chosen.",
    description=(
        "Saved as chosen, finished or not; each field is only held to its shape. Whether "
        "the choices would do is what the preflight says."
    ),
)
async def save_preset(preset: Preset):
    version = _store().save_preset(preset)
    return PresetData(preset=preset, version=version)


@router.get(
    "/auto-onboarding/scopes",
    response_model=ScopesData,
    operation_id="list_auto_onboarding_scopes",
    summary="List the groups that have profiles to onboard.",
    description=(
        "Groups with at least one profile that has an email address and is not onboarded "
        "yet, with both counts. With `group`, also that group's profiles that have an "
        "address, newest first and not paginated. Only profiles the caller may see count."
    ),
)
async def list_scopes(group: str | None = Query(None, max_length=120)):
    manager = get_profile_manager()
    allowed = await visible_profile_ids()
    store = _store()
    await _catch_up(store)
    onboarded = store.onboarded()
    groups = await scopes.list_groups(manager, allowed, onboarded)
    rows = await scopes.list_rows(manager, group, allowed, onboarded) if group else None
    if rows:
        latest = store.latest_of([row["profile_id"] for row in rows])
        launchable = await visible_profile_ids("launch")
        for row in rows:
            row["can_launch"] = launchable is None or row["profile_id"] in launchable
            found = latest.get(row["profile_id"])
            # An authorization asked while the profile had another address is
            # another account's.
            if found is not None and found.email == row["email"].strip().lower():
                row["authorization"] = _view(found)
    return ScopesData(
        groups=[ScopeGroup(**item) for item in groups],
        rows=None if rows is None else [ScopeRow(**row) for row in rows],
    )


def _view(authorization: Authorization | None) -> AuthorizationData | None:
    if authorization is None:
        return None
    return AuthorizationData(
        status=authorization.status,
        outcome=authorization.outcome,
        error_code=authorization.error_code,
        account_email=authorization.account_email,
        node=authorization.node,
        proxy=authorization.proxy,
        expires_at=authorization.expires_at,
        cooldown_until=authorization.cooldown_until,
        created_at=authorization.created_at,
    )


async def _catch_up(store: OnboardingStore) -> None:
    """Ask the platform about every authorization still open, so the list is current.

    Nothing runs in the background here: an authorization that finished or
    lapsed while the page was closed is learned of when the list is next read.
    Best effort; what cannot be asked about now stays as it was.
    """
    pending = store.open_authorizations()
    saved = store.saved()
    token = store.token()
    if not pending or token is None or saved is None or saved.refused_at is not None:
        return
    for authorization in pending:
        try:
            await authorizations.sync(store, token, authorization)
        except platform.TokenRefused:
            store.refused()
            return
        except OnboardingError:
            continue


async def _row(profile_id: str) -> tuple[OnboardingStore, dict]:
    """A row the caller may act on, as for launching its browser."""
    await require_profile_access(profile_id, "launch")
    store = _store()
    row = await scopes.row_of(get_profile_manager(), profile_id, store.onboarded())
    if row is None:
        raise refusal(
            "row_unavailable", "Only a profile in a group with an email address can be onboarded"
        )
    return store, row


def _token(store: OnboardingStore) -> str:
    token = store.token()
    if token is None:
        raise OnboardingError("Save an onboarder token first", 409, "onboard_token_missing")
    return token


def _job(
    store: OnboardingStore,
    row: dict,
    authorization: Authorization | None,
    link: str | None = None,
    opened: bool | None = None,
    opened_in: Literal["tab", "window"] | None = None,
) -> RowJobData:
    return RowJobData(
        profile_id=row["profile_id"],
        onboarded=store.is_onboarded(row["profile_id"], row["email"]),
        authorization=_view(authorization),
        authorize_url=link
        if authorization is not None and authorization.status == "pending"
        else None,
        opened=opened,
        opened_in=opened_in,
    )


@router.post(
    "/auto-onboarding/rows/{profile_id}/job",
    response_model=RowJobData,
    operation_id="create_auto_onboarding_authorization",
    summary="Make an authorize link for a row and open it in the row's browser.",
    description=(
        "Asks the platform for an authorize link with the saved choices, and opens it in a "
        "new tab of the window the browser running for this profile already shows (a new "
        "window of that browser when no tab can be had; `opened_in` says which). Nothing is "
        "filled in or pressed on the page. A row that already has an open authorization "
        "gets that one back, and "
        "nothing is opened again. Refused with 409 before any job is made when there is no "
        "usable token, the choices are unfinished, the row is onboarded or cannot be "
        "onboarded with them, or its browser is not running; the error code says which. A "
        "refusal by the platform comes back as 409 with its code behind `platform_`."
    ),
)
async def create_authorization(profile_id: str):
    async with authorizations.lock_for(profile_id):
        store, row = await _row(profile_id)
        token = _token(store)
        manager = get_profile_manager()
        try:
            current = store.latest(profile_id, row["email"])
            if current is not None and current.open:
                # Whatever it has come to meanwhile is the answer; a second job
                # is never made beside one that is open.
                current, link = await authorizations.sync(store, token, current)
                return _job(store, row, current, link)
            if row["onboarded"]:
                raise refusal("onboarded", "This profile's account is already onboarded")
            if not manager.browser_sessions.is_running(profile_id):
                raise refusal("browser_not_running", "Run this profile's browser first")
            authorization, link = await authorizations.create(store, token, row)
        except platform.TokenRefused:
            store.refused()
            raise
        # In a tab of the window the row's browser already shows, where one can be had.
        where = await manager.browser_sessions.open_page(profile_id, link) if link else None
        await record_audit("create_onboard_authorization", profile_id)
        return _job(store, row, authorization, link, where is not None, where)


@router.get(
    "/auto-onboarding/rows/{profile_id}/job",
    response_model=RowJobData,
    operation_id="get_auto_onboarding_authorization",
    summary="Ask the platform how a row's authorization stands.",
    description=(
        "The row's last authorization, asked of the platform anew while it is open, with "
        "its link while that waits for the code. A job the platform reports as done for "
        "the row's own account makes the row onboarded; nothing short of that does."
    ),
)
async def get_authorization(profile_id: str):
    store, row = await _row(profile_id)
    current = store.latest(profile_id, row["email"])
    link = None
    if current is not None and current.open:
        try:
            current, link = await authorizations.sync(store, _token(store), current)
        except platform.TokenRefused:
            store.refused()
            raise
    return _job(store, row, current, link)


def _open(store: OnboardingStore, row: dict, waiting_only: bool) -> Authorization:
    current = store.latest(row["profile_id"], row["email"])
    wanted = ("pending",) if waiting_only else ("pending", "working")
    if current is None or current.status not in wanted:
        raise refusal("authorization_not_waiting", "This row has no authorization waiting")
    return current


@router.post(
    "/auto-onboarding/rows/{profile_id}/job/code",
    response_model=RowJobData,
    operation_id="submit_auto_onboarding_code",
    summary="Hand the pasted authorization code to the platform.",
    description=(
        "The code is handed on as it was pasted and is not kept. A code that is incomplete "
        "or belongs to another row is refused by the platform (422 `callback_invalid`) and "
        "the authorization goes on waiting. An accepted code is not success: the platform "
        "then works on it, and only its report of done is."
    ),
)
async def submit_code(profile_id: str, request: CodeRequest):
    async with authorizations.lock_for(profile_id):
        store, row = await _row(profile_id)
        current = _open(store, row, waiting_only=True)
        try:
            current = await authorizations.hand_over(
                store, _token(store), current, request.code.strip()
            )
        except platform.TokenRefused:
            store.refused()
            raise
        await record_audit("submit_onboard_code", profile_id)
        return _job(store, row, current)


@router.post(
    "/auto-onboarding/rows/{profile_id}/job/cancel",
    response_model=RowJobData,
    operation_id="cancel_auto_onboarding_authorization",
    summary="Cancel a row's open authorization.",
    description=(
        "The platform cancels the job and frees what it reserved. One that finished "
        "meanwhile stays finished, and is reported as such."
    ),
)
async def cancel_authorization(profile_id: str):
    async with authorizations.lock_for(profile_id):
        store, row = await _row(profile_id)
        current = _open(store, row, waiting_only=False)
        try:
            current = await authorizations.cancel(store, _token(store), current)
        except platform.TokenRefused:
            store.refused()
            raise
        await record_audit("cancel_onboard_authorization", profile_id)
        return _job(store, row, current)


@router.post(
    "/auto-onboarding/preflight",
    response_model=PreflightData,
    operation_id="check_auto_onboarding_preset",
    summary="Check the saved choices.",
    description=(
        "Reads the platform anew and says what is missing from the saved choices or no "
        "longer on offer, and of every profile in the chosen group whether it would be "
        "onboarded. Opens no browser and creates nothing on the platform. 409 without a "
        "token, or with one the platform refuses."
    ),
)
async def preflight():
    store = _store()
    token = store.token()
    if token is None:
        raise OnboardingError("Save an onboarder token first", 409, "onboard_token_missing")
    preset, version = store.preset()
    try:
        options = await platform.read_options(token, preset.node_id)
    except platform.TokenRefused:
        store.refused()
        raise
    store.accepted(options["label"])

    found = issues(preset, options)
    manager = get_profile_manager()
    allowed = await visible_profile_ids()
    onboarded = store.onboarded()
    rows = []
    if preset.scope_group is None:
        found.append(Issue(code="scope_missing", field="scope_group"))
    elif preset.scope_group not in {
        group["id"] for group in await scopes.list_groups(manager, allowed, onboarded)
    }:
        found.append(Issue(code="scope_unavailable", field="scope_group"))
    else:
        rows = scopes.check_rows(
            await scopes.list_rows(manager, preset.scope_group, allowed, onboarded),
            preset,
            options,
        )
    ready = sum(row["status"] == "ready" for row in rows)
    if rows and not ready:
        found.append(Issue(code="no_ready_rows", field="scope_group"))
    return PreflightData(
        ok=not found,
        version=version,
        issues=found,
        rows=[CheckedRow(**row) for row in rows],
        ready=ready,
        blocked=len(rows) - ready,
    )
