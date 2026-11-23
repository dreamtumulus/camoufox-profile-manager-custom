"""Auto-onboarding parameters: what is kept, what is offered, and what would do."""

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from camoufox_pm.config import Settings
from camoufox_pm.core import crypto
from camoufox_pm.core.auto_onboarding import platform
from camoufox_pm.core.auto_onboarding.preset import Preset, expected_domain, issues
from camoufox_pm.core.auto_onboarding.scopes import check_rows
from camoufox_pm.core.auto_onboarding.store import OnboardingStore, preview_of
from camoufox_pm.core.database import StorageManager
from camoufox_pm.core.onboarding import OnboardingError, OneDayClient

TOKEN = "1dk_onb_demo-only"


@pytest.fixture
async def store(tmp_path):
    storage = StorageManager(str(tmp_path / "onboarding.db"))
    await storage.initialize()
    yield OnboardingStore(storage)
    await storage.close()


@pytest.fixture
def secret_key(monkeypatch):
    """Give the installation a key, or take it away with `secret_key(None)`."""

    def use(key: str | None) -> None:
        chosen = Settings(_env_file=None, secret_key=key)
        monkeypatch.setattr(crypto, "get_settings", lambda: chosen)

    use(Fernet.generate_key().decode())
    return use


# --- the store ---------------------------------------------------------------


def test_the_preview_is_the_platforms_own():
    assert preview_of(TOKEN) == "1dk_onb_demo…only"


async def test_nothing_is_saved_at_first(store):
    assert store.saved() is None
    assert store.token() is None
    assert store.preset() == (Preset(), 0)


async def test_a_saved_token_is_kept_encrypted_and_shown_masked(store, secret_key):
    store.save_token(TOKEN, "Shift A")

    saved = store.saved()
    assert (saved.preview, saved.label, saved.refused_at) == ("1dk_onb_demo…only", "Shift A", None)
    assert saved.encrypted is True
    assert store.token() == TOKEN
    kept = store.connection.execute("SELECT token FROM auto_onboarding_connection").fetchone()[0]
    assert kept.startswith("enc:") and TOKEN not in kept


async def test_without_a_key_the_token_is_kept_as_typed_and_that_is_said(store, secret_key):
    secret_key(None)
    store.save_token(TOKEN, "Shift A")

    assert store.saved().encrypted is False
    assert store.token() == TOKEN


async def test_a_second_token_takes_the_place_of_the_first(store, secret_key):
    store.save_token(TOKEN, "Shift A")
    store.refused()
    store.save_token("1dk_onb_second-0002", "Shift B")

    rows = store.connection.execute("SELECT COUNT(*) FROM auto_onboarding_connection").fetchone()
    assert rows[0] == 1
    saved = store.saved()
    assert (saved.preview, saved.label, saved.refused_at) == ("1dk_onb_seco…0002", "Shift B", None)


async def test_a_refusal_is_remembered_until_the_platform_accepts_again(store, secret_key):
    store.save_token(TOKEN, "Shift A")

    store.refused()
    assert store.saved().refused_at is not None
    assert store.token() == TOKEN, "a refused token stays, to be replaced"

    store.accepted("Shift A, renamed")
    saved = store.saved()
    assert (saved.refused_at, saved.label) == (None, "Shift A, renamed")


async def test_choices_are_kept_and_counted(store):
    first = Preset(node_id="direct-1", proxy_mode="auto", proxy_group="UK", note="  batch 3 ")

    assert store.save_preset(first) == 1
    assert store.save_preset(first.model_copy(update={"rpm_limit": 20})) == 2
    preset, version = store.preset()
    assert version == 2
    assert (preset.node_id, preset.proxy_group, preset.note, preset.rpm_limit) == (
        "direct-1",
        "UK",
        "batch 3",
        20,
    )


async def test_choices_this_version_cannot_read_start_over(store):
    store.save_preset(Preset(node_id="direct-1"))
    with store.connection:
        store.connection.execute("UPDATE auto_onboarding_preset SET value = '{\"mode\": 7}'")

    assert store.preset() == (Preset(), 1)


