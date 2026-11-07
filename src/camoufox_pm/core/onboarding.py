from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from camoufox_pm.config import get_settings

from .crypto import decrypt, encrypt

ONBOARDING_ORIGIN = "https://1dayapi.com"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
)


class OnboardingError(RuntimeError):
    def __init__(self, message: str, status_code: int = 409, code: str = "onboarding_error"):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class Selection(BaseModel):
    node_id: str
    node_name: str
    org_id: str
    org_name: str
    proxy_group: str | None = None
    proxy_id: str
    exit_ip: str


class AccountInput(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    org_id: str
    org_name: str


class SessionInput(BaseModel):
    account_id: str
    selection: Selection


class RemoteConfig(BaseModel):
    server_url: str
    server_token: str = Field(min_length=32, max_length=512)
    credential_token: str | None = Field(default=None, max_length=512)


def _config_path() -> Path:
    return Path(get_settings().db_path).expanduser().resolve().parent / "onboarding-config.json"


def remote_status() -> dict[str, Any]:
    try:
        config = _read_config()
        return {"configured": True, "server_url": config["server_url"]}
    except OnboardingError as exc:
        return {"configured": False, "server_url": None, "message": str(exc)}


def _read_config() -> dict[str, str]:
    url = os.environ.get("CPM_ONBOARDING_SERVER_URL")
    token = os.environ.get("CPM_ONBOARDING_SERVER_TOKEN")
    if url and token:
        return {"server_url": url.rstrip("/"), "server_token": token}
    path = _config_path()
    if not path.exists():
        raise OnboardingError("尚未配置共享服务器", 503, "remote_not_configured")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        server_token = decrypt(data["server_token"])
        if data["server_url"] and len(server_token) >= 32:
            return {"server_url": data["server_url"].rstrip("/"), "server_token": server_token}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise OnboardingError("共享服务器配置无法读取", 500, "remote_config_invalid") from exc
    raise OnboardingError("共享服务器配置不完整", 500, "remote_config_invalid")


def save_remote_config(request: RemoteConfig) -> RemoteClient:
    parsed = urlparse(request.server_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise OnboardingError("共享服务器地址格式不正确", 422, "remote_url_invalid")
    if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise OnboardingError("非本机共享服务器必须使用 HTTPS", 422, "remote_https_required")
    client = RemoteClient(request.server_url, request.server_token)
    return client


async def write_remote_config(request: RemoteConfig) -> None:
    client = save_remote_config(request)
    path = _config_path()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {"server_url": request.server_url.rstrip("/"), "server_token": encrypt(request.server_token)},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)
    if request.credential_token:
        await client.put_credential(request.credential_token)


def _response_error(response: httpx.Response, service: str) -> OnboardingError:
    message = f"{service}返回 HTTP {response.status_code}"
    try:
        body = response.json()
        error = body.get("error", body)
        if isinstance(error, dict):
            message = error.get("message") or error.get("code") or error.get("detail") or message
        elif isinstance(error, str):
            message = error
        elif body.get("detail"):
            message = str(body["detail"])
    except (ValueError, TypeError):
        if response.text.strip():
            message = f"{message}: {response.text[:200]}"
    return OnboardingError(str(message), 502, "upstream_error")


class RemoteClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    @classmethod
    def configured(cls) -> RemoteClient:
        config = _read_config()
        return cls(config["server_url"], config["server_token"])

    async def request(self, method: str, path: str, body: Any = None) -> Any:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(35, connect=10)) as client:
                response = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    json=body,
                    headers={"Authorization": f"Bearer {self.token}"},
                )
        except httpx.HTTPError as exc:
            raise OnboardingError(f"无法连接共享服务器：{type(exc).__name__}", 503, "remote_unavailable") from exc
        if not response.is_success:
            raise _response_error(response, "共享服务器")
        return response.json()

    async def state(self) -> dict[str, Any]:
        return await self.request("GET", "/v1/state")

    async def preferences(self, values: dict[str, Any]) -> dict[str, Any]:
        return await self.request("PUT", "/v1/preferences", {"values": values})

    async def put_credential(self, token: str) -> None:
        await self.request("PUT", "/v1/credential", {"token": token})

    async def credential(self) -> str:
        return (await self.request("GET", "/v1/credential"))["token"]

    async def save_account(self, request: AccountInput) -> dict[str, Any]:
        return await self.request("POST", "/v1/accounts", request.model_dump())

    async def delete_account(self, account_id: str) -> None:
        await self.request("DELETE", f"/v1/accounts/{account_id}")

    async def create_session(self, account_id: str, email: str, selection: Selection) -> dict[str, Any]:
        return await self.request(
            "POST",
            "/v1/sessions",
            {
                "account_id": account_id,
                "email": email,
                "selection": selection.model_dump(),
            },
        )

    async def session(self, session_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/v1/sessions/{session_id}")

    async def update_session(self, session_id: str, **values: Any) -> dict[str, Any]:
        return await self.request("PATCH", f"/v1/sessions/{session_id}", values)

    async def session_secret(self, session_id: str) -> str:
        return (await self.request("GET", f"/v1/sessions/{session_id}/secret"))["sk"]


class OneDayClient:
    def __init__(self, token: str):
        self.token = token

    async def request(self, method: str, path: str, body: Any = None) -> Any:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Origin": ONBOARDING_ORIGIN,
            "Referer": f"{ONBOARDING_ORIGIN}/onboard",
            "User-Agent": BROWSER_USER_AGENT,
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(40, connect=10)) as client:
                response = await client.request(
                    method,
                    f"{ONBOARDING_ORIGIN}{path}",
                    json=body,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise OnboardingError(f"无法连接 1dayApi：{type(exc).__name__}", 503, "oneday_unavailable") from exc
        if not response.is_success:
            raise _response_error(response, "1dayApi")
        result = response.json()
        if not isinstance(result, dict) or "data" not in result:
            raise OnboardingError("1dayApi 返回格式不正确", 502, "oneday_response_invalid")
        return result["data"]

    async def options(self) -> dict[str, Any]:
        return await self.request("GET", "/api/onboard/me")

    async def groups(self, node_id: str) -> list[dict[str, Any]]:
        return await self.request("GET", f"/api/onboard/node-groups?nodeId={node_id}")

    async def proxies(self, node_id: str) -> list[dict[str, Any]]:
        return await self.request("GET", f"/api/onboard/proxies?nodeId={node_id}")

    async def submit(self, sk: str, selection: Selection, email: str) -> dict[str, Any]:
        return await self.request(
            "POST",
            "/api/onboard/jobs",
            {
                "method": "session_key",
                "sessionKey": sk,
                "orgId": selection.org_id,
                "nodeId": selection.node_id,
                "proxyGroup": selection.proxy_group,
                "proxyId": selection.proxy_id,
                "name": email,
            },
        )

    async def job(self, job_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/api/onboard/jobs/{job_id}")


def proxy_host(server: str) -> str:
    value = server.strip()
    if "://" not in value:
        value = f"//{value}"
    return (urlparse(value).hostname or "").lower().strip("[]")


def matching_proxy(proxies: list[dict[str, Any]], exit_ip: str) -> dict[str, Any]:
    expected = exit_ip.lower()
    host_matches = [proxy for proxy in proxies if proxy_host(proxy.get("server", "")) == expected]
    exit_matches = [
        proxy
        for proxy in proxies
        if (proxy.get("last_check") or {}).get("ip", "").lower() == expected
    ]
    candidates = {proxy["id"]: proxy for proxy in [*host_matches, *exit_matches]}
    if not candidates:
        raise OnboardingError(
            f"本地代理库没有出口 IP 为 {exit_ip} 的代理，请先手动添加并检测",
            409,
            "proxy_mapping_missing",
        )
    if len(candidates) > 1:
        raise OnboardingError(
            f"出口 IP {exit_ip} 对应多个本地代理，请只保留一个匹配项",
            409,
            "proxy_mapping_ambiguous",
        )
    proxy = next(iter(candidates.values()))
    check = proxy.get("last_check") or {}
    if check and not check.get("reachable", False):
        raise OnboardingError(
            f"本地代理 {proxy['name']} 上次检测失败：{check.get('error') or '未知错误'}",
            409,
            "proxy_unhealthy",
        )
    if check.get("ip") and check["ip"].lower() != expected:
        raise OnboardingError(
            f"本地代理 {proxy['name']} 检测出口为 {check['ip']}，与所选 {expected} 不一致",
            409,
            "proxy_exit_mismatch",
        )
    return proxy


async def oneday_client(remote: RemoteClient) -> OneDayClient:
    return OneDayClient(await remote.credential())
