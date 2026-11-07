from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from camoufox_pm.api.access import require_admin
from camoufox_pm.api.dependencies import get_profile_manager
from camoufox_pm.core.onboarding import (
    AccountInput,
    OnboardingError,
    RemoteClient,
    RemoteConfig,
    Selection,
    SessionInput,
    matching_proxy,
    oneday_client,
    remote_status,
    write_remote_config,
)
from camoufox_pm.core.proxy_inventory import ProxyInventory

router = APIRouter()


def _public_result(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[已隐藏]" if key.lower() in {"sk", "sessionkey", "token", "authorization"} else _public_result(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_public_result(item) for item in value]
    return value


@router.get("/onboarding/config")
async def onboarding_config():
    await require_admin()
    return remote_status()


@router.put("/onboarding/config")
async def save_onboarding_config(request: RemoteConfig):
    await require_admin()
    await write_remote_config(request)
    return remote_status()


@router.get("/onboarding/bootstrap")
async def onboarding_bootstrap():
    await require_admin()
    remote = RemoteClient.configured()
    state = await remote.state()
    client = await oneday_client(remote)
    return {"shared": state, "oneDay": await client.options()}


@router.get("/onboarding/nodes/{node_id}/groups")
async def onboarding_groups(node_id: str):
    await require_admin()
    remote = RemoteClient.configured()
    client = await oneday_client(remote)
    return await client.groups(node_id)


@router.get("/onboarding/nodes/{node_id}/proxies")
async def onboarding_proxies(node_id: str):
    await require_admin()
    remote = RemoteClient.configured()
    client = await oneday_client(remote)
    return await client.proxies(node_id)


@router.put("/onboarding/preferences")
async def onboarding_preferences(selection: Selection):
    await require_admin()
    return await RemoteClient.configured().preferences(selection.model_dump())


@router.post("/onboarding/accounts")
async def onboarding_account(request: AccountInput):
    await require_admin()
    return await RemoteClient.configured().save_account(request)


@router.delete("/onboarding/accounts/{account_id}")
async def onboarding_delete_account(account_id: str):
    await require_admin()
    await RemoteClient.configured().delete_account(account_id)
    return {"deleted": True}


@router.post("/onboarding/sessions")
async def onboarding_create_session(request: SessionInput):
    await require_admin()
    remote = RemoteClient.configured()
    shared = await remote.state()
    account = next((item for item in shared["accounts"] if item["id"] == request.account_id), None)
    if account is None:
        raise OnboardingError("所选邮箱不存在，请刷新后重新选择", 409, "account_missing")
    if account["org_id"] != request.selection.org_id:
        raise OnboardingError("邮箱所属组织与当前选择不一致", 409, "account_org_mismatch")

    manager = get_profile_manager()
    proxies = await ProxyInventory(manager).list()
    local_proxy = matching_proxy(proxies, request.selection.exit_ip)
    session = await remote.create_session(
        account["id"], account["email"], request.selection
    )
    session_id = session["id"]
    profile_id = None
    try:
        secret_proxy = ProxyInventory(manager)._secret_config(local_proxy["id"])
        profile = await manager.create_profile(
            name=f"Claude Team · {account['email']}",
            browser_settings={"os": "windows"},
            proxy_config=secret_proxy.model_dump(),
            notes=f"Claude Team onboarding account: {account['id']}; session: {session_id}",
            tags=["claude-team"],
        )
        profile_id = profile.id
        session = await remote.update_session(session_id, profile_id=profile_id, status="launching")
        launched = await manager.launch_browser(profile_id, headless=False, geoip=True, humanize=True)
        browser = manager.browser_sessions.active_sessions[profile_id]
        pages = list(browser.context.pages)
        page = pages[0] if pages else await browser.context.new_page()
        await page.goto("https://claude.ai/login", wait_until="domcontentloaded", timeout=60000)
        session = await remote.update_session(session_id, status="browser_open", error=None)
        return {"session": session, "profile_id": profile_id, "launch_status": launched["status"]}
    except Exception as exc:
        message = str(exc)
        if profile_id and manager.browser_sessions.is_running(profile_id):
            try:
                await manager.close_browser(profile_id)
            except Exception as close_exc:
                message = f"{message}；浏览器关闭也需要手动检查：{close_exc}"
        session = await remote.update_session(session_id, status="error", error=message)
        if isinstance(exc, OnboardingError):
            raise
        raise OnboardingError(f"创建或启动浏览器失败：{message}", 500, "browser_launch_failed") from exc


@router.post("/onboarding/sessions/{session_id}/extract")
async def onboarding_extract_sk(session_id: str, optional: bool = False):
    await require_admin()
    remote = RemoteClient.configured()
    session = await remote.session(session_id)
    profile_id = session.get("profile_id")
    manager = get_profile_manager()
    browser = manager.browser_sessions.active_sessions.get(profile_id) if profile_id else None
    if browser is None:
        raise OnboardingError("浏览器窗口未运行，无法提取 SK", 409, "browser_not_running")
    cookies = await browser.context.cookies(
        ["https://claude.ai", "https://www.claude.ai"]
    )
    keys = [
        cookie["value"]
        for cookie in cookies
        if cookie.get("name") == "sessionKey" and "claude.ai" in cookie.get("domain", "")
    ]
    if not keys:
        if optional:
            return {"found": False, "session": session}
        session = await remote.update_session(
            session_id,
            status="error",
            error="未提取到 SK，请确认已在当前窗口完成 Claude 登录",
        )
        raise OnboardingError(
            "未提取到 SK，请确认已在当前窗口完成 Claude 登录", 409, "sk_not_found"
        )
    if len(keys) > 1:
        raise OnboardingError("检测到多个 Claude sessionKey，请先清理当前配置的登录状态", 409, "sk_ambiguous")
    sk = keys[0]
    if not sk.startswith("sk-ant-"):
        raise OnboardingError("提取到的 sessionKey 格式不正确", 409, "sk_invalid")
    session = await remote.update_session(
        session_id, sk=sk, status="extracted", error=None, result=None
    )
    return {"message": "提取完SK", "session": session}


@router.post("/onboarding/sessions/{session_id}/submit")
async def onboarding_submit_sk(session_id: str):
    await require_admin()
    remote = RemoteClient.configured()
    session = await remote.session(session_id)
    if not session.get("sk_masked"):
        raise OnboardingError("尚未提取 SK，不能提交", 409, "sk_not_extracted")
    if session.get("job_id"):
        raise OnboardingError("该 SK 已提交，请使用“刷新返回结果”查看", 409, "sk_already_submitted")
    await remote.update_session(session_id, status="submitting", error=None)
    try:
        sk = await remote.session_secret(session_id)
        client = await oneday_client(remote)
        selection = Selection(**session["selection"])
        result = await client.submit(sk, selection, session["email"])
        session = await remote.update_session(
            session_id,
            status="submitted",
            job_id=result.get("jobId"),
            result=_public_result(result),
            error=None,
        )
        return {"message": "SK 已提交，返回结果如下", "session": session, "result": _public_result(result)}
    except Exception as exc:
        message = str(exc)
        session = await remote.update_session(session_id, status="error", error=message)
        if isinstance(exc, OnboardingError):
            raise
        raise OnboardingError(f"提交 SK 失败：{message}", 502, "sk_submit_failed") from exc


@router.get("/onboarding/sessions/{session_id}/result")
async def onboarding_result(session_id: str):
    await require_admin()
    remote = RemoteClient.configured()
    session = await remote.session(session_id)
    job_id = session.get("job_id")
    if not job_id:
        raise OnboardingError("尚未取得 1dayApi 作业编号", 409, "job_missing")
    try:
        client = await oneday_client(remote)
        result = await client.job(job_id)
        status = "completed" if result.get("status") == "done" else "failed" if result.get("status") == "failed" else "submitted"
        session = await remote.update_session(
            session_id,
            status=status,
            result=_public_result(result),
            error=result.get("error") if status == "failed" else None,
        )
        return {"session": session, "result": _public_result(result)}
    except Exception as exc:
        message = str(exc)
        await remote.update_session(session_id, status="error", error=message)
        raise


@router.post("/onboarding/sessions/{session_id}/close")
async def onboarding_close_browser(session_id: str):
    await require_admin()
    remote = RemoteClient.configured()
    session = await remote.session(session_id)
    profile_id = session.get("profile_id")
    if not profile_id:
        raise OnboardingError("该记录没有本地浏览器窗口", 409, "browser_missing")
    result = await get_profile_manager().close_browser(profile_id)
    session = await remote.update_session(session_id, status="closed")
    return {"session": session, "browser": result}
