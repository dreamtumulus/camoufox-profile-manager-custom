"""Email addresses on profiles: written only after a passing proxy check, and only once."""

import io
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from openpyxl import load_workbook

from camoufox_pm.api import dependencies
from camoufox_pm.core import profile_manager as rules
from camoufox_pm.core.database import EmailTaken, StaleWriteError, StorageManager
from camoufox_pm.core.models import Profile, ProxyCheckRecord

PROXY = {"type": "http", "server": "proxy.example:8080"}


async def profile(client, name="Account", proxy=PROXY, checked="passed", age=timedelta(0)):
    """Create a profile and leave its proxy in the given state of having been checked."""
    body = {"name": name, **({"proxy_config": proxy} if proxy else {})}
    created = (await client.post("/api/v1/profiles", json=body)).json()
    if checked:
        await dependencies.get_storage_manager().set_proxy_check(
            created["id"],
            ProxyCheckRecord(
                checked_at=datetime.now(timezone.utc) - age,
                reachable=checked == "passed",
                error=None if checked == "passed" else "The proxy did not answer in time.",
            ),
        )
    return created


async def give(client, *pairs):
    """POST the import for (profile, email) pairs; return the response."""
    return await client.post(
        "/api/v1/profiles/emails",
        json={
            "assignments": [
                {"id": item["id"], "row_version": item["row_version"], "email": email}
                for item, email in pairs
            ]
        },
    )


async def stored(client, item):
    return (await client.get(f"/api/v1/profiles/{item['id']}")).json()


@pytest.mark.asyncio
async def test_a_profile_whose_proxy_just_passed_gets_its_address(client):
    first, second = await profile(client, "One"), await profile(client, "Two")

    response = await give(client, (first, " One@Example.com "), (second, "two@example.com"))

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["skipped"] == []
    assert [item["email"] for item in data["updated"]] == ["One@Example.com", "two@example.com"]
    assert (await stored(client, first))["email"] == "One@Example.com"
    assert data["updated"][0]["row_version"] == first["row_version"] + 1
    # Found by its address, and through the legacy alias as well.
    found = (await client.get("/api/profiles", params={"search": "one@example"})).json()
    assert [item["name"] for item in found["profiles"]] == ["One"]
    actions = [
        row[0]
        for row in dependencies.get_storage_manager().db._connection.execute(
            "SELECT action FROM audit_events WHERE action = 'import_profile_email'"
        )
    ]
    assert len(actions) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "proxy,checked,age,reason",
    [
        (None, None, timedelta(0), rules.EMAIL_NO_PROXY),
        (None, "passed", timedelta(0), rules.EMAIL_NO_PROXY),  # a direct connection is no proxy
        (PROXY, None, timedelta(0), rules.EMAIL_NOT_CHECKED),
        (PROXY, "failed", timedelta(0), rules.EMAIL_PROXY_FAILED),
        (PROXY, "passed", timedelta(minutes=31), rules.EMAIL_CHECK_TOO_OLD),
    ],
)
async def test_no_address_without_a_recent_passing_proxy_check(client, proxy, checked, age, reason):
    refused = await profile(client, "Refused", proxy, checked, age)
    passing = await profile(client, "Passing")

    data = (await give(client, (refused, "a@example.com"), (passing, "b@example.com"))).json()[
        "data"
    ]

    assert data["skipped"] == [{"id": refused["id"], "reason": reason}]
    assert [item["id"] for item in data["updated"]] == [passing["id"]]  # the rest still land
    assert (await stored(client, refused))["email"] is None
    assert (await stored(client, refused))["row_version"] == refused["row_version"]


@pytest.mark.asyncio
async def test_a_check_just_inside_the_window_still_counts(client):
    item = await profile(client, age=timedelta(minutes=29))
    assert (await give(client, (item, "a@example.com"))).json()["data"]["skipped"] == []


@pytest.mark.asyncio
async def test_changing_the_proxy_after_the_check_means_checking_again(client):
    item = await profile(client)
    edited = await client.put(
        f"/api/v1/profiles/{item['id']}",
        json={"proxy_config": {"type": "http", "server": "other.example:8080"}},
    )
    assert edited.status_code == 200

    data = (await give(client, (edited.json(), "a@example.com"))).json()["data"]

    assert data["skipped"] == [{"id": item["id"], "reason": rules.EMAIL_NOT_CHECKED}]


