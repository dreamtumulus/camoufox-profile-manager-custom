"""Authorizing a row over the API: a link is made and opened, a code is handed on.

Only what the platform reports as done for the row's own account makes it
onboarded. The platform is a stand-in; no test here reaches the network.
"""

import asyncio
import json

import pytest

from camoufox_pm.api import dependencies
from tests.integration.test_api_auto_onboarding import BASE, account, group, save_token, sign_in
from tests.support import oneday
from tests.support.oneday import LINK, SECOND, TOKEN

CHOICES = {"node_id": "direct-1", "org_id": "org-a", "domain": "example.com"}


@pytest.fixture
def remote(monkeypatch):
    return oneday.install(monkeypatch)


class Browsers:
    """Which profiles have a browser running, and what was opened in them."""

    def __init__(self) -> None:
        self.running: set[str] = set()
        self.opened: list[tuple[str, str]] = []
        self.window_gone = False
        # Whether a tab can be had in a window that is there, or only a new window.
        self.where = "tab"

    def is_running(self, profile_id: str) -> bool:
        return profile_id in self.running

    async def open_page(self, profile_id: str, url: str) -> str | None:
        if self.window_gone or profile_id not in self.running:
            return None
        self.opened.append((profile_id, url))
        return self.where


@pytest.fixture
def browsers(client, monkeypatch):
    stand_in = Browsers()
    sessions = dependencies.get_profile_manager().browser_sessions
    monkeypatch.setattr(sessions, "is_running", stand_in.is_running)
    monkeypatch.setattr(sessions, "open_page", stand_in.open_page)
    return stand_in


@pytest.fixture
async def uk(client, remote):
    """A saved token, finished choices, and a group to onboard."""
    await save_token(client)
    group_id = await group(client, "UK")
    await client.put(f"{BASE}/presets", json={**CHOICES, "scope_group": group_id})
    return group_id


def job(profile):
    return f"{BASE}/rows/{profile['id']}/job"


async def link_for(client, browsers, profile):
    """Run the row's browser and have a link made for it."""
    browsers.running.add(profile["id"])
    response = await client.post(job(profile))
    assert response.status_code == 200, response.text
    return response.json()


async def counts(client):
    groups = (await client.get(f"{BASE}/scopes")).json()["groups"]
    return [(item["pending"], item["onboarded"]) for item in groups]


async def refused(client, profile, code, remote):
    """A link is refused with this code, and the platform was asked for no job."""
    response = await client.post(job(profile))
    assert (response.status_code, response.json()["error"]["code"]) == (409, code), response.text
    assert remote.created == [] and remote.jobs == {}
    return response


# --- what stands in the way of a link ------------------------------------------------


async def test_no_link_without_a_token(client, remote, browsers):
    uk_id = await group(client, "UK")
    row = await account(client, uk_id, "UK 18", "user18@example.com")
    browsers.running.add(row["id"])

    await refused(client, row, "onboard_token_missing", remote)
    assert remote.asked == [], "the platform is not asked at all"


async def test_no_link_while_the_browser_is_not_running(client, remote, browsers, uk):
    row = await account(client, uk, "UK 18", "user18@example.com")

    await refused(client, row, "browser_not_running", remote)
    assert browsers.opened == []


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"node_id": None}, "node_missing"),
        ({"org_id": "org-b", "domain": None}, "domain_missing"),
        ({"proxy_mode": "pool"}, "proxy_missing"),
        ({"proxy_mode": "pool", "proxy_id": "gone"}, "proxy_unavailable"),
    ],
)
async def test_no_link_while_the_choices_are_unfinished(
    client, remote, browsers, uk, changes, code
):
    row = await account(client, uk, "UK 18", "user18@example.com")
    browsers.running.add(row["id"])
    await client.put(f"{BASE}/presets", json={**CHOICES, "scope_group": uk, **changes})

    await refused(client, row, code, remote)


@pytest.mark.parametrize(
    ("email", "exit_ip", "code"),
    [
        ("user16@example.net", "203.0.113.18", "domain_mismatch"),
        ("user16@example.com", "203.0.113.16", "exit_not_in_pool"),
        ("user16@example.com", None, "exit_unknown"),
    ],
)
async def test_no_link_for_a_row_the_choices_would_not_onboard(
    client, remote, browsers, uk, email, exit_ip, code
):
    row = await account(client, uk, "UK 16", email, exit_ip)
    browsers.running.add(row["id"])

    await refused(client, row, code, remote)