# --- the choices -------------------------------------------------------------


def test_blank_text_is_no_choice():
    preset = Preset(node_id="  ", proxy_group="", org_uuid=" ", note="")

    assert preset == Preset()


@pytest.mark.parametrize(
    "values",
    [
        {"proxy_mode": "own"},
        {"concurrency_limit": -1},
        {"rpm_limit": 100001},
        {"concurrency_limit": 1.5},
        {"note": "x" * 501},
        {"account_group": "grp-a"},
    ],
)
def test_a_choice_out_of_shape_is_refused(values):
    with pytest.raises(ValidationError):
        Preset(**values)


def test_another_token_keeps_only_what_the_platform_did_not_offer():
    preset = Preset(
        node_id="direct-1",
        proxy_mode="pool",
        proxy_group="UK",
        proxy_id="p1",
        org_id="org-a",
        domain="example.com",
        org_manual=True,
        org_uuid="x",
        note="keep",
        rpm_limit=20,
        scope_group="g1",
    )

    assert preset.for_another_token() == Preset(
        proxy_mode="pool", note="keep", rpm_limit=20, scope_group="g1"
    )


def options(**changes):
    base = {
        "label": "Shift A",
        "nodes": [
            {"id": "direct-1", "name": "direct-1", "online": True, "used": 13, "capacity": 20},
            {"id": "direct-9", "name": "direct-9", "online": False, "used": 0, "capacity": 20},
        ],
        "orgs": [
            {"id": "org-a", "name": "Team A", "domains": ["example.com"]},
            {"id": "org-b", "name": "Team B", "domains": ["example.com", "Example.NET"]},
            {"id": "org-c", "name": "Team C", "domains": []},
        ],
        "pool": {
            "node_id": "direct-1",
            "groups": [{"name": "UK", "assignable": 12}],
            "proxies": [
                {"id": "p18", "exit_ip": "203.0.113.18", "group": "UK", "slots": 3},
                {"id": "p17", "exit_ip": "203.0.113.17", "group": "UK", "slots": 3},
                {"id": "p17b", "exit_ip": "203.0.113.17", "group": "UK", "slots": 1},
            ],
        },
    }
    return {**base, **changes}


def codes(preset, offered=None):
    return [issue.code for issue in issues(preset, offered or options())]


COMPLETE = Preset(node_id="direct-1", org_id="org-a", domain="example.com")


def test_finished_choices_have_no_issue():
    assert codes(COMPLETE) == []
    assert codes(COMPLETE.model_copy(update={"org_id": "org-c", "domain": None})) == []


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"node_id": None}, ["node_missing"]),
        ({"node_id": "gone"}, ["node_unavailable"]),
        ({"proxy_mode": "auto", "proxy_group": "US"}, ["proxy_group_unavailable"]),
        ({"proxy_mode": "auto", "proxy_group": "UK"}, []),
        ({"proxy_mode": "auto"}, []),
        ({"proxy_mode": "pool"}, ["proxy_missing"]),
        ({"proxy_mode": "pool", "proxy_id": "gone"}, ["proxy_unavailable"]),
        ({"proxy_mode": "pool", "proxy_id": "p18"}, []),
        # A group or a proxy left over from another way of choosing is not looked at.
        ({"proxy_mode": "row", "proxy_group": "US", "proxy_id": "gone"}, []),
        ({"org_id": None}, ["org_missing"]),
        ({"org_id": "gone"}, ["org_unavailable"]),
        ({"domain": None}, ["domain_missing"]),
        ({"domain": "example.org"}, ["domain_unavailable"]),
        ({"org_id": "org-b", "domain": "example.net"}, []),
    ],
)
def test_what_is_missing_or_no_longer_offered_is_named(changes, expected):
    assert codes(COMPLETE.model_copy(update=changes)) == expected