@pytest.mark.asyncio
async def test_an_address_is_never_replaced_or_shared(client):
    first, second = await profile(client, "One"), await profile(client, "Two")
    first = (await give(client, (first, "one@example.com"))).json()["data"]["updated"][0]

    again = (await give(client, (first, "new@example.com"))).json()["data"]
    assert again["skipped"] == [{"id": first["id"], "reason": rules.EMAIL_ALREADY_SET}]
    assert (await stored(client, first))["email"] == "one@example.com"

    # The same mailbox in other capitals is still the same mailbox.
    shared = (await give(client, (second, "ONE@example.COM"))).json()["data"]
    assert shared["skipped"] == [{"id": second["id"], "reason": rules.EMAIL_TAKEN}]
    assert (await stored(client, second))["email"] is None

    # Trash keeps the address: the profile can come back with it.
    assert (await client.delete(f"/api/v1/profiles/{first['id']}")).status_code == 200
    trashed = (await give(client, (second, "one@example.com"))).json()["data"]
    assert trashed["skipped"] == [{"id": second["id"], "reason": rules.EMAIL_TAKEN}]


@pytest.mark.asyncio
async def test_missing_trashed_and_concurrently_edited_profiles_are_skipped(client):
    edited, trashed = await profile(client, "Edited"), await profile(client, "Trashed")
    renamed = await client.put(f"/api/v1/profiles/{edited['id']}", json={"name": "Renamed"})
    assert renamed.status_code == 200
    assert (await client.delete(f"/api/v1/profiles/{trashed['id']}")).status_code == 200

    data = (
        await give(
            client,
            (edited, "a@example.com"),  # the version from before the rename
            (trashed, "b@example.com"),
            ({"id": "nope", "row_version": 0}, "c@example.com"),
        )
    ).json()["data"]

    assert data["updated"] == []
    assert data["skipped"] == [
        {"id": edited["id"], "reason": rules.EMAIL_PROFILE_CHANGED},
        {"id": trashed["id"], "reason": rules.EMAIL_PROFILE_MISSING},
        {"id": "nope", "reason": rules.EMAIL_PROFILE_MISSING},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "emails",
    [
        ["not-an-email", "b@example.com"],
        ["a@example.com", "A@Example.com"],  # one address for two profiles
        ["a@example.com", "x" * 250 + "@example.com"],
        ["a@example.com", ""],
    ],
)
async def test_a_request_that_is_wrong_writes_nothing(client, emails):
    first, second = await profile(client, "One"), await profile(client, "Two")

    response = await give(client, (first, emails[0]), (second, emails[1]))

    assert response.status_code == 422
    assert (await stored(client, first))["email"] is None


@pytest.mark.asyncio
async def test_one_profile_twice_or_too_many_profiles_is_refused(client):
    item = await profile(client)
    assert (await give(client, (item, "a@example.com"), (item, "b@example.com"))).status_code == 422
    many = [({"id": f"p{n}", "row_version": 0}, f"u{n}@example.com") for n in range(101)]
    assert (await give(client, *many)).status_code == 422
    assert (
        await client.post("/api/v1/profiles/emails", json={"assignments": []})
    ).status_code == 422


@pytest.mark.asyncio
async def test_nothing_else_writes_or_carries_the_address(client):
    """Edits, clones and spreadsheets go around the proxy check, so none of them may."""
    item = await profile(client, "Source")
    item = (await give(client, (item, "kept@example.com"))).json()["data"]["updated"][0]

    edited = await client.put(
        f"/api/v1/profiles/{item['id']}",
        json={
            "name": "Renamed",
            "email": "sneaked@example.com",
            "row_version": item["row_version"],
        },
    )
    assert edited.status_code == 200 and edited.json()["email"] == "kept@example.com"
    assert (await stored(client, item))["email"] == "kept@example.com"

    cloned = await client.post(f"/api/v1/profiles/{item['id']}/clone", json={"new_name": "Copy"})
    assert cloned.status_code == 201 and cloned.json()["email"] is None
    assert (await stored(client, cloned.json()))["email"] is None

    sheet = await client.get("/api/v1/profiles/export/excel")
    rows = list(load_workbook(io.BytesIO(sheet.content)).active.values)
    column = rows[0].index("Email")
    assert column == len(rows[0]) - 1  # last, so no other column moved
    assert {row[1]: row[column] for row in rows[1:]} == {
        "Renamed": "kept@example.com",
        "Copy": None,
    }
    imported = await client.post(
        "/api/v1/profiles/import/excel",
        files={"file": ("profiles.xlsx", sheet.content, "application/octet-stream")},
    )
    assert imported.status_code == 200 and imported.json()["data"]["created_count"] == 2
    listed = (await client.get("/api/v1/profiles", params={"per_page": 100})).json()["profiles"]
    assert sorted(item["email"] or "" for item in listed) == ["", "", "", "kept@example.com"]


