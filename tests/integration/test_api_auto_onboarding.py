"""Auto-onboarding parameters over the API: the token never comes back, and nothing is started."""

from datetime import datetime, timezone

import pytest

from camoufox_pm.api import dependencies
from camoufox_pm.config import get_settings
from camoufox_pm.core import auth
from camoufox_pm.core.models import ProxyCheckRecord
from tests.support import oneday
from tests.support.oneday import SECOND, TOKEN

BASE = "/api/v1/auto-onboarding"
PASSWORD = "strong-test-password"


@pytest.fixture
def remote(monkeypatch):
    return oneday.install(monkeypatch)


async def save_token(client, token=TOKEN):
    return await client.put(f"{BASE}/connection", json={"token": token})


async def account(client, group, name, email, exit_ip="203.0.113.18"):
    """A profile in a group, with an email address and a proxy that was checked."""
    created = (await client.post("/api/v1/profiles", json={"name": name, "group": group})).json()
    storage = dependencies.get_storage_manager()
    if exit_ip:
        await storage.set_proxy_check(
            created["id"],
            ProxyCheckRecord(checked_at=datetime.now(timezone.utc), reachable=True, ip=exit_ip),
        )
    if email:
        current = (await client.get(f"/api/v1/profiles/{created['id']}")).json()
        await storage.db.set_profile_email(created["id"], email, current["row_version"])
    return created


async def group(client, name):
    return (await client.post("/api/v1/groups", json={"name": name})).json()["id"]


# --- the token ---------------------------------------------------------------


async def test_nothing_is_saved_at_first(client, remote):
    connection = (await client.get(f"{BASE}/connection")).json()

    assert connection["saved"] is False
    assert connection["platform"] == "https://1dayapi.com"
    presets = (await client.get(f"{BASE}/presets")).json()
    assert presets["options"] is None and presets["options_error"] is None
    assert presets["version"] == 0
    assert remote.asked == [], "without a token the platform is not asked"


@pytest.mark.parametrize("token", ["sk-ant-something", "1dk_onb_with space", "   "])
async def test_what_is_not_an_onboarder_token_is_refused_unasked(client, remote, token):
    response = await save_token(client, token)

    assert response.status_code == 422
    assert remote.asked == []
    assert (await client.get(f"{BASE}/connection")).json()["saved"] is False


async def test_a_token_the_platform_refuses_is_not_saved(client, remote):
    response = await save_token(client, "1dk_onb_revoked")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "onboard_token_refused"
    assert (await client.get(f"{BASE}/connection")).json()["saved"] is False


async def test_an_accepted_token_is_saved_and_never_comes_back(client, remote):
    response = await save_token(client, f"  {TOKEN}\n")

    assert response.status_code == 200
    saved = response.json()
    assert saved["replaced"] is False
    assert saved["connection"]["preview"] == "1dk_onb_demo…only"
    assert saved["connection"]["label"] == "Shift A"
    assert saved["connection"]["refused"] is False
    presets = await client.get(f"{BASE}/presets")
    for answer in (response, presets, await client.get(f"{BASE}/connection")):
        assert TOKEN not in answer.text
    assert "proxy.internal" not in presets.text
    connection = dependencies.get_storage_manager().db._connection
    audited = connection.execute(
        "SELECT action, object_id FROM audit_events WHERE action LIKE '%onboard_token'"
    ).fetchall()
    assert [tuple(row) for row in audited] == [("save_onboard_token", None)]
    everything = " ".join(
        str(tuple(row)) for row in connection.execute("SELECT * FROM audit_events")
    )
    assert TOKEN not in everything


async def test_a_refused_replacement_leaves_the_saved_token(client, remote):
    await save_token(client)

    response = await save_token(client, "1dk_onb_revoked")

    assert response.status_code == 409
    connection = (await client.get(f"{BASE}/connection")).json()
    assert (connection["preview"], connection["refused"]) == ("1dk_onb_demo…only", False)


async def test_replacing_the_token_asks_again_for_what_the_platform_offered(client, remote):
    await save_token(client)
    chosen = {
        "node_id": "direct-1",
        "proxy_mode": "pool",
        "proxy_id": "p18",
        "org_id": "org-a",
        "domain": "example.com",
        "note": "keep",
        "scope_group": "g1",
    }
    await client.put(f"{BASE}/presets", json=chosen)

    again = (await save_token(client)).json()
    assert again["replaced"] is False, "the same token is not a replacement"
    assert (await client.get(f"{BASE}/presets")).json()["preset"]["org_id"] == "org-a"

    replaced = (await save_token(client, SECOND)).json()
    assert replaced["replaced"] is True
    assert replaced["connection"]["label"] == "Shift B"
    preset = (await client.get(f"{BASE}/presets")).json()["preset"]
    assert (preset["node_id"], preset["proxy_id"], preset["org_id"], preset["domain"]) == (
        None,
        None,
        None,
        None,
    )
    assert (preset["proxy_mode"], preset["note"], preset["scope_group"]) == ("pool", "keep", "g1")


# --- what the token may use, and what is chosen --------------------------------


