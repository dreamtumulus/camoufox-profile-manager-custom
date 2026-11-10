"""Per-operator onboarding on the shared server.

The administrator (the server token) owns operators, the proxy pool and the
e-mail accounts. Each operator signs in with a personal account and receives
only the proxies and e-mails assigned to them. The server, not the client,
decides which proxy an e-mail uses, checks ownership on every call and submits
session keys to the platform itself — the platform credential and other
operators' data never reach an operator's computer.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from camoufox_pm.core import auth
from camoufox_pm.core.proxy_import import parse_proxy_import

TOKEN_TTL = timedelta(days=30)
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_ATTEMPTS = 10
Platform = Literal["1dayapi", "api-key.xyz"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS operators (
    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS operator_tokens (
    token_hash TEXT PRIMARY KEY, operator_id TEXT NOT NULL,
    created_at TEXT NOT NULL, expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS proxies (
    id TEXT PRIMARY KEY, platform TEXT NOT NULL,
    node_id TEXT NOT NULL, node_name TEXT NOT NULL, proxy_group TEXT,
    platform_proxy_id TEXT NOT NULL, exit_ip TEXT NOT NULL,
    type TEXT NOT NULL, server TEXT NOT NULL, username TEXT, password_encrypted TEXT,
    capacity INTEGER NOT NULL DEFAULT 2, defaults_json TEXT NOT NULL DEFAULT '{}',
    operator_id TEXT, created_at TEXT NOT NULL,
    UNIQUE(platform, platform_proxy_id)
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
    actor TEXT NOT NULL, action TEXT NOT NULL, detail_json TEXT NOT NULL
);
"""
ADDED_COLUMNS = {
    "accounts": {"platform": "TEXT", "operator_id": "TEXT", "proxy_id": "TEXT"},
    "sessions": {"operator_id": "TEXT"},
}
SECRET_KEYS = {"sk", "sessionkey", "session_key", "token", "authorization"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def public_result(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[已隐藏]" if key.lower() in SECRET_KEYS else public_result(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [public_result(item) for item in value]
    return value


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class OperatorCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9._@-]+$")
    password: str = Field(min_length=8, max_length=256)


class OperatorUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=8, max_length=256)
    disabled: bool | None = None


class PlatformProxy(BaseModel):
    """One proxy as the platform lists it; ``host``/``port`` are the match key."""

    id: str
    host: str
    port: int
    exit_ip: str
    group: str | None = None


class ProxyImport(BaseModel):
    platform: Platform = "1dayapi"
    node_id: str = Field(min_length=1, max_length=128)
    node_name: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1, max_length=64000)
    capacity: int = Field(default=2, ge=1, le=1000)
    defaults: dict[str, Any] = Field(default_factory=dict)
    operator_id: str | None = None
    # Supplied by the caller in tests; fetched from the platform when omitted.
    platform_proxies: list[PlatformProxy] | None = None


class ProxyAssign(BaseModel):
    proxy_ids: list[str] = Field(min_length=1, max_length=2000)
    operator_id: str | None = None


class AccountImport(BaseModel):
    operator_id: str
    platform: Platform = "1dayapi"
    org_id: str = Field(min_length=1, max_length=128)
    org_name: str = Field(min_length=1, max_length=255)
    emails: list[str] = Field(min_length=1, max_length=2000)


class OperatorSessionCreate(BaseModel):
    account_id: str


class OperatorSessionUpdate(BaseModel):
    status: Literal["launching", "browser_open", "extracted", "closed", "error"] | None = None
    profile_id: str | None = None
    sk: str | None = Field(default=None, max_length=4096)
    error: str | None = Field(default=None, max_length=2000)