@pytest.mark.asyncio
async def test_lookup_names_the_profile_holding_an_address(client):
    live, trashed = await profile(client, "Live"), await profile(client, "Gone")
    await give(client, (live, "live@example.com"), (trashed, "gone@example.com"))
    await client.delete(f"/api/v1/profiles/{trashed['id']}")

    response = await client.post(
        "/api/v1/profiles/emails/taken",
        json={"emails": ["LIVE@example.com", "gone@example.com", "free@example.com", "junk"]},
    )

    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert sorted(response.json()["data"]["taken"], key=lambda item: item["email"]) == [
        {
            "email": "gone@example.com",
            "profile_id": trashed["id"],
            "profile_name": "Gone",
            "in_trash": True,
        },
        {
            "email": "live@example.com",
            "profile_id": live["id"],
            "profile_name": "Live",
            "in_trash": False,
        },
    ]


@pytest.mark.asyncio
async def test_clearing_an_address_lets_a_corrected_one_be_imported(client):
    item = await profile(client)
    item = (await give(client, (item, "wrong@example.com"))).json()["data"]["updated"][0]

    stale = await client.delete(
        f"/api/v1/profiles/{item['id']}/email", params={"row_version": item["row_version"] - 1}
    )
    assert stale.status_code == 409
    assert (await client.delete(f"/api/v1/profiles/{item['id']}/email")).status_code == 422
    missing = await client.delete("/api/v1/profiles/nope/email", params={"row_version": 0})
    assert missing.status_code == 404
    assert (await stored(client, item))["email"] == "wrong@example.com"

    cleared = await client.delete(
        f"/api/v1/profiles/{item['id']}/email", params={"row_version": item["row_version"]}
    )
    assert cleared.status_code == 200 and cleared.json()["data"]["email"] is None
    corrected = (await give(client, (cleared.json()["data"], "right@example.com"))).json()["data"]
    assert corrected["skipped"] == [] and corrected["updated"][0]["email"] == "right@example.com"


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["operator", "viewer"])
async def test_only_an_administrator_imports_looks_up_or_clears(client, monkeypatch, role):
    from tests.integration.test_profile_authorization import setup_actor

    manager, own, _ = await setup_actor(client, role, monkeypatch)
    await manager.storage.set_proxy_check(
        own.id, ProxyCheckRecord(checked_at=datetime.now(timezone.utc), reachable=True)
    )
    target = {"id": own.id, "row_version": own.row_version}

    assert (await give(client, (target, "a@example.com"))).status_code == 403
    lookup = await client.post("/api/v1/profiles/emails/taken", json={"emails": ["a@example.com"]})
    assert lookup.status_code == 403
    cleared = await client.delete(f"/api/v1/profiles/{own.id}/email", params={"row_version": 0})
    assert cleared.status_code == 403
    assert (await manager.get_profile(own.id)).email is None
    # They can still read the address of a profile they may see.
    assert "email" in (await client.get(f"/api/v1/profiles/{own.id}")).json()


@pytest.mark.parametrize("locale", ["zh-CN", "ru"])
def test_every_reason_a_profile_is_skipped_for_has_an_interface_translation(locale):
    """The interface shows an untranslated reason as it is, so a missing one goes unnoticed."""
    import json
    from pathlib import Path

    catalog = json.loads(
        (Path(__file__).parents[2] / "web/src/lib/locales" / f"{locale}.json").read_text("utf-8")
    )
    reasons = [
        value
        for name, value in vars(rules).items()
        if name.startswith("EMAIL_") and isinstance(value, str)
    ]
    assert len(reasons) == 9
    for reason in reasons:
        assert catalog.get(reason), reason


async def make_older(*items):
    """Leave these profiles as an upgrade leaves the rows it found: not allowed an address."""
    connection = dependencies.get_storage_manager().db._connection
    for item in items:
        connection.execute("UPDATE profiles SET email_allowed = 0 WHERE id = ?", (item["id"],))
    connection.commit()


@pytest.mark.asyncio
async def test_a_profile_from_before_email_addresses_never_receives_one(client):
    older, newer = await profile(client, "Older"), await profile(client, "Newer")
    assert older["email_allowed"] is True  # created by this version
    await make_older(older)
    assert (await stored(client, older))["email_allowed"] is False

    data = (await give(client, (older, "a@example.com"), (newer, "b@example.com"))).json()["data"]

    assert data["skipped"] == [{"id": older["id"], "reason": rules.EMAIL_NOT_ALLOWED}]
    assert [item["id"] for item in data["updated"]] == [newer["id"]]
    assert (await stored(client, older))["email"] is None
    # The storage statement refuses on its own, whatever the caller checked.
    with pytest.raises(StaleWriteError):
        await dependencies.get_storage_manager().db.set_profile_email(
            older["id"], "a@example.com", older["row_version"]
        )


