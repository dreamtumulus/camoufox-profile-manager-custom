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
        self.session = None
        self.proxies = [{
            "id": "proxy-a", "label": None, "group": "Group A", "host": "127.0.0.1", "port": 8080,
            "status": "live", "bound": False, "quarantined": False, "boundAccounts": 0,
            "reservedAccounts": 0, "normalSlots": 2, "overflowSlots": 1, "exitIp": "10.0.0.1", "country": "US",
        }]

    def route(self, route):
        request = route.request
        path = urlsplit(request.url).path.split("/api/v1", 1)[1]
        if path == "/onboarding/config":
            return route.fulfill(json={"configured": True, "server_url": "https://shared.example"})
        if path == "/onboarding/bootstrap":
            return route.fulfill(json={
                "shared": {"preferences": {}, "accounts": self.accounts, "sessions": [self.session] if self.session else [], "credential_configured": True},
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
            return route.fulfill(json=self.proxies)
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
                "sk_masked": "sk-ant…test", "job_id": None, "result": None, "error": None,
                "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
            }
            self.session = session
            return route.fulfill(json={"session": session, "profile_id": "profile-a"})
        if path.endswith("/extract"):
            return route.fulfill(json={"found": False, "session": self.session})
        if path.endswith("/submit"):
            self.session = {**self.session, "status": "submitted", "job_id": "job-a", "result": {"jobId": "job-a"}}
            return route.fulfill(json={"session": self.session, "result": self.session["result"]})
        if path.endswith("/close"):
            return route.fulfill(json={"session": self.session})
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
        expect(self.page.get_by_text("4. 登录、提取并提交", exact=True)).to_be_visible()
        expect(self.page.get_by_role("button", name="复制邮箱 second@example.com", exact=True).first).to_be_visible()
        self.assertEqual(self.created_sessions[0]["account_id"], "account-second@example.com")

    def test_completed_account_synchronizes_and_advances_to_the_next_ip(self):
        self.proxies.append({
            **self.proxies[0], "id": "proxy-b", "exitIp": "10.0.0.2", "port": 8081,
        })
        self.page.goto(self.origin + "/onboarding/")
        self.page.get_by_role("button", name="下一步：选择分组、IP 和邮箱", exact=True).click()
        self.page.get_by_role("button", name="新增邮箱", exact=True).click()
        self.page.get_by_label("批量邮箱列表", exact=True).fill("first@example.com\nsecond@example.com")
        self.page.get_by_role("button", name="保存 2 个邮箱到共享服务器", exact=True).click()
        self.page.get_by_role("button", name="使用 first@example.com 创建窗口", exact=True).click()
        self.page.get_by_role("button", name="提交 SK（仅一次）", exact=True).click()
        expect(self.page.get_by_text("代理池和 IP 容量已同步", exact=False)).to_be_visible()
        self.page.get_by_role("button", name="完成此账号，继续下一个", exact=True).click()
        expect(self.page.locator("#onboarding-exit-ip")).to_have_value("proxy-b")
        expect(self.page.get_by_role("listitem").filter(has_text="first@example.com").get_by_text("已上号", exact=True)).to_be_visible()
        expect(self.page.get_by_role("listitem").filter(has_text="second@example.com").get_by_text("未上号", exact=True)).to_be_visible()


if __name__ == "__main__":
    suite = unittest.TestSuite(
        OnboardingAccountsUI(name)
        for name in OnboardingAccountsUI.__dict__
        if name.startswith("test_")
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
