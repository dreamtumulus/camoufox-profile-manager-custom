from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal
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
    platform: Literal["1dayapi", "api-key.xyz"] = "1dayapi"
    node_id: str
    node_name: str
    org_id: str
    org_name: str
    proxy_group: str | None = None
    proxy_id: str
    exit_ip: str
    seats: int = Field(default=500, ge=1)
    rpm: int = Field(default=500, ge=1)
    pool_tags: list[str] = Field(default_factory=list)
    required_traits: list[str] = Field(default_factory=list)
    forbidden_traits: list[str] = Field(default_factory=list)


class AccountInput(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    org_id: str
    org_name: str


class AccountBatchInput(BaseModel):
    emails: list[str] = Field(min_length=1, max_length=500)
    org_id: str
    org_name: str


class SessionInput(BaseModel):
    account_id: str
    selection: Selection


class RemoteConfig(BaseModel):
    server_url: str
    server_token: str = Field(min_length=32, max_length=512)
    credential_token: str | None = Field(default=None, max_length=512)
    api_key_token: str | None = Field(default=None, max_length=512)


def _config_path() -> Path:
    return Path(get_settings().db_path).expanduser().resolve().parent / "onboarding-config.json"


class OperatorLogin(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    server_url: str | None = None


def remote_status() -> dict[str, Any]:
    try:
        config = _read_config()
        return {
            "configured": True,
            "server_url": config["server_url"],
            "mode": config["mode"],
            "operator": config.get("operator"),
        }
    except OnboardingError as exc:
        return {
            "configured": False,
            "server_url": _stored_server_url(),
            "mode": None,
            "operator": None,
            "message": str(exc),
        }


def _stored_server_url() -> str | None:
    """The shared server's address, known even before anyone has signed in."""
    url = os.environ.get("CPM_ONBOARDING_SERVER_URL")
    if url:
        return url.rstrip("/")
    try:
        return json.loads(_config_path().read_text(encoding="utf-8")).get("server_url") or None
    except (OSError, ValueError, AttributeError):
        return None


def _read_config() -> dict[str, Any]:
    """Who this installation is to the shared server.

    ``mode`` is ``operator`` after a personal sign-in (the normal case on an
    operator's computer) and ``admin`` when the server's own token is stored.
    An operator sign-in wins: it is the narrower identity.
    """
    path = _config_path()
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except (OSError, ValueError) as exc:
            raise OnboardingError("共享服务器配置无法读取", 500, "remote_config_invalid") from exc
    url = os.environ.get("CPM_ONBOARDING_SERVER_URL") or data.get("server_url")
    try:
        if url and data.get("operator_token"):
            return {
                "server_url": url.rstrip("/"),
                "server_token": decrypt(data["operator_token"]),
                "mode": "operator",
                "operator": data.get("operator"),
            }
        token = os.environ.get("CPM_ONBOARDING_SERVER_TOKEN")
        if not token and data.get("server_token"):
            token = decrypt(data["server_token"])
    except (ValueError, KeyError, TypeError) as exc:
        raise OnboardingError("共享服务器配置无法读取", 500, "remote_config_invalid") from exc
    if url and token and len(token) >= 32:
        return {"server_url": url.rstrip("/"), "server_token": token, "mode": "admin"}
    if url:
        raise OnboardingError("请先登录上号员账号", 409, "operator_login_required")
    raise OnboardingError("尚未配置共享服务器", 503, "remote_not_configured")


def _write_config(values: dict[str, Any]) -> None:
    path = _config_path()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _validated_url(server_url: str) -> str:
    parsed = urlparse(server_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise OnboardingError("共享服务器地址格式不正确", 422, "remote_url_invalid")
    if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise OnboardingError("非本机共享服务器必须使用 HTTPS", 422, "remote_https_required")
    return server_url.rstrip("/")


async def operator_login(request: OperatorLogin) -> dict[str, Any]:
    """Sign in to the shared server with a personal account and remember it."""
    url = request.server_url or _stored_server_url()
    if not url:
        raise OnboardingError("请填写共享服务器地址", 422, "remote_not_configured")
    url = _validated_url(url)
    result = await RemoteClient(url, "").request(
        "POST", "/v1/auth/login", {"username": request.username, "password": request.password}
    )
    try:
        stored = json.loads(_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    _write_config(
        {
            **(stored if isinstance(stored, dict) else {}),
            "server_url": url,
            "operator_token": encrypt(result["token"]),
            "operator": result["operator"],
        }
    )
    return remote_status()


async def operator_logout() -> dict[str, Any]:
    try:
        config = _read_config()
    except OnboardingError:
        config = None
    if config and config["mode"] == "operator":
        try:
            await RemoteClient(config["server_url"], config["server_token"]).request(
                "POST", "/v1/auth/logout"
            )
        except OnboardingError:
            pass  # The local credential is removed either way.
    try:
        stored = json.loads(_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    if isinstance(stored, dict) and ("operator_token" in stored or "operator" in stored):
        stored.pop("operator_token", None)
        stored.pop("operator", None)
        _write_config(stored)
    return remote_status()


def forget_operator_login() -> None:
    """Drop a token the server no longer accepts, so the UI asks to sign in."""
    try:
        stored = json.loads(_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(stored, dict) and stored.pop("operator_token", None) is not None:
        _write_config(stored)


def save_remote_config(request: RemoteConfig) -> RemoteClient:
    return RemoteClient(_validated_url(request.server_url), request.server_token)


async def write_remote_config(request: RemoteConfig) -> None:
    client = save_remote_config(request)
    try:
        stored = json.loads(_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    _write_config(
        {
            **(stored if isinstance(stored, dict) else {}),
            "server_url": request.server_url.rstrip("/"),
            "server_token": encrypt(request.server_token),
        }
    )
    if request.credential_token:
        await client.put_credential(request.credential_token)
    if request.api_key_token:
        await client.put_api_key_credential(request.api_key_token)


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
    def __init__(self, base_url: str, token: str, mode: str = "admin"):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.mode = mode

    @classmethod
    def configured(cls) -> RemoteClient:
        config = _read_config()
        return cls(config["server_url"], config["server_token"], config["mode"])

    @classmethod
    def admin(cls) -> RemoteClient:
        """The server-token identity, for administration from this installation."""
        path = _config_path()
        url = os.environ.get("CPM_ONBOARDING_SERVER_URL")
        token = os.environ.get("CPM_ONBOARDING_SERVER_TOKEN")
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            url = url or data.get("server_url")
            if not token and data.get("server_token"):
                token = decrypt(data["server_token"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise OnboardingError("共享服务器配置无法读取", 500, "remote_config_invalid") from exc
        if not url or not token:
            raise OnboardingError("此电脑未保存管理员密钥", 403, "admin_token_missing")
        return cls(url, token, "admin")

    @property
    def is_operator(self) -> bool:
        return self.mode == "operator"

    @property
    def _sessions(self) -> str:
        return "/v1/me/sessions" if self.is_operator else "/v1/sessions"

    async def request(self, method: str, path: str, body: Any = None) -> Any:
        if self.is_operator and not path.startswith(("/v1/me/", "/v1/auth/")):
            raise OnboardingError("上号员账号不能执行此操作", 409, "operator_not_allowed")
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(35, connect=10)) as client:
                response = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    json=body,
                    headers={"Authorization": f"Bearer {self.token}"} if self.token else {},
                )
        except httpx.HTTPError as exc:
            raise OnboardingError(f"无法连接共享服务器：{type(exc).__name__}", 503, "remote_unavailable") from exc
        if response.status_code == 401 and self.is_operator:
            forget_operator_login()
            raise OnboardingError("登录已失效，请重新登录", 409, "operator_login_required")
        if response.status_code == 404 and path.startswith(("/v1/admin/", "/v1/me/", "/v1/auth/")):
            try:
                unknown_route = response.json().get("detail") == "Not Found"
            except (ValueError, AttributeError):
                unknown_route = True
            if unknown_route:
                # The route itself is missing: a server from before operator accounts.
                raise OnboardingError(
                    "共享服务器版本过旧，不支持上号员与分配功能，请先更新共享服务器",
                    409,
                    "remote_outdated",
                )
        if not response.is_success:
            error = _response_error(response, "共享服务器")
            # The shared server's own answers (wrong password, not yours, already
            # submitted) are the operator's to read, not a gateway failure.
            # 401/403 are reserved for this app's own login in the web UI.
            if 400 <= response.status_code < 500:
                error.status_code = (
                    409 if response.status_code in {401, 403} else response.status_code
                )
                error.code = "remote_rejected"
            raise error
        return response.json()

    async def bundle(self) -> dict[str, Any]:
        return await self.request("GET", "/v1/me/bundle")

    async def start_task(self, account_id: str) -> dict[str, Any]:
        return await self.request("POST", "/v1/me/sessions", {"account_id": account_id})

    async def submit_session(self, session_id: str) -> dict[str, Any]:
        return await self.request("POST", f"/v1/me/sessions/{session_id}/submit")

    async def refresh_session(self, session_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/v1/me/sessions/{session_id}/result")

    async def admin_request(self, method: str, path: str, body: Any = None) -> Any:
        return await self.request(method, f"/v1/admin{path}", body)

    async def state(self) -> dict[str, Any]:
        return await self.request("GET", "/v1/state")

    async def preferences(self, values: dict[str, Any]) -> dict[str, Any]:
        return await self.request("PUT", "/v1/preferences", {"values": values})

    async def put_credential(self, token: str) -> None:
        await self.request("PUT", "/v1/credential", {"token": token})

    async def credential(self) -> str:
        return (await self.request("GET", "/v1/credential"))["token"]

    async def put_api_key_credential(self, token: str) -> None:
        await self.request("PUT", "/v1/api-key-credential", {"token": token})

    async def api_key_credential(self) -> str:
        return (await self.request("GET", "/v1/api-key-credential"))["token"]

    async def save_account(self, request: AccountInput) -> dict[str, Any]:
        return await self.request("POST", "/v1/accounts", request.model_dump())

    async def save_accounts(self, request: AccountBatchInput) -> dict[str, Any]:
        return await self.request("POST", "/v1/accounts/batch", request.model_dump())

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
        return await self.request("GET", f"{self._sessions}/{session_id}")

    async def update_session(self, session_id: str, **values: Any) -> dict[str, Any]:
        if self.is_operator:
            # The server owns the platform result; an operator reports progress only.
            values = {key: value for key, value in values.items() if key not in {"result", "job_id"}}
        return await self.request("PATCH", f"{self._sessions}/{session_id}", values)

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
