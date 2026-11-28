"""Offline contracts of the grouped profile list, run against the static export in CI."""

import re
import unittest
from urllib.parse import urlsplit

from playwright.sync_api import expect
from stage_two_ui import StageTwoUI, profile


class ProfileGroupsUI(StageTwoUI):
    # No remembered layout: these tests start where a new browser does.
    profile_layout = None

    def setUp(self):
        super().setUp()
        self.groups = [
            {"id": "g10", "name": "Homes 10", "profile_count": 2},
            {"id": "g9", "name": "Homes 9", "profile_count": 3},
        ]
        self.profiles = [
            profile(1, group="g9"),
            profile(2, group="g9", status="blocked"),
            profile(3, group="g9"),
            profile(4, group="g10"),
            profile(5, group="g10"),
            profile(6),
        ]
        self.running_profile_ids = ["profile-1"]

    def route(self, route):
        if urlsplit(route.request.url).path.endswith("/api/v1/groups"):
            return route.fulfill(json={"groups": self.groups, "total": len(self.groups)})
        super().route(route)

    def section(self, name):
        return self.page.get_by_role("button", name=name, exact=True)

    def line(self, name):
        return self.page.get_by_role("row").filter(has=self.section(name))

    def row(self, number):
        return self.page.get_by_role("table").get_by_text(f"Account {number:03}", exact=True)

    def open_list(self):
        self.page.goto(self.origin + "/")
        expect(self.section("Homes 9")).to_be_visible()

    def test_groups_start_folded_in_counted_order_and_stay_open_once_opened(self):
        self.open_list()
        # The row menus are expandable buttons too; a group line has no popup.
        toggles = self.page.locator("tbody button[aria-expanded]:not([aria-haspopup])")
        expect(toggles).to_have_text(["Homes 9", "Homes 10", "Ungrouped"])
        expect(self.page.locator("tbody tr")).to_have_count(3)
        expect(self.line("Homes 9")).to_contain_text("3 profiles")
        expect(self.line("Homes 9")).to_contain_text("1 running")
        expect(self.line("Homes 9")).to_contain_text("1 blocked")
        expect(self.line("Homes 10")).not_to_contain_text("running")
        # Every row would repeat the line above it, and pages would cut a group in two.
        expect(self.page.get_by_role("button", name="Group", exact=True)).to_have_count(0)
        expect(self.page.get_by_role("button", name="Next page")).to_have_count(0)
        expect(self.page.get_by_text("2 groups · 6 profiles", exact=True)).to_be_visible()

        self.line("Homes 9").click()
        expect(self.section("Homes 9")).to_have_attribute("aria-expanded", "true")
        for number in (1, 2, 3):
            expect(self.row(number)).to_be_visible()
        expect(self.row(4)).to_have_count(0)

        self.page.reload()
        expect(self.row(1)).to_be_visible()
        expect(self.section("Homes 10")).to_have_attribute("aria-expanded", "false")
        self.page.get_by_role("button", name="Collapse all", exact=True).click()
        expect(self.page.locator("tbody tr")).to_have_count(3)
        self.page.get_by_role("button", name="Expand all", exact=True).click()
        expect(self.page.locator("tbody tr")).to_have_count(9)

    def test_search_opens_what_it_matched_and_leaves_the_folds_as_they_were(self):
        self.open_list()
        search = self.page.get_by_label("Search profiles", exact=True)
        search.fill("Account 004")
        expect(self.row(4)).to_be_visible()
        expect(self.line("Homes 10")).to_contain_text("1 of 2 match")
        expect(self.section("Homes 9")).to_have_count(0)
        # Folding a result is allowed, and forgotten with the search that opened it.
        self.section("Homes 10").click()
        expect(self.row(4)).to_have_count(0)
        search.fill("Account 00")
        expect(self.row(4)).to_be_visible()
        search.fill("")
        expect(self.section("Homes 9")).to_be_visible()
        expect(self.page.locator("tbody tr")).to_have_count(3)

        # One section has nothing to be folded against.
        self.page.get_by_label("Group", exact=True).select_option("g10")
        expect(self.row(5)).to_be_visible()
        expect(self.page.get_by_role("button", name="Expand all", exact=True)).to_have_count(0)

    def test_group_checkbox_selects_folded_profiles_and_shows_a_partial_choice(self):
        self.open_list()
        box = self.page.get_by_role("checkbox", name="Select all profiles in Homes 9", exact=True)
        box.check()
        expect(self.page.get_by_text("3 selected", exact=True)).to_be_visible()
        # The box sits on the line that toggles, and must not toggle it.
        expect(self.section("Homes 9")).to_have_attribute("aria-expanded", "false")

        self.section("Homes 9").click()
        self.page.get_by_role("checkbox", name="Select Account 002", exact=True).uncheck()
        expect(self.page.get_by_text("2 selected", exact=True)).to_be_visible()
        self.assertTrue(box.evaluate("box => box.indeterminate && !box.checked"))

        everything = self.page.get_by_role(
            "checkbox", name="Select all matching profiles", exact=True
        )
        self.assertTrue(everything.evaluate("box => box.indeterminate"))
        everything.check()
        expect(self.page.get_by_text("6 selected", exact=True)).to_be_visible()
        expect(box).to_be_checked()

    def test_a_long_group_is_drawn_fifty_rows_at_a_time(self):
        self.profiles = [profile(number, group="g9") for number in range(1, 56)]
        self.page.goto(self.origin + "/")
        # The only section is open without being asked.
        expect(self.section("Homes 9")).to_have_attribute("aria-expanded", "true")
        rows = self.page.get_by_role("checkbox", name=re.compile(r"^Select Account \d+$"))
        expect(rows).to_have_count(50)
        self.page.get_by_role("button", name="Show 5 more", exact=True).click()
        expect(rows).to_have_count(55)
        expect(self.page.get_by_role("button", name=re.compile("^Show .* more$"))).to_have_count(0)

    def test_list_layout_brings_back_the_flat_table_and_is_remembered(self):
        self.open_list()
        self.page.get_by_role("button", name="List", exact=True).click()
        expect(self.section("Homes 9")).to_have_count(0)
        expect(self.page.locator("tbody tr")).to_have_count(6)
        expect(self.page.get_by_role("button", name="Group", exact=True)).to_be_visible()
        expect(
            self.page.get_by_role("checkbox", name="Select all on this page", exact=True)
        ).to_be_visible()
        self.page.reload()
        expect(self.row(6)).to_be_visible()
        expect(self.page.get_by_role("button", name="List", exact=True)).to_have_attribute(
            "aria-pressed", "true"
        )
        self.page.get_by_role("button", name="By group", exact=True).click()
        expect(self.section("Ungrouped")).to_be_visible()


if __name__ == "__main__":
    suite = unittest.TestSuite(
        ProfileGroupsUI(name) for name in ProfileGroupsUI.__dict__ if name.startswith("test_")
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