async def test_the_options_are_read_anew_with_the_saved_choices(client, remote):
    await save_token(client)
    remote.asked.clear()

    presets = (await client.get(f"{BASE}/presets")).json()

    assert sorted(path.split("?")[0] for path in remote.asked) == [
        "/api/onboard/me",
        "/api/onboard/node-groups",
        "/api/onboard/proxies",
    ]
    assert presets["options"]["nodes"] == [
        {"id": "direct-1", "name": "direct-1", "online": True, "used": 13, "capacity": 20}
    ]
    assert presets["options"]["pool"]["groups"] == [{"name": "UK", "assignable": 2}]
    assert presets["options"]["pool"]["proxies"][0] == {
        "id": "p18",
        "exit_ip": "203.0.113.18",
        "group": "UK",
        "slots": 2,
    }
    assert presets["preset"]["proxy_mode"] == "row"


async def test_a_refresh_the_platform_refuses_marks_the_token_and_keeps_it(client, remote):
    await save_token(client)
    await client.put(f"{BASE}/presets", json={"node_id": "direct-1"})
    del remote.accepted[TOKEN]

    presets = await client.get(f"{BASE}/presets")

    assert presets.status_code == 200
    body = presets.json()
    assert body["options"] is None
    assert body["connection"]["refused"] is True
    assert body["connection"]["preview"] == "1dk_onb_demo…only"
    assert body["preset"]["node_id"] == "direct-1", "the choices are still there"
    check = await client.post(f"{BASE}/preflight")
    assert (check.status_code, check.json()["error"]["code"]) == (409, "onboard_token_refused")

    assert (await save_token(client, SECOND)).json()["connection"]["refused"] is False
    assert (await client.get(f"{BASE}/presets")).json()["options"] is not None


async def test_a_platform_that_cannot_be_reached_is_said_and_refuses_nothing(client, remote):
    await save_token(client)
    remote.down = True

    body = (await client.get(f"{BASE}/presets")).json()

    assert body["options"] is None
    assert body["options_error"] == "oneday_unavailable"
    assert body["connection"]["refused"] is False
    assert (await client.post(f"{BASE}/preflight")).status_code == 503


async def test_choices_are_saved_as_made_and_held_to_their_shape(client, remote):
    first = await client.put(f"{BASE}/presets", json={"proxy_mode": "pool", "note": " batch 3 "})
    assert first.status_code == 200
    assert first.json() == {
        "version": 1,
        "preset": {
            "node_id": None,
            "proxy_mode": "pool",
            "proxy_group": None,
            "proxy_id": None,
            "org_id": None,
            "domain": None,
            "org_manual": False,
            "org_uuid": None,
            "note": "batch 3",
            "concurrency_limit": None,
            "rpm_limit": None,
            "scope_group": None,
        },
    }
    assert (await client.put(f"{BASE}/presets", json={"rpm_limit": 20})).json()["version"] == 2

    for unfit in (
        {"rpm_limit": 100001},
        {"concurrency_limit": -1},
        {"proxy_mode": "own"},
        {"group_id": "grp-a"},
    ):
        refused = await client.put(f"{BASE}/presets", json=unfit)
        assert refused.status_code == 422, unfit
    assert (await client.get(f"{BASE}/presets")).json()["version"] == 2
    assert remote.asked == [], "saving a choice does not ask the platform"


# --- which profiles ------------------------------------------------------------


async def test_only_groups_with_an_address_to_onboard_are_offered(client, remote):
    uk, empty, bare = (
        await group(client, "UK"),
        await group(client, "Empty"),
        await group(client, "Bare"),
    )
    first = await account(client, uk, "UK 16", "user16@example.com", "203.0.113.16")
    second = await account(client, uk, "UK 17", "user17@example.com", "203.0.113.17")
    await account(client, uk, "UK no address", None)
    await account(client, bare, "Bare", None)
    await account(client, None, "Ungrouped", "loose@example.com")
    assert empty

    scopes = (await client.get(f"{BASE}/scopes")).json()

    assert scopes == {
        "groups": [{"id": uk, "name": "UK", "pending": 2, "onboarded": 0}],
        "rows": None,
    }
    rows = (await client.get(f"{BASE}/scopes", params={"group": uk})).json()["rows"]
    assert [row["profile_id"] for row in rows] == [second["id"], first["id"]], "newest first"
    assert rows[0] == {
        "profile_id": second["id"],
        "name": "UK 17",
        "email": "user17@example.com",
        "exit_ip": "203.0.113.17",
        "exit_checked": True,
        "exit_reachable": True,
        "onboarded": False,
        "can_launch": True,
        "authorization": None,
    }


# --- whether the choices would do ----------------------------------------------


async def test_the_check_needs_a_token(client, remote):
    response = await client.post(f"{BASE}/preflight")

    assert (response.status_code, response.json()["error"]["code"]) == (
        409,
        "onboard_token_missing",
    )


