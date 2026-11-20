"""Email addresses on profiles: the column, the checked import, and Run copying the address.

.venv/bin/python web/tests/profile_emails_ui.py
Uses the same local server and isolated Chromium fixture as stage_one_ui.
"""

import unittest
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect
from stage_two_ui import StageTwoUI, profile

PROXY = {"type": "http", "server": "proxy.example:8080"}


def newer(number, **values):
    """A profile created since email addresses were introduced, so one it may receive."""
    return profile(number, email_allowed=True, **values)


class ProfileEmailsUI(StageTwoUI):
    def setUp(self):
        super().setUp()
        self.profiles = [
            newer(1, proxy_config=PROXY, email="held@example.com"),
            newer(2, proxy_config=PROXY),
            newer(3, proxy_config=PROXY),
            newer(4),  # no proxy: nothing to check, so never an address
            newer(5, proxy_config=PROXY),
        ]
        self.unreachable = {"profile-3"}
        self.check_failures = set()
        self.taken_in_trash = {}
        self.refusals = {}
        self.imports = []
        self.checked = []
        self.launched = []

    def find(self, profile_id):
        return next(item for item in self.profiles if item["id"] == profile_id)

    def route(self, route):
        request = route.request
        full_path = request.url.split("/api/v1", 1)[1]
        parsed = urlsplit(full_path)
        path, method = parsed.path, request.method
        if path == "/profiles/emails/taken":
            wanted = {email.lower() for email in request.post_data_json["emails"]}
            data = {
                "taken": [
                    {"email": email, "profile_id": "gone", "profile_name": name, "in_trash": True}
                    for email, name in self.taken_in_trash.items()
                    if email in wanted
                ]
            }
        elif path == "/profiles/emails":
            body = request.post_data_json
            self.imports.append(body)
            updated, skipped = [], []
            for item in body["assignments"]:
                if item["id"] in self.refusals:
                    skipped.append({"id": item["id"], "reason": self.refusals[item["id"]]})
                    continue
                target = self.find(item["id"])
                target.update(email=item["email"], row_version=target["row_version"] + 1)
                updated.append(target)
            data = {"updated": updated, "skipped": skipped}
        elif path.endswith("/check-proxy"):
            profile_id = path.split("/")[2]
            self.checked.append(profile_id)
            self.calls.append((method, full_path, request.post_data))
            if profile_id in self.check_failures:
                return route.fulfill(status=500, json={"detail": "private upstream detail"})
            reachable = profile_id not in self.unreachable
            return route.fulfill(
                json={
                    "reachable": reachable,
                    "error": None if reachable else "The proxy did not answer in time.",
                    "latency_ms": 120 if reachable else None,
                    "location": {
                        "ip": "203.0.113.7",
                        "country": "BE",
                        "timezone": "Europe/Brussels",
                    }
                    if reachable
                    else None,
                    "findings": [],
                    "checked_at": "2026-11-20T08:00:00Z",
                }
            )
        elif path.endswith("/email") and method == "DELETE":
            target = self.find(path.split("/")[2])
            assert parse_qs(parsed.query)["row_version"] == [str(target["row_version"])]
            target.update(email=None, row_version=target["row_version"] + 1)
            data = target
        elif path.endswith("/launch"):
            self.launched.append(path.split("/")[2])
            self.calls.append((method, full_path, request.post_data))
            return route.fulfill(json={"status": "launched", "profile_id": path.split("/")[2]})
        else:
            return super().route(route)
        self.calls.append((method, full_path, request.post_data))
        route.fulfill(json={"success": True, "message": "", "data": data})

    def open_import(self):
        self.page.get_by_role("button", name="Import emails", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="Import emails")
        return dialog, dialog.get_by_label("One email address per line (up to 100)", exact=True)

    def row(self, number):
        return self.page.get_by_role("row", name=f"Account {number:03}")

    def test_email_column_follows_the_name_and_search_finds_an_address(self):
        self.open_profiles()
        headers = self.page.get_by_role("columnheader").all_text_contents()
        self.assertEqual([text.strip() for text in headers[1:3]], ["Name", "Email"])
        expect(self.row(1)).to_contain_text("held@example.com")
        expect(self.row(2).get_by_role("cell").nth(2)).to_have_text("—")
        self.page.get_by_label("Search profiles", exact=True).fill("HELD@example")
        expect(self.row(1)).to_be_visible()
        expect(self.row(2)).to_have_count(0)

    def test_import_writes_only_after_the_checks_and_only_to_profiles_that_passed(self):
        self.open_profiles()
        dialog, field = self.open_import()
        expect(dialog).to_contain_text("without an email address (4)")
        expect(dialog).to_contain_text("already have an email address are never changed: 1.")
        start = dialog.get_by_role("button", name="Check proxies and preview", exact=True)
        expect(start).to_be_disabled()
        field.fill(
            "first@example.com\nnot-an-email\nHELD@example.com\nsecond@example.com\n"
            "first@example.com\nthird@example.com"
        )
        expect(dialog).to_contain_text("Lines: 6; usable: 3; cannot be imported: 3.")
        expect(dialog).to_contain_text("Line 2: not a valid email address")
        expect(dialog).to_contain_text('Line 3: already on profile "Account 001"')
        expect(dialog).to_contain_text("Line 5: repeats an earlier line")
        start.click()

        confirm = dialog.get_by_role("button", name="Import 2 email addresses", exact=True)
        expect(confirm).to_be_visible()
        # Checked, previewed, and still nothing written.
        self.assertEqual(sorted(self.checked), ["profile-2", "profile-3", "profile-5"])
        self.assertEqual(self.imports, [])
        expect(dialog).to_contain_text(
            "Profiles to receive an address: 2; profiles skipped: 2; addresses left over: 1."
        )
        pairs = dialog.get_by_role("list", name="Profiles and the email address each receives")
        rows = pairs.get_by_role("listitem")
        expect(rows.nth(0)).to_contain_text("Proxy reachable")
        expect(rows.nth(0)).to_contain_text("first@example.com")
        expect(rows.nth(1)).to_contain_text("Proxy not reachable")
        expect(rows.nth(1)).to_contain_text("Skipped")
        expect(rows.nth(2)).to_contain_text("No proxy configured")
        # The address the unreachable profile would have had moved on to the next one.
        expect(rows.nth(3)).to_contain_text("second@example.com")
        expect(dialog).to_contain_text("Addresses left over: 1")
        expect(dialog).to_contain_text("third@example.com")

        confirm.click()
        expect(dialog.get_by_text("Email addresses written: 2.", exact=True)).to_be_visible()
        self.assertEqual(
            self.imports,
            [
                {
                    "assignments": [
                        {"id": "profile-2", "row_version": 3, "email": "first@example.com"},
                        {"id": "profile-5", "row_version": 3, "email": "second@example.com"},
                    ]
                }
            ],
        )
        dialog.get_by_role("button", name="Done", exact=True).click()
        expect(self.row(2)).to_contain_text("first@example.com")
        expect(self.row(5)).to_contain_text("second@example.com")
        expect(self.row(3).get_by_role("cell").nth(2)).to_have_text("—")
        # The checks made for the import are the ones the list now shows.
        expect(self.row(3)).to_contain_text("The proxy did not answer in time.")

    def test_profiles_from_before_this_version_are_never_targets(self):
        # As the server reports the rows an upgrade found: no address, and none allowed.
        self.find("profile-2")["email_allowed"] = False
        del self.find("profile-5")["email_allowed"]  # an answer without the field is not a yes
        self.open_profiles()
        dialog, field = self.open_import()
        expect(dialog).to_contain_text("without an email address (2)")
        expect(dialog).to_contain_text(
            "Profiles created before this version cannot receive an email address: 2."
        )
        field.fill("a@example.com\nb@example.com")
        dialog.get_by_role("button", name="Check proxies and preview", exact=True).click()
        # What is left is one unreachable proxy and one profile without a proxy.
        expect(dialog.get_by_role("alert")).to_contain_text("No profile passed the proxy check")
        expect(dialog).not_to_contain_text("Account 002")
        expect(dialog).not_to_contain_text("Account 005")
        self.assertEqual(self.checked, ["profile-3"])
        dialog.get_by_role("button", name="Cancel", exact=True).click()

        # Selecting them does not make them targets either.
        for number in (2, 5):
            self.page.get_by_role(
                "checkbox", name=f"Select Account {number:03}", exact=True
            ).check()
        dialog, field = self.open_import()
        expect(dialog.get_by_role("alert")).to_have_text(
            "No selected profile can receive an email address."
        )
        field.fill("a@example.com")
        expect(
            dialog.get_by_role("button", name="Check proxies and preview", exact=True)
        ).to_be_disabled()
        self.assertEqual(self.checked, ["profile-3"])
        self.assertEqual(self.imports, [])

    def test_selected_rows_are_the_only_targets_and_checking_stops_once_every_address_has_a_home(
        self,
    ):
        self.profiles += [newer(number, proxy_config=PROXY) for number in range(6, 12)]
        self.open_profiles()
        dialog, field = self.open_import()
        field.fill("only@example.com")
        dialog.get_by_role("button", name="Check proxies and preview", exact=True).click()
        expect(
            dialog.get_by_role("button", name="Import 1 email address", exact=True)
        ).to_be_visible()
        # Nine profiles have a proxy; one address needs one that passes.
        self.assertLess(len(self.checked), 9)
        dialog.get_by_role("button", name="Cancel", exact=True).click()
        self.assertEqual(self.imports, [])

        self.checked.clear()
        for number in (1, 5):
            self.page.get_by_role(
                "checkbox", name=f"Select Account {number:03}", exact=True
            ).check()
        dialog, field = self.open_import()
        expect(dialog).to_contain_text("selected profiles without an email address (1)")
        field.fill("picked@example.com")
        dialog.get_by_role("button", name="Check proxies and preview", exact=True).click()
        dialog.get_by_role("button", name="Import 1 email address", exact=True).click()
        expect(dialog.get_by_text("Email addresses written: 1.", exact=True)).to_be_visible()
        self.assertEqual(self.checked, ["profile-5"])
        self.assertEqual([item["id"] for item in self.imports[0]["assignments"]], ["profile-5"])

    def test_address_held_in_trash_and_a_server_refusal_are_both_said(self):
        self.taken_in_trash = {"old@example.com": "Retired account"}
        self.refusals = {"profile-2": "The proxy check is too old; check the proxy again"}
        self.open_profiles()
        dialog, field = self.open_import()
        field.fill("old@example.com\nfresh@example.com\nspare@example.com")
        start = dialog.get_by_role("button", name="Check proxies and preview", exact=True)
        start.click()
        expect(dialog).to_contain_text(
            'Line 1: already on profile "Retired account", which is in Trash'
        )
        expect(dialog.get_by_role("alert").last).to_contain_text("were left out")
        self.assertEqual(self.checked, [])
        start.click()
        dialog.get_by_role("button", name="Import 2 email addresses", exact=True).click()
        expect(dialog.get_by_text("Email addresses written: 1.", exact=True)).to_be_visible()
        expect(dialog).to_contain_text(
            "Account 002: The proxy check is too old; check the proxy again"
        )
        # The refused profile's address is handed back, not lost.
        expect(dialog).to_contain_text("Addresses left over: 1")
        expect(dialog).to_contain_text("fresh@example.com")

    def test_a_check_that_fails_is_not_a_pass_and_shows_no_upstream_detail(self):
        self.check_failures = {"profile-2", "profile-5"}
        self.open_profiles()
        dialog, field = self.open_import()
        field.fill("a@example.com")
        dialog.get_by_role("button", name="Check proxies and preview", exact=True).click()
        expect(dialog.get_by_role("alert")).to_contain_text("No profile passed the proxy check")
        expect(
            dialog.get_by_role("button", name="Import 0 email addresses", exact=True)
        ).to_be_disabled()
        expect(dialog).to_contain_text("Could not check proxy")
        self.assertNotIn("private upstream detail", dialog.inner_text())
        dialog.get_by_role("button", name="Back", exact=True).click()
        expect(field).to_have_value("a@example.com")

    def test_run_copies_the_rows_address_and_a_row_without_one_leaves_the_clipboard_alone(self):
        self.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=self.origin)
        self.open_profiles()
        self.row(1).get_by_role("button", name="Run", exact=True).click()
        expect(self.page.get_by_text("Email copied", exact=True)).to_be_visible()
        self.assertEqual(self.page.evaluate("navigator.clipboard.readText()"), "held@example.com")
        self.assertEqual(self.launched, ["profile-1"])

        self.row(2).get_by_role("button", name="Run", exact=True).click()
        for _ in range(40):
            if len(self.launched) == 2:
                break
            self.page.wait_for_timeout(100)
        self.assertEqual(self.launched, ["profile-1", "profile-2"])
        expect(self.page.get_by_text("Email copied", exact=True)).to_have_count(1)
        self.assertEqual(self.page.evaluate("navigator.clipboard.readText()"), "held@example.com")

    def test_run_still_launches_when_the_address_cannot_be_copied(self):
        self.page.add_init_script(
            "document.execCommand = () => false;"
            "Object.defineProperty(navigator, 'clipboard', {value: {writeText: () => Promise.reject(new Error('denied'))}})"
        )
        self.open_profiles()
        self.row(1).get_by_role("button", name="Run", exact=True).click()
        expect(self.page.get_by_text("Email not copied", exact=True)).to_be_visible()
        expect(self.page.get_by_text("by hand: held@example.com")).to_be_visible()
        expect(self.page.get_by_text("Could not launch browser")).to_have_count(0)
        self.assertEqual(self.launched, ["profile-1"])

    def test_clearing_an_address_makes_the_profile_a_target_again(self):
        self.open_profiles()
        self.open_actions("Account 002")
        expect(self.page.get_by_role("menuitem", name="Clear email", exact=True)).to_have_count(0)
        self.page.keyboard.press("Escape")
        self.open_actions("Account 001")
        self.page.get_by_role("menuitem", name="Clear email", exact=True).click()
        confirm = self.page.get_by_role("dialog", name="Clear email")
        expect(confirm).to_contain_text('held@example.com will be removed from "Account 001"')
        confirm.get_by_role("button", name="Clear email", exact=True).click()
        expect(self.page.get_by_text("Email address removed", exact=True)).to_be_visible()
        expect(self.row(1).get_by_role("cell").nth(2)).to_have_text("—")
        dialog, _ = self.open_import()
        expect(dialog).to_contain_text("without an email address (5)")

    def test_chinese_import_flow(self):
        self.page.add_init_script("localStorage.setItem('camoufox-pm.ui-language', 'zh-CN')")
        self.open_profiles()
        expect(self.page.get_by_role("columnheader", name="邮箱", exact=True)).to_be_visible()
        self.page.get_by_role("button", name="导入邮箱", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="导入邮箱")
        dialog.get_by_label("每行一个邮箱（最多 100 个）", exact=True).fill(
            "a@example.com\nb@example.com"
        )
        expect(dialog).to_contain_text("共 2 行；2 个可用；0 个不能导入。")
        dialog.get_by_role("button", name="检测代理并预览", exact=True).click()
        dialog.get_by_role("button", name="导入 2 个邮箱", exact=True).click()
        expect(dialog.get_by_text("已为 2 个配置写入邮箱。", exact=True)).to_be_visible()
        self.assertEqual(
            [item["email"] for item in self.imports[0]["assignments"]],
            ["a@example.com", "b@example.com"],
        )


if __name__ == "__main__":
    suite = unittest.TestSuite(
        ProfileEmailsUI(name) for name in ProfileEmailsUI.__dict__ if name.startswith("test_")
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
