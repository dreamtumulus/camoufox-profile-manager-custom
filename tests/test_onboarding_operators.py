"""Operators see and touch only what the administrator assigned to them."""

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from camoufox_pm import onboarding_operators
from camoufox_pm.core import onboarding as core_onboarding
from camoufox_pm.onboarding_server import OnboardingServerStore, create_app

ADMIN = {"Authorization": "Bearer " + "a" * 40}
PLATFORM = [
    {"id": "px-1", "host": "198.51.100.1", "port": 1080, "exit_ip": "203.0.113.1", "group": "g1"},
    {"id": "px-2", "host": "198.51.100.2", "port": 1080, "exit_ip": "203.0.113.2", "group": "g1"},
    {"id": "px-3", "host": "198.51.100.3", "port": 8080, "exit_ip": "203.0.113.3", "group": None},
]
LINES = "\n".join(
    [
        "socks5://user1:pass1@198.51.100.1:1080",
        "socks5://user2:pass2@198.51.100.2:1080",
        "http://user3:pa%3Ass3@198.51.100.3:8080",
        "socks5://nobody:nothing@198.51.100.99:1080",
    ]
)


@pytest.fixture
def client(tmp_path):
    store = OnboardingServerStore(tmp_path / "server.db", Fernet.generate_key().decode())
    store.set_credential("platform-credential-0123456789")
    with TestClient(create_app(store, "a" * 40)) as test_client:
        test_client.store = store
        yield test_client


def operator(client, name):
    created = client.post(
        "/v1/admin/operators", json={"username": name, "password": "correct-horse"}, headers=ADMIN
    )
    assert created.status_code == 200, created.text
    login = client.post("/v1/auth/login", json={"username": name, "password": "correct-horse"})
    assert login.status_code == 200, login.text
    return created.json()["id"], {"Authorization": f"Bearer {login.json()['token']}"}


