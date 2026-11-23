"""Batch creation UI: no writes before confirmation and no duplicate work after a lost response."""

import copy
import json
import re
import unittest
from urllib.parse import urlsplit

from playwright.sync_api import expect
from stage_two_ui import StageTwoUI


class TemplateBatchesUI(StageTwoUI):
    def setUp(self):
        super().setUp()
        self.templates = [
            {
                "id": "template-one",
                "name": "Work",
                "browser_settings": {"os": "linux"},
                "tags": [],
                "notes": None,
                "extensions": [],
                "row_version": 2,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        ]
        self.library.append(
            {
                **self.library[0],
                "id": "library-two",
                "name": "Second",
                "server": "second.example:8080",
            }
        )
        self.library = [{**item, "group_name": "Team proxies"} for item in self.library]
        self.previews = []
        self.profile_group_names = {"Team"}  # the profile groups stage_two serves
        self.creations = []
        self.saved = {}
        self.drop_once = False
        self.fail_once = False
        self.duplicate = False

    def route(self, route):
        path = urlsplit(route.request.url).path.split("/api/v1", 1)[1]
        if path.endswith("/batch-preview"):
            body = route.request.post_data_json
            self.previews.append(body)
            if self.duplicate and not body["allow_duplicate_names"]:
                return route.fulfill(
                    status=422,
                    json={
                        "error": {
                            "message": "A profile name already exists; change the names or explicitly allow duplicates"
                        }
                    },
                )
            mapping = [
                {key: proxy[key] for key in ("id", "name", "type", "server")}
                for ref in body["proxy_refs"]
                for proxy in self.library
                if proxy["id"] == ref["id"]
            ]
            chosen = [
                proxy
                for ref in body["proxy_refs"]
                for proxy in self.library
                if proxy["id"] == ref["id"]
            ]
            proxy_groups = {proxy.get("group_name") for proxy in chosen}
            if len(proxy_groups) > 1:
                return route.fulfill(
                    status=422,
                    json={
                        "error": {
                            "message": "A batch cannot mix proxy groups. Select proxies from one proxy group."
                        }
                    },
                )
            followed = {}
            if chosen:
                followed["proxy_group"] = next(iter(proxy_groups))
            if body.get("group_from_proxies"):
                name = next(iter(proxy_groups))
                followed["profile_group"] = (
                    {"name": name, "new": name not in self.profile_group_names} if name else None
                )
            return route.fulfill(
                json={
                    "success": True,
                    "data": {"names": body["names"], "proxy_mapping": mapping, **followed},
                }
            )
        if path.endswith("/profile-batches"):
            body = route.request.post_data_json
            self.creations.append(body)
            prior = self.saved.get(body["batch_id"])
            value = prior or {
                "id": body["batch_id"],
                "template_id": "template-one",
                "created_at": "2026-01-01T00:00:00Z",
                "request": copy.deepcopy(body),
                "preview": {
                    "names": body["names"],
                    "proxy_mapping": [
                        {key: proxy[key] for key in ("id", "name", "type", "server")}
                        for ref in body["proxy_refs"]
                        for proxy in self.library
                        if proxy["id"] == ref["id"]
                    ],
                },
                "items": [],
            }
            if not prior:
                value["items"] = [
                    {
                        "position": i,
                        "name": name,
                        "profile_id": f"new-{i}",
                        "status": "created",
                        "error": None,
                    }
                    for i, name in enumerate(body["names"])
                ]
            if self.fail_once or self.drop_once:
                value["items"][-1].update(
                    profile_id=None,
                    status="failed",
                    error="Creation failed. Check the template, proxy and storage before retrying.",
                )
            else:
                for i, row in enumerate(value["items"]):
                    if not row["profile_id"]:
                        row.update(profile_id=f"new-{i}", status="created", error=None)
            self.saved[value["id"]] = copy.deepcopy(value)
            self.fail_once = False
            if self.drop_once:
                self.drop_once = False
                return route.abort("failed")
            return route.fulfill(json={"success": True, "data": value})
        if path == "/template-profile-batches":
            return route.fulfill(
                json={"success": True, "data": {"batches": list(self.saved.values())}}
            )
        if path.startswith("/template-profile-batches/"):
            return route.fulfill(json={"success": True, "data": self.saved[path.rsplit("/", 1)[1]]})
        super().route(route)

    def open_new_template(self, user_agent):
        # The suggested system comes from the user agent, so pin it per test.
        agent = json.dumps(user_agent)
        self.page.add_init_script(
            f"Object.defineProperty(navigator, 'userAgent', {{get: () => {agent}}})"
        )
        self.page.goto(self.origin + "/templates/")
        self.page.get_by_role("button", name="New template", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="New template")
        name = dialog.get_by_label("Template name", exact=True)
        system = dialog.locator("label", has_text="Operating system").locator("select")
        return dialog, name, system

    def test_new_template_starts_on_this_computers_system_with_a_suggested_name(self):
        self.templates.append({**self.templates[0], "id": "template-two", "name": "macOS template"})
        dialog, name, system = self.open_new_template(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)"
        )
        expect(system).to_have_value("macos")
        # "macOS template" is taken by a saved template, so the suggestion is numbered.
        expect(name).to_have_value("macOS template 2")
        # The suggestion follows the system until the user names the template.
        system.select_option("linux")
        expect(name).to_have_value("Linux template")
        name.fill("My own")
        system.select_option("windows")
        expect(name).to_have_value("My own")
        dialog.get_by_role("button", name="Save template", exact=True).click()
        expect(dialog).not_to_be_visible()
        body = next(
            json.loads(body)
            for method, path, body in self.calls
            if method == "POST" and path == "/templates"
        )
        self.assertEqual(body["name"], "My own")
        self.assertEqual(body["browser_settings"]["os"], "windows")

    def test_new_template_suggestion_is_saved_as_is_and_replaced_by_typing(self):
        dialog, name, system = self.open_new_template(
            "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"
        )
        expect(system).to_have_value("linux")
        expect(name).to_have_value("Linux template")
        # The suggestion is selected on open, so typing replaces it.
        expect(name).to_be_focused()
        self.page.keyboard.type("Team")
        expect(name).to_have_value("Team")
        name.fill("Linux template")
        dialog.get_by_role("button", name="Save template", exact=True).click()
        expect(dialog).not_to_be_visible()
        body = next(
            json.loads(body)
            for method, path, body in self.calls
            if method == "POST" and path == "/templates"
        )
        self.assertEqual(body["name"], "Linux template")
        self.assertEqual(body["browser_settings"]["os"], "linux")

    def test_new_template_suggested_name_is_translated(self):
        self.page.add_init_script("localStorage.setItem('camoufox-pm.ui-language', 'zh-CN')")
        self.page.add_init_script(
            "Object.defineProperty(navigator, 'userAgent', {get: () => 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})"
        )
        self.page.goto(self.origin + "/templates/")
        self.page.get_by_role("button", name="新建模板", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="新建模板")
        expect(dialog.get_by_label("模板名称", exact=True)).to_have_value("Windows 模板")

    def open_batch(self):
        self.page.goto(self.origin + "/templates/")
        self.page.get_by_role("button", name="Create batch", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="Create profiles from template")
        dialog.get_by_label("Count", exact=True).fill("2")
        return dialog

    def choose_proxy_group(self, dialog, group="Team proxies"):
        """Nothing in the proxy list can be ticked until its group is chosen."""
        dialog.get_by_label("Proxy group", exact=True).select_option(group)

    def test_preview_names_and_proxy_mapping_before_any_creation(self):
        dialog = self.open_batch()
        self.choose_proxy_group(dialog)
        dialog.get_by_role("checkbox", name=re.compile("Second")).check()
        dialog.get_by_role("checkbox", name=re.compile("Office")).check()
        dialog.get_by_role("button", name="Preview batch", exact=True).click()
        expect(dialog.get_by_text("Work 001", exact=True)).to_be_visible()
        expect(dialog.get_by_text("Work 002", exact=True)).to_be_visible()
        self.assertEqual(self.creations, [])
        self.assertEqual(
            [ref["id"] for ref in self.previews[0]["proxy_refs"]], ["library-two", "library-one"]
        )
        self.assertNotIn("password", str(self.previews[0]))
        dialog.get_by_role("button", name="Create 2 profiles", exact=True).click()
        expect(dialog.get_by_text("2 of 2 profiles created", exact=True)).to_be_visible()
        self.assertEqual(self.creations, self.previews)

    def test_proxy_group_selects_the_filtered_batch_in_order(self):
        dialog = self.open_batch()
        self.choose_proxy_group(dialog)
        dialog.get_by_role("checkbox", name="Select all", exact=True).check()
        expect(dialog).to_contain_text("2 proxies selected")
        dialog.get_by_role("button", name="Preview batch", exact=True).click()
        self.assertEqual(
            [ref["id"] for ref in self.previews[0]["proxy_refs"]], ["library-one", "library-two"]
        )

    def new_batch(self):
        self.page.goto(self.origin + "/templates/")
        self.page.get_by_role("button", name="Create batch", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="Create profiles from template")
        count = dialog.get_by_label("Count", exact=True)
        select_all = dialog.get_by_role("checkbox", name="Select all", exact=True)
        return dialog, count, select_all

    def test_profile_number_is_a_fixed_suffix_outside_the_name_field(self):
        dialog, count, _ = self.new_batch()
        name = dialog.get_by_label("Name pattern", exact=True)
        # The field holds only the name; the number placeholder sits beside it and cannot be edited.
        expect(name).to_have_value("Work ")
        suffix = dialog.get_by_text("{n}", exact=True)
        expect(suffix).to_be_visible()
        self.assertEqual(suffix.evaluate("element => element.tagName"), "SPAN")
        expect(dialog).to_contain_text("First profile: Work 001")
        name.fill("Client-")
        dialog.get_by_label("Start number", exact=True).fill("7")
        expect(dialog).to_contain_text("First profile: Client-007")
        count.fill("2")
        dialog.get_by_role("button", name="Preview batch", exact=True).click()
        expect(dialog.get_by_text("Client-008", exact=True)).to_be_visible()
        self.assertEqual(self.previews[0]["names"], ["Client-007", "Client-008"])

    def third_proxy(self, group):
        self.library.append(
            {
                **self.library[0],
                "id": "library-three",
                "name": "Third",
                "server": "third.example:8080",
                "group_name": group,
            }
        )

    def test_selected_proxies_set_the_count_and_select_all_acts_on_the_shown_list(self):
        dialog, count, select_all = self.new_batch()
        expect(count).to_have_value("5")
        expect(count).to_have_attribute("max", "100")
        self.choose_proxy_group(dialog)
        dialog.get_by_role("checkbox", name=re.compile("Second")).check()
        expect(count).to_have_value("1")
        # With part of the list selected the box is neither on nor off; a click selects the rest.
        self.assertTrue(select_all.evaluate("element => element.indeterminate"))
        select_all.check()
        expect(count).to_have_value("2")
        expect(dialog).to_contain_text("2 proxies selected")
        self.assertFalse(select_all.evaluate("element => element.indeterminate"))
        dialog.get_by_role("checkbox", name=re.compile("Second")).uncheck()
        expect(count).to_have_value("1")
        dialog.get_by_role("button", name="Preview batch", exact=True).click()
        expect(dialog.get_by_text("Work 001", exact=True)).to_be_visible()
        self.assertEqual(self.previews[0]["names"], ["Work 001"])
        self.assertEqual([ref["id"] for ref in self.previews[0]["proxy_refs"]], ["library-one"])

    def test_proxies_cannot_be_ticked_before_a_proxy_group_is_chosen(self):
        """Select all over two groups once put half a batch on the other group's proxies."""
        self.third_proxy("Other")
        dialog, count, select_all = self.new_batch()
        office = dialog.get_by_role("checkbox", name=re.compile("Office"))
        third = dialog.get_by_role("checkbox", name=re.compile("Third"))
        number = dialog.get_by_label("Number of proxies to select", exact=True)
        # The whole library is listed, but nothing says which group is meant yet.
        expect(dialog).to_contain_text("Choose a proxy group first, then tick proxies below.")
        expect(dialog).to_contain_text("0 / 3")
        for control in (select_all, number, office, third):
            expect(control).to_be_disabled()

        self.choose_proxy_group(dialog)
        expect(dialog).not_to_contain_text("Choose a proxy group first")
        # Only the chosen group is listed, so a proxy of another group cannot join.
        expect(third).to_have_count(0)
        expect(office).to_be_enabled()
        expect(number).to_be_enabled()
        select_all.check()
        expect(dialog).to_contain_text("2 proxies selected")
        expect(dialog).to_contain_text("2 / 2")
        expect(count).to_have_value("2")

        dialog.get_by_role("button", name="Preview batch", exact=True).click()
        expect(dialog.get_by_text("Work 002", exact=True)).to_be_visible()
        self.assertEqual(
            [ref["id"] for ref in self.previews[0]["proxy_refs"]], ["library-one", "library-two"]
        )

    def test_changing_the_proxy_group_clears_the_selection(self):
        self.third_proxy("Other")
        dialog, count, select_all = self.new_batch()
        self.choose_proxy_group(dialog)
        select_all.check()
        expect(dialog).to_contain_text("2 proxies selected")
        self.choose_proxy_group(dialog, "Other")
        expect(dialog).to_contain_text("0 proxies selected")
        expect(dialog.get_by_label("Number of proxies to select", exact=True)).to_have_value("")
        # The count is left as it was, like when Select all clears the list.
        expect(count).to_have_value("2")
        expect(dialog.get_by_role("checkbox", name=re.compile("Third"))).not_to_be_checked()
        select_all.check()
        expect(count).to_have_value("1")
        dialog.get_by_role("button", name="Preview batch", exact=True).click()
        expect(dialog.get_by_text("Work 001", exact=True)).to_be_visible()
        self.assertEqual([ref["id"] for ref in self.previews[0]["proxy_refs"]], ["library-three"])

    def test_a_typed_number_ticks_that_many_proxies_from_the_top_of_the_list(self):
        self.third_proxy("Team proxies")
        dialog, count, select_all = self.new_batch()
        office = dialog.get_by_role("checkbox", name=re.compile("Office"))
        second = dialog.get_by_role("checkbox", name=re.compile("Second"))
        third = dialog.get_by_role("checkbox", name=re.compile("Third"))
        number = dialog.get_by_label("Number of proxies to select", exact=True)
        self.choose_proxy_group(dialog)
        expect(number).to_have_value("")
        expect(number).to_have_attribute("max", "3")
        number.fill("2")
        expect(office).to_be_checked()
        expect(second).to_be_checked()
        expect(third).not_to_be_checked()
        expect(count).to_have_value("2")
        expect(dialog).to_contain_text("2 proxies selected")
        # Ticking by hand afterwards still works, and the field follows the selection.
        third.check()
        expect(number).to_have_value("3")
        expect(count).to_have_value("3")
        # A typed number replaces the selection rather than adding to it.
        second.uncheck()
        number.fill("1")
        expect(office).to_be_checked()
        expect(third).not_to_be_checked()
        expect(count).to_have_value("1")
        # Zero clears the list; the count is left as it was.
        number.fill("0")
        expect(dialog).to_contain_text("0 proxies selected")
        expect(count).to_have_value("1")
        # An emptied field changes nothing.
        number.fill("2")
        number.fill("")
        expect(dialog).to_contain_text("2 proxies selected")
        dialog.get_by_role("button", name="Preview batch", exact=True).click()
        expect(dialog.get_by_text("Work 002", exact=True)).to_be_visible()
        self.assertEqual(
            [ref["id"] for ref in self.previews[0]["proxy_refs"]], ["library-one", "library-two"]
        )

    def test_a_number_above_the_group_ticks_every_proxy_and_says_so(self):
        dialog, count, select_all = self.new_batch()
        number = dialog.get_by_label("Number of proxies to select", exact=True)
        self.choose_proxy_group(dialog)
        number.fill("5")
        expect(dialog).to_contain_text("2 proxies selected")
        expect(dialog).to_contain_text("Proxies in this group: 2. All of them are selected.")
        expect(count).to_have_value("2")
        # Leaving the field shows what was really selected.
        number.blur()
        expect(number).to_have_value("2")
        # The remark is about the typed number, so it goes once the selection is changed by hand.
        dialog.get_by_role("checkbox", name=re.compile("Second")).uncheck()
        expect(dialog).not_to_contain_text("Proxies in this group:")

    def test_profiles_follow_the_proxy_group_unless_another_group_is_chosen(self):
        dialog, count, select_all = self.new_batch()
        group = dialog.get_by_label("Group", exact=True)
        expect(group).to_have_value("__proxy__")
        expect(dialog).to_contain_text(
            "Select proxies below and the profiles join the profile group"
        )
        self.choose_proxy_group(dialog)
        select_all.check()
        # No profile group is called "Team proxies" yet, so creating the batch creates it.
        expect(dialog).to_contain_text(
            'These profiles join a new profile group "Team proxies", created with them.'
        )
        dialog.get_by_role("button", name="Preview batch", exact=True).click()
        expect(dialog).to_contain_text("Proxy group: Team proxies")
        expect(dialog).to_contain_text("Profile group: Team proxies (will be created)")
        self.assertIsNone(self.previews[0]["group"])
        self.assertIs(self.previews[0]["group_from_proxies"], True)
        dialog.get_by_role("button", name="Create 2 profiles", exact=True).click()
        expect(dialog.get_by_text("2 of 2 profiles created", exact=True)).to_be_visible()
        self.assertEqual(self.creations, self.previews)
        expect(dialog).not_to_contain_text("(will be created)")

    def test_an_explicit_group_or_no_proxies_does_not_follow(self):
        dialog, count, select_all = self.new_batch()
        group = dialog.get_by_label("Group", exact=True)
        count.fill("1")
        # Nothing to follow without proxies: the request stays as it always was.
        dialog.get_by_role("button", name="Preview batch", exact=True).click()
        expect(dialog.get_by_text("Work 001", exact=True)).to_be_visible()
        self.assertNotIn("group_from_proxies", self.previews[0])
        self.assertIsNone(self.previews[0]["group"])
        expect(dialog).not_to_contain_text("Profile group:")

        dialog.get_by_role("button", name="Edit draft", exact=True).click()
        self.choose_proxy_group(dialog)
        select_all.check()
        group.select_option("team")
        expect(dialog).not_to_contain_text("These profiles join")
        dialog.get_by_role("button", name="Preview batch", exact=True).click()
        expect(dialog).to_contain_text("Proxy group: Team proxies")
        self.assertEqual(self.previews[1]["group"], "team")
        self.assertNotIn("group_from_proxies", self.previews[1])

        dialog.get_by_role("button", name="Edit draft", exact=True).click()
        group.select_option("")
        dialog.get_by_role("button", name="Preview batch", exact=True).click()
        expect(dialog).to_contain_text("Proxy group: Team proxies")
        self.assertIsNone(self.previews[2]["group"])
        self.assertNotIn("group_from_proxies", self.previews[2])

    def test_ungrouped_proxies_are_one_group_and_leave_the_profiles_ungrouped(self):
        self.library = [{**item, "group_name": None} for item in self.library]
        self.third_proxy("Other")
        dialog, count, select_all = self.new_batch()
        self.choose_proxy_group(dialog, "__ungrouped__")
        select_all.check()
        expect(count).to_have_value("2")
        expect(dialog).to_contain_text(
            "The selected proxies have no group, so these profiles stay ungrouped."
        )
        dialog.get_by_role("button", name="Preview batch", exact=True).click()
        expect(dialog).to_contain_text("Profile group: none, because the proxies are ungrouped")

    def test_a_server_refusal_of_mixed_groups_is_said_in_the_interface_language(self):
        """The library can change after the dialog loaded it; the server has the last word."""
        self.page.add_init_script("localStorage.setItem('camoufox-pm.ui-language', 'zh-CN')")
        self.page.goto(self.origin + "/templates/")
        self.page.get_by_role("button", name="批量创建", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="从模板批量创建配置")
        expect(dialog.get_by_label("分组", exact=True)).to_have_value("__proxy__")
        expect(dialog).to_contain_text("请先选择一个代理分组，再勾选下面的代理。")
        dialog.get_by_label("代理分组", exact=True).select_option("Team proxies")
        dialog.get_by_role("checkbox", name="全选", exact=True).check()
        expect(dialog).to_contain_text("这批配置会放入新的配置分组「Team proxies」")
        self.library[1]["group_name"] = "Moved meanwhile"
        dialog.get_by_role("button", name="预览批次", exact=True).click()
        expect(dialog.get_by_role("alert")).to_have_text(
            "一批不能混用多个代理分组的代理，请只选择同一个代理分组的代理。"
        )
        self.assertEqual(self.creations, [])

    def test_select_all_stops_at_the_batch_limit(self):
        self.library = [
            {
                **self.library[0],
                "id": f"proxy-{number}",
                "name": f"Proxy {number:03}",
                "server": f"p{number}.example:8080",
            }
            for number in range(1, 102)
        ]
        dialog, count, select_all = self.new_batch()
        self.choose_proxy_group(dialog)
        select_all.click()
        expect(count).to_have_value("100")
        expect(dialog).to_contain_text("100 proxies selected")
        expect(dialog).to_contain_text("A batch holds up to 100 profiles")
        expect(dialog.get_by_role("checkbox", name=re.compile("Proxy 101"))).to_be_disabled()
        # Nothing more fits, so the next click clears the list; the count is left as it was.
        select_all.click()
        expect(dialog).to_contain_text("0 proxies selected")
        expect(count).to_have_value("100")
        # A typed number stops at the same limit.
        dialog.get_by_label("Number of proxies to select", exact=True).fill("150")
        expect(dialog).to_contain_text("100 proxies selected")
        expect(dialog).to_contain_text("A batch holds up to 100 profiles")
        expect(dialog).not_to_contain_text("Proxies in this group:")
        dialog.get_by_role("button", name="Preview batch", exact=True).click()
        expect(dialog.get_by_text("Work 100", exact=True)).to_be_visible()
        self.assertEqual(len(self.previews[0]["names"]), 100)
        self.assertEqual(
            [ref["id"] for ref in self.previews[0]["proxy_refs"]],
            [f"proxy-{number}" for number in range(1, 101)],
        )

    def test_incomplete_proxy_selection_and_duplicate_names_require_correction(self):
        dialog = self.open_batch()
        self.choose_proxy_group(dialog)
        dialog.get_by_role("checkbox", name=re.compile("Office")).check()
        # Selecting set the count to 1; a count typed afterwards must still match the selection.
        dialog.get_by_label("Count", exact=True).fill("2")
        dialog.get_by_role("button", name="Preview batch", exact=True).click()
        expect(dialog.get_by_role("alert")).to_contain_text("exactly 2 distinct proxies")
        self.assertEqual(self.previews, [])
        dialog.get_by_role("checkbox", name=re.compile("Office")).uncheck()
        self.duplicate = True
        dialog.get_by_role("button", name="Preview batch", exact=True).click()
        expect(dialog.get_by_role("alert")).to_contain_text("already exists")
        self.assertEqual(self.creations, [])
        dialog.get_by_role(
            "checkbox",
            name="Allow duplicate profile names. Existing profiles will never be overwritten.",
        ).check()
        dialog.get_by_role("button", name="Preview batch", exact=True).click()
        expect(dialog.get_by_role("button", name="Create 2 profiles", exact=True)).to_be_visible()

    def test_lost_response_retry_retains_the_exact_batch_identifier_and_intent(self):
        self.drop_once = True
        dialog = self.open_batch()
        dialog.get_by_role("button", name="Preview batch", exact=True).click()
        dialog.get_by_role("button", name="Create 2 profiles", exact=True).click()
        expect(dialog.get_by_role("alert")).to_contain_text("Check the saved batch")
        dialog.get_by_role("button", name="Retry same batch", exact=True).click()
        expect(dialog.get_by_text("2 of 2 profiles created", exact=True)).to_be_visible()
        self.assertEqual(len(self.creations), 2)
        self.assertEqual(self.creations[0], self.creations[1])
        self.assertEqual(len(self.saved), 1)

    def test_recent_batch_review_does_not_resume_automatically_after_reload(self):
        self.fail_once = True
        dialog = self.open_batch()
        dialog.get_by_role("button", name="Preview batch", exact=True).click()
        dialog.get_by_role("button", name="Create 2 profiles", exact=True).click()
        expect(dialog.get_by_text("1 of 2 profiles created", exact=True)).to_be_visible()
        dialog.get_by_role("button", name="Done", exact=True).click()
        self.page.reload()
        self.page.get_by_role("button", name="Recent batches", exact=True).click()
        self.page.get_by_role("button", name=re.compile("Review batch")).click()
        dialog = self.page.get_by_role("dialog", name="Create profiles from template")
        expect(dialog.get_by_text("Failed", exact=True)).to_be_visible()
        self.assertEqual(len(self.creations), 1)
        dialog.get_by_role("button", name="Resume incomplete", exact=True).click()
        expect(dialog.get_by_text("2 of 2 profiles created", exact=True)).to_be_visible()
        self.assertEqual(self.creations[0], self.creations[1])

    def test_chinese_creation_flow_preserves_names_and_substitutes_counts(self):
        self.page.add_init_script("localStorage.setItem('camoufox-pm.ui-language', 'zh-CN')")
        self.page.goto(self.origin + "/templates/")
        self.page.get_by_role("button", name="批量创建", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="从模板批量创建配置")
        dialog.get_by_label("名称格式", exact=True).fill("中文 ")
        dialog.get_by_label("数量", exact=True).fill("2")
        dialog.get_by_role("button", name="预览批次", exact=True).click()
        expect(dialog.get_by_text("中文 001", exact=True)).to_be_visible()
        dialog.get_by_role("button", name="创建 2 个配置", exact=True).click()
        expect(dialog.get_by_text("已创建 2/2 个配置", exact=True)).to_be_visible()
        self.assertEqual(self.creations[0]["names"], ["中文 001", "中文 002"])


if __name__ == "__main__":
    suite = unittest.TestSuite(
        TemplateBatchesUI(name) for name in TemplateBatchesUI.__dict__ if name.startswith("test_")
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