async def test_no_link_for_a_profile_without_an_address_or_outside_every_group(
    client, remote, browsers, uk
):
    bare = await account(client, uk, "UK bare", None)
    loose = await account(client, None, "Loose", "loose@example.com")
    browsers.running.update({bare["id"], loose["id"]})

    for profile in (bare, loose):
        await refused(client, profile, "row_unavailable", remote)
    missing = await client.post(f"{BASE}/rows/nope/job")
    assert missing.json()["error"]["code"] == "row_unavailable", "a profile that is not there"


# --- a link is made, and opened in the row's browser ---------------------------------


async def test_a_link_is_made_with_the_choices_and_opened_in_the_rows_browser(
    client, remote, browsers, uk
):
    row = await account(client, uk, "UK 18", "User18@Example.com")
    browsers.running.add(row["id"])

    response = await client.post(job(row))

    assert response.status_code == 200
    made = response.json()
    assert remote.created == [
        {
            "method": "auth_url",
            "nodeId": "direct-1",
            "name": "User18@Example.com",
            "proxyId": "p18",
            "orgId": "org-a",
            "domain": "example.com",
        }
    ]
    assert made["authorize_url"] == LINK + "state-1"
    assert browsers.opened == [(row["id"], LINK + "state-1")], "opened once, in this row's browser"
    assert (made["opened"], made["opened_in"], made["onboarded"]) == (True, "tab", False)
    assert made["authorization"] == {
        "status": "pending",
        "outcome": None,
        "error_code": None,
        "account_email": None,
        "node": "direct-1",
        "proxy": "203.0.113.18",
        "expires_at": "2030-01-01T00:30:00Z",
        "cooldown_until": None,
        "created_at": made["authorization"]["created_at"],
    }
    assert TOKEN not in response.text
    listed = (await client.get(f"{BASE}/scopes", params={"group": uk})).json()["rows"][0]
    assert listed["authorization"]["status"] == "pending"
    assert "authorize_url" not in listed["authorization"], "the list never carries a link"


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"proxy_mode": "auto"}, {"proxyId": "auto"}),
        ({"proxy_mode": "auto", "proxy_group": "UK"}, {"proxyId": "auto", "proxyGroup": "UK"}),
        ({"proxy_mode": "pool", "proxy_id": "p17"}, {"proxyId": "p17"}),
        (
            {"note": "batch 3", "concurrency_limit": 0, "rpm_limit": 20},
            {"proxyId": "p18", "note": "batch 3", "concurrencyLimit": 0, "rpmLimit": 20},
        ),
    ],
)
async def test_the_job_is_asked_for_as_chosen(client, remote, browsers, uk, changes, expected):
    row = await account(client, uk, "UK 18", "user18@example.com")
    await client.put(f"{BASE}/presets", json={**CHOICES, "scope_group": uk, **changes})

    made = await link_for(client, browsers, row)

    asked = remote.created[0]
    assert {key: asked[key] for key in expected} == expected
    assert ("proxyGroup" in asked) == ("proxyGroup" in expected)
    assert "groupId" not in asked, "an onboarder token cannot name an account group"
    wanted = {"auto": "auto", "p17": "203.0.113.17", "p18": "203.0.113.18"}[expected["proxyId"]]
    if expected.get("proxyGroup"):
        wanted = "auto:UK"
    assert made["authorization"]["proxy"] == wanted


async def test_a_token_without_organization_profiles_names_none_or_a_uuid(
    client, remote, browsers, uk
):
    remote.orgs = []
    row = await account(client, uk, "UK 18", "user18@example.com")
    other = await account(client, uk, "UK 17", "user17@example.com", "203.0.113.17")
    uuid = "0f8fad5b-d9cb-469f-a165-70867728950e"

    await client.put(f"{BASE}/presets", json={"node_id": "direct-1", "scope_group": uk})
    await link_for(client, browsers, row)
    await client.put(
        f"{BASE}/presets",
        json={"node_id": "direct-1", "scope_group": uk, "org_manual": True, "org_uuid": uuid},
    )
    await link_for(client, browsers, other)

    assert [("orgId" in asked, asked.get("orgUuid")) for asked in remote.created] == [
        (False, None),
        (False, uuid),
    ]


