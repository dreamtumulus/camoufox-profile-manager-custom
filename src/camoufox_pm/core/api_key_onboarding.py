from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .onboarding import OnboardingError, Selection

API_KEY_ORIGIN = "https://api-key.xyz"


class ApiKeyClient:
    def __init__(self, token: str):
        self.token = token

    async def request(self, method: str, path: str, body: Any = None) -> Any:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(40, connect=10)) as client:
                response = await client.request(
                    method,
                    f"{API_KEY_ORIGIN}{path}",
                    json=body,
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "Accept": "application/json",
                        "Origin": API_KEY_ORIGIN,
                        "Referer": f"{API_KEY_ORIGIN}/console/",
                    },
                )
        except httpx.HTTPError as exc:
            raise OnboardingError(
                f"无法连接 api-key.xyz：{type(exc).__name__}", 503, "api_key_unavailable"
            ) from exc
        if not response.is_success:
            message = f"api-key.xyz 返回 HTTP {response.status_code}"
            try:
                result = response.json()
                detail = result.get("detail")
                error = result.get("error")
                if isinstance(error, dict):
                    detail = detail or error.get("message") or error.get("code")
                errors = result.get("errors")
                details = [
                    f"{item.get('location')}: {item.get('message')}"
                    for item in errors or []
                    if isinstance(item, dict) and item.get("message")
                ]
                if detail:
                    message = str(detail)
                if details:
                    message = f"{message}；{'；'.join(details)}"
            except (ValueError, TypeError):
                pass
            raise OnboardingError(message, 502, "api_key_upstream_error")
        if response.status_code == 204:
            return {}
        result = response.json()
        if not isinstance(result, dict):
            raise OnboardingError("api-key.xyz 返回格式不正确", 502, "api_key_response_invalid")
        return result

    async def options(self) -> dict[str, Any]:
        me, defaults, groups = await asyncio.gather(
            self.request("GET", "/api/v1/me"),
            self.request("GET", "/api/v1/me/accounts/claude-code/session-key"),
            self.request("GET", "/api/v1/me/ip-groups"),
        )
        return {"me": me, "defaults": defaults, "groups": groups.get("items", [])}

    async def submit(self, sk: str, selection: Selection, email: str) -> dict[str, Any]:
        return await self.request(
            "POST",
            "/api/v1/me/accounts/claude-code/session-key",
            {
                "session_key": sk,
                "seats": selection.seats,
                "rpm": selection.rpm,
                "pool_tags": selection.pool_tags,
                "required_traits": selection.required_traits,
                "forbidden_traits": selection.forbidden_traits,
                "ip": selection.proxy_id,
                "name": email,
            },
        )