def import_proxies(client, operator_id=None, capacity=2):
    response = client.post(
        "/v1/admin/proxies/import",
        headers=ADMIN,
        json={
            "node_id": "node-1", "node_name": "Node 1", "text": LINES, "capacity": capacity,
            "operator_id": operator_id, "platform_proxies": PLATFORM,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def import_accounts(client, operator_id, emails):
    response = client.post(
        "/v1/admin/accounts/import",
        headers=ADMIN,
        json={"operator_id": operator_id, "org_id": "org-1", "org_name": "Org", "emails": emails},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_proxy_import_matches_the_platform_list_and_never_lists_passwords(client):
    result = import_proxies(client)
    assert [proxy["platform_proxy_id"] for proxy in result["imported"]] == ["px-1", "px-2", "px-3"]
    assert result["unmatched"] == [{"line": 4, "server": "198.51.100.99:1080"}]
    listed = client.get("/v1/admin/overview", headers=ADMIN).json()["proxies"]
    assert all("password" not in proxy and proxy["has_password"] for proxy in listed)
    assert {proxy["exit_ip"] for proxy in listed} == {"203.0.113.1", "203.0.113.2", "203.0.113.3"}
    # Importing again updates in place instead of duplicating.
    import_proxies(client)
    assert len(client.get("/v1/admin/overview", headers=ADMIN).json()["proxies"]) == 3
    raw = client.store.path.read_bytes()
    assert b"pass1" not in raw and b"pa:ss3" not in raw


def test_emails_are_paired_with_the_operators_own_proxies_within_capacity(client):
    alice, alice_auth = operator(client, "alice")
    bob, bob_auth = operator(client, "bob")
    proxies = {proxy["platform_proxy_id"]: proxy["id"] for proxy in import_proxies(client)["imported"]}
    assign = lambda who, names: client.post(  # noqa: E731
        "/v1/admin/proxies/assign",
        headers=ADMIN,
        json={"proxy_ids": [proxies[name] for name in names], "operator_id": who},
    )
    assert assign(alice, ["px-1", "px-2"]).json() == {"assigned": 2}
    assert assign(bob, ["px-3"]).json() == {"assigned": 1}

    result = import_accounts(
        client, alice, ["a1@x.io", "A2@x.io", "a3@x.io", "a4@x.io", "a5@x.io", "bad", "a1@x.io"]
    )
    assert [item["email"] for item in result["placed"]] == ["a1@x.io", "a2@x.io", "a3@x.io", "a4@x.io"]
    assert [item["email"] for item in result["unplaced"]] == ["a5@x.io"]
    assert [item["email"] for item in result["invalid"]] == ["bad"]
    per_ip = {}
    for item in result["placed"]:
        per_ip[item["exit_ip"]] = per_ip.get(item["exit_ip"], 0) + 1
    assert per_ip == {"203.0.113.1": 2, "203.0.113.2": 2}
    import_accounts(client, bob, ["b1@x.io"])

    bundle = client.get("/v1/me/bundle", headers=alice_auth).json()
    assert {proxy["exit_ip"] for proxy in bundle["proxies"]} == {"203.0.113.1", "203.0.113.2"}
    assert {proxy["password"] for proxy in bundle["proxies"]} == {"pass1", "pass2"}
    assert [task["email"] for task in bundle["tasks"]] == ["a1@x.io", "a2@x.io", "a3@x.io", "a4@x.io"]
    other = client.get("/v1/me/bundle", headers=bob_auth).json()
    assert [task["email"] for task in other["tasks"]] == ["b1@x.io"]
    assert [proxy["password"] for proxy in other["proxies"]] == ["pa:ss3"]

    # A proxy that still carries someone's e-mails cannot be handed to another operator.
    assert assign(bob, ["px-1"]).status_code == 409


def test_operators_cannot_reach_admin_routes_or_each_others_records(client, monkeypatch):
    alice, alice_auth = operator(client, "alice")
    bob, bob_auth = operator(client, "bob")
    import_proxies(client, operator_id=alice)
    account = import_accounts(client, alice, ["a1@x.io"])["placed"][0]

    for path in ("/v1/admin/overview", "/v1/state", "/v1/credential"):
        assert client.get(path, headers=alice_auth).status_code == 401
    assert client.get("/v1/me/bundle", headers=ADMIN).status_code == 401
    assert client.get("/v1/me/bundle").status_code == 401

    assert client.post(
        "/v1/me/sessions", json={"account_id": account["id"]}, headers=bob_auth
    ).status_code == 404
    session = client.post("/v1/me/sessions", json={"account_id": account["id"]}, headers=alice_auth)
    assert session.status_code == 200, session.text
    session = session.json()
    assert session["operator_id"] == alice
    assert session["selection"]["proxy_id"].startswith("px-")
    assert session["selection"]["exit_ip"] == account["exit_ip"]
    assert session["selection"]["org_id"] == "org-1"
    path = f"/v1/me/sessions/{session['id']}"
    assert client.get(path, headers=bob_auth).status_code == 404
    assert client.patch(path, json={"status": "error"}, headers=bob_auth).status_code == 404
    assert client.post(f"{path}/submit", headers=bob_auth).status_code == 404
    # The operator cannot set server-owned fields such as the platform job.
    assert client.patch(path, json={"status": "completed"}, headers=alice_auth).status_code == 422

    submitted = []

    async def submit(self, sk, selection, email):
        submitted.append((self.token, sk, selection.proxy_id, selection.node_id, email))
        return {"jobId": "job-7", "sessionKey": sk}

    async def job(self, job_id):
        return {"status": "done", "jobId": job_id}

    monkeypatch.setattr(core_onboarding.OneDayClient, "submit", submit)
    monkeypatch.setattr(core_onboarding.OneDayClient, "job", job)
    assert client.post(f"{path}/submit", headers=alice_auth).status_code == 409
    updated = client.patch(
        path, json={"sk": "sk-ant-sid01-secret-value-1234", "status": "extracted"}, headers=alice_auth
    ).json()
    assert updated["sk"] is None and updated["sk_masked"].startswith("sk-ant-sid")
    done = client.post(f"{path}/submit", headers=alice_auth)
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "submitted" and done.json()["job_id"] == "job-7"
    assert done.json()["result"]["sessionKey"] == "[已隐藏]"
    assert submitted == [
        ("platform-credential-0123456789", "sk-ant-sid01-secret-value-1234",
         session["selection"]["proxy_id"], "node-1", "a1@x.io")
    ]
    assert client.post(f"{path}/submit", headers=alice_auth).status_code == 409
    assert client.get(f"{path}/result", headers=alice_auth).json()["status"] == "completed"
    # Finished work cannot be redone or taken back.
    assert client.post(
        "/v1/me/sessions", json={"account_id": account["id"]}, headers=alice_auth
    ).status_code == 409
    assert client.post(
        f"/v1/admin/accounts/{account['id']}/unassign", headers=ADMIN
    ).status_code == 409
    actions = [row["action"] for row in client.get("/v1/admin/overview", headers=ADMIN).json()["audit"]]
    assert {"operator.login", "session.create", "session.submit", "proxy.import"} <= set(actions)


def test_a_failed_platform_submit_is_recorded_and_can_be_retried(client, monkeypatch):
    alice, alice_auth = operator(client, "alice")
    import_proxies(client, operator_id=alice)
    account = import_accounts(client, alice, ["a1@x.io"])["placed"][0]
    session = client.post(
        "/v1/me/sessions", json={"account_id": account["id"]}, headers=alice_auth
    ).json()
    path = f"/v1/me/sessions/{session['id']}"
    client.patch(path, json={"sk": "sk-ant-sid01-secret-value-1234"}, headers=alice_auth)
    calls = []

    async def submit(self, sk, selection, email):
        calls.append(email)
        if len(calls) == 1:
            raise core_onboarding.OnboardingError("proxy_pool_empty", 502, "upstream_error")
        return {"jobId": "job-8"}

    monkeypatch.setattr(core_onboarding.OneDayClient, "submit", submit)
    failed = client.post(f"{path}/submit", headers=alice_auth)
    assert failed.status_code == 502 and "proxy_pool_empty" in failed.json()["detail"]
    assert client.get(path, headers=alice_auth).json()["status"] == "error"
    assert client.post(f"{path}/submit", headers=alice_auth).json()["job_id"] == "job-8"


def test_password_reset_and_suspension_end_existing_logins(client):
    alice, alice_auth = operator(client, "alice")
    assert client.get("/v1/me/bundle", headers=alice_auth).status_code == 200
    client.patch(f"/v1/admin/operators/{alice}", json={"password": "another-horse"}, headers=ADMIN)
    assert client.get("/v1/me/bundle", headers=alice_auth).status_code == 401
    assert client.post(
        "/v1/auth/login", json={"username": "alice", "password": "correct-horse"}
    ).status_code == 401
    fresh = client.post("/v1/auth/login", json={"username": "ALICE", "password": "another-horse"})
    token = {"Authorization": f"Bearer {fresh.json()['token']}"}
    client.patch(f"/v1/admin/operators/{alice}", json={"disabled": True}, headers=ADMIN)
    assert client.get("/v1/me/bundle", headers=token).status_code == 401
    assert client.post(
        "/v1/auth/login", json={"username": "alice", "password": "another-horse"}
    ).status_code == 401
    assert client.post(
        "/v1/admin/operators", json={"username": "alice", "password": "correct-horse"}, headers=ADMIN
    ).status_code == 409


def test_repeated_wrong_passwords_are_throttled(client, monkeypatch):
    operator(client, "alice")
    monkeypatch.setattr(onboarding_operators, "LOGIN_ATTEMPTS", 3)
    wrong = {"username": "alice", "password": "wrong-password"}
    assert [client.post("/v1/auth/login", json=wrong).status_code for _ in range(4)] == [
        401, 401, 401, 429,
    ]
    assert client.post(
        "/v1/auth/login", json={"username": "alice", "password": "correct-horse"}
    ).status_code == 429


def test_legacy_shared_token_routes_keep_working(client):
    assert client.get("/v1/state", headers=ADMIN).status_code == 200
    saved = client.post(
        "/v1/accounts", headers=ADMIN,
        json={"email": "old@x.io", "org_id": "org-1", "org_name": "Org"},
    )
    assert saved.status_code == 200 and saved.json()["operator_id"] is None


@pytest.fixture
def desktop(client, tmp_path, monkeypatch):
    """The operator's own installation, talking to the shared server in-process."""
    import httpx

    from camoufox_pm.config import get_settings

    monkeypatch.setenv("CPM_DB_PATH", str(tmp_path / "local" / "profiles.db"))
    monkeypatch.setenv("CPM_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("CPM_ONBOARDING_SERVER_URL", raising=False)
    monkeypatch.delenv("CPM_ONBOARDING_SERVER_TOKEN", raising=False)
    (tmp_path / "local").mkdir()
    get_settings.cache_clear()
    transport = httpx.ASGITransport(app=client.app)
    real = httpx.AsyncClient

    def through_app(**kwargs):
        return real(transport=transport, **{k: v for k, v in kwargs.items() if k != "transport"})

    monkeypatch.setattr(core_onboarding.httpx, "AsyncClient", through_app)
    yield core_onboarding
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_operator_signs_in_locally_and_receives_only_their_assignment(client, desktop):
    alice, _ = operator(client, "alice")
    import_proxies(client, operator_id=alice)
    account = import_accounts(client, alice, ["a1@x.io"])["placed"][0]

    assert desktop.remote_status()["configured"] is False
    with pytest.raises(desktop.OnboardingError) as wrong:
        await desktop.operator_login(
            desktop.OperatorLogin(
                username="alice", password="not-the-password", server_url="https://team.example"
            )
        )
    assert wrong.value.status_code == 409  # never 401: that means this app's own login

    status = await desktop.operator_login(
        desktop.OperatorLogin(
            username="alice", password="correct-horse", server_url="https://team.example"
        )
    )
    assert status["mode"] == "operator" and status["operator"]["username"] == "alice"
    stored = desktop._config_path().read_text()
    assert "correct-horse" not in stored and "operator_token" in stored

    remote = desktop.RemoteClient.configured()
    bundle = await remote.bundle()
    assert [task["email"] for task in bundle["tasks"]] == ["a1@x.io"]
    assert bundle["proxies"][0]["password"] == "pass1"
    session = await remote.start_task(account["id"])
    updated = await remote.update_session(
        session["id"], status="extracted", sk="sk-ant-sid01-secret-value-1234", job_id="forged"
    )
    assert updated["job_id"] is None and updated["sk"] is None
    with pytest.raises(desktop.OnboardingError, match="不能执行此操作"):
        await remote.credential()
    with pytest.raises(desktop.OnboardingError, match="不能执行此操作"):
        await remote.session_secret(session["id"])

    # The administrator suspends the account: the next call asks for a new sign-in.
    client.patch(f"/v1/admin/operators/{alice}", json={"disabled": True}, headers=ADMIN)
    with pytest.raises(desktop.OnboardingError, match="重新登录"):
        await remote.bundle()
    assert desktop.remote_status()["configured"] is False
    assert desktop.remote_status()["server_url"] == "https://team.example"