class OperatorStore:
    """Operator tables living in the shared store's database."""

    def __init__(self, store):
        self.store = store
        self._failures: dict[str, list[float]] = {}
        with store.lock, store.connect() as connection:
            connection.executescript(SCHEMA)
            for table, columns in ADDED_COLUMNS.items():
                present = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                for name, kind in columns.items():
                    if name not in present:
                        connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")

    # -- helpers ---------------------------------------------------------

    def _audit(self, connection: sqlite3.Connection, actor: str, action: str, **detail) -> None:
        connection.execute(
            "INSERT INTO audit_log(at, actor, action, detail_json) VALUES(?,?,?,?)",
            (_now().isoformat(), actor, action, json.dumps(detail, ensure_ascii=False)),
        )

    def _operator(self, connection: sqlite3.Connection, operator_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM operators WHERE id = ?", (operator_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "上号员不存在")
        return row

    @staticmethod
    def _operator_view(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "username": row["username"],
            "disabled": bool(row["disabled"]),
            "created_at": row["created_at"],
        }

    def _proxy_view(self, row: sqlite3.Row, *, secret: bool, used: int = 0) -> dict[str, Any]:
        view = {
            key: row[key]
            for key in (
                "id", "platform", "node_id", "node_name", "proxy_group", "platform_proxy_id",
                "exit_ip", "type", "server", "username", "capacity", "operator_id",
            )
        }
        view["used"] = used
        view["has_password"] = bool(row["password_encrypted"])
        if secret:
            view["password"] = (
                self.store.fernet.decrypt(row["password_encrypted"]).decode()
                if row["password_encrypted"]
                else None
            )
        return view

    @staticmethod
    def _usage(connection: sqlite3.Connection) -> dict[str, int]:
        return dict(
            connection.execute(
                "SELECT proxy_id, COUNT(*) FROM accounts WHERE proxy_id IS NOT NULL GROUP BY proxy_id"
            ).fetchall()
        )

    # -- operators and sign-in --------------------------------------------

    def operators(self) -> list[dict[str, Any]]:
        with self.store.lock, self.store.connect() as connection:
            rows = connection.execute("SELECT * FROM operators ORDER BY username").fetchall()
            proxies = dict(connection.execute(
                "SELECT operator_id, COUNT(*) FROM proxies GROUP BY operator_id").fetchall())
            accounts = dict(connection.execute(
                "SELECT operator_id, COUNT(*) FROM accounts GROUP BY operator_id").fetchall())
            done = dict(connection.execute(
                "SELECT operator_id, COUNT(DISTINCT account_id) FROM sessions "
                "WHERE status = 'completed' GROUP BY operator_id").fetchall())
        return [
            {
                **self._operator_view(row),
                "proxies": proxies.get(row["id"], 0),
                "accounts": accounts.get(row["id"], 0),
                "completed": done.get(row["id"], 0),
            }
            for row in rows
        ]

    def create_operator(self, request: OperatorCreate) -> dict[str, Any]:
        operator_id = uuid.uuid4().hex
        with self.store.lock, self.store.connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO operators(id, username, password_hash, created_at) VALUES(?,?,?,?)",
                    (operator_id, request.username, auth.hash_password(request.password),
                     _now().isoformat()),
                )
            except sqlite3.IntegrityError:
                raise HTTPException(409, "该上号员账号已存在") from None
            self._audit(connection, "admin", "operator.create", operator=request.username)
            return self._operator_view(self._operator(connection, operator_id))

    def update_operator(self, operator_id: str, request: OperatorUpdate) -> dict[str, Any]:
        with self.store.lock, self.store.connect() as connection:
            row = self._operator(connection, operator_id)
            if request.password is not None:
                connection.execute(
                    "UPDATE operators SET password_hash = ? WHERE id = ?",
                    (auth.hash_password(request.password), operator_id),
                )
            if request.disabled is not None:
                connection.execute(
                    "UPDATE operators SET disabled = ? WHERE id = ?",
                    (int(request.disabled), operator_id),
                )
            # A reset or a suspension must end the sessions it was meant to end.
            if request.password is not None or request.disabled:
                connection.execute(
                    "DELETE FROM operator_tokens WHERE operator_id = ?", (operator_id,)
                )
            self._audit(
                connection, "admin", "operator.update", operator=row["username"],
                password=request.password is not None, disabled=request.disabled,
            )
            return self._operator_view(self._operator(connection, operator_id))

    def login(self, request: LoginRequest) -> dict[str, Any]:
        key = request.username.strip().lower()
        now = time.monotonic()
        recent = [at for at in self._failures.get(key, []) if now - at < LOGIN_WINDOW_SECONDS]
        if len(recent) >= LOGIN_ATTEMPTS:
            raise HTTPException(429, "登录失败次数过多，请稍后再试")
        with self.store.lock, self.store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM operators WHERE username = ?", (request.username.strip(),)
            ).fetchone()
        if row is None:
            auth.verify_dummy(request.password)
            valid = False
        else:
            valid = auth.verify_password(row["password_hash"], request.password)
        if not valid or row["disabled"]:
            self._failures[key] = [*recent, now]
            raise HTTPException(401, "账号或密码不正确，或账号已停用")
        self._failures.pop(key, None)
        token = secrets.token_urlsafe(32)
        issued = _now()
        with self.store.lock, self.store.connect() as connection:
            connection.execute(
                "DELETE FROM operator_tokens WHERE expires_at < ?", (issued.isoformat(),)
            )
            connection.execute(
                "INSERT INTO operator_tokens(token_hash, operator_id, created_at, expires_at) "
                "VALUES(?,?,?,?)",
                (_hash(token), row["id"], issued.isoformat(), (issued + TOKEN_TTL).isoformat()),
            )
            self._audit(connection, row["username"], "operator.login")
        return {"token": token, "operator": self._operator_view(row)}

    def logout(self, token: str) -> None:
        with self.store.lock, self.store.connect() as connection:
            connection.execute("DELETE FROM operator_tokens WHERE token_hash = ?", (_hash(token),))

    def operator_for_token(self, token: str) -> dict[str, Any] | None:
        with self.store.lock, self.store.connect() as connection:
            row = connection.execute(
                "SELECT o.* FROM operator_tokens t JOIN operators o ON o.id = t.operator_id "
                "WHERE t.token_hash = ? AND t.expires_at > ? AND o.disabled = 0",
                (_hash(token), _now().isoformat()),
            ).fetchone()
        return self._operator_view(row) if row else None

    # -- proxy pool ---------------------------------------------------------

    def proxies(self) -> list[dict[str, Any]]:
        with self.store.lock, self.store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM proxies ORDER BY node_name, exit_ip"
            ).fetchall()
            usage = self._usage(connection)
        return [self._proxy_view(row, secret=False, used=usage.get(row["id"], 0)) for row in rows]

    def import_proxies(
        self, request: ProxyImport, platform_proxies: list[PlatformProxy]
    ) -> dict[str, Any]:
        """Store connection details for proxies the platform already knows.

        The platform never returns proxy credentials, so the administrator
        pastes the same list here; each line is matched to its platform entry
        by ``host:port``. Lines the platform does not list are reported, never
        stored: a proxy without a platform id cannot be submitted.
        """
        try:
            parsed = parse_proxy_import(request.text)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        known = {(item.host.lower().strip("[]"), item.port): item for item in platform_proxies}
        imported: list[dict[str, Any]] = []
        unmatched: list[dict[str, Any]] = []
        with self.store.lock, self.store.connect() as connection:
            if request.operator_id:
                self._operator(connection, request.operator_id)
            for line, config in parsed:
                host, _, port = config.server.rpartition(":")
                match = known.get((host.lower().strip("[]"), int(port)))
                if match is None:
                    unmatched.append({"line": line, "server": config.server})
                    continue
                encrypted = (
                    self.store.fernet.encrypt(config.password.encode()).decode()
                    if config.password
                    else None
                )
                connection.execute(
                    "INSERT INTO proxies(id, platform, node_id, node_name, proxy_group, "
                    "platform_proxy_id, exit_ip, type, server, username, password_encrypted, "
                    "capacity, defaults_json, operator_id, created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(platform, platform_proxy_id) DO UPDATE SET "
                    "node_id = excluded.node_id, node_name = excluded.node_name, "
                    "proxy_group = excluded.proxy_group, exit_ip = excluded.exit_ip, "
                    "type = excluded.type, server = excluded.server, "
                    "username = excluded.username, "
                    "password_encrypted = excluded.password_encrypted, "
                    "capacity = excluded.capacity, defaults_json = excluded.defaults_json, "
                    "operator_id = COALESCE(excluded.operator_id, proxies.operator_id)",
                    (
                        uuid.uuid4().hex, request.platform, request.node_id, request.node_name,
                        match.group, match.id, match.exit_ip, config.type.value, config.server,
                        config.username, encrypted, request.capacity,
                        json.dumps(request.defaults, ensure_ascii=False), request.operator_id,
                        _now().isoformat(),
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM proxies WHERE platform = ? AND platform_proxy_id = ?",
                    (request.platform, match.id),
                ).fetchone()
                imported.append(self._proxy_view(row, secret=False))
            self._audit(
                connection, "admin", "proxy.import", platform=request.platform,
                node=request.node_id, imported=len(imported), unmatched=len(unmatched),
            )
        return {"imported": imported, "unmatched": unmatched}

    def assign_proxies(self, request: ProxyAssign) -> dict[str, Any]:
        with self.store.lock, self.store.connect() as connection:
            if request.operator_id:
                self._operator(connection, request.operator_id)
            marks = ",".join("?" * len(request.proxy_ids))
            if request.operator_id is None:
                busy = connection.execute(
                    f"SELECT COUNT(*) FROM accounts WHERE proxy_id IN ({marks})", request.proxy_ids
                ).fetchone()[0]
            else:
                busy = connection.execute(
                    f"SELECT COUNT(*) FROM accounts WHERE proxy_id IN ({marks}) "
                    "AND operator_id IS NOT ?",
                    (*request.proxy_ids, request.operator_id),
                ).fetchone()[0]
            if busy:
                raise HTTPException(409, "所选 IP 上还有其他上号员的邮箱，请先收回这些邮箱")
            changed = connection.execute(
                f"UPDATE proxies SET operator_id = ? WHERE id IN ({marks})",
                (request.operator_id, *request.proxy_ids),
            ).rowcount
            self._audit(
                connection, "admin", "proxy.assign", operator_id=request.operator_id,
                proxies=changed,
            )
        return {"assigned": changed}

    def delete_proxy(self, proxy_id: str) -> None:
        with self.store.lock, self.store.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM accounts WHERE proxy_id = ? LIMIT 1", (proxy_id,)
            ).fetchone():
                raise HTTPException(409, "该 IP 已绑定邮箱，不能删除")
            if not connection.execute("DELETE FROM proxies WHERE id = ?", (proxy_id,)).rowcount:
                raise HTTPException(404, "IP 不存在")
            self._audit(connection, "admin", "proxy.delete", proxy_id=proxy_id)

    # -- e-mail accounts ------------------------------------------------------

    def import_accounts(self, request: AccountImport) -> dict[str, Any]:
        """Give e-mails to an operator, pairing each with one of their proxies.

        Each e-mail goes to the operator's proxy with the most free capacity on
        the requested platform. E-mails that do not fit are reported and left
        untouched, so the administrator can assign more proxies and retry.
        """
        placed: list[dict[str, Any]] = []
        unplaced: list[dict[str, str]] = []
        invalid: list[dict[str, str]] = []
        with self.store.lock, self.store.connect() as connection:
            operator = self._operator(connection, request.operator_id)
            usage = self._usage(connection)
            pool = [
                {"id": row["id"], "exit_ip": row["exit_ip"],
                 "free": row["capacity"] - usage.get(row["id"], 0)}
                for row in connection.execute(
                    "SELECT * FROM proxies WHERE operator_id = ? AND platform = ? "
                    "ORDER BY created_at, id",
                    (request.operator_id, request.platform),
                )
            ]
            seen: set[str] = set()
            for value in request.emails:
                email = value.strip().lower()
                if not email or email in seen:
                    continue
                seen.add(email)
                if "@" not in email or email.startswith("@") or email.endswith("@") or len(email) > 320:
                    invalid.append({"email": value, "reason": "邮箱地址格式不正确"})
                    continue
                existing = connection.execute(
                    "SELECT * FROM accounts WHERE email = ?", (email,)
                ).fetchone()
                if existing is not None and existing["proxy_id"]:
                    if existing["operator_id"] == request.operator_id:
                        placed.append(dict(existing))
                        continue
                    if connection.execute(
                        "SELECT 1 FROM sessions WHERE account_id = ? LIMIT 1", (existing["id"],)
                    ).fetchone():
                        unplaced.append({"email": email, "reason": "已有其他上号员的上号记录"})
                        continue
                    for proxy in pool:
                        if proxy["id"] == existing["proxy_id"]:
                            proxy["free"] += 1
                target = max(pool, key=lambda proxy: proxy["free"], default=None)
                if target is None or target["free"] <= 0:
                    unplaced.append({"email": email, "reason": "该上号员没有空闲的 IP 名额"})
                    continue
                target["free"] -= 1
                connection.execute(
                    "INSERT INTO accounts(id, email, org_id, org_name, created_at, platform, "
                    "operator_id, proxy_id) VALUES(?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(email) DO UPDATE SET org_id = excluded.org_id, "
                    "org_name = excluded.org_name, platform = excluded.platform, "
                    "operator_id = excluded.operator_id, proxy_id = excluded.proxy_id",
                    (uuid.uuid4().hex, email, request.org_id, request.org_name,
                     _now().isoformat(), request.platform, request.operator_id, target["id"]),
                )
                row = dict(connection.execute(
                    "SELECT * FROM accounts WHERE email = ?", (email,)).fetchone())
                row["exit_ip"] = target["exit_ip"]
                placed.append(row)
            self._audit(
                connection, "admin", "account.import", operator=operator["username"],
                placed=len(placed), unplaced=len(unplaced), invalid=len(invalid),
            )
        return {"placed": placed, "unplaced": unplaced, "invalid": invalid}

    def unassign_account(self, account_id: str) -> None:
        with self.store.lock, self.store.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM sessions WHERE account_id = ? AND status IN "
                "('submitting','submitted','completed') LIMIT 1", (account_id,)
            ).fetchone():
                raise HTTPException(409, "该邮箱已提交上号，不能收回")
            if not connection.execute(
                "UPDATE accounts SET operator_id = NULL, proxy_id = NULL WHERE id = ?",
                (account_id,),
            ).rowcount:
                raise HTTPException(404, "邮箱账号不存在")
            self._audit(connection, "admin", "account.unassign", account_id=account_id)

    def overview(self) -> dict[str, Any]:
        with self.store.lock, self.store.connect() as connection:
            accounts = [dict(row) for row in connection.execute(
                "SELECT a.*, (SELECT s.status FROM sessions s WHERE s.account_id = a.id "
                "ORDER BY s.created_at DESC LIMIT 1) AS status FROM accounts a ORDER BY a.email")]
            audit = [dict(row) for row in connection.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT 200")]
        return {
            "operators": self.operators(),
            "proxies": self.proxies(),
            "accounts": accounts,
            "audit": audit,
        }

    # -- what one operator sees and does --------------------------------------

    def bundle(self, operator: dict[str, Any]) -> dict[str, Any]:
        with self.store.lock, self.store.connect() as connection:
            usage = self._usage(connection)
            proxies = [
                self._proxy_view(row, secret=True, used=usage.get(row["id"], 0))
                for row in connection.execute(
                    "SELECT * FROM proxies WHERE operator_id = ? ORDER BY exit_ip", (operator["id"],)
                )
            ]
            accounts = [dict(row) for row in connection.execute(
                "SELECT * FROM accounts WHERE operator_id = ? ORDER BY email", (operator["id"],))]
            session_ids = [row[0] for row in connection.execute(
                "SELECT id FROM sessions WHERE operator_id = ? ORDER BY created_at DESC LIMIT 500",
                (operator["id"],))]
        sessions = [self.store.session(session_id) for session_id in session_ids]
        latest: dict[str, dict[str, Any]] = {}
        for session in sessions:
            latest.setdefault(session["account_id"], session)
        by_id = {proxy["id"]: proxy for proxy in proxies}
        tasks = [
            {
                **account,
                "exit_ip": by_id[account["proxy_id"]]["exit_ip"] if account["proxy_id"] in by_id else None,
                "session": latest.get(account["id"]),
            }
            for account in accounts
        ]
        return {"operator": operator, "proxies": proxies, "tasks": tasks, "sessions": sessions}

    def create_session(self, operator: dict[str, Any], account_id: str) -> dict[str, Any]:
        """Open an onboarding record; the selection comes from the assignment."""
        session_id = uuid.uuid4().hex
        now = _now().isoformat()
        with self.store.lock, self.store.connect() as connection:
            account = connection.execute(
                "SELECT * FROM accounts WHERE id = ? AND operator_id = ?",
                (account_id, operator["id"]),
            ).fetchone()
            if account is None:
                raise HTTPException(404, "该邮箱未分配给你")
            proxy = connection.execute(
                "SELECT * FROM proxies WHERE id = ? AND operator_id = ?",
                (account["proxy_id"], operator["id"]),
            ).fetchone()
            if proxy is None:
                raise HTTPException(409, "该邮箱对应的 IP 已被收回，请联系管理员")
            if connection.execute(
                "SELECT 1 FROM sessions WHERE account_id = ? AND status IN "
                "('submitting','submitted','completed') LIMIT 1", (account_id,)
            ).fetchone():
                raise HTTPException(409, "该邮箱已提交上号，不能重复上号")
            selection = {
                **json.loads(proxy["defaults_json"]),
                "platform": proxy["platform"],
                "node_id": proxy["node_id"],
                "node_name": proxy["node_name"],
                "org_id": account["org_id"],
                "org_name": account["org_name"],
                "proxy_group": proxy["proxy_group"],
                "proxy_id": proxy["platform_proxy_id"],
                "exit_ip": proxy["exit_ip"],
            }
            connection.execute(
                "INSERT INTO sessions(id, account_id, email, selection_json, status, "
                "created_at, updated_at, operator_id) VALUES(?,?,?,?,?,?,?,?)",
                (session_id, account_id, account["email"],
                 json.dumps(selection, ensure_ascii=False), "creating", now, now, operator["id"]),
            )
            self._audit(connection, operator["username"], "session.create",
                        email=account["email"], exit_ip=proxy["exit_ip"])
        session = self.store.session(session_id)
        session["proxy_id"] = proxy["id"]
        return session

    def owned_session(
        self, operator: dict[str, Any], session_id: str, reveal_sk: bool = False
    ) -> dict[str, Any]:
        with self.store.lock, self.store.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM sessions WHERE id = ? AND operator_id = ?",
                (session_id, operator["id"]),
            ).fetchone()
        if row is None:
            # Another operator's record is indistinguishable from a missing one.
            raise HTTPException(404, "上号记录不存在")
        return self.store.session(session_id, reveal_sk=reveal_sk)

    def audit(self, actor: str, action: str, **detail) -> None:
        with self.store.lock, self.store.connect() as connection:
            self._audit(connection, actor, action, **detail)