async def test_asking_again_gives_the_same_link_and_opens_nothing_more(
    client, remote, browsers, uk
):
    row = await account(client, uk, "UK 18", "user18@example.com")
    first = await link_for(client, browsers, row)

    again = (await client.post(job(row))).json()
    asked = (await client.get(job(row))).json()

    assert len(remote.jobs) == 1
    assert again["authorize_url"] == asked["authorize_url"] == first["authorize_url"]
    assert (again["opened"], asked["opened"]) == (None, None), "only a link just made is opened"
    assert (again["opened_in"], asked["opened_in"]) == (None, None)
    assert len(browsers.opened) == 1


async def test_two_requests_at_once_make_one_job(client, remote, browsers, uk):
    row = await account(client, uk, "UK 18", "user18@example.com")
    browsers.running.add(row["id"])

    answers = await asyncio.gather(client.post(job(row)), client.post(job(row)))

    assert [answer.status_code for answer in answers] == [200, 200]
    assert len(remote.jobs) == 1 and len(browsers.opened) == 1
    assert {answer.json()["authorize_url"] for answer in answers} == {LINK + "state-1"}


async def test_a_window_that_went_away_leaves_the_link_to_be_copied(client, remote, browsers, uk):
    row = await account(client, uk, "UK 18", "user18@example.com")
    browsers.window_gone = True

    made = await link_for(client, browsers, row)

    assert (made["opened"], made["opened_in"]) == (False, None)
    assert made["authorization"]["status"] == "pending"
    assert made["authorize_url"] == LINK + "state-1", "the authorization stands; the link is shown"


async def test_a_link_that_only_a_new_window_could_show_says_so(client, remote, browsers, uk):
    row = await account(client, uk, "UK 18", "user18@example.com")
    browsers.where = "window"

    made = await link_for(client, browsers, row)

    assert (made["opened"], made["opened_in"]) == (True, "window")
    assert browsers.opened == [(row["id"], LINK + "state-1")]


@pytest.mark.parametrize(
    ("answer", "code"),
    [
        ((400, "proxy_pool_empty", "proxy_full: exit has no free slot"), "platform_proxy_full"),
        ((400, "proxy_pool_empty", "No compatible verified proxy"), "platform_proxy_pool_empty"),
        ((400, "node_at_capacity", "Node capacity reached (20/20)"), "platform_node_at_capacity"),
        ((400, "invalid_request", "name is too long (max 80)"), "platform_invalid_request"),
    ],
)
async def test_a_refusal_by_the_platform_is_said_and_leaves_nothing_behind(
    client, remote, browsers, uk, answer, code
):
    row = await account(client, uk, "UK 18", "user18@example.com")
    other = await account(client, uk, "UK 17", "user17@example.com", "203.0.113.17")
    browsers.running.add(row["id"])
    remote.refuse_job = answer

    response = await client.post(job(row))

    assert (response.status_code, response.json()["error"]["code"]) == (409, code)
    assert browsers.opened == []
    rows = (await client.get(f"{BASE}/scopes", params={"group": uk})).json()["rows"]
    assert [item["authorization"] for item in rows] == [None, None]
    assert (await link_for(client, browsers, other))["authorization"]["status"] == "pending"
    assert (await link_for(client, browsers, row))["authorization"]["status"] == "pending"


# --- the code ---------------------------------------------------------------------------


async def test_another_rows_code_is_refused_and_onboards_nobody(client, remote, browsers, uk):
    row = await account(client, uk, "UK 18", "user18@example.com")
    other = await account(client, uk, "UK 17", "user17@example.com", "203.0.113.17")
    await link_for(client, browsers, row)
    await link_for(client, browsers, other)

    response = await client.post(f"{job(row)}/code", json={"code": remote.code_for("job-2")})

    assert (response.status_code, response.json()["error"]["code"]) == (422, "callback_invalid")
    assert remote.codes == []
    still = (await client.get(job(row))).json()
    assert (still["authorization"]["status"], still["onboarded"]) == ("pending", False)
    assert still["authorize_url"] == LINK + "state-1", "it goes on waiting"
    assert await counts(client) == [(2, 0)]


