"""Reusable settings and extension snapshots with a fresh identity per profile."""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import datetime, timezone

from . import fingerprint_store
from .database import StaleWriteError
from .extension_library import (
    ExtensionLibrary,
    _install_snapshots,
    drain_operation,
    profile_extension_payloads,
    validate_xpi,
)
from .models import BrowserSettings, Profile, generate_short_id, normalize_tags
from .profile_operations import profile_data_path, profile_operation, run_file_operation

_FPGEN_DEVICE_FIELDS = frozenset(
    {
        "screen",
        "user_agent",
        "hardware_concurrency",
        "device_memory",
        "max_touch_points",
        "fonts",
    }
)


def initialize_tables(connection) -> None:
    connection.execute("""CREATE TABLE IF NOT EXISTS profile_templates(
        id TEXT PRIMARY KEY, name TEXT NOT NULL, browser_settings TEXT NOT NULL,
        notes TEXT, tags TEXT NOT NULL DEFAULT '[]', row_version INTEGER NOT NULL DEFAULT 0,
        fingerprint_source TEXT NOT NULL DEFAULT 'fpgen', fingerprint_preset_id TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    connection.execute("""CREATE TABLE IF NOT EXISTS template_extensions(
        template_id TEXT NOT NULL, position INTEGER NOT NULL, xpi BLOB NOT NULL,
        PRIMARY KEY(template_id, position))""")


class ProfileTemplates:
    def __init__(self, manager):
        self.manager = manager
        self.connection = manager.storage.db._connection
        self.extensions = ExtensionLibrary(manager)

    def _row(self, template_id):
        row = self.connection.execute(
            "SELECT * FROM profile_templates WHERE id=?", (template_id,)
        ).fetchone()
        if row is None:
            raise LookupError("Template not found")
        return row

    def _payloads(self, template_id):
        return [
            bytes(row[0])
            for row in self.connection.execute(
                "SELECT xpi FROM template_extensions WHERE template_id=? ORDER BY position",
                (template_id,),
            )
        ]

    def get(self, template_id):
        row = self._row(template_id)
        return {
            **dict(row),
            "browser_settings": json.loads(row["browser_settings"]),
            "tags": json.loads(row["tags"]),
            "extensions": [validate_xpi(payload) for payload in self._payloads(template_id)],
        }

    async def list(self):
        return [
            self.get(row[0])
            for row in self.connection.execute(
                "SELECT id FROM profile_templates ORDER BY created_at DESC"
            )
        ]

    async def create(
        self,
        name,
        browser_settings,
        notes=None,
        tags=None,
        extension_ids=None,
        *,
        payloads=None,
        fingerprint_source="fpgen",
        fingerprint_preset_id=None,
    ):
        if fingerprint_source not in ("fpgen", "preset"):
            raise ValueError("Unknown fingerprint source")
        parsed = BrowserSettings(**browser_settings)
        parsed.fingerprint_source = None
        parsed.fingerprint_preset_id = None
        settings = parsed.model_dump(mode="json")
        if fingerprint_source == "fpgen":
            fingerprint_preset_id = None
        snapshots = self.extensions.payloads(extension_ids or []) if payloads is None else payloads
        # Validate the complete selection without installing files.
        metadata = [validate_xpi(payload) for payload in snapshots]
        if (
            len(snapshots) > 16
            or len({item["addon_id"] for item in metadata}) != len(metadata)
            or sum(item["unpacked_bytes"] for item in metadata) > 128 * 1024 * 1024
        ):
            raise ValueError("Invalid template extension selection")
        template_id = generate_short_id()
        now = datetime.now(timezone.utc).isoformat()
        with self.connection:
            self.connection.execute(
                "INSERT INTO profile_templates(id,name,browser_settings,notes,tags,fingerprint_source,fingerprint_preset_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    template_id,
                    name,
                    json.dumps(settings),
                    notes,
                    json.dumps(normalize_tags(tags or [])),
                    fingerprint_source,
                    fingerprint_preset_id,
                    now,
                    now,
                ),
            )
            self.connection.executemany(
                "INSERT INTO template_extensions(template_id,position,xpi) VALUES(?,?,?)",
                [(template_id, position, payload) for position, payload in enumerate(snapshots)],
            )
        return self.get(template_id)

    async def from_profile(self, profile_id, profile_row_version, name):
        async with profile_operation(self.manager, profile_id) as profile:
            if profile.row_version != profile_row_version:
                raise StaleWriteError(profile_id, profile_row_version)
            payloads = await run_file_operation(
                profile_extension_payloads, profile, profile_data_path(self.manager, profile)
            )
            latest = await self.manager.get_profile(profile_id)
            if latest is None or latest.row_version != profile_row_version:
                raise StaleWriteError(profile_id, profile_row_version)
            return await self.create(
                name,
                profile.browser_settings.model_dump(),
                profile.notes,
                getattr(profile, "tags", []),
                payloads=payloads,
                fingerprint_source=profile.browser_settings.fingerprint_source or "fpgen",
                fingerprint_preset_id=profile.browser_settings.fingerprint_preset_id,
            )

    async def update(self, template_id, row_version, changes):
        before = self._row(template_id)
        if before["row_version"] != row_version:
            raise StaleWriteError(template_id, row_version)
        fields = []
        values = []
        for field in (
            "name",
            "browser_settings",
            "notes",
            "tags",
            "fingerprint_source",
            "fingerprint_preset_id",
        ):
            if field in changes:
                value = changes[field]
                if field == "tags":
                    value = normalize_tags(value)
                if field == "browser_settings":
                    parsed = BrowserSettings(**value)
                    parsed.fingerprint_source = None
                    parsed.fingerprint_preset_id = None
                    value = parsed.model_dump(mode="json")
                if field == "fingerprint_source" and value not in ("fpgen", "preset"):
                    raise ValueError("Unknown fingerprint source")
                fields.append(f"{field}=?")
                values.append(json.dumps(value) if field in ("browser_settings", "tags") else value)
        if changes.get("fingerprint_source") == "fpgen" and "fingerprint_preset_id" not in changes:
            fields.append("fingerprint_preset_id=?")
            values.append(None)
        snapshots = (
            self.extensions.payloads(changes["extension_ids"])
            if "extension_ids" in changes
            else None
        )
        if snapshots is not None:
            metadata = [validate_xpi(payload) for payload in snapshots]
            if (
                len({item["addon_id"] for item in metadata}) != len(metadata)
                or sum(item["unpacked_bytes"] for item in metadata) > 128 * 1024 * 1024
            ):
                raise ValueError("Invalid template extension selection")
        fields.extend(["row_version=row_version+1", "updated_at=?"])
        values.extend([datetime.now(timezone.utc).isoformat(), template_id, row_version])
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE profile_templates SET "
                + ",".join(fields)
                + " WHERE id=? AND row_version=?",
                values,
            )
            if not cursor.rowcount:
                raise StaleWriteError(template_id, row_version)
            if snapshots is not None:
                self.connection.execute(
                    "DELETE FROM template_extensions WHERE template_id=?", (template_id,)
                )
                self.connection.executemany(
                    "INSERT INTO template_extensions(template_id,position,xpi) VALUES(?,?,?)",
                    [
                        (template_id, position, payload)
                        for position, payload in enumerate(snapshots)
                    ],
                )
        return self.get(template_id)

    async def delete(self, template_id, row_version):
        self._row(template_id)
        with self.connection:
            cursor = self.connection.execute(
                "DELETE FROM profile_templates WHERE id=? AND row_version=?",
                (template_id, row_version),
            )
            if not cursor.rowcount:
                raise StaleWriteError(template_id, row_version)
            self.connection.execute(
                "DELETE FROM template_extensions WHERE template_id=?", (template_id,)
            )

    async def create_profile(
        self, template_id, row_version, name, group=None, *, before_publish=None, on_publish=None
    ):
        template = self.get(template_id)
        if template["row_version"] != row_version:
            raise StaleWriteError(template_id, row_version)
        payloads = self._payloads(template_id)
        generation_settings = template["browser_settings"]
        if template["fingerprint_source"] == "fpgen":
            generation_settings = {
                key: value
                for key, value in generation_settings.items()
                if key not in _FPGEN_DEVICE_FIELDS
            }
        for attempt in range(12):
            profile = Profile(
                name=name,
                group=group,
                browser_settings=BrowserSettings(**template["browser_settings"]),
                notes=template["notes"],
                tags=template["tags"],
            )
            await self.manager.prepare_profile_identity(
                profile,
                generation_settings,
                fingerprint_preset=template["fingerprint_preset_id"],
                allocate_unused_preset=True,
                use_real_device=template["fingerprint_source"] == "preset",
            )
            directory = self.manager.profiles_dir / f"profile_{profile.id}"
            directory.mkdir(exist_ok=False)
            profile.storage_path = str(directory)
            published = False
            try:
                task = asyncio.create_task(
                    asyncio.to_thread(_install_snapshots, payloads, directory)
                )
                (profile.extensions, _), cancelled = await drain_operation(task)
                if cancelled:
                    raise asyncio.CancelledError
                if before_publish is not None:
                    await before_publish()
                # Publish the complete row only after snapshot files exist. A write
                # reservation guards the generated ID; never upsert another identity.
                with self.connection:
                    self.connection.execute("BEGIN IMMEDIATE")
                    current = self.connection.execute(
                        "SELECT row_version FROM profile_templates WHERE id=?", (template_id,)
                    ).fetchone()
                    if current is None:
                        raise LookupError("Template not found")
                    if current[0] != row_version:
                        raise StaleWriteError(template_id, row_version)
                    if self.connection.execute(
                        "SELECT 1 FROM profiles WHERE id=?", (profile.id,)
                    ).fetchone():
                        raise ValueError("Profile ID collision; retry creating the profile")
                    fingerprint_store.reserve_identity(self.connection, profile)
                    if on_publish is not None:
                        on_publish(profile)
                    self.manager.storage.db._upsert_profile(profile)
                published = True
                return profile
            except fingerprint_store.DuplicateFingerprint:
                if attempt + 1 == 12:
                    raise ValueError(
                        "Could not generate an unused device fingerprint; retry"
                    ) from None
            finally:
                if not published:
                    shutil.rmtree(directory, ignore_errors=True)
        raise AssertionError("unreachable")