@pytest.mark.asyncio
async def test_nothing_done_to_an_older_profile_makes_it_eligible(client):
    """Edits, a proxy change and a fresh check all save the row; none of them is its creation."""
    older = await profile(client, "Older")
    await make_older(older)
    edited = await client.put(
        f"/api/v1/profiles/{older['id']}",
        json={
            "name": "Renamed",
            "email_allowed": True,
            "proxy_config": {"type": "http", "server": "other.example:8080"},
        },
    )
    assert edited.status_code == 200 and edited.json()["email_allowed"] is False
    await dependencies.get_storage_manager().set_proxy_check(
        older["id"], ProxyCheckRecord(checked_at=datetime.now(timezone.utc), reachable=True)
    )
    target = {"id": older["id"], "row_version": edited.json()["row_version"]}
    tagged = await client.post("/api/v1/profiles/tags", json={"profiles": [target], "add": ["x"]})
    current = tagged.json()["data"]["updated"][0]

    data = (await give(client, (current, "a@example.com"))).json()["data"]

    assert data["skipped"] == [{"id": older["id"], "reason": rules.EMAIL_NOT_ALLOWED}]
    assert (await stored(client, older))["email_allowed"] is False


@pytest.mark.asyncio
async def test_a_copy_of_an_older_profile_is_a_new_profile(client):
    older = await profile(client, "Older")
    await make_older(older)

    cloned = await client.post(f"/api/v1/profiles/{older['id']}/clone", json={"new_name": "Copy"})

    assert cloned.status_code == 201 and cloned.json()["email_allowed"] is True
    copy = await stored(client, cloned.json())
    assert copy["email_allowed"] is True
    await dependencies.get_storage_manager().set_proxy_check(
        copy["id"], ProxyCheckRecord(checked_at=datetime.now(timezone.utc), reachable=True)
    )
    data = (await give(client, (copy, "a@example.com"))).json()["data"]
    assert data["skipped"] == [] and data["updated"][0]["email"] == "a@example.com"


@pytest.mark.asyncio
async def test_upgrading_a_database_leaves_its_profiles_without_and_new_ones_with(tmp_path):
    """The rows an upgrade finds can never be given an address; rows created after it can."""
    path = str(tmp_path / "old.db")
    storage = StorageManager(path)
    await storage.initialize()
    await storage.close()
    with sqlite3.connect(path) as connection:
        # Back to the schema of the release before email addresses.
        connection.execute("DROP INDEX idx_profiles_email")
        connection.execute("ALTER TABLE profiles DROP COLUMN email")
        connection.execute("ALTER TABLE profiles DROP COLUMN email_allowed")
        connection.execute(
            "INSERT INTO profiles (id, name, browser_settings) VALUES ('old', 'Old profile', '{}')"
        )

    storage = StorageManager(path)
    await storage.initialize()
    try:
        old = await storage.get_profile("old")
        assert old.email is None and old.email_allowed is False
        with pytest.raises(StaleWriteError):
            await storage.db.set_profile_email("old", "a@example.com", 0)

        # Saving the old row again is not creating it.
        await storage.save_profile(old)
        assert (await storage.get_profile("old")).email_allowed is False

        # Both ways a row is created mark it, and one address still has one holder.
        await storage.save_profile(Profile(id="new", name="New"))
        assert await storage.insert_profile_if_absent(Profile(id="reserved", name="Reserved"))
        assert not await storage.insert_profile_if_absent(Profile(id="old", name="Again"))
        assert (await storage.get_profile("new")).email_allowed is True
        assert (await storage.get_profile("reserved")).email_allowed is True
        assert (await storage.get_profile("old")).email_allowed is False
        await storage.db.set_profile_email("new", "a@example.com", 0)
        with pytest.raises(EmailTaken):
            await storage.db.set_profile_email("reserved", "A@EXAMPLE.com", 0)
    finally:
        await storage.close()

    # A second start changes nothing: the mark is set once, at the upgrade.
    storage = StorageManager(path)
    await storage.initialize()
    try:
        assert (await storage.get_profile("old")).email_allowed is False
        assert (await storage.get_profile("new")).email_allowed is True
    finally:
        await storage.close()