async def test_an_accepted_code_is_not_success_until_the_platform_is_done(
    client, remote, browsers, uk
):
    row = await account(client, uk, "UK 18", "User18@Example.com")
    await link_for(client, browsers, row)
    code = remote.code_for("job-1")

    handed = await client.post(f"{job(row)}/code", json={"code": f"  {code}\n"})

    assert handed.status_code == 200
    assert remote.codes == [("job-1", code)], "handed on as pasted, trimmed"
    working = handed.json()
    assert (working["authorization"]["status"], working["onboarded"]) == ("working", False)
    assert working["authorize_url"] is None
    assert await counts(client) == [(1, 0)]

    remote.finish("job-1", accountEmail="user18@example.com", cooldownUntil="2030-01-01T04:00:00Z")
    done = (await client.get(job(row))).json()

    assert done["onboarded"] is True
    assert done["authorization"]["status"] == "done"
    assert done["authorization"]["outcome"] == "onboarded"
    assert done["authorization"]["account_email"] == "user18@example.com"
    assert done["authorization"]["cooldown_until"] == "2030-01-01T04:00:00Z"
    assert (await client.get(f"{BASE}/scopes")).json()["groups"] == [], "nothing is left to onboard"
    listed = (await client.get(f"{BASE}/scopes", params={"group": uk})).json()["rows"][0]
    assert listed["onboarded"] is True


async def test_an_onboarded_row_gets_no_further_link(client, remote, browsers, uk):
    row = await account(client, uk, "UK 18", "user18@example.com")
    await link_for(client, browsers, row)
    await client.post(f"{job(row)}/code", json={"code": remote.code_for("job-1")})
    remote.finish("job-1", accountEmail="user18@example.com")
    await client.get(job(row))

    again = await client.post(job(row))

    assert (again.status_code, again.json()["error"]["code"]) == (409, "onboarded")
    assert len(remote.jobs) == 1 and len(browsers.opened) == 1
    check = (await client.post(f"{BASE}/preflight")).json()
    assert [item["code"] for item in check["issues"]] == ["scope_unavailable"]


async def test_what_finished_while_nobody_looked_is_learned_when_the_list_is_read(
    client, remote, browsers, uk
):
    row = await account(client, uk, "UK 18", "user18@example.com")
    await account(client, uk, "UK 17", "user17@example.com", "203.0.113.17")
    await link_for(client, browsers, row)
    await client.post(f"{job(row)}/code", json={"code": remote.code_for("job-1")})
    remote.finish("job-1", accountEmail="user18@example.com")

    assert await counts(client) == [(1, 1)]


async def test_a_code_whose_answer_was_lost_is_not_sent_twice(client, remote, browsers, uk):
    row = await account(client, uk, "UK 18", "user18@example.com")
    await link_for(client, browsers, row)
    remote.lose_answer = True

    handed = await client.post(f"{job(row)}/code", json={"code": remote.code_for("job-1")})

    assert handed.status_code == 200
    assert handed.json()["authorization"]["status"] == "working"
    assert len(remote.codes) == 1
    late = await client.post(f"{job(row)}/code", json={"code": remote.code_for("job-1")})
    assert (late.status_code, late.json()["error"]["code"]) == (409, "authorization_not_waiting")
    assert len(remote.codes) == 1


@pytest.mark.parametrize("code", ["", "x" * 4001])
async def test_a_code_out_of_shape_is_refused_unasked(client, remote, browsers, uk, code):
    row = await account(client, uk, "UK 18", "user18@example.com")
    await link_for(client, browsers, row)

    assert (await client.post(f"{job(row)}/code", json={"code": code})).status_code == 422
    assert remote.codes == []


# --- how it can end otherwise ---------------------------------------------------------


async def finished(client, remote, browsers, row, **result):
    await link_for(client, browsers, row)
    await client.post(f"{job(row)}/code", json={"code": remote.code_for(remote.last_job())})
    remote.finish(remote.last_job(), **result)
    return (await client.get(job(row))).json()


async def test_an_account_that_was_already_there_and_authorized_again_adds_nothing(
    client, remote, browsers, uk
):
    row = await account(client, uk, "UK 18", "user18@example.com")

    done = await finished(
        client, remote, browsers, row, accountEmail="user18@example.com", reused=True
    )

    assert (done["authorization"]["outcome"], done["onboarded"]) == ("reused", True)


async def test_another_account_than_the_rows_is_said_and_not_counted(client, remote, browsers, uk):
    row = await account(client, uk, "UK 18", "user18@example.com")

    done = await finished(client, remote, browsers, row, accountEmail="another@example.com")

    assert done["authorization"]["status"] == "done"
    assert done["authorization"]["outcome"] == "mismatch"
    assert done["authorization"]["account_email"] == "another@example.com"
    assert done["onboarded"] is False
    assert await counts(client) == [(1, 0)]
    assert (await link_for(client, browsers, row))["authorize_url"] == LINK + "state-2"


