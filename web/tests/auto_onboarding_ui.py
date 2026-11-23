"""Auto onboarding: the token, the access preset card, the check, and a row's authorization.

.venv/bin/python web/tests/auto_onboarding_ui.py
Uses the same local server and isolated Chromium fixture as stage_one_ui.
"""

import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect
from stage_one_ui import DesktopUI

TOKEN = "1dk_onb_demo-only"
SECOND = "1dk_onb_second-0002"
EMPTY = {
    "node_id": None, "proxy_mode": "row", "proxy_group": None, "proxy_id": None, "org_id": None,
    "domain": None, "org_manual": False, "org_uuid": None, "note": None, "concurrency_limit": None,
    "rpm_limit": None, "scope_group": None,
}
POOLS = {
    "direct-1": {
        "node_id": "direct-1",
        "groups": [{"name": "UK", "assignable": 12}, {"name": "US", "assignable": 8}],
        "proxies": [
            {"id": "p18", "exit_ip": "203.0.113.18", "group": "UK", "slots": 3},
            {"id": "p17", "exit_ip": "203.0.113.17", "group": "UK", "slots": 3},
            {"id": "p41", "exit_ip": "198.51.100.41", "group": "US", "slots": 1},
        ],
    },
    "direct-2": {
        "node_id": "direct-2",
        "groups": [{"name": "UK B", "assignable": 4}],
        "proxies": [{"id": "p61", "exit_ip": "198.51.100.61", "group": "UK B", "slots": 2}],
    },
}
NODES = [
    {"id": "direct-1", "name": "direct-1", "online": True, "used": 13, "capacity": 20},
    {"id": "direct-2", "name": "direct-2", "online": True, "used": 4, "capacity": 20},
]
ORGS = [
    {"id": "org-a", "name": "Team A", "domains": ["example.com"]},
    {"id": "org-b", "name": "Team B", "domains": ["example.com", "example.net"]},
    {"id": "org-c", "name": "Team C", "domains": []},
]


def row(number, **values):
    base = {
        "profile_id": f"profile-{number}", "name": f"UK HTTP · 0{number}",
        "email": f"user0{number}@example.com", "exit_ip": f"203.0.113.{number}",
        "exit_checked": True, "exit_reachable": True, "onboarded": False, "can_launch": True,
        "authorization": None,
    }
    return {**base, **values}


def authorization(**values):
    """An authorization as the server reports it, with the half hour a new link has."""
    expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    base = {
        "status": "pending", "outcome": None, "error_code": None, "account_email": None, "node": "direct-1",
        "proxy": "203.0.113.18", "expires_at": expires, "cooldown_until": None,
        "created_at": "2026-11-23T10:00:00+00:00",
    }
    return {**base, **values}


