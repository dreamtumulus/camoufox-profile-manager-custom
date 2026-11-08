from __future__ import annotations

import argparse
import hmac
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uvicorn
from cryptography.fernet import Fernet
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _masked_sk(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) < 16:
        return "••••"
    return f"{value[:10]}…{value[-4:]}"


class PreferencesRequest(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class CredentialRequest(BaseModel):
    token: str = Field(min_length=20, max_length=512)


class AccountRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    org_id: str = Field(min_length=1, max_length=128)
    org_name: str = Field(min_length=1, max_length=255)


class AccountBatchRequest(BaseModel):
    emails: list[str] = Field(min_length=1, max_length=500)
    org_id: str = Field(min_length=1, max_length=128)
    org_name: str = Field(min_length=1, max_length=255)


class SessionCreateRequest(BaseModel):
    account_id: str
    email: str
    selection: dict[str, Any]


class SessionUpdateRequest(BaseModel):
    status: str | None = None
    profile_id: str | None = None
    sk: str | None = None
    job_id: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


class OnboardingServerStore:
    def __init__(self, path: Path, secret_key: str):
        self.path = path
        self.fernet = Fernet(secret_key.encode())
        self.lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS preferences (
                    id INTEGER PRIMARY KEY CHECK(id = 1), values_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS credential (
                    id INTEGER PRIMARY KEY CHECK(id = 1), token_encrypted TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS api_key_credential (
                    id INTEGER PRIMARY KEY CHECK(id = 1), token_encrypted TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE,
                    org_id TEXT NOT NULL, org_name TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, account_id TEXT NOT NULL, email TEXT NOT NULL,
                    selection_json TEXT NOT NULL, profile_id TEXT, status TEXT NOT NULL,
                    sk_encrypted TEXT, job_id TEXT, result_json TEXT, error TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                """
            )

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def preferences(self) -> dict[str, Any]:
        with self.lock, self.connect() as connection:
            row = connection.execute("SELECT values_json FROM preferences WHERE id = 1").fetchone()
        return json.loads(row[0]) if row else {}

    def set_preferences(self, values: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(values, ensure_ascii=False)
        with self.lock, self.connect() as connection:
            connection.execute(
                "INSERT INTO preferences(id, values_json) VALUES(1, ?) "
                "ON CONFLICT(id) DO UPDATE SET values_json = excluded.values_json",
                (encoded,),
            )
        return values

    def has_credential(self) -> bool:
        with self.lock, self.connect() as connection:
            row = connection.execute("SELECT 1 FROM credential WHERE id = 1").fetchone()
        return row is not None

    def set_credential(self, token: str) -> None:
        encrypted = self.fernet.encrypt(token.encode()).decode()
        with self.lock, self.connect() as connection:
            connection.execute(
                "INSERT INTO credential(id, token_encrypted) VALUES(1, ?) "
                "ON CONFLICT(id) DO UPDATE SET token_encrypted = excluded.token_encrypted",
                (encrypted,),
            )

    def credential(self) -> str:
        with self.lock, self.connect() as connection:
            row = connection.execute("SELECT token_encrypted FROM credential WHERE id = 1").fetchone()
        if row is None:
            raise HTTPException(409, "1dayApi 登录密钥尚未配置")
        return self.fernet.decrypt(row[0]).decode()

    def has_api_key_credential(self) -> bool:
        with self.lock, self.connect() as connection:
            row = connection.execute("SELECT 1 FROM api_key_credential WHERE id = 1").fetchone()
        return row is not None

    def set_api_key_credential(self, token: str) -> None:
        encrypted = self.fernet.encrypt(token.encode()).decode()
        with self.lock, self.connect() as connection:
            connection.execute(
                "INSERT INTO api_key_credential(id, token_encrypted) VALUES(1, ?) "
                "ON CONFLICT(id) DO UPDATE SET token_encrypted = excluded.token_encrypted",
                (encrypted,),
            )

    def api_key_credential(self) -> str:
        with self.lock, self.connect() as connection:
            row = connection.execute("SELECT token_encrypted FROM api_key_credential WHERE id = 1").fetchone()
        if row is None:
            raise HTTPException(409, "api-key.xyz Bearer 密钥尚未配置")
        return self.fernet.decrypt(row[0]).decode()

    def accounts(self) -> list[dict[str, Any]]:
        with self.lock, self.connect() as connection:
            rows = connection.execute("SELECT * FROM accounts ORDER BY email").fetchall()
        return [dict(row) for row in rows]

    def _save_account(self, connection: sqlite3.Connection, request: AccountRequest) -> dict[str, Any]:
        email = request.email.strip().lower()
        if "@" not in email or email.startswith("@") or email.endswith("@"):
            raise HTTPException(422, "邮箱地址格式不正确")
        connection.execute(
            "INSERT INTO accounts(id, email, org_id, org_name, created_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(email) DO UPDATE SET org_id = excluded.org_id, "
            "org_name = excluded.org_name",
            (uuid.uuid4().hex, email, request.org_id, request.org_name, _now()),
        )
        row = connection.execute("SELECT * FROM accounts WHERE email = ?", (email,)).fetchone()
        return dict(row)

    def save_account(self, request: AccountRequest) -> dict[str, Any]:
        with self.lock, self.connect() as connection:
            return self._save_account(connection, request)

    def save_accounts(self, request: AccountBatchRequest) -> dict[str, Any]:
        accounts: list[dict[str, Any]] = []
        invalid: list[dict[str, str]] = []
        with self.lock, self.connect() as connection:
            for value in request.emails:
                try:
                    account = self._save_account(
                        connection,
                        AccountRequest(email=value, org_id=request.org_id, org_name=request.org_name),
                    )
                    accounts.append(account)
                except HTTPException as exc:
                    invalid.append({"email": value, "reason": str(exc.detail)})
                except ValueError:
                    invalid.append({"email": value, "reason": "邮箱地址格式不正确"})
        return {"accounts": accounts, "invalid": invalid}

    def delete_account(self, account_id: str) -> None:
        with self.lock, self.connect() as connection:
            used = connection.execute(
                "SELECT 1 FROM sessions WHERE account_id = ? LIMIT 1", (account_id,)
            ).fetchone()
            if used:
                raise HTTPException(409, "该邮箱已有上号记录，不能删除")
            cursor = connection.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        if not cursor.rowcount:
            raise HTTPException(404, "邮箱账号不存在")

    def create_session(self, request: SessionCreateRequest) -> dict[str, Any]:
        session_id = uuid.uuid4().hex
        now = _now()
        with self.lock, self.connect() as connection:
            account = connection.execute(
                "SELECT 1 FROM accounts WHERE id = ? AND email = ?",
                (request.account_id, request.email),
            ).fetchone()
            if account is None:
                raise HTTPException(409, "邮箱账号不存在或已变更，请刷新后重新选择")
            connection.execute(
                "INSERT INTO sessions(id, account_id, email, selection_json, status, "
                "created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                (
                    session_id,
                    request.account_id,
                    request.email,
                    json.dumps(request.selection, ensure_ascii=False),
                    "creating",
                    now,
                    now,
                ),
            )
        return self.session(session_id)

    def session(self, session_id: str, reveal_sk: bool = False) -> dict[str, Any]:
        with self.lock, self.connect() as connection:
            row = connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "上号记录不存在")
        result = dict(row)
        result["selection"] = json.loads(result.pop("selection_json"))
        result["result"] = json.loads(result.pop("result_json")) if result["result_json"] else None
        sk_encrypted = result.pop("sk_encrypted")
        sk = self.fernet.decrypt(sk_encrypted).decode() if sk_encrypted else None
        result["sk"] = sk if reveal_sk else None
        result["sk_masked"] = _masked_sk(sk)
        return result

    def sessions(self) -> list[dict[str, Any]]:
        with self.lock, self.connect() as connection:
            rows = connection.execute(
                "SELECT id FROM sessions ORDER BY created_at DESC LIMIT 200"
            ).fetchall()
        return [self.session(row[0]) for row in rows]

    def update_session(self, session_id: str, request: SessionUpdateRequest) -> dict[str, Any]:
        current = self.session(session_id)
        values = request.model_dump(exclude_unset=True)
        columns: list[str] = []
        parameters: list[Any] = []
        for key in ("status", "profile_id", "job_id", "error"):
            if key in values:
                columns.append(f"{key} = ?")
                parameters.append(values[key])
        if "sk" in values:
            columns.append("sk_encrypted = ?")
            parameters.append(
                self.fernet.encrypt(values["sk"].encode()).decode() if values["sk"] else None
            )
        if "result" in values:
            columns.append("result_json = ?")
            parameters.append(
                json.dumps(values["result"], ensure_ascii=False) if values["result"] is not None else None
            )
        if not columns:
            return current
        columns.append("updated_at = ?")
        parameters.extend([_now(), session_id])
        with self.lock, self.connect() as connection:
            connection.execute(
                f"UPDATE sessions SET {', '.join(columns)} WHERE id = ?", parameters
            )
        return self.session(session_id)


def create_app(store: OnboardingServerStore, api_token: str) -> FastAPI:
    app = FastAPI(title="Camoufox Onboarding Shared Store", docs_url=None, redoc_url=None)

    async def authorized(request: Request) -> None:
        expected = f"Bearer {api_token}"
        if not hmac.compare_digest(request.headers.get("authorization", ""), expected):
            raise HTTPException(401, "共享服务器认证失败")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/v1/state", dependencies=[Depends(authorized)])
    async def state():
        return {
            "preferences": store.preferences(),
            "accounts": store.accounts(),
            "sessions": store.sessions(),
            "credential_configured": store.has_credential(),
            "api_key_configured": store.has_api_key_credential(),
        }

    @app.put("/v1/preferences", dependencies=[Depends(authorized)])
    async def preferences(request: PreferencesRequest):
        return store.set_preferences(request.values)

    @app.put("/v1/credential", dependencies=[Depends(authorized)])
    async def credential(request: CredentialRequest):
        store.set_credential(request.token)
        return {"configured": True}

    @app.get("/v1/credential", dependencies=[Depends(authorized)])
    async def read_credential():
        return {"token": store.credential()}

    @app.put("/v1/api-key-credential", dependencies=[Depends(authorized)])
    async def set_api_key_credential(request: CredentialRequest):
        store.set_api_key_credential(request.token)
        return {"configured": True}

    @app.get("/v1/api-key-credential", dependencies=[Depends(authorized)])
    async def read_api_key_credential():
        return {"token": store.api_key_credential()}

    @app.post("/v1/accounts", dependencies=[Depends(authorized)])
    async def account(request: AccountRequest):
        return store.save_account(request)

    @app.post("/v1/accounts/batch", dependencies=[Depends(authorized)])
    async def accounts_batch(request: AccountBatchRequest):
        return store.save_accounts(request)

    @app.delete("/v1/accounts/{account_id}", dependencies=[Depends(authorized)])
    async def delete_account(account_id: str):
        store.delete_account(account_id)
        return {"deleted": True}

    @app.post("/v1/sessions", dependencies=[Depends(authorized)])
    async def session(request: SessionCreateRequest):
        return store.create_session(request)

    @app.get("/v1/sessions/{session_id}", dependencies=[Depends(authorized)])
    async def session_detail(session_id: str):
        return store.session(session_id)

    @app.patch("/v1/sessions/{session_id}", dependencies=[Depends(authorized)])
    async def update_session(session_id: str, request: SessionUpdateRequest):
        return store.update_session(session_id, request)

    @app.get("/v1/sessions/{session_id}/secret", dependencies=[Depends(authorized)])
    async def session_secret(session_id: str):
        session = store.session(session_id, reveal_sk=True)
        if not session["sk"]:
            raise HTTPException(409, "尚未提取 SK")
        return {"sk": session["sk"]}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Camoufox onboarding shared store")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9878)
    args = parser.parse_args()
    api_token = os.environ.get("CPM_ONBOARDING_SERVER_TOKEN", "")
    secret_key = os.environ.get("CPM_ONBOARDING_SECRET_KEY", "")
    if len(api_token) < 32:
        raise SystemExit("CPM_ONBOARDING_SERVER_TOKEN 至少需要 32 个字符")
    try:
        store = OnboardingServerStore(
            Path(os.environ.get("CPM_ONBOARDING_DB", "data/onboarding-server.db")), secret_key
        )
    except (ValueError, TypeError) as exc:
        raise SystemExit("CPM_ONBOARDING_SECRET_KEY 必须是有效的 Fernet 密钥") from exc
    uvicorn.run(
        create_app(store, api_token),
        host=args.host,
        port=args.port,
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1,::1",
    )


if __name__ == "__main__":
    main()