@pytest.mark.parametrize(
    ("error", "code"),
    [
        ("account_exists: account 7 already holds this identity", "account_exists"),
        ("domain_mismatch: account email is outside example.com", "domain_mismatch"),
        ("invalid_grant: code was already used", "invalid_grant"),
        ("callback_expired: Authorization reservation expired", "callback_expired"),
    ],
)
async def test_a_failure_is_said_by_its_code_and_can_be_tried_again(
    client, remote, browsers, uk, error, code
):
    row = await account(client, uk, "UK 18", "user18@example.com")

    failed = await finished(client, remote, browsers, row, error=error)

    assert failed["authorization"]["status"] == "failed"
    assert failed["authorization"]["error_code"] == code
    assert failed["onboarded"] is False
    assert await counts(client) == [(1, 0)]
    assert "already holds" not in json.dumps(failed), "an onboarder is told the code only"
    assert (await link_for(client, browsers, row))["authorize_url"] == LINK + "state-2"


async def test_a_link_that_lapsed_while_waiting_is_said(client, remote, browsers, uk):
    row = await account(client, uk, "UK 18", "user18@example.com")
    await link_for(client, browsers, row)

    remote.finish("job-1", error="callback_expired: Authorization reservation expired")
    lapsed = (await client.get(job(row))).json()

    assert lapsed["authorization"]["error_code"] == "callback_expired"
    assert lapsed["authorize_url"] is None
    late = await client.post(f"{job(row)}/code", json={"code": remote.code_for("job-1")})
    assert late.json()["error"]["code"] == "authorization_not_waiting"


async def test_cancelling_ends_it_and_the_row_can_ask_again(client, remote, browsers, uk):
    row = await account(client, uk, "UK 18", "user18@example.com")
    await link_for(client, browsers, row)

    cancelled = await client.post(f"{job(row)}/cancel")

    assert cancelled.status_code == 200
    assert cancelled.json()["authorization"]["error_code"] == "job_cancelled"
    assert remote.jobs["job-1"]["status"] == "failed"
    nothing = await client.post(f"{job(row)}/cancel")
    assert nothing.json()["error"]["code"] == "authorization_not_waiting"
    assert (await link_for(client, browsers, row))["authorize_url"] == LINK + "state-2"


async def test_a_cancel_that_comes_after_the_platform_was_done_keeps_what_was_done(
    client, remote, browsers, uk
):
    row = await account(client, uk, "UK 18", "user18@example.com")
    await link_for(client, browsers, row)
    await client.post(f"{job(row)}/code", json={"code": remote.code_for("job-1")})
    remote.finish("job-1", accountEmail="user18@example.com")

    cancelled = (await client.post(f"{job(row)}/cancel")).json()

    assert (cancelled["authorization"]["outcome"], cancelled["onboarded"]) == ("onboarded", True)


async def test_a_profile_given_another_address_is_another_account(client, remote, browsers, uk):
    row = await account(client, uk, "UK 18", "user18@example.com")
    await finished(client, remote, browsers, row, accountEmail="user18@example.com")
    storage = dependencies.get_storage_manager()

    current = (await client.get(f"/api/v1/profiles/{row['id']}")).json()
    cleared = await storage.db.clear_profile_email(row["id"], current["row_version"])
    await storage.db.set_profile_email(row["id"], "fresh@example.com", cleared.row_version)

    listed = (await client.get(f"{BASE}/scopes", params={"group": uk})).json()
    assert listed["groups"] == [{"id": uk, "name": "UK", "pending": 1, "onboarded": 0}]
    assert (listed["rows"][0]["onboarded"], listed["rows"][0]["authorization"]) == (False, None)
    assert (await link_for(client, browsers, row))["authorize_url"] == LINK + "state-2"
    assert remote.created[1]["name"] == "fresh@example.com"


# --- the token and its authorizations ---------------------------------------------------


async def test_the_token_is_not_replaced_while_an_authorization_is_open(
    client, remote, browsers, uk
):
    row = await account(client, uk, "UK 18", "user18@example.com")
    await link_for(client, browsers, row)

    replacement = await save_token(client, SECOND)

    assert replacement.status_code == 409
    assert replacement.json()["error"]["code"] == "authorizations_open"
    assert "user18@example.com" in replacement.json()["error"]["message"]
    assert (await client.get(f"{BASE}/connection")).json()["label"] == "Shift A"
    assert (await save_token(client)).status_code == 200, "saving the same token again is no change"

    await client.post(f"{job(row)}/cancel")
    assert (await save_token(client, SECOND)).json()["replaced"] is True