def test_an_offline_node_is_said_and_its_pool_still_judged():
    offered = options(pool={"node_id": "direct-9", "groups": [], "proxies": []})
    preset = COMPLETE.model_copy(update={"node_id": "direct-9", "proxy_mode": "pool"})

    assert codes(preset, offered) == ["node_offline", "proxy_missing"]


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, []),
        ({"org_manual": True}, ["org_uuid_missing"]),
        ({"org_manual": True, "org_uuid": "not-a-uuid"}, ["org_uuid_invalid"]),
        ({"org_manual": True, "org_uuid": "0A1B2C3D-0000-4000-8000-000000000000"}, []),
        # Left unspecified, whatever was once typed is not looked at.
        ({"org_manual": False, "org_uuid": "not-a-uuid"}, []),
    ],
)
def test_a_token_without_profiles_may_leave_the_organization_or_name_it(changes, expected):
    preset = Preset(node_id="direct-1", **changes)

    assert codes(preset, options(orgs=[])) == expected


def test_the_expected_domain_is_the_chosen_one_of_a_profile_that_has_domains():
    assert expected_domain(COMPLETE, options()) == "example.com"
    other = COMPLETE.model_copy(update={"org_id": "org-b", "domain": "Example.NET"})
    assert expected_domain(other, options()) == "example.net"
    assert expected_domain(COMPLETE.model_copy(update={"org_id": "org-c"}), options()) is None
    assert (
        expected_domain(Preset(node_id="direct-1", domain="example.com"), options(orgs=[])) is None
    )


# --- the rows ----------------------------------------------------------------


def row(number, **values):
    base = {
        "profile_id": f"profile-{number}",
        "name": f"UK {number}",
        "email": f"user{number}@example.com",
        "exit_ip": f"203.0.113.{number}",
        "exit_checked": True,
        "exit_reachable": True,
        "onboarded": False,
    }
    return {**base, **values}


def statuses(rows, preset=COMPLETE, offered=None):
    return [
        (item["status"], item["proxy_id"])
        for item in check_rows(rows, preset, offered or options())
    ]


def test_each_row_goes_through_the_pool_entry_that_has_its_own_exit():
    rows = [
        row(18),
        row(17),  # two pool entries come out there
        row(16),  # none does
        row(15, exit_checked=False, exit_reachable=False, exit_ip=None),
        row(14, exit_reachable=False, exit_ip=None),
    ]

    assert statuses(rows) == [
        ("ready", "p18"),
        ("exit_ambiguous", None),
        ("exit_not_in_pool", None),
        ("exit_unknown", None),
        ("exit_failed", None),
    ]


@pytest.mark.parametrize("mode", ["auto", "pool"])
def test_with_a_proxy_chosen_for_the_batch_a_rows_own_exit_is_not_looked_at(mode):
    preset = COMPLETE.model_copy(update={"proxy_mode": mode, "proxy_id": "p18"})
    rows = [row(16), row(15, exit_checked=False, exit_reachable=False, exit_ip=None)]

    assert statuses(rows, preset) == [("ready", None), ("ready", None)]


def test_an_address_outside_the_chosen_domain_is_said_whatever_its_case():
    rows = [row(18, email="User18@EXAMPLE.com"), row(17, email="user17@example.net")]

    assert [status for status, _ in statuses(rows)] == ["ready", "domain_mismatch"]


def test_an_onboarded_row_is_said_first():
    rows = [row(16, onboarded=True, email="user16@example.net")]

    assert statuses(rows) == [("onboarded", None)]


# --- the platform ------------------------------------------------------------