async def _platform_proxies(store, request: ProxyImport) -> list[PlatformProxy]:
    """Ask the platform which proxies it has on the node, for host:port matching."""
    from camoufox_pm.core.api_key_onboarding import ApiKeyClient
    from camoufox_pm.core.onboarding import OnboardingError, OneDayClient

    try:
        if request.platform == "api-key.xyz":
            options = await ApiKeyClient(store.api_key_credential()).options()
            return [
                PlatformProxy(
                    id=str(ip["id"]), host=str(ip["server"]), port=int(ip["port"]),
                    exit_ip=str(ip.get("observed_ip") or ip["server"]), group=group.get("name"),
                )
                for group in options["groups"]
                for ip in group.get("ips", [])
            ]
        rows = await OneDayClient(store.credential()).proxies(request.node_id)
        return [
            PlatformProxy(
                id=str(row["id"]), host=str(row["host"]), port=int(row["port"]),
                exit_ip=str(row.get("exitIp") or row["host"]), group=row.get("group"),
            )
            for row in rows
            if row.get("host") and row.get("port")
        ]
    except OnboardingError as exc:
        raise HTTPException(502, f"无法读取平台代理列表：{exc}") from None


async def submit_session(operators: OperatorStore, store, operator, session_id: str):
    """Submit the stored session key to the platform from the server."""
    from camoufox_pm.core.api_key_onboarding import ApiKeyClient
    from camoufox_pm.core.onboarding import OnboardingError, OneDayClient, Selection
    from camoufox_pm.onboarding_server import SessionUpdateRequest

    session = operators.owned_session(operator, session_id, reveal_sk=True)
    if not session["sk"]:
        raise HTTPException(409, "尚未提取 SK，不能提交")
    if session["job_id"] or session["status"] in {"submitting", "submitted", "completed"}:
        raise HTTPException(409, "该 SK 已提交，请刷新返回结果")
    store.update_session(session_id, SessionUpdateRequest(status="submitting", error=None))
    try:
        selection = Selection(**session["selection"])
        if selection.platform == "api-key.xyz":
            result = await ApiKeyClient(store.api_key_credential()).submit(
                session["sk"], selection, session["email"]
            )
            account = result.get("account")
            job_id = result.get("id") or (account.get("id") if isinstance(account, dict) else None)
        else:
            result = await OneDayClient(store.credential()).submit(
                session["sk"], selection, session["email"]
            )
            job_id = result.get("jobId")
    except (OnboardingError, HTTPException) as exc:
        message = str(exc.detail if isinstance(exc, HTTPException) else exc)
        store.update_session(session_id, SessionUpdateRequest(status="error", error=message))
        raise HTTPException(502, f"提交 SK 失败：{message}") from None
    operators.audit(operator["username"], "session.submit", email=session["email"])
    return store.update_session(
        session_id,
        SessionUpdateRequest(
            status="submitted", job_id=str(job_id) if job_id else None,
            result=public_result(result), error=None,
        ),
    )