async def test_one_that_lapsed_meanwhile_does_not_stand_in_the_way(client, remote, browsers, uk):
    row = await account(client, uk, "UK 18", "user18@example.com")
    await link_for(client, browsers, row)
    remote.finish("job-1", error="callback_expired: Authorization reservation expired")

    assert (await save_token(client, SECOND)).status_code == 200


async def test_a_token_the_platform_refuses_can_be_replaced_and_its_authorizations_are_lost(
    client, remote, browsers, uk
):
    row = await account(client, uk, "UK 18", "user18@example.com")
    await link_for(client, browsers, row)
    del remote.accepted[TOKEN]

    asked = await client.get(job(row))
    assert (asked.status_code, asked.json()["error"]["code"]) == (409, "onboard_token_refused")
    assert (await client.get(f"{BASE}/connection")).json()["refused"] is True
    for path in ("code", "cancel"):
        body = {"json": {"code": "anything"}} if path == "code" else {}
        answer = await client.post(f"{job(row)}/{path}", **body)
        assert answer.json()["error"]["code"] == "onboard_token_refused"

    assert (await save_token(client, SECOND)).status_code == 200
    await client.put(f"{BASE}/presets", json={**CHOICES, "scope_group": uk})
    listed = (await client.get(f"{BASE}/scopes", params={"group": uk})).json()["rows"][0]
    assert listed["authorization"]["status"] == "lost"
    assert (await link_for(client, browsers, row))["authorize_url"] == LINK + "state-2"


# --- what is kept, and who may ------------------------------------------------------------


async def test_no_link_code_or_state_is_kept_or_audited(client, remote, browsers, uk):
    row = await account(client, uk, "UK 18", "user18@example.com")
    await finished(client, remote, browsers, row, accountEmail="user18@example.com")
    code = remote.codes[0][1]
    storage = dependencies.get_storage_manager()

    kept = json.dumps(
        [
            dict(found)
            for table in (
                "auto_onboarding_authorization",
                "auto_onboarding_onboarded",
                "audit_events",
            )
            for found in storage.db._connection.execute(f"SELECT * FROM {table}")  # noqa: S608
        ],
        default=str,
    )

    assert "job-1" in kept and "user18@example.com" in kept
    for secret in (code, code.split("#")[0], "state-1", LINK, TOKEN):
        assert secret not in kept
    audited = [entry["action"] for entry in await storage.list_audit()]
    assert {"create_onboard_authorization", "submit_onboard_code"} <= set(audited)


async def test_a_viewer_has_no_business_with_a_row(client, remote, browsers, monkeypatch):
    uk_id = await group(client, "UK")
    row = await account(client, uk_id, "UK 18", "user18@example.com")
    await sign_in(client, "viewer", monkeypatch)

    for method, path in (("post", ""), ("get", ""), ("post", "/code"), ("post", "/cancel")):
        body = {"json": {"code": "anything"}} if path == "/code" else {}
        response = await getattr(client, method)(job(row) + path, **body)
        assert response.status_code == 403, f"{method} {path}"
    assert remote.asked == []


async def test_an_operator_authorizes_only_the_profiles_granted(
    client, remote, browsers, monkeypatch
):
    uk_id = await group(client, "UK")
    mine = await account(client, uk_id, "UK 18", "user18@example.com")
    theirs = await account(client, uk_id, "UK 17", "user17@example.com", "203.0.113.17")
    await sign_in(client, "operator", monkeypatch)
    await dependencies.get_storage_manager().set_profile_grant("member", mine["id"])
    await save_token(client)
    await client.put(f"{BASE}/presets", json={**CHOICES, "scope_group": uk_id})
    browsers.running.update({mine["id"], theirs["id"]})

    assert (await client.post(job(mine))).status_code == 200
    for method, path in (("post", ""), ("get", ""), ("post", "/code"), ("post", "/cancel")):
        body = {"json": {"code": "anything"}} if path == "/code" else {}
        response = await getattr(client, method)(job(theirs) + path, **body)
        assert response.status_code in (403, 404), f"{method} {path}"
    assert len(remote.jobs) == 1 and [opened[0] for opened in browsers.opened] == [mine["id"]]


async def test_the_legacy_prefix_serves_the_row_routes(client, remote, browsers, uk):
    row = await account(client, uk, "UK 18", "user18@example.com")
    browsers.running.add(row["id"])

    made = await client.post(f"/api/auto-onboarding/rows/{row['id']}/job")

    assert made.status_code == 200 and made.json()["authorization"]["status"] == "pending"