@pytest.fixture
def platform_answers(monkeypatch):
    """Stand in for 1DayAPI: answers by path, and records what was asked with which token."""
    asked: list[tuple[str, str]] = []
    answers: dict[str, object] = {
        "/api/onboard/me": {
            "label": "Shift A",
            "node": {
                "nodeId": "direct-1",
                "name": None,
                "online": True,
                "capacity": 20,
                "used": 13,
            },
            "nodes": [
                {
                    "nodeId": "direct-1",
                    "name": None,
                    "online": True,
                    "capacity": 20,
                    "used": 13,
                    "freeProxies": 5,
                },
                {"nodeId": "node/2", "name": "Second", "online": False, "capacity": 5, "used": 0},
            ],
            "orgs": [
                {
                    "id": "org-a",
                    "name": "Team A",
                    "note": "private",
                    "status": "active",
                    "domains": ["example.com"],
                    "accounts": 4,
                    "routes": [],
                }
            ],
        },
        "/api/onboard/node-groups": [
            {"group": "UK", "total": 3, "live": 3, "assignable": 2},
            {"group": None, "total": 1, "live": 1, "assignable": 1},
        ],
        "/api/onboard/proxies": [
            {
                "id": "p18",
                "label": "uk-18",
                "group": "UK",
                "host": "proxy.internal",
                "port": 8080,
                "exitIp": "203.0.113.18",
                "normalSlots": 1,
                "overflowSlots": 2,
                "resourceKey": "rk",
                "boundAccounts": 1,
                "reservedAccounts": 0,
                "country": "GB",
            },
        ],
    }

    async def request(self, method, path, body=None):
        asked.append((self.token, path))
        answer = answers[path.split("?")[0]]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(OneDayClient, "request", request)
    return answers, asked


async def test_the_options_are_trimmed_to_what_the_screen_needs(platform_answers):
    _, asked = platform_answers

    offered = await platform.read_options(TOKEN, None)

    assert offered["label"] == "Shift A"
    assert offered["nodes"] == [
        {"id": "direct-1", "name": "direct-1", "online": True, "used": 13, "capacity": 20},
        {"id": "node/2", "name": "Second", "online": False, "used": 0, "capacity": 5},
    ]
    assert offered["orgs"] == [{"id": "org-a", "name": "Team A", "domains": ["example.com"]}]
    assert offered["pool"] == {
        "node_id": "direct-1",
        "groups": [{"name": "UK", "assignable": 2}],
        "proxies": [{"id": "p18", "exit_ip": "203.0.113.18", "group": "UK", "slots": 2}],
    }
    assert "proxy.internal" not in repr(offered), "how to reach a proxy is not the screen's to know"
    assert {token for token, _ in asked} == {TOKEN}


async def test_the_pool_is_the_asked_nodes_and_its_id_is_escaped(platform_answers):
    _, asked = platform_answers

    offered = await platform.read_options(TOKEN, "node/2")

    assert offered["pool"]["node_id"] == "node/2"
    assert ("/api/onboard/proxies?nodeId=node%2F2") in [path for _, path in asked]


async def test_a_node_the_token_does_not_reach_falls_back_to_the_first(platform_answers):
    offered = await platform.read_options(TOKEN, "gone")

    assert offered["pool"]["node_id"] == "direct-1"


async def test_a_token_bound_to_one_node_offers_that_node(platform_answers):
    answers, _ = platform_answers
    del answers["/api/onboard/me"]["nodes"]

    offered = await platform.read_options(TOKEN, None)

    assert [node["id"] for node in offered["nodes"]] == ["direct-1"]


async def test_a_token_the_platform_refuses_is_told_apart_from_other_failures(platform_answers):
    answers, _ = platform_answers
    refusal = OnboardingError("Invalid or revoked onboarder token", 502, "upstream_error")
    refusal.upstream_status = 401
    answers["/api/onboard/me"] = refusal

    with pytest.raises(platform.TokenRefused) as caught:
        await platform.read_identity(TOKEN)
    assert (caught.value.status_code, caught.value.code) == (409, "onboard_token_refused")

    other = OnboardingError("1dayApi返回 HTTP 500", 502, "upstream_error")
    other.upstream_status = 500
    answers["/api/onboard/me"] = other
    with pytest.raises(OnboardingError) as caught:
        await platform.read_identity(TOKEN)
    assert not isinstance(caught.value, platform.TokenRefused)


# --- authorizations ------------------------------------------------------------


