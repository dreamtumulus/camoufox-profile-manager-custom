"""Retry receipts bind fresh profiles to exact batch intent, including lost responses."""

import asyncio
import json
import sqlite3
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from camoufox_pm.api.models.templates import TemplateBatchRequest
from camoufox_pm.config import get_settings
from camoufox_pm.core.database import StaleWriteError
from camoufox_pm.core.profile_templates import ProfileTemplates
from camoufox_pm.core.proxy_inventory import ProxyInventory
from camoufox_pm.core.template_batches import BatchAccessLost, BatchConflict, TemplateBatches


async def authorized():
    pass


def request(names, **values):
    return TemplateBatchRequest(batch_id=uuid4(), row_version=0, names=names, **values).model_dump(
        mode="json"
    )


@pytest.fixture
def encryption_key(monkeypatch):
    monkeypatch.setenv("CPM_SECRET_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_fresh_profiles_and_proxy_copies_are_independent_and_receipts_have_no_secrets(
    profile_manager, encryption_key
):
    templates = ProfileTemplates(profile_manager)
    template = await templates.create("Defaults", {"os": "linux"}, tags=["work"])
    proxies = ProxyInventory(profile_manager)
    refs = []
    for i in range(2):
        proxy = await proxies.create(
            {
                "name": f"Proxy {i}",
                "type": "socks5",
                "server": f"host{i}:1080",
                "username": "private-user",
                "password": "private-password",
            }
        )
        refs.append({"id": proxy["id"], "row_version": 0})
    data = request(["Profile 001", "Profile 002"], proxy_refs=refs)
    batches = TemplateBatches(profile_manager)
    result = await batches.submit(template["id"], data, authorize=authorized)
    assert [row["status"] for row in result["items"]] == ["created", "created"]
    profiles = [await profile_manager.get_profile(row["profile_id"]) for row in result["items"]]
    assert profiles[0].id != profiles[1].id and profiles[0].storage_path != profiles[1].storage_path
    assert all(p.fingerprint is None and p.tags == ["work"] for p in profiles)
    assert [p.proxy.server for p in profiles] == ["host0:1080", "host1:1080"]
    assert all(p.proxy.password == "private-password" for p in profiles)
    assert "private-password" not in json.dumps(result) and "private-user" not in json.dumps(result)
    assert "private-password" not in batches._batch(data["batch_id"])["request_json"]
    for row in profile_manager.storage.db._connection.execute("SELECT proxy_config FROM profiles"):
        assert "private-password" not in row[0] and "enc:" in row[0]


@pytest.mark.asyncio
async def test_lost_response_replay_and_deleted_results_never_recreate_profiles(profile_manager):
    templates = ProfileTemplates(profile_manager)
    template = await templates.create("Defaults", {})
    batches = TemplateBatches(profile_manager)
    data = request(["first", "second"])
    first = await batches.submit(template["id"], data, authorize=authorized)
    await templates.delete(template["id"], 0)
    again = await batches.submit(template["id"], data, authorize=authorized)
    assert first == again
    profile_id = first["items"][0]["profile_id"]
    await profile_manager.delete_profile(profile_id)
    assert (await batches.submit(template["id"], data, authorize=authorized))["items"][0][
        "status"
    ] == "in_trash"
    await profile_manager.permanently_delete_profile(profile_id)
    assert (await batches.submit(template["id"], data, authorize=authorized))["items"][0][
        "status"
    ] == "unavailable"
    assert len(await profile_manager.storage.list_profiles()) == 1


@pytest.mark.asyncio
async def test_reusing_a_batch_identifier_with_changed_intent_is_a_conflict(profile_manager):
    template = await ProfileTemplates(profile_manager).create("Defaults", {})
    batches = TemplateBatches(profile_manager)
    data = request(["first"])
    await batches.submit(template["id"], data, authorize=authorized)
    with pytest.raises(BatchConflict):
        await batches.submit(template["id"], {**data, "names": ["changed"]}, authorize=authorized)
    assert len(await profile_manager.storage.list_profiles()) == 1


