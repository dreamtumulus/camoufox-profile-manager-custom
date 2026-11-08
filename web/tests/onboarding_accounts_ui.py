"""Isolated account-batch and selected-email browser workflow."""

import unittest
from urllib.parse import urlsplit

from playwright.sync_api import expect
from stage_one_ui import DesktopUI


class OnboardingAccountsUI(DesktopUI):
    def setUp(self):
        super().setUp()
        self.accounts = []
        self.created_sessions = []

    def route(self, route):
        request = route.request
        path = urlsplit(request.url).path.split("/api/v1", 1)[1]
        if path == "/onboarding/config":
            return route.fulfill(json={"configured": True, "server_url": "https://shared.example"})
        if path == "/onboarding/bootstrap":
            return route.fulfill(json={
                "shared": {"preferences": {}, "accounts": self.accounts, "sessions": [], "credential_configured": True},
                "oneDay": {
                    "label": "Test", "anyNode": False,
                    "node": {"nodeId": "node-a", "name": "Node A", "online": True, "capacity": 1, "used": 0, "freeProxies": 1},
                    "nodes": [{"nodeId": "node-a", "name": "Node A", "online": True, "capacity": 1, "used": 0, "freeProxies": 1}],
                    "orgs": [{"id": "org-a", "name": "Org A", "note": None, "status": "active", "domains": [], "accounts": 0}],
                },
            })
        if path == "/onboarding/nodes/node-a/groups":
            return route.fulfill(json=[{"group": "Group A", "total": 1, "live": 1, "assignable": 1}])
        if path == "/onboarding/nodes/node-a/proxies":
            return route.fulfill(json=[{
                "id": "proxy-a", "label": None, "group": "Group A", "host": "127.0.0.1", "port": 8080,
                "status": "live", "bound": False, "quarantined": False, "boundAccounts": 0,
                "reservedAccounts": 0, "normalSlots": 2, "overflowSlots": 1, "exitIp": "10.0.0.1", "country": "US",
            }])
        if path == "/onboarding/accounts/batch" and request.method == "POST":
            body = request.post_data_json
            for email in body["emails"]:
                self.accounts.append({
                    "id": f"account-{email}", "email": email, "org_id": body["org_id"],
                    "org_name": body["org_name"], "created_at": "2026-01-01T00:00:00Z",
                })
            return route.fulfill(json={"accounts": self.accounts, "invalid": []})
        if path == "/onboarding/preferences":
            return route.fulfill(json=request.post_data_json)
        if path == "/onboarding/sessions" and request.method == "POST":
            body = request.post_data_json
            self.created_sessions.append(body)
            account = next(item for item in self.accounts if item["id"] == body["account_id"])
            session = {
                "id": "session-a", "account_id": account["id"], "email": account["email"],
                "selection": body["selection"], "profile_id": "profile-a", "status": "browser_open",
                "sk_masked": None, "job_id": None, "result": None, "error": None,
                "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
            }
            return route.fulfill(json={"session": session, "profile_id": "profile-a"})
        super().route(route)

    def test_batch_accounts_are_visible_and_selected_email_creates_the_window(self):
        self.page.goto(self.origin + "/onboarding/")
        self.page.get_by_role("button", name="下一步：选择分组、IP 和邮箱", exact=True).click()
        self.page.get_by_role("button", name="新增邮箱", exact=True).click()
        self.page.get_by_label("批量邮箱列表", exact=True).fill("first@example.com\nsecond@example.com")
        self.page.get_by_role("button", name="保存 2 个邮箱到共享服务器", exact=True).click()
        expect(self.page.get_by_text("已将 2 个邮箱保存到共享服务器", exact=False)).to_be_visible()
        expect(self.page.get_by_role("button", name="second@example.com", exact=True)).to_be_visible()
        self.page.get_by_role("button", name="second@example.com", exact=True).click()
        create = self.page.get_by_role("button", name="使用 second@example.com 创建窗口", exact=True)
        expect(create).to_be_visible()
        create.click()
        expect(self.page.get_by_text("邮箱：second@example.com", exact=True)).to_be_visible()
        self.assertEqual(self.created_sessions[0]["account_id"], "account-second@example.com")


if __name__ == "__main__":
    unittest.main()