class AutoOnboardingUI(DesktopUI):
    def setUp(self):
        super().setUp()
        self.browser_state = {**self.browser_state, "installed": True, "state": "ready", "version": "135"}
        # The stand-in server: one saved token, what each token may use, and what is chosen.
        self.accepted = {TOKEN: "Demo token", SECOND: "Demo token 2"}
        self.saved_token = TOKEN
        self.refused = False
        self.nodes = copy.deepcopy(NODES)
        self.orgs = copy.deepcopy(ORGS)
        self.preset = dict(EMPTY)
        self.version = 0
        self.groups = [{"id": "uk", "name": "UK HTTP", "pending": 3, "onboarded": 0}]
        self.rows = [row(18), row(17), row(16)]
        self.preflight = None
        # Set to (status, code) to have the check fail.
        self.preflight_error = None
        self.saves = []
        self.role = "administrator"
        # A row's authorization: the link and state the platform made for it, what was
        # pasted back, and what the platform makes of a code once it has one (None: it
        # goes on working).
        self.links, self.states, self.codes, self.made = {}, {}, [], 0
        self.finish = {"status": "done", "outcome": "onboarded"}
        self.job_refusal = None
        # Where a link just made was opened in the row's browser: "tab", "window", or None.
        self.link_opens = "tab"

    def connection(self):
        if self.saved_token is None:
            return {"saved": False, "preview": None, "label": None, "read_at": None, "refused": False,
                    "refused_at": None, "encrypted": None, "platform": "https://1dayapi.com"}
        token = self.saved_token
        return {
            "saved": True, "preview": f"{token[:12]}…{token[-4:]}", "label": self.accepted.get(token, "?"),
            "read_at": "2026-11-22T10:00:00+00:00", "refused": self.refused,
            "refused_at": "2026-11-22T10:05:00+00:00" if self.refused else None, "encrypted": True,
            "platform": "https://1dayapi.com",
        }

    def options(self, node_id):
        if self.saved_token is None or self.refused or self.saved_token not in self.accepted:
            return None
        known = [node["id"] for node in self.nodes]
        chosen = node_id if node_id in known else (known[0] if known else None)
        return {"label": self.accepted[self.saved_token], "nodes": self.nodes, "orgs": self.orgs,
                "pool": POOLS[chosen] if chosen else None}

    def route(self, route):
        request = route.request
        url = urlsplit(request.url)
        path = url.path.split("/api/v1", 1)[1]
        if path == "/auth/session":
            return route.fulfill(json={
                "user_auth_enabled": self.role != "administrator", "authenticated": True,
                "username": None if self.role == "administrator" else "member", "role": self.role,
                "actor_kind": "anonymous_local" if self.role == "administrator" else "session",
                "can_manage_access": self.role == "administrator",
            })
        if request.method == "POST" and path.startswith("/profiles/") and path.endswith(("/launch", "/close")):
            self.calls.append((request.method, path, request.post_data))
            profile_id = path.split("/")[2]
            if path.endswith("/close"):
                self.running_profile_ids = [item for item in self.running_profile_ids if item != profile_id]
                return route.fulfill(json={"status": "closed", "profile_id": profile_id, "message": "Browser closed successfully"})
            if profile_id not in self.running_profile_ids:
                self.running_profile_ids.append(profile_id)
            return route.fulfill(json={"profile_id": profile_id, "browser_session_id": "s", "status": "launched", "message": "Browser launched successfully"})
        if not path.startswith("/auto-onboarding/"):
            return super().route(route)
        self.calls.append((request.method, path, request.post_data))
        if path == "/auto-onboarding/connection" and request.method == "PUT":
            token = request.post_data_json["token"]
            if token not in self.accepted:
                return route.fulfill(status=409, json={"error": {"code": "onboard_token_refused", "message": "refused"}})
            replaced = self.saved_token is not None and self.saved_token != token
            still_open = [item["email"] for item in self.rows if (item["authorization"] or {}).get("status") in ("pending", "working")]
            if replaced and still_open and not self.refused:
                return route.fulfill(status=409, json={"error": {"code": "authorizations_open", "message": "Authorizations are still open: " + ", ".join(still_open)}})
            self.saved_token, self.refused = token, False
            if replaced:
                self.preset = {**self.preset, "node_id": None, "proxy_group": None, "proxy_id": None,
                               "org_id": None, "domain": None, "org_manual": False, "org_uuid": None}
            return route.fulfill(json={"connection": self.connection(), "replaced": replaced})
        if path == "/auto-onboarding/presets" and request.method == "GET":
            if self.saved_token is not None and self.saved_token not in self.accepted:
                self.refused = True
            node = parse_qs(url.query).get("node_id", [self.preset["node_id"]])[0]
            return route.fulfill(json={
                "connection": self.connection(), "options": self.options(node), "options_error": None,
                "preset": self.preset, "version": self.version,
            })
        if path == "/auto-onboarding/presets" and request.method == "PUT":
            self.preset = request.post_data_json
            self.version += 1
            self.saves.append(dict(self.preset))
            return route.fulfill(json={"preset": self.preset, "version": self.version})
        if path == "/auto-onboarding/scopes":
            group = parse_qs(url.query).get("group")
            return route.fulfill(json={"groups": self.groups, "rows": self.rows if group else None})
        if path == "/auto-onboarding/preflight":
            if self.refused or self.saved_token not in self.accepted:
                self.refused = True
                return route.fulfill(status=409, json={"error": {"code": "onboard_token_refused", "message": "refused"}})
            if self.preflight_error:
                status, code = self.preflight_error
                return route.fulfill(status=status, json={"error": {"code": code, "message": "unavailable"}})
            return route.fulfill(json=self.preflight)
        if path.startswith("/auto-onboarding/rows/"):
            return self.row_route(route, path.split("/")[3], path.split("/job", 1)[1])
        raise AssertionError(f"Unexpected API call: {request.method} {path}")

    def job_of(self, found, made=False):
        current = found["authorization"]
        waiting = current is not None and current["status"] == "pending"
        return {"profile_id": found["profile_id"], "onboarded": found["onboarded"], "authorization": current,
                "authorize_url": self.links.get(found["profile_id"]) if waiting else None,
                "opened": (self.link_opens is not None) if made else None,
                "opened_in": self.link_opens if made else None}

    def row_route(self, route, profile_id, tail):
        """A row's authorization, as the server answers for it."""
        request = route.request
        found = next(item for item in self.rows if item["profile_id"] == profile_id)
        current = found["authorization"]
        is_open = current is not None and current["status"] in ("pending", "working")

        def refuse(status, code, message):
            return route.fulfill(status=status, json={"error": {"code": code, "message": message}})

        if self.refused or self.saved_token not in self.accepted:
            self.refused = True
            return refuse(409, "onboard_token_refused", "refused")
        if tail == "" and request.method == "POST":
            if is_open:
                return route.fulfill(json=self.job_of(found))
            if profile_id not in self.running_profile_ids:
                return refuse(409, "browser_not_running", "Run this profile's browser first")
            if self.job_refusal:
                refusal, self.job_refusal = self.job_refusal, None
                return refuse(409, *refusal)
            self.made += 1
            self.states[profile_id] = f"state-{profile_id}-{self.made}"
            self.links[profile_id] = f"https://claude.example/oauth/authorize?client_id=demo-only&state={self.states[profile_id]}"
            found["authorization"] = authorization(proxy=found["exit_ip"])
            return route.fulfill(json=self.job_of(found, made=True))
        if tail == "" and request.method == "GET":
            if current is not None and current["status"] == "working" and self.finish is not None:
                current.update({"account_email": found["email"], **self.finish})
                if current["outcome"] in ("onboarded", "reused"):
                    found["onboarded"] = True
                    self.groups[0]["pending"] -= 1
                    self.groups[0]["onboarded"] += 1
                    self.groups = [group for group in self.groups if group["pending"] > 0]
            return route.fulfill(json=self.job_of(found))
        if not is_open:
            return refuse(409, "authorization_not_waiting", "This row has no authorization waiting")
        if tail == "/code":
            code = request.post_data_json["code"]
            if current["status"] != "pending":
                return refuse(409, "authorization_not_waiting", "This row has no authorization waiting")
            if not code.endswith("#" + self.states[profile_id]):
                return refuse(422, "callback_invalid", "The platform refused the code")
            self.codes.append((profile_id, code))
            current["status"] = "working"
            return route.fulfill(json=self.job_of(found))
        if tail == "/cancel":
            current.update(status="failed", outcome="failed", error_code="job_cancelled")
            return route.fulfill(json=self.job_of(found))
        raise AssertionError(f"Unexpected row call: {request.method} {tail}")

    def tr(self, number):
        return self.page.locator(f'[data-profile="profile-{number}"]')

    def browser_button(self, number):
        return self.tr(number).locator('[data-action="browser"]')

    def link_button(self, number):
        return self.tr(number).locator('[data-action="authorize"]')

    @property
    def dialog(self):
        return self.page.get_by_role("dialog", name="Complete the authorization")

    def posts(self, suffix):
        return [path for method, path, _ in self.calls if method == "POST" and path.endswith(suffix)]

    def run_row(self, number):
        self.browser_button(number).click()
        expect(self.tr(number).get_by_test_id("browser-state")).to_contain_text("Running")

    def make_link(self, number):
        """Run a row's browser and have a link made for it; the dialog opens."""
        self.run_row(number)
        self.link_button(number).click()
        expect(self.dialog).to_be_visible()
        # The dialog takes the focus once it is ready for the keyboard.
        expect(self.dialog.get_by_label("Authorization code", exact=True)).to_be_focused()

    def paste_own_code(self, number):
        self.dialog.get_by_label("Authorization code", exact=True).fill(f"code-{number}#{self.states[f'profile-{number}']}")
        self.dialog.get_by_test_id("submit-code").click()

    @staticmethod
    def checked(*numbers, onboarded=()):
        """What the server's check says of these rows: ready, or onboarded already."""
        rows = [
            {**row(number, onboarded=number in onboarded),
             "status": "onboarded" if number in onboarded else "ready",
             "proxy_id": None if number in onboarded else f"p{number}"}
            for number in numbers
        ]
        ready = sum(item["status"] == "ready" for item in rows)
        return {"ok": True, "version": 1, "issues": [], "rows": rows, "ready": ready, "blocked": len(rows) - ready}

    def scope_options(self):
        return self.page.get_by_label("Profiles to onboard", exact=True).locator("option").all_inner_texts()

    def open(self):
        self.page.goto(f"{self.origin}/auto-onboarding/")
        expect(self.page.get_by_role("heading", name="Auto onboarding", exact=True)).to_be_visible()
        expect(self.page.get_by_role("heading", name="Platform access preset", exact=True)).to_be_visible()

    @property
    def card(self):
        return self.page.get_by_test_id("preset-card")

    def summary(self):
        return [" ".join(text.split()) for text in self.page.get_by_test_id("preset-summary").locator("> span").all_inner_texts()]

    def wait_saved(self, **expected):
        """Wait until the server holds these choices."""
        self.page.wait_for_function("() => true")
        for _ in range(40):
            if all(self.preset.get(key) == value for key, value in expected.items()):
                return
            self.page.wait_for_timeout(50)
        self.fail(f"not saved: {expected} in {self.preset}")

    # --- the token ---------------------------------------------------------------

    def test_without_a_token_there_is_a_field_and_nothing_to_choose(self):
        self.saved_token = None
        self.open()
        field = self.page.get_by_label("Onboarder token", exact=True)
        expect(field).to_be_visible()
        expect(field).to_have_attribute("type", "password")
        expect(self.page.get_by_role("button", name="Cancel", exact=True)).not_to_be_visible()
        expect(self.page.get_by_test_id("token-status")).to_have_text("No token saved yet. Save one to choose a node and an organization.")
        expect(self.page.get_by_test_id("preset-empty")).to_contain_text("Once an onboarder token is saved and read")
        expect(self.page.get_by_test_id("preset-summary")).not_to_be_visible()
        expect(self.page.get_by_text("Platform https://1dayapi.com · the token is kept on this computer and is sent only to this address.", exact=True)).to_be_visible()
        expect(self.page.get_by_role("button", name="Check parameters", exact=True)).to_be_disabled()

        save = self.page.get_by_role("button", name="Save and read", exact=True)
        save.click()
        expect(self.page.get_by_role("alert").filter(has_text="Enter the onboarder token.")).to_be_visible()
        field.fill("sk-ant-something")
        save.click()
        expect(self.page.get_by_role("alert").filter(has_text="it starts with 1dk_onb_")).to_be_visible()
        self.assertFalse(any(method == "PUT" and path.endswith("/connection") for method, path, _ in self.calls), "nothing malformed is sent")

        field.fill("1dk_onb_revoked")
        field.press("Enter")
        expect(self.page.get_by_role("alert").filter(has_text="The platform refused this token: it is invalid or was revoked.")).to_be_visible()
        expect(field).to_be_visible()

        self.page.get_by_role("button", name="Show", exact=True).click()
        expect(field).to_have_attribute("type", "text")
        field.fill(f"  {TOKEN} ")
        save.click()
        expect(self.page.get_by_test_id("token-preview")).to_have_text("1dk_onb_demo…only")
        expect(field).not_to_be_visible()
        expect(self.page.get_by_test_id("token-status")).to_contain_text("Read · token “Demo token” · nodes 2 · organization profiles 3")
        expect(self.page.get_by_test_id("preset-summary")).to_be_visible()
        sent = [json.loads(body)["token"] for method, path, body in self.calls if method == "PUT" and path.endswith("/connection")]
        self.assertEqual(sent, ["1dk_onb_revoked", TOKEN])
        self.assertNotIn(TOKEN, self.page.content(), "a saved token is not shown again")

    def test_a_saved_token_can_be_replaced_or_kept(self):
        self.preset = {**EMPTY, "node_id": "direct-2", "org_id": "org-c", "note": "keep"}
        self.open()
        expect(self.page.get_by_label("Exit node", exact=True)).to_have_value("direct-2")
        check = self.page.get_by_role("button", name="Check parameters", exact=True)
        expect(check).to_be_enabled()

        self.page.get_by_role("button", name="Change", exact=True).click()
        field = self.page.get_by_label("Onboarder token", exact=True)
        expect(field).to_have_value("")
        expect(check).to_be_disabled()
        expect(self.page.get_by_text("Finish or cancel the token change before checking.", exact=True)).to_be_visible()
        expect(self.page.get_by_label("Exit node", exact=True)).to_be_disabled()
        field.fill("1dk_onb_typed-and-dropped")
        field.press("Escape")
        expect(self.page.get_by_test_id("token-preview")).to_have_text("1dk_onb_demo…only")
        expect(check).to_be_enabled()

        self.page.get_by_role("button", name="Change", exact=True).click()
        expect(field).to_have_value("")
        field.fill("1dk_onb_revoked")
        self.page.get_by_role("button", name="Save and read", exact=True).click()
        expect(self.page.get_by_role("alert").filter(has_text="The saved token is unchanged.")).to_be_visible()

        field.fill(SECOND)
        self.page.get_by_role("button", name="Save and read", exact=True).click()
        expect(self.page.get_by_test_id("token-preview")).to_have_text("1dk_onb_seco…0002")
        expect(self.page.get_by_test_id("token-notice")).to_have_text("The token was replaced. The node and the organization were read anew for it; confirm them.")
        expect(self.page.get_by_label("Exit node", exact=True)).to_have_value("direct-1")
        expect(self.page.get_by_label("Organization", exact=True)).to_have_value("org-a")
        self.wait_saved(node_id="direct-1", org_id="org-a", domain="example.com", note="keep")

    # --- the card ----------------------------------------------------------------

    def test_the_first_choices_are_made_and_summed_up_below_the_third_step(self):
        self.open()
        self.wait_saved(node_id="direct-1", org_id="org-a", domain="example.com", scope_group="uk")
        self.assertEqual(self.summary(), [
            "Method authorize link", "Node direct-1", "Proxy each row’s own exit",
            "Organization Team A · @example.com", "Account group default group",
        ])
        steps = self.card.locator("[data-step]")
        expect(steps).to_have_count(3)
        self.assertEqual([" ".join(text.split()) for text in steps.locator("> div:first-child").all_inner_texts()],
                         ["1 Credentials", "2 Exit", "3 Ownership and limits"])
        third, line = steps.nth(2).bounding_box(), self.page.get_by_test_id("preset-summary").bounding_box()
        self.assertGreaterEqual(line["y"], third["y"] + third["height"] - 1, "the line comes after the third step")
        expect(self.card.locator("> *").last).to_have_attribute("data-testid", "preset-summary")
        expect(self.page.get_by_test_id("account-group")).to_have_text("Default group")
        expect(self.card.get_by_label("Account group")).to_have_count(0)
        expect(self.card.get_by_text("Authorize link", exact=True)).to_be_visible()

    def test_the_proxy_is_chosen_one_of_three_ways_for_the_whole_batch(self):
        self.open()
        pressed = lambda: self.card.locator('[data-proxy][aria-pressed="true"]').get_attribute("data-proxy")  # noqa: E731
        self.assertEqual([" ".join(text.split()) for text in self.card.locator("[data-proxy]").all_inner_texts()],
                         ["Each row’s own exit", "Assigned by the platform", "Pick from the pool (3)"])
        self.assertEqual(pressed(), "row")
        expect(self.page.get_by_test_id("proxy-hint")).to_contain_text("Each row is onboarded through the pool proxy")
        expect(self.page.locator('[data-profile="profile-18"]')).to_contain_text("matches a proxy in the node’s pool")
        expect(self.page.locator('[data-profile="profile-16"]')).to_contain_text("not in the node’s pool, or full")

        self.card.locator('[data-proxy="auto"]').click()
        self.assertEqual(pressed(), "auto")
        group = self.page.get_by_label("Proxy group", exact=True)
        self.assertEqual(group.locator("option").all_inner_texts(), ["Any group", "UK · assignable 12", "US · assignable 8"])
        expect(self.page.get_by_test_id("proxy-hint")).to_contain_text("may differ from the exit the row’s browser signed in from")
        expect(self.page.locator('[data-profile="profile-16"]')).to_contain_text("proxy assigned by the platform")
        group.select_option("UK")
        self.assertEqual(self.summary()[2], "Proxy assigned within group UK")
        self.wait_saved(proxy_mode="auto", proxy_group="UK")

        self.card.locator('[data-proxy="pool"]').click()
        expect(group).not_to_be_visible()
        pool = self.page.get_by_label("Pool proxy", exact=True)
        self.assertEqual(pool.locator("option").all_inner_texts(), [
            "Choose a proxy…", "203.0.113.18 · UK · can take 3 more", "203.0.113.17 · UK · can take 3 more",
            "198.51.100.41 · US · can take 1 more",
        ])
        self.assertEqual(self.summary()[2], "Proxy from the pool, not picked")
        expect(self.page.locator('[data-profile="profile-18"]')).to_contain_text("no proxy picked yet")
        pool.select_option("p41")
        self.assertEqual(self.summary()[2], "Proxy 198.51.100.41")
        expect(self.page.locator('[data-profile="profile-18"]')).to_contain_text("onboarded through 198.51.100.41")
        self.wait_saved(proxy_mode="pool", proxy_id="p41", proxy_group="UK")
        expect(self.card.get_by_text("Own proxy", exact=False)).to_have_count(0)

        # Another node has another pool: what was picked on the first does not follow.
        self.page.get_by_label("Exit node", exact=True).select_option("direct-2")
        expect(self.card.locator('[data-proxy="pool"]')).to_have_text("Pick from the pool (1)")
        expect(pool).to_have_value("")
        self.wait_saved(node_id="direct-2", proxy_id=None, proxy_group=None)

    def test_the_organization_and_its_domain(self):
        self.open()
        org = self.page.get_by_label("Organization", exact=True)
        domain = self.page.get_by_label("Email domain", exact=True)
        self.assertEqual(org.locator("option").all_inner_texts(), ["Team A", "Team B", "Team C"])
        expect(domain).to_have_value("example.com")

        org.select_option("org-b")
        self.assertEqual(domain.locator("option").all_inner_texts(), ["Choose a domain…", "@example.com", "@example.net"])
        expect(domain).to_have_value("")
        self.assertEqual(self.summary()[3], "Organization Team B · no domain chosen")
        domain.select_option("example.net")
        self.assertEqual(self.summary()[3], "Organization Team B · @example.net")
        self.wait_saved(org_id="org-b", domain="example.net")

        org.select_option("org-c")
        expect(domain).not_to_be_visible()
        self.assertEqual(self.summary()[3], "Organization Team C")
        self.wait_saved(org_id="org-c", domain=None)

    def test_a_token_without_profiles_may_leave_the_organization_or_name_it(self):
        self.orgs = []
        self.open()
        expect(self.page.get_by_label("Organization", exact=True)).to_have_count(0)
        mode = self.page.get_by_label("Organization (optional)", exact=True)
        expect(mode).to_have_value("")
        expect(self.page.get_by_test_id("virgin-hint")).to_contain_text("an exit that carries no account yet")
        self.assertEqual(self.summary()[3], "Organization unspecified")

        mode.select_option("manual")
        expect(self.page.get_by_test_id("virgin-hint")).not_to_be_visible()
        self.assertEqual(self.summary()[3], "Organization UUID not entered")
        uuid = self.page.get_by_label("Organization UUID", exact=True)
        uuid.fill("not-a-uuid")
        uuid.blur()
        expect(self.page.get_by_role("alert").filter(has_text="An organization UUID looks like")).to_be_visible()
        self.wait_saved(org_manual=True, org_uuid=None)
        uuid.fill("0a1b2c3d-0000-4000-8000-000000000000")
        uuid.blur()
        self.assertEqual(self.summary()[3], "Organization 0a1b2c3d…")
        self.wait_saved(org_uuid="0a1b2c3d-0000-4000-8000-000000000000")

    def test_the_note_and_the_limits_are_kept_only_when_they_fit(self):
        self.open()
        expect(self.page.get_by_test_id("advanced-summary")).to_have_text("· note, limits")
        self.page.get_by_test_id("advanced").locator("summary").click()
        expect(self.page.get_by_text("Each row uses its own email address", exact=True)).to_be_visible()
        concurrency = self.page.get_by_label("Concurrency limit", exact=True)
        rpm = self.page.get_by_label("RPM limit", exact=True)
        for unfit in ("-1", "1.5", "100001"):
            rpm.fill(unfit)
            rpm.blur()
            expect(self.page.get_by_role("alert").filter(has_text="A whole number from 0 to 100000")).to_be_visible()
        self.assertFalse(any(save["rpm_limit"] is not None for save in self.saves), "an unfit limit is not saved")
        rpm.fill("20")
        rpm.blur()
        concurrency.fill("2")
        concurrency.press("Enter")
        note = self.page.get_by_label("Note (optional)", exact=True)
        note.fill("  batch 3 ")
        note.blur()
        expect(self.page.get_by_test_id("advanced-summary")).to_have_text("· set: concurrency 2 · RPM 20 · has a note")
        self.wait_saved(rpm_limit=20, concurrency_limit=2, note="batch 3")
        rpm.fill("")
        rpm.blur()
        self.wait_saved(rpm_limit=None)

    # --- refresh -------------------------------------------------------------------

    def test_a_refresh_keeps_what_is_still_offered_and_says_so(self):
        self.open()
        self.page.get_by_role("button", name="Refresh", exact=True).click()
        expect(self.page.get_by_test_id("token-notice")).to_have_text(
            "Read again what this token may use; the choices are all still on offer. The check has to be run again.")
        expect(self.page.get_by_label("Exit node", exact=True)).to_have_value("direct-1")

    def test_a_refresh_says_which_choices_are_gone(self):
        self.preset = {**EMPTY, "node_id": "direct-1", "proxy_mode": "auto", "proxy_group": "US",
                       "org_id": "org-a", "domain": "example.com", "scope_group": "uk"}
        self.open()
        self.nodes = [NODES[1]]
        self.orgs = ORGS[1:]

        self.page.get_by_role("button", name="Refresh", exact=True).click()

        expect(self.page.get_by_test_id("token-notice")).to_have_text(
            "Changed since the last read: the chosen node direct-1 is no longer among the usable nodes; changed to direct-2; "
            "proxy group US belonged to the former node; changed to any group; "
            "the chosen organization is no longer among the organization profiles; changed to Team B, choose its email domain. "
            "Confirm the choices, then check again.")
        expect(self.page.get_by_label("Exit node", exact=True)).to_have_value("direct-2")
        expect(self.page.get_by_label("Organization", exact=True)).to_have_value("org-b")
        expect(self.page.get_by_label("Email domain", exact=True)).to_have_value("")
        self.assertEqual(self.summary()[1:4], ["Node direct-2", "Proxy assigned by the platform", "Organization Team B · no domain chosen"])
        self.wait_saved(node_id="direct-2", proxy_group=None, org_id="org-b", domain=None)

    def test_a_token_revoked_since_keeps_its_preview_and_offers_nothing(self):
        self.open()
        del self.accepted[TOKEN]

        self.page.get_by_role("button", name="Refresh", exact=True).click()

        expect(self.page.get_by_test_id("token-status")).to_contain_text("The platform no longer accepts this token (refused at")
        expect(self.page.get_by_test_id("token-preview")).to_have_text("1dk_onb_demo…only")
        expect(self.page.get_by_test_id("preset-empty")).to_contain_text("The saved token can no longer be used.")
        expect(self.card.locator("[data-step]")).to_have_count(0)
        expect(self.page.get_by_test_id("preset-summary")).not_to_be_visible()
        expect(self.page.locator('[data-profile="profile-18"]')).to_contain_text("token invalid, cannot match")
        expect(self.page.get_by_role("button", name="Check parameters", exact=True)).to_be_disabled()
        expect(self.page.get_by_role("button", name="Refresh", exact=True)).to_be_enabled()

        self.page.get_by_role("button", name="Change", exact=True).click()
        self.page.get_by_label("Onboarder token", exact=True).fill(SECOND)
        self.page.get_by_role("button", name="Save and read", exact=True).click()
        expect(self.card.locator("[data-step]")).to_have_count(3)
        expect(self.page.get_by_role("button", name="Check parameters", exact=True)).to_be_enabled()

    # --- which profiles, and the check ----------------------------------------------

    def test_profiles_are_listed_for_the_chosen_group_without_a_choice_of_rows(self):
        self.groups = [{"id": "uk", "name": "UK HTTP", "pending": 3, "onboarded": 0},
                       {"id": "us", "name": "US", "pending": 1, "onboarded": 0}]
        self.rows = [row(18), row(17, exit_checked=False, exit_reachable=False, exit_ip=None), row(16)]
        self.open()
        scope = self.page.get_by_label("Profiles to onboard", exact=True)
        self.assertEqual(scope.locator("option").all_inner_texts(),
                         ["UK HTTP · to onboard 3 · onboarded 0", "US · to onboard 1 · onboarded 0"])
        expect(scope).to_have_value("uk")
        table = self.page.get_by_role("table", name="Profiles in the chosen group")
        expect(table.locator("tbody tr")).to_have_count(3)
        expect(table.locator("input[type=checkbox]")).to_have_count(0)
        expect(self.page.locator('[data-profile="profile-17"]')).to_contain_text("proxy never checked")
        expect(self.page.locator('[data-profile="profile-17"]')).to_contain_text("No link made yet")
        expect(self.page.get_by_role("button", name="Start", exact=False)).to_have_count(0)

        scope.select_option("us")
        self.wait_saved(scope_group="us")
        asked = [path for method, path, _ in self.calls if path == "/auto-onboarding/scopes"]
        self.assertTrue(asked)

    def test_no_group_left_to_onboard_says_so(self):
        self.groups, self.rows = [], []
        self.open()
        scope = self.page.get_by_label("Profiles to onboard", exact=True)
        expect(scope).to_be_disabled()
        self.assertEqual(scope.locator("option").all_inner_texts(), ["No profiles to onboard"])
        expect(self.page.get_by_role("table", name="Profiles in the chosen group")).to_contain_text(
            "No profiles to onboard. Only profiles in a group that have an email address and are not onboarded yet are listed here.")
        expect(self.page.get_by_label("Exit node", exact=True)).to_be_enabled()

    def test_the_check_names_what_is_missing_and_what_each_profile_would_do(self):
        self.open()
        check = self.page.get_by_role("button", name="Check parameters", exact=True)
        result = self.page.get_by_test_id("check-result")
        expect(result).to_have_text("Not checked yet.")

        self.preflight = {"ok": False, "version": 1, "rows": [], "ready": 0, "blocked": 0,
                          "issues": [{"code": "proxy_missing", "field": "proxy_id"}, {"code": "domain_missing", "field": "domain"}]}
        check.click()
        expect(result).to_contain_text("The parameters would not do yet.")
        expect(result.get_by_role("listitem")).to_have_text(
            ["Pick a proxy from the pool first.", "Choose an email domain for the organization first."])

        self.preflight = {"ok": True, "version": 2, "issues": [], "ready": 1, "blocked": 2, "rows": [
            {**row(18), "status": "ready", "proxy_id": "p18"},
            {**row(17), "status": "domain_mismatch", "proxy_id": None},
            {**row(16), "status": "exit_not_in_pool", "proxy_id": None},
        ]}
        check.click()
        expect(result).to_have_text("Check passed: 1 of 3 profiles would be onboarded.")
        expect(self.tr(18).get_by_test_id("row-check")).to_have_text("Check: Would be onboarded")
        expect(self.tr(17).get_by_test_id("row-check")).to_have_text("Check: Address is not under the chosen domain")
        expect(self.tr(16).get_by_test_id("row-check")).to_have_text("Check: Exit is not in the node’s pool, or is full")

        # A choice made after the check makes it stale.
        self.card.locator('[data-proxy="auto"]').click()
        expect(result).to_have_text("Not checked yet.")
        expect(self.tr(18).get_by_test_id("row-check")).to_have_count(0)

    def test_a_check_on_a_token_revoked_since_shows_it_refused(self):
        self.open()
        del self.accepted[TOKEN]
        self.page.get_by_role("button", name="Check parameters", exact=True).click()
        expect(self.page.get_by_test_id("check-result")).to_have_text(
            "The platform refused the saved token: it is invalid or was revoked. Change it to a valid one first.")
        expect(self.page.get_by_test_id("token-status")).to_contain_text("The platform no longer accepts this token")

    # --- who reaches the page -------------------------------------------------------

    # --- a row: its browser, and its authorization ---------------------------------

    def test_each_row_has_run_and_a_link_button_and_says_why_there_is_no_link_yet(self):
        self.rows = [row(19, onboarded=True), row(18), row(17, can_launch=False)]
        self.groups = [{"id": "uk", "name": "UK HTTP", "pending": 2, "onboarded": 1}]
        self.open()
        table = self.page.get_by_role("table", name="Profiles in the chosen group")
        self.assertEqual(table.locator("thead th").all_inner_texts(), ["PROFILE / EMAIL", "OWN EXIT", "STATUS", "ACTIONS"])
        expect(table.locator("input[type=checkbox]")).to_have_count(0)
        expect(self.page.get_by_test_id("run-note")).to_contain_text("Signing in and consenting are done by hand")

        expect(self.browser_button(18)).to_have_text("Run")
        expect(self.tr(18).get_by_test_id("browser-state")).to_have_text("Browser Stopped")
        expect(self.tr(18).get_by_test_id("authorization-state")).to_have_text("Authorization No link made yet")
        expect(self.link_button(18)).to_have_text("Make authorize link")
        expect(self.link_button(18)).to_be_disabled()
        expect(self.tr(18).get_by_test_id("link-block")).to_have_text("Run the browser first")

        # Onboarded earlier: its browser can still be run, and it gets no link.
        expect(self.browser_button(19)).to_be_enabled()
        expect(self.link_button(19)).to_be_disabled()
        expect(self.tr(19).get_by_test_id("link-block")).to_have_text("Already onboarded")
        expect(self.tr(19)).to_contain_text("Onboarded earlier; it cannot be onboarded again.")

        # No permission to launch: neither button.
        expect(self.browser_button(17)).to_be_disabled()
        expect(self.link_button(17)).to_be_disabled()
        expect(self.tr(17).get_by_test_id("link-block")).to_have_text("No permission to run this profile")
        self.assertEqual(self.posts("/job"), [])

    def test_run_and_stop_are_the_profiles_own_requests(self):
        self.open()
        self.run_row(18)
        launched = [(path, json.loads(body)) for method, path, body in self.calls if path.endswith("/launch")]
        self.assertEqual(launched, [("/profiles/profile-18/launch", {"headless": False})])
        expect(self.browser_button(18)).to_have_text("Stop")
        expect(self.link_button(18)).to_be_enabled()
        expect(self.tr(18).get_by_test_id("link-block")).to_have_count(0)
        expect(self.tr(17).get_by_test_id("browser-state")).to_have_text("Browser Stopped")

        self.browser_button(18).click()
        expect(self.tr(18).get_by_test_id("browser-state")).to_have_text("Browser Stopped")
        self.assertEqual(self.posts("/close"), ["/profiles/profile-18/close"])
        expect(self.browser_button(18)).to_have_text("Run")
        expect(self.link_button(18)).to_be_disabled()
        self.assertEqual(self.posts("/job"), [], "running a browser asks the platform for nothing")

    def test_a_link_is_made_and_finished_with_the_authorization_code(self):
        self.finish = None
        self.open()
        self.make_link(18)
        link = self.links["profile-18"]
        expect(self.dialog).to_contain_text("UK HTTP · 018 · user018@example.com")
        expect(self.dialog).to_contain_text("Authorizing from another IP may get the account flagged.")
        expect(self.dialog.get_by_test_id("authorize-url")).to_have_text(link)
        expect(self.dialog.get_by_test_id("link-where")).to_contain_text(
            "The link was opened in a new tab of this row’s browser. Check there that user018@example.com is signed in")
        expect(self.dialog.get_by_role("button", name="Copy", exact=True)).to_be_visible()
        # The link was opened when it was made; there is no button to open it.
        expect(self.dialog.get_by_role("button").filter(has_text="Open")).to_have_count(0)
        self.assertNotIn("callback", self.dialog.inner_text().lower())
        self.assertRegex(self.dialog.get_by_test_id("time-left").inner_text(), r"^(29|30):\d\d$")
        submit = self.dialog.get_by_test_id("submit-code")
        expect(submit).to_have_text("Submit the authorization code")
        expect(submit).to_be_disabled()
        expect(self.tr(18).get_by_test_id("authorization-state")).to_contain_text("Waiting for the authorization code")
        expect(self.tr(18)).to_contain_text("This authorization: direct-1 · 203.0.113.18")

        self.paste_own_code(18)
        expect(self.dialog.get_by_test_id("authorize-working")).to_contain_text("The platform is finishing the onboarding…")
        expect(self.dialog.get_by_test_id("submit-code")).to_have_count(0)
        self.assertEqual(self.codes, [("profile-18", f"code-18#{self.states['profile-18']}")])
        # A code the platform took is not success.
        expect(self.tr(18).get_by_test_id("authorization-state")).to_contain_text("The platform is working on it")
        self.assertEqual(self.scope_options(), ["UK HTTP · to onboard 3 · onboarded 0"])

        self.finish = {"status": "done", "outcome": "onboarded"}
        expect(self.dialog.get_by_test_id("result-title")).to_have_text("Onboarded")
        expect(self.dialog).to_contain_text("user018@example.com joined the platform’s default group.")
        expect(self.dialog.get_by_test_id("dialog-done")).to_have_text("Done")
        expect(self.dialog.get_by_test_id("regenerate")).to_have_count(0)
        expect(self.tr(18).get_by_test_id("authorization-state")).to_contain_text("Onboarded")
        self.assertEqual(self.scope_options(), ["UK HTTP · to onboard 2 · onboarded 1"])

        # Close browser closes this row's browser, and with the account onboarded the dialog too.
        self.dialog.get_by_test_id("close-browser").click()
        expect(self.dialog).to_have_count(0)
        self.assertEqual(self.posts("/close"), ["/profiles/profile-18/close"])
        expect(self.tr(18).get_by_test_id("browser-state")).to_have_text("Browser Stopped")
        expect(self.link_button(18)).to_be_disabled()
        expect(self.tr(18).get_by_test_id("link-block")).to_have_text("Already onboarded")

    def test_closing_the_dialog_cancels_nothing_and_the_same_link_is_come_back_to(self):
        self.open()
        self.make_link(18)
        link = self.links["profile-18"]
        self.page.keyboard.press("Escape")
        expect(self.dialog).to_have_count(0)
        expect(self.tr(18).get_by_test_id("authorization-state")).to_contain_text("Waiting for the authorization code")
        expect(self.link_button(18)).to_have_text("Continue authorization")
        self.assertEqual(self.posts("/job/cancel"), [])

        self.link_button(18).click()
        expect(self.dialog.get_by_test_id("authorize-url")).to_have_text(link)
        expect(self.dialog.get_by_test_id("link-where")).to_contain_text("The link was opened in this row’s browser when it was made.")
        self.assertEqual(self.posts("/rows/profile-18/job"), ["/auto-onboarding/rows/profile-18/job"], "no second link is made")

        # The browser closed while the code is awaited: the dialog and the authorization stay.
        self.dialog.get_by_test_id("close-browser").click()
        expect(self.dialog.get_by_test_id("link-where")).to_contain_text("This row’s browser is stopped, and the authorize page went with it.")
        expect(self.dialog.get_by_test_id("close-browser")).to_be_disabled()
        expect(self.dialog.get_by_test_id("authorize-pending")).to_be_visible()
        self.assertEqual(self.posts("/job/cancel"), [])

    def test_another_rows_code_is_refused_and_the_authorization_goes_on_waiting(self):
        self.open()
        self.make_link(18)
        self.dialog.get_by_label("Authorization code", exact=True).fill("code-17#state-profile-17-9")
        self.dialog.get_by_test_id("submit-code").click()
        expect(self.dialog.get_by_test_id("dialog-error")).to_contain_text("The platform refused this code (callback_invalid)")
        expect(self.dialog.get_by_test_id("authorize-pending")).to_be_visible()
        self.assertEqual(self.codes, [])
        self.assertEqual(self.scope_options(), ["UK HTTP · to onboard 3 · onboarded 0"])

    def test_cancel_ends_the_authorization_and_a_new_link_can_be_made(self):
        self.open()
        self.make_link(18)
        self.dialog.get_by_test_id("cancel-authorization").click()
        expect(self.dialog).to_have_count(0)
        self.assertEqual(self.posts("/job/cancel"), ["/auto-onboarding/rows/profile-18/job/cancel"])
        expect(self.tr(18).get_by_test_id("authorization-state")).to_contain_text("Cancelled")
        expect(self.link_button(18)).to_have_text("Make authorize link")
        expect(self.link_button(18)).to_be_enabled()
        expect(self.tr(18).get_by_test_id("browser-state")).to_contain_text("Running")

    def test_a_refusal_by_the_platform_is_shown_on_that_row_alone(self):
        self.job_refusal = ("platform_proxy_full", "proxy_full: exit has no free slot")
        self.open()
        self.run_row(18)
        self.link_button(18).click()
        expect(self.tr(18).get_by_test_id("row-error")).to_contain_text("this exit has no room for another account")
        expect(self.dialog).to_have_count(0)
        expect(self.tr(17).get_by_test_id("row-error")).to_have_count(0)
        expect(self.tr(18).get_by_test_id("authorization-state")).to_contain_text("No link made yet")
        expect(self.link_button(18)).to_be_enabled()

    def test_what_is_not_this_rows_own_account_onboarded_is_said_and_not_counted(self):
        self.finish = {"status": "done", "outcome": "mismatch", "account_email": "another@example.com"}
        self.open()
        self.make_link(18)
        self.paste_own_code(18)
        expect(self.dialog.get_by_test_id("result-title")).to_have_text("The account onboarded is not this row’s")
        expect(self.dialog).to_contain_text("another@example.com was onboarded, not this row’s user018@example.com.")
        expect(self.dialog.get_by_test_id("dialog-done")).to_have_text("Close")
        self.assertEqual(self.scope_options(), ["UK HTTP · to onboard 3 · onboarded 0"])

        self.finish = {"status": "failed", "outcome": "failed", "error_code": "account_exists"}
        self.dialog.get_by_test_id("regenerate").click()
        expect(self.dialog.get_by_test_id("authorize-url")).to_have_text(
            "https://claude.example/oauth/authorize?client_id=demo-only&state=state-profile-18-2")
        self.paste_own_code(18)
        expect(self.dialog.get_by_test_id("result-title")).to_have_text(
            "The platform already has this account, to be looked into (account_exists)")
        expect(self.dialog).to_contain_text("This row is not counted as onboarded.")
        self.dialog.get_by_test_id("dialog-done").click()
        expect(self.link_button(18)).to_have_text("Make authorize link")
        self.assertEqual(self.scope_options(), ["UK HTTP · to onboard 3 · onboarded 0"])

    def test_a_check_on_screen_is_renewed_once_a_row_is_onboarded(self):
        self.finish = None
        self.open()
        result = self.page.get_by_test_id("check-result")
        self.preflight = self.checked(18, 17, 16)
        self.page.get_by_role("button", name="Check parameters", exact=True).click()
        expect(result).to_have_text("Check passed: 3 of 3 profiles would be onboarded.")
        expect(self.tr(18).get_by_test_id("row-check")).to_have_text("Check: Would be onboarded")

        self.make_link(18)
        self.paste_own_code(18)
        expect(self.dialog.get_by_test_id("authorize-working")).to_be_visible()
        self.assertEqual(len(self.posts("/preflight")), 1, "a code handed over changes nothing the check judged")

        # The platform is done; the server's check now knows the row as onboarded.
        self.preflight = self.checked(18, 17, 16, onboarded=(18,))
        self.finish = {"status": "done", "outcome": "onboarded"}
        expect(self.dialog.get_by_test_id("result-title")).to_have_text("Onboarded")
        expect(result).to_have_text("Check passed: 2 of 3 profiles would be onboarded.")
        expect(self.tr(18).get_by_test_id("row-check")).to_have_text("Check: Already onboarded")
        expect(self.tr(17).get_by_test_id("row-check")).to_have_text("Check: Would be onboarded")

        # Closing the dialog reads the rows again; the check is not asked for a third time.
        self.dialog.get_by_test_id("close-browser").click()
        expect(self.dialog).to_have_count(0)
        self.page.wait_for_timeout(700)
        self.assertEqual(len(self.posts("/preflight")), 2)
        expect(self.tr(18).get_by_test_id("row-check")).to_have_text("Check: Already onboarded")

    def test_the_check_is_dropped_once_the_groups_last_profile_is_onboarded(self):
        self.rows = [row(18)]
        self.groups = [{"id": "uk", "name": "UK HTTP", "pending": 1, "onboarded": 0}]
        self.open()
        result = self.page.get_by_test_id("check-result")
        self.preflight = self.checked(18)
        self.page.get_by_role("button", name="Check parameters", exact=True).click()
        expect(result).to_have_text("Check passed: 1 of 1 profiles would be onboarded.")

        self.make_link(18)
        self.paste_own_code(18)
        expect(self.dialog.get_by_test_id("result-title")).to_have_text("Onboarded")
        # Nothing is left for the server to judge: it would only say the group is empty.
        expect(result).to_have_text("Not checked yet.")
        expect(self.tr(18).get_by_test_id("row-check")).to_have_count(0)
        self.dialog.get_by_test_id("dialog-done").click()
        self.page.wait_for_timeout(700)
        self.assertEqual(len(self.posts("/preflight")), 1)
        self.assertEqual(self.scope_options(), ["UK HTTP · to onboard 0 · onboarded 1"])

    def test_a_check_that_cannot_be_renewed_is_dropped_without_a_word(self):
        self.open()
        result = self.page.get_by_test_id("check-result")
        check = self.page.get_by_role("button", name="Check parameters", exact=True)
        self.preflight = self.checked(18, 17, 16)
        check.click()
        expect(self.tr(17).get_by_test_id("row-check")).to_have_text("Check: Would be onboarded")

        self.preflight_error = (503, "oneday_unavailable")
        self.make_link(18)
        self.paste_own_code(18)
        expect(self.dialog.get_by_test_id("result-title")).to_have_text("Onboarded")
        expect(result).to_have_text("Not checked yet.")
        expect(self.page.locator('[data-testid="row-check"]')).to_have_count(0)
        self.dialog.get_by_test_id("dialog-done").click()
        self.page.wait_for_timeout(700)
        self.assertEqual(len(self.posts("/preflight")), 2, "asked once more for the row, and not again")
        expect(check).to_be_enabled()

        # Asked for by hand, the same failure is said.
        check.click()
        expect(result).to_have_text("The platform could not be reached, so nothing was checked. Try again.")

    def test_a_link_that_could_not_be_opened_is_left_to_be_copied(self):
        self.link_opens = None
        self.open()
        self.make_link(18)
        expect(self.dialog.get_by_test_id("link-where")).to_contain_text(
            "The link could not be opened in this row’s browser. Press Copy")
        expect(self.dialog.get_by_test_id("authorize-url")).to_have_text(self.links["profile-18"])

    def test_a_link_that_only_a_new_window_could_show_says_so(self):
        self.link_opens = "window"
        self.open()
        self.make_link(18)
        expect(self.dialog.get_by_test_id("link-where")).to_contain_text(
            "The link was opened in a new window of this row’s browser, because no tab could be opened")
        expect(self.dialog.get_by_test_id("link-where")).to_contain_text("user018@example.com")

    def test_the_token_is_not_changed_while_an_authorization_is_open(self):
        self.open()
        self.make_link(18)
        self.page.keyboard.press("Escape")
        self.page.get_by_role("button", name="Change", exact=True).click()
        self.page.get_by_label("Onboarder token", exact=True).fill(SECOND)
        self.page.get_by_role("button", name="Save and read", exact=True).click()
        expect(self.page.get_by_role("alert").filter(has_text="Authorizations are still open (user018@example.com).")).to_be_visible()
        self.assertEqual(self.saved_token, TOKEN)

    def test_an_authorization_made_with_a_token_refused_since_cannot_be_asked_about(self):
        self.rows = [row(18, authorization=authorization()), row(17)]
        self.refused = True
        self.running_profile_ids = ["profile-18", "profile-17"]
        self.open()
        expect(self.tr(18).get_by_test_id("authorization-state")).to_contain_text("Cannot be asked about · token invalid")
        expect(self.link_button(18)).to_have_text("Continue authorization")
        expect(self.link_button(18)).to_be_disabled()
        expect(self.link_button(17)).to_be_disabled()
        expect(self.tr(17).get_by_test_id("link-block")).to_have_text("Token invalid")
        expect(self.browser_button(17)).to_be_enabled()

    def test_the_page_is_in_the_menu_for_administrators_and_operators_only(self):
        self.profiles = []
        self.page.goto(self.origin)
        link = self.page.get_by_role("link", name="Auto onboarding", exact=True)
        expect(link).to_be_visible()
        link.click()
        expect(self.page.get_by_role("heading", name="Platform access preset", exact=True)).to_be_visible()

        self.role = "operator"
        self.open()
        expect(self.page.get_by_role("link", name="Auto onboarding", exact=True)).to_be_visible()

        self.role = "viewer"
        self.page.goto(f"{self.origin}/auto-onboarding/")
        expect(self.page.get_by_role("alert").filter(has_text="Access denied")).to_be_visible()
        expect(self.page.get_by_role("link", name="Auto onboarding", exact=True)).to_have_count(0)


if __name__ == "__main__":
    suite = unittest.TestSuite(
        AutoOnboardingUI(name) for name in AutoOnboardingUI.__dict__ if name.startswith("test_")
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