@pytest.mark.asyncio
async def test_partial_failure_retries_only_unpublished_rows(profile_manager, monkeypatch):
    from camoufox_pm.core import profile_templates as module

    template = await ProfileTemplates(profile_manager).create("Defaults", {})
    batches = TemplateBatches(profile_manager)
    data = request(["one", "two", "three"])
    install = module._install_snapshots
    calls = 0

    def sometimes_fails(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("private-provider-password")
        return install(*args)

    monkeypatch.setattr(module, "_install_snapshots", sometimes_fails)
    first = await batches.submit(template["id"], data, authorize=authorized)
    assert [row["status"] for row in first["items"]] == ["created", "failed", "created"]
    assert "private-provider-password" not in json.dumps(first)
    final = await batches.submit(template["id"], data, authorize=authorized)
    assert all(row["status"] == "created" for row in final["items"])
    assert calls == 4
    for position in (0, 2):
        assert first["items"][position]["profile_id"] == final["items"][position]["profile_id"]
    assert len(await profile_manager.storage.list_profiles()) == 3
    assert len(list(profile_manager.profiles_dir.glob("profile_*"))) == 3


@pytest.mark.asyncio
async def test_concurrent_identical_submissions_publish_each_row_once(profile_manager):
    template = await ProfileTemplates(profile_manager).create("Defaults", {})
    data = request(["one", "two", "three"])
    batches = TemplateBatches(profile_manager)
    results = await asyncio.gather(
        *(batches.submit(template["id"], data, authorize=authorized) for _ in range(2))
    )
    assert [row["profile_id"] for row in results[0]["items"]] == [
        row["profile_id"] for row in results[1]["items"]
    ]
    assert len(await profile_manager.storage.list_profiles()) == 3
    assert len(list(profile_manager.profiles_dir.glob("profile_*"))) == 3


@pytest.mark.asyncio
async def test_duplicate_names_are_explicit_and_validated_before_any_batch_write(profile_manager):
    template = await ProfileTemplates(profile_manager).create("Defaults", {})
    await profile_manager.create_profile("Existing", generate_fingerprint=False)
    batches = TemplateBatches(profile_manager)
    for names in (["fresh", "ＥＸＩＳＴＩＮＧ"], ["same", "SAME"]):
        with pytest.raises(ValueError):
            await batches.submit(template["id"], request(names), authorize=authorized)
    assert batches.list() == []
    assert len(await profile_manager.storage.list_profiles()) == 1
    result = await batches.submit(
        template["id"],
        request(["Existing", "Existing"], allow_duplicate_names=True),
        authorize=authorized,
    )
    assert all(row["status"] == "created" for row in result["items"])


@pytest.mark.asyncio
async def test_missing_proxy_in_the_last_row_blocks_the_whole_new_batch(profile_manager):
    template = await ProfileTemplates(profile_manager).create("Defaults", {})
    proxy = await ProxyInventory(profile_manager).create(
        {"name": "Valid", "type": "http", "server": "host:80"}
    )
    batches = TemplateBatches(profile_manager)
    with pytest.raises(ValueError):
        await batches.submit(
            template["id"],
            request(
                ["one", "two"],
                proxy_refs=[
                    {"id": proxy["id"], "row_version": 0},
                    {"id": "missing", "row_version": 0},
                ],
            ),
            authorize=authorized,
        )
    assert batches.list() == []
    assert not await profile_manager.storage.list_profiles()


@pytest.mark.asyncio
async def test_receipt_rolls_back_if_profile_publication_fails(profile_manager, monkeypatch):
    template = await ProfileTemplates(profile_manager).create("Defaults", {})
    batches = TemplateBatches(profile_manager)
    data = request(["one"])
    publish = profile_manager.storage.db._upsert_profile

    def failed(*_args):
        raise sqlite3.OperationalError("disk write failed")

    monkeypatch.setattr(profile_manager.storage.db, "_upsert_profile", failed)
    result = await batches.submit(template["id"], data, authorize=authorized)
    assert result["items"][0]["status"] == "failed" and result["items"][0]["profile_id"] is None
    assert not await profile_manager.storage.list_profiles()
    assert not list(profile_manager.profiles_dir.glob("profile_*"))
    monkeypatch.setattr(profile_manager.storage.db, "_upsert_profile", publish)
    assert (await batches.submit(template["id"], data, authorize=authorized))["items"][0][
        "status"
    ] == "created"


@pytest.mark.asyncio
async def test_interruption_after_one_commit_can_resume_without_duplicate_creation(profile_manager):
    template = await ProfileTemplates(profile_manager).create("Defaults", {})
    batches = TemplateBatches(profile_manager)
    data = request(["one", "two"])

    async def interrupted(_profile_id):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await batches.submit(template["id"], data, authorize=authorized, on_created=interrupted)
    saved = batches.get(data["batch_id"])
    assert [row["status"] for row in saved["items"]] == ["created", "pending"]
    result = await batches.submit(template["id"], data, authorize=authorized)
    assert result["items"][0]["profile_id"] == saved["items"][0]["profile_id"]
    assert len(await profile_manager.storage.list_profiles()) == 2


@pytest.mark.asyncio
async def test_revocation_before_publication_leaves_no_profile_or_directory(profile_manager):
    template = await ProfileTemplates(profile_manager).create("Defaults", {})
    batches = TemplateBatches(profile_manager)
    calls = 0

    async def authority():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise BatchAccessLost

    with pytest.raises(BatchAccessLost):
        await batches.submit(template["id"], request(["one", "two"]), authorize=authority)
    assert not await profile_manager.storage.list_profiles()
    assert not list(profile_manager.profiles_dir.glob("profile_*"))


@pytest.mark.asyncio
async def test_stale_template_does_not_write_a_new_batch(profile_manager):
    templates = ProfileTemplates(profile_manager)
    template = await templates.create("Defaults", {})
    await templates.update(template["id"], 0, {"notes": "newer"})
    batches = TemplateBatches(profile_manager)
    with pytest.raises(StaleWriteError):
        await batches.submit(template["id"], request(["one"]), authorize=authorized)
    assert batches.list() == []


@pytest.mark.asyncio
async def test_changed_proxy_is_not_copied_after_preparation_and_preview_keeps_original_mapping(
    profile_manager,
):
    template = await ProfileTemplates(profile_manager).create("Defaults", {})
    inventory = ProxyInventory(profile_manager)
    proxy = await inventory.create({"name": "Original", "type": "http", "server": "original:80"})
    batches = TemplateBatches(profile_manager)
    data = request(["one"], proxy_refs=[{"id": proxy["id"], "row_version": 0}])
    calls = 0

    async def authority():
        nonlocal calls
        calls += 1
        if calls == 3:
            await inventory.update(proxy["id"], {"server": "changed:80"}, 0)

    result = await batches.submit(template["id"], data, authorize=authority)
    assert result["items"][0]["status"] == "failed"
    assert result["preview"]["proxy_mapping"][0]["server"] == "original:80"
    assert not await profile_manager.storage.list_profiles()
    assert not list(profile_manager.profiles_dir.glob("profile_*"))
    with pytest.raises(StaleWriteError):
        await batches.submit(template["id"], data, authorize=authorized)


@pytest.mark.asyncio
async def test_receipts_remain_effective_after_reopening_the_database(profile_manager):
    template = await ProfileTemplates(profile_manager).create("Defaults", {})
    data = request(["one"])
    result = await TemplateBatches(profile_manager).submit(
        template["id"], data, authorize=authorized
    )
    await profile_manager.storage.db.close()
    await profile_manager.storage.db.initialize()
    repeated = await TemplateBatches(profile_manager).submit(
        template["id"], data, authorize=authorized
    )
    assert result == repeated
    assert len(await profile_manager.storage.list_profiles()) == 1


async def proxies_in(profile_manager, *groups):
    """One saved proxy per name given, filed under that library group (None is ungrouped)."""
    inventory = ProxyInventory(profile_manager)
    refs = []
    for group in groups:
        number = len(await inventory.list())
        proxy = await inventory.create(
            {
                "name": f"Proxy {number}",
                "type": "http",
                "server": f"host{number}.example:8080",
                "group_name": group,
            }
        )
        refs.append({"id": proxy["id"], "row_version": proxy["row_version"]})
    return refs


def profile_groups(profile_manager):
    return [
        tuple(row)
        for row in profile_manager.storage.db._connection.execute(
            "SELECT id, name FROM profile_groups ORDER BY name"
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("groups", [("UK", "Belgium"), ("UK", None), (None, "UK", "UK")])
async def test_a_batch_cannot_mix_proxy_groups(profile_manager, groups):
    """Half of a batch named for one country was once created on another country's proxies."""
    template = await ProfileTemplates(profile_manager).create("Defaults", {})
    refs = await proxies_in(profile_manager, *groups)
    batches = TemplateBatches(profile_manager)
    for follow in (False, True):
        data = request(
            [f"UK {n:03}" for n in range(len(refs))], proxy_refs=refs, group_from_proxies=follow
        )
        with pytest.raises(ValueError, match="cannot mix proxy groups"):
            batches.preview(template["id"], data)
        with pytest.raises(ValueError, match="cannot mix proxy groups"):
            await batches.submit(template["id"], data, authorize=authorized)
    assert not await profile_manager.storage.list_profiles()
    assert batches.list() == [] and profile_groups(profile_manager) == []
    assert not list(profile_manager.profiles_dir.glob("profile_*"))


@pytest.mark.asyncio
async def test_profiles_follow_the_proxy_group_and_the_profile_group_is_created_once(
    profile_manager,
):
    template = await ProfileTemplates(profile_manager).create("Defaults", {})
    batches = TemplateBatches(profile_manager)
    refs = await proxies_in(profile_manager, "UK", "UK")
    data = request(["UK 001", "UK 002"], proxy_refs=refs, group_from_proxies=True)

    preview = batches.preview(template["id"], data)
    assert preview["proxy_group"] == "UK"
    assert preview["profile_group"] == {"name": "UK", "new": True}
    assert profile_groups(profile_manager) == []  # a preview writes nothing

    result = await batches.submit(template["id"], data, authorize=authorized)
    assert result["preview"]["profile_group"] == {"name": "UK", "new": True}
    ((group_id, name),) = profile_groups(profile_manager)
    assert name == "UK"
    created = [await profile_manager.get_profile(row["profile_id"]) for row in result["items"]]
    assert [profile.group for profile in created] == [group_id, group_id]
    listed = {
        group.name: group.profile_count
        for group in await profile_manager.storage.list_profile_groups()
    }
    assert listed == {"UK": 2}

    # A later batch from the same library group joins the group that now exists.
    more = request(
        ["UK 003"], proxy_refs=await proxies_in(profile_manager, "UK"), group_from_proxies=True
    )
    assert batches.preview(template["id"], more)["profile_group"] == {"name": "UK", "new": False}
    again = await batches.submit(template["id"], more, authorize=authorized)
    assert (await profile_manager.get_profile(again["items"][0]["profile_id"])).group == group_id
    assert profile_groups(profile_manager) == [(group_id, "UK")]


@pytest.mark.asyncio
async def test_following_ungrouped_proxies_leaves_the_profiles_ungrouped(profile_manager):
    template = await ProfileTemplates(profile_manager).create("Defaults", {})
    batches = TemplateBatches(profile_manager)
    refs = await proxies_in(profile_manager, None, None)
    data = request(["one", "two"], proxy_refs=refs, group_from_proxies=True)
    preview = batches.preview(template["id"], data)
    assert preview["proxy_group"] is None and preview["profile_group"] is None
    result = await batches.submit(template["id"], data, authorize=authorized)
    created = [await profile_manager.get_profile(row["profile_id"]) for row in result["items"]]
    assert [profile.group for profile in created] == [None, None]
    assert profile_groups(profile_manager) == []


@pytest.mark.asyncio
async def test_without_following_the_chosen_group_is_used_and_none_is_created(profile_manager):
    template = await ProfileTemplates(profile_manager).create("Defaults", {})
    batches = TemplateBatches(profile_manager)
    chosen = await profile_manager.create_group("Chosen")
    refs = await proxies_in(profile_manager, "UK")
    data = request(["one"], proxy_refs=refs, group=chosen["id"])
    preview = batches.preview(template["id"], data)
    assert preview["proxy_group"] == "UK" and "profile_group" not in preview
    result = await batches.submit(template["id"], data, authorize=authorized)
    assert (await profile_manager.get_profile(result["items"][0]["profile_id"])).group == chosen[
        "id"
    ]
    assert [name for _, name in profile_groups(profile_manager)] == ["Chosen"]


@pytest.mark.asyncio
async def test_a_profile_that_is_not_published_leaves_no_group_behind(profile_manager, monkeypatch):
    template = await ProfileTemplates(profile_manager).create("Defaults", {})
    batches = TemplateBatches(profile_manager)
    refs = await proxies_in(profile_manager, "UK")
    data = request(["one"], proxy_refs=refs, group_from_proxies=True)
    publish = profile_manager.storage.db._upsert_profile

    def failed(*_args):
        raise sqlite3.OperationalError("disk write failed")

    monkeypatch.setattr(profile_manager.storage.db, "_upsert_profile", failed)
    result = await batches.submit(template["id"], data, authorize=authorized)
    assert result["items"][0]["status"] == "failed"
    assert profile_groups(profile_manager) == []
    monkeypatch.setattr(profile_manager.storage.db, "_upsert_profile", publish)
    await batches.submit(template["id"], data, authorize=authorized)
    assert [name for _, name in profile_groups(profile_manager)] == ["UK"]


@pytest.mark.parametrize(
    "values,message",
    [
        ({"group_from_proxies": True, "group": "team"}, "not both"),
        ({"group_from_proxies": True}, "requires selected proxies"),
    ],
)
def test_following_the_proxy_group_needs_proxies_and_no_other_group(values, message):
    refs = [{"id": "p", "row_version": 0}] if "group" in values else []
    with pytest.raises(ValueError, match=message):
        request(["one"], proxy_refs=refs, **values)


@pytest.mark.asyncio
async def test_requests_from_before_the_option_keep_their_receipt(profile_manager):
    """An incomplete batch recorded by an older version must still match when resumed."""
    template = await ProfileTemplates(profile_manager).create("Defaults", {})
    batches = TemplateBatches(profile_manager)
    current = request(["one"])
    older = {key: value for key, value in current.items() if key != "group_from_proxies"}
    first = await batches.submit(template["id"], older, authorize=authorized)
    assert "group_from_proxies" not in first["request"]
    assert await batches.submit(template["id"], current, authorize=authorized) == first
    with pytest.raises(BatchConflict):
        refs = await proxies_in(profile_manager, "UK")
        changed = {**current, "proxy_refs": refs, "group_from_proxies": True}
        await batches.submit(template["id"], changed, authorize=authorized)


@pytest.mark.asyncio
async def test_a_recorded_batch_resumes_even_if_its_proxies_groups_now_differ(
    profile_manager, monkeypatch
):
    """The rule is for new batches; it must not strand one that is half created."""
    from camoufox_pm.core import profile_templates as module

    template = await ProfileTemplates(profile_manager).create("Defaults", {})
    batches = TemplateBatches(profile_manager)
    refs = await proxies_in(profile_manager, "UK", "UK")
    data = request(["one", "two"], proxy_refs=refs)
    install = module._install_snapshots
    calls = 0

    def second_fails(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk full")
        return install(*args)

    monkeypatch.setattr(module, "_install_snapshots", second_fails)
    first = await batches.submit(template["id"], data, authorize=authorized)
    assert [row["status"] for row in first["items"]] == ["created", "failed"]
    # As a library edited by an older version could leave it: regrouped, version unchanged.
    connection = profile_manager.storage.db._connection
    connection.execute(
        "UPDATE proxy_inventory SET group_name='Belgium' WHERE id=?", (refs[1]["id"],)
    )
    connection.commit()
    final = await batches.submit(template["id"], data, authorize=authorized)
    assert [row["status"] for row in final["items"]] == ["created", "created"]


@pytest.mark.parametrize("locale", ["zh-CN", "ru"])
def test_the_mixed_groups_refusal_has_an_interface_translation(locale):
    from pathlib import Path

    from camoufox_pm.core.template_batches import MIXED_PROXY_GROUPS

    catalog = json.loads(
        (Path(__file__).parents[2] / "web/src/lib/locales" / f"{locale}.json").read_text("utf-8")
    )
    assert catalog.get(MIXED_PROXY_GROUPS)