async def test_the_check_names_what_is_missing(client, remote):
    await save_token(client)
    await client.put(f"{BASE}/presets", json={"proxy_mode": "pool", "org_id": "org-b"})

    check = (await client.post(f"{BASE}/preflight")).json()

    assert check["ok"] is False
    assert [(issue["code"], issue["field"]) for issue in check["issues"]] == [
        ("node_missing", "node_id"),
        ("domain_missing", "domain"),
        ("scope_missing", "scope_group"),
    ]
    assert check["rows"] == []


async def test_the_check_says_of_every_profile_whether_it_would_be_onboarded(client, remote):
    await save_token(client)
    uk = await group(client, "UK")
    unknown = await account(client, uk, "UK 15", "user15@example.com", None)
    outside = await account(client, uk, "UK 16", "user16@example.com", "203.0.113.16")
    other = await account(client, uk, "UK 17", "user17@example.net", "203.0.113.17")
    ready = await account(client, uk, "UK 18", "User18@Example.com", "203.0.113.18")
    preset = {"node_id": "direct-1", "org_id": "org-a", "domain": "example.com", "scope_group": uk}
    version = (await client.put(f"{BASE}/presets", json=preset)).json()["version"]

    check = (await client.post(f"{BASE}/preflight")).json()

    assert (check["ok"], check["issues"], check["version"]) == (True, [], version)
    assert (check["ready"], check["blocked"]) == (1, 3)
    assert [(row["profile_id"], row["status"], row["proxy_id"]) for row in check["rows"]] == [
        (ready["id"], "ready", "p18"),
        (other["id"], "domain_mismatch", None),
        (outside["id"], "exit_not_in_pool", None),
        (unknown["id"], "exit_unknown", None),
    ]

    await client.put(f"{BASE}/presets", json={**preset, "proxy_mode": "auto", "proxy_group": "UK"})
    automatic = (await client.post(f"{BASE}/preflight")).json()
    assert (automatic["ok"], automatic["ready"], automatic["blocked"]) == (True, 3, 1)


async def test_a_group_with_nothing_ready_does_not_pass(client, remote):
    await save_token(client)
    uk = await group(client, "UK")
    await account(client, uk, "UK 16", "user16@example.com", "203.0.113.16")
    preset = {"node_id": "direct-1", "org_id": "org-a", "domain": "example.com"}

    await client.put(f"{BASE}/presets", json={**preset, "scope_group": uk})
    nothing = (await client.post(f"{BASE}/preflight")).json()
    assert (nothing["ok"], [issue["code"] for issue in nothing["issues"]]) == (
        False,
        ["no_ready_rows"],
    )

    await client.put(f"{BASE}/presets", json={**preset, "scope_group": "gone"})
    gone = (await client.post(f"{BASE}/preflight")).json()
    assert [issue["code"] for issue in gone["issues"]] == ["scope_unavailable"]


# --- who may --------------------------------------------------------------------


async def sign_in(client, role, monkeypatch):
    monkeypatch.delenv("CPM_API_KEY", raising=False)
    get_settings.cache_clear()
    storage = dependencies.get_storage_manager()
    await storage.create_user(auth.new_user_id(), "admin", auth.hash_password(PASSWORD))
    await storage.create_user(auth.new_user_id(), "member", auth.hash_password(PASSWORD), role=role)
    login = await client.post(
        "/api/v1/auth/login", json={"username": "member", "password": PASSWORD}
    )
    assert login.status_code == 200


async def test_a_viewer_has_no_business_with_the_token(client, remote, monkeypatch):
    await sign_in(client, "viewer", monkeypatch)

    for method, path in (
        ("get", "connection"),
        ("put", "connection"),
        ("get", "presets"),
        ("put", "presets"),
        ("get", "scopes"),
        ("post", "preflight"),
    ):
        body = {"json": {"token": TOKEN}} if method == "put" else {}
        response = await getattr(client, method)(f"{BASE}/{path}", **body)
        assert response.status_code == 403, f"{method} {path}"
    assert remote.asked == []


async def test_an_operator_uses_it_and_sees_only_granted_profiles(client, remote, monkeypatch):
    uk = await group(client, "UK")
    mine = await account(client, uk, "UK 18", "user18@example.com")
    await account(client, uk, "UK 17", "user17@example.com", "203.0.113.17")
    await sign_in(client, "operator", monkeypatch)
    await dependencies.get_storage_manager().set_profile_grant("member", mine["id"])

    assert (await save_token(client)).status_code == 200
    scopes = (await client.get(f"{BASE}/scopes", params={"group": uk})).json()
    assert scopes["groups"] == [{"id": uk, "name": "UK", "pending": 1, "onboarded": 0}]
    assert [row["profile_id"] for row in scopes["rows"]] == [mine["id"]]
    preset = {"node_id": "direct-1", "org_id": "org-a", "domain": "example.com", "scope_group": uk}
    assert (await client.put(f"{BASE}/presets", json=preset)).status_code == 200
    check = (await client.post(f"{BASE}/preflight")).json()
    assert (check["ok"], [row["profile_id"] for row in check["rows"]]) == (True, [mine["id"]])


async def test_the_legacy_prefix_serves_the_same_routes(client, remote):
    assert (await client.get("/api/auto-onboarding/connection")).json()["saved"] is False