async def test_an_authorization_is_kept_by_profile_and_address(store):
    assert store.latest("profile-18", "user18@example.com") is None

    made = store.begin(
        "profile-18", "User18@Example.com", "job-1", "direct-1", "203.0.113.18", None
    )

    assert (made.status, made.email, made.open) == ("pending", "user18@example.com", True)
    assert store.latest("profile-18", "USER18@example.com") == made
    assert store.latest("profile-18", "other@example.com") is None, (
        "another address, another account"
    )
    assert store.open_authorizations() == [made]

    working = store.update(made, status="working", expires_at="2030-01-01T00:03:00Z")
    assert (working.status, working.expires_at, working.open) == (
        "working",
        "2030-01-01T00:03:00Z",
        True,
    )
    failed = store.update(working, status="failed", outcome="failed", error_code="invalid_grant")
    assert (failed.open, store.open_authorizations()) == (False, [])

    again = store.begin("profile-18", "user18@example.com", "job-2", "direct-1", "auto", None)
    assert store.latest("profile-18", "user18@example.com") == again, "the last one asked"
    assert store.latest_of(["profile-18", "profile-17"]) == {"profile-18": again}


async def test_what_is_onboarded_is_told_by_profile_and_address(store):
    assert store.onboarded() == set()

    store.mark_onboarded("profile-18", "User18@Example.com", "acct-1")
    store.mark_onboarded("profile-18", "user18@example.com", "acct-2")

    assert store.onboarded() == {("profile-18", "user18@example.com")}
    assert store.is_onboarded("profile-18", "USER18@EXAMPLE.COM")
    assert not store.is_onboarded("profile-18", "fresh@example.com")
    assert not store.is_onboarded("profile-17", "user18@example.com")


def started(store, email="user18@example.com"):
    return store.begin("profile-18", email, "job-1", "direct-1", "203.0.113.18", None)


@pytest.mark.parametrize(
    ("job", "expected"),
    [
        ({"status": "pending_callback", "expiresAt": "T1"}, ("pending", None, False)),
        ({"status": "working"}, ("working", None, False)),
        (
            {"status": "done", "accountId": "a1", "accountEmail": "User18@Example.com"},
            ("done", "onboarded", True),
        ),
        ({"status": "done", "accountId": "a1"}, ("done", "onboarded", True)),
        (
            {
                "status": "done",
                "accountId": "a1",
                "accountEmail": "user18@example.com",
                "reused": True,
            },
            ("done", "reused", True),
        ),
        (
            {"status": "done", "accountId": "a1", "accountEmail": "another@example.com"},
            ("done", "mismatch", False),
        ),
        (
            {
                "status": "failed",
                "error": "account_exists: account authorization could not be completed",
            },
            ("failed", "failed", False),
        ),
    ],
)
async def test_only_done_for_the_rows_own_account_makes_it_onboarded(store, job, expected):
    from camoufox_pm.core.auto_onboarding.authorizations import settle

    settled = settle(store, started(store), job)

    assert (settled.status, settled.outcome) == expected[:2]
    assert store.is_onboarded("profile-18", "user18@example.com") is expected[2]


async def test_a_failure_is_kept_by_its_code_and_one_without_a_code_is_still_a_failure(store):
    from camoufox_pm.core.auto_onboarding.authorizations import settle

    told = settle(store, started(store), {"status": "failed", "error": "invalid_grant: used"})
    untold = settle(store, started(store), {"status": "failed", "error": "Something went wrong"})

    assert (told.error_code, untold.error_code) == ("invalid_grant", "authorization_failed")


def test_the_job_is_asked_for_with_the_rows_address_and_the_chosen_proxy():
    from camoufox_pm.core.auto_onboarding.authorizations import job_request

    by_row = job_request(COMPLETE, options(), "user18@example.com", "p18")
    automatic = job_request(
        COMPLETE.model_copy(update={"proxy_mode": "auto", "proxy_group": "UK"}),
        options(),
        "user18@example.com",
        "p18",
    )
    unnamed = job_request(Preset(node_id="direct-1"), options(orgs=[]), "user18@example.com", "p18")

    assert by_row == {
        "nodeId": "direct-1",
        "name": "user18@example.com",
        "proxyId": "p18",
        "orgId": "org-a",
        "domain": "example.com",
    }
    assert (automatic["proxyId"], automatic["proxyGroup"]) == ("auto", "UK")
    assert unnamed == {"nodeId": "direct-1", "name": "user18@example.com", "proxyId": "p18"}