async def refresh_session(operators: OperatorStore, store, operator, session_id: str):
    from camoufox_pm.core.onboarding import OnboardingError, OneDayClient
    from camoufox_pm.onboarding_server import SessionUpdateRequest

    session = operators.owned_session(operator, session_id)
    if session["selection"].get("platform") == "api-key.xyz":
        return session
    if not session["job_id"]:
        raise HTTPException(409, "尚未取得 1dayApi 作业编号")
    try:
        result = await OneDayClient(store.credential()).job(session["job_id"])
    except OnboardingError as exc:
        raise HTTPException(502, f"无法读取上号结果：{exc}") from None
    state = result.get("status")
    status = "completed" if state == "done" else "failed" if state == "failed" else "submitted"
    return store.update_session(
        session_id,
        SessionUpdateRequest(
            status=status, result=public_result(result),
            error=result.get("error") if status == "failed" else None,
        ),
    )


def register(app: FastAPI, store, api_token: str) -> OperatorStore:
    """Add the administrator and operator routes to the shared-store app."""
    from camoufox_pm.onboarding_server import SessionUpdateRequest

    operators = OperatorStore(store)

    def bearer(request: Request) -> str:
        value = request.headers.get("authorization", "")
        return value[7:] if value.startswith("Bearer ") else ""

    def admin(request: Request) -> None:
        if not hmac.compare_digest(bearer(request), api_token):
            raise HTTPException(401, "需要管理员密钥")

    def operator(request: Request) -> dict[str, Any]:
        found = operators.operator_for_token(bearer(request)) if bearer(request) else None
        if found is None:
            raise HTTPException(401, "登录已失效，请重新登录")
        return found

    @app.post("/v1/auth/login")
    async def login(request: LoginRequest):
        return operators.login(request)

    @app.post("/v1/auth/logout")
    async def logout(request: Request):
        operator(request)
        operators.logout(bearer(request))
        return {"ok": True}

    @app.get("/v1/me/bundle")
    async def bundle(request: Request):
        return operators.bundle(operator(request))

    @app.post("/v1/me/sessions")
    async def create_session(body: OperatorSessionCreate, request: Request):
        return operators.create_session(operator(request), body.account_id)

    @app.get("/v1/me/sessions/{session_id}")
    async def read_session(session_id: str, request: Request):
        return operators.owned_session(operator(request), session_id)

    @app.patch("/v1/me/sessions/{session_id}")
    async def update_session(session_id: str, body: OperatorSessionUpdate, request: Request):
        current = operators.owned_session(operator(request), session_id)
        if current["status"] in {"submitting", "submitted", "completed"}:
            values = body.model_dump(exclude_unset=True)
            if set(values) - {"profile_id"} and values.get("status") != "closed":
                raise HTTPException(409, "该记录已提交，不能再修改")
            if values.get("status") == "closed":
                values.pop("status")
            return store.update_session(session_id, SessionUpdateRequest(**values))
        return store.update_session(
            session_id, SessionUpdateRequest(**body.model_dump(exclude_unset=True))
        )

    @app.post("/v1/me/sessions/{session_id}/submit")
    async def submit(session_id: str, request: Request):
        return await submit_session(operators, store, operator(request), session_id)

    @app.get("/v1/me/sessions/{session_id}/result")
    async def result(session_id: str, request: Request):
        return await refresh_session(operators, store, operator(request), session_id)

    @app.get("/v1/admin/overview")
    async def overview(request: Request):
        admin(request)
        return operators.overview()

    @app.post("/v1/admin/operators")
    async def create_operator(body: OperatorCreate, request: Request):
        admin(request)
        return operators.create_operator(body)

    @app.patch("/v1/admin/operators/{operator_id}")
    async def update_operator(operator_id: str, body: OperatorUpdate, request: Request):
        admin(request)
        return operators.update_operator(operator_id, body)

    @app.post("/v1/admin/proxies/import")
    async def import_proxies(body: ProxyImport, request: Request):
        admin(request)
        listed = body.platform_proxies
        if listed is None:
            listed = await _platform_proxies(store, body)
        return operators.import_proxies(body, listed)

    @app.post("/v1/admin/proxies/assign")
    async def assign_proxies(body: ProxyAssign, request: Request):
        admin(request)
        return operators.assign_proxies(body)

    @app.delete("/v1/admin/proxies/{proxy_id}")
    async def delete_proxy(proxy_id: str, request: Request):
        admin(request)
        operators.delete_proxy(proxy_id)
        return {"deleted": True}

    @app.post("/v1/admin/accounts/import")
    async def import_accounts(body: AccountImport, request: Request):
        admin(request)
        return operators.import_accounts(body)

    @app.post("/v1/admin/accounts/{account_id}/unassign")
    async def unassign_account(account_id: str, request: Request):
        admin(request)
        operators.unassign_account(account_id)
        return {"unassigned": True}

    return operators
