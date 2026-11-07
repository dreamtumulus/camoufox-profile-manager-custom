import sqlite3

import pytest
from cryptography.fernet import Fernet

from camoufox_pm.core.onboarding import OnboardingError, Selection, matching_proxy, proxy_host
from camoufox_pm.onboarding_server import (
    AccountRequest,
    OnboardingServerStore,
    SessionCreateRequest,
    SessionUpdateRequest,
)


def test_proxy_host_variants():
    assert proxy_host("127.0.0.1:8080") == "127.0.0.1"
    assert proxy_host("http://127.0.0.1:8080") == "127.0.0.1"
    assert proxy_host("socks5://[::1]:1080") == "::1"


def test_matching_proxy_requires_exactly_one_consistent_proxy():
    proxies = [
        {"id": "a", "name": "A", "server": "10.0.0.1:8080", "last_check": None},
        {"id": "b", "name": "B", "server": "10.0.0.2:8080", "last_check": None},
    ]
    assert matching_proxy(proxies, "10.0.0.1")["id"] == "a"
    with pytest.raises(OnboardingError, match="没有出口 IP"):
        matching_proxy(proxies, "10.0.0.3")
    with pytest.raises(OnboardingError, match="多个本地代理"):
        matching_proxy([proxies[0], {**proxies[0], "id": "c", "name": "C"}], "10.0.0.1")


def test_matching_proxy_rejects_failed_or_mismatched_check():
    failed = {
        "id": "a",
        "name": "A",
        "server": "10.0.0.1:8080",
        "last_check": {"reachable": False, "error": "timeout"},
    }
    with pytest.raises(OnboardingError, match="上次检测失败"):
        matching_proxy([failed], "10.0.0.1")
    mismatch = {
        **failed,
        "last_check": {"reachable": True, "ip": "10.0.0.9"},
    }
    with pytest.raises(OnboardingError, match="不一致"):
        matching_proxy([mismatch], "10.0.0.1")


def test_shared_store_persists_and_encrypts_secrets(tmp_path):
    path = tmp_path / "shared.db"
    store = OnboardingServerStore(path, Fernet.generate_key().decode())
    store.set_credential("1dk_onb_test_secret")
    store.set_preferences({"node_id": "node-a", "exit_ip": "10.0.0.1"})
    account = store.save_account(
        AccountRequest(email="Person@Example.com", org_id="org-a", org_name="Org A")
    )
    assert account["email"] == "person@example.com"
    session = store.create_session(
        SessionCreateRequest(
            account_id=account["id"],
            email=account["email"],
            selection=Selection(
                node_id="node-a",
                node_name="Node A",
                org_id="org-a",
                org_name="Org A",
                proxy_id="proxy-a",
                exit_ip="10.0.0.1",
            ).model_dump(),
        )
    )
    updated = store.update_session(
        session["id"],
        SessionUpdateRequest(sk="sk-ant-test-secret-value", status="extracted", profile_id="profile-a"),
    )
    assert updated["sk"] is None
    assert updated["sk_masked"].endswith("alue")
    assert store.session(session["id"], reveal_sk=True)["sk"] == "sk-ant-test-secret-value"
    assert store.credential() == "1dk_onb_test_secret"
    assert store.preferences()["exit_ip"] == "10.0.0.1"

    raw = sqlite3.connect(path)
    credential = raw.execute("SELECT token_encrypted FROM credential").fetchone()[0]
    sk = raw.execute("SELECT sk_encrypted FROM sessions").fetchone()[0]
    assert "1dk_onb_test_secret" not in credential
    assert "sk-ant-test-secret-value" not in sk
