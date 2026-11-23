"""What the auto-onboarding screen keeps between visits.

The token and the choices, in two tables of one row each. They are their own
tables rather than keys in `app_settings` because the token is a credential,
and nothing that lists settings should ever come across it.

And, for every row: the authorizations asked of the platform for it, and
whether its account is on the platform. An authorize link, the code pasted
back and the job's state are never kept: the link is asked of the platform
again when it is shown, and the code is only handed on.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ..crypto import decrypt, encrypt
from .preset import Preset

if TYPE_CHECKING:
    from ..database import StorageManager

_ENCRYPTED = "enc:"


def initialize_tables(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS auto_onboarding_connection (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            token TEXT NOT NULL,
            preview TEXT NOT NULL,
            label TEXT NOT NULL,
            read_at TIMESTAMP NOT NULL,
            refused_at TIMESTAMP
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS auto_onboarding_preset (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            value TEXT NOT NULL,
            version INTEGER NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS auto_onboarding_authorization (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id TEXT NOT NULL,
            email TEXT NOT NULL,
            job_id TEXT NOT NULL,
            status TEXT NOT NULL,
            outcome TEXT,
            error_code TEXT,
            account_id TEXT,
            account_email TEXT,
            node TEXT,
            proxy TEXT,
            expires_at TEXT,
            cooldown_until TEXT,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
    """)
    connection.execute(
        "CREATE INDEX IF NOT EXISTS auto_onboarding_authorization_profile "
        "ON auto_onboarding_authorization (profile_id, id)"
    )
    connection.execute("""
        CREATE TABLE IF NOT EXISTS auto_onboarding_onboarded (
            profile_id TEXT NOT NULL,
            email TEXT NOT NULL,
            account_id TEXT,
            source TEXT NOT NULL,
            onboarded_at TIMESTAMP NOT NULL,
            PRIMARY KEY (profile_id, email)
        )
    """)


def preview_of(token: str) -> str:
    """As the platform previews a token: its first twelve characters and its last four."""
    return f"{token[:12]}…{token[-4:]}"


@dataclass(frozen=True)
class Connection:
    """The saved token as it may be shown: never the token itself."""

    preview: str
    label: str
    read_at: str
    # When the platform last refused this token; None while it is accepted.
    refused_at: str | None
    # False when the installation has no CPM_SECRET_KEY, so the token is kept as it was typed.
    encrypted: bool


# An authorization the platform has not finished with: its link still waits for
# the code, or the code was handed over and the platform is working on it.
OPEN = ("pending", "working")


@dataclass(frozen=True)
class Authorization:
    """One authorization asked of the platform for a profile's account.

    `status` is the platform's own word for the job (pending, working, done,
    failed), or "lost" for a job that can no longer be asked about. `outcome`
    says what a finished one came to: "onboarded", "reused" (an account that
    was already there was authorized again), "mismatch" (another account than
    the row's was onboarded) or "failed".
    """

    id: int
    profile_id: str
    email: str
    job_id: str
    status: str
    outcome: str | None
    error_code: str | None
    account_id: str | None
    account_email: str | None
    node: str | None
    proxy: str | None
    expires_at: str | None
    cooldown_until: str | None
    created_at: str

    @property
    def open(self) -> bool:
        return self.status in OPEN


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _address(email: str) -> str:
    """An account is told apart by its address without regard to case, as the platform does."""
    return email.strip().lower()


class OnboardingStore:
    def __init__(self, storage: "StorageManager"):
        self.connection = storage.db._connection

    def saved(self) -> Connection | None:
        row = self.connection.execute(
            "SELECT token, preview, label, read_at, refused_at FROM auto_onboarding_connection"
        ).fetchone()
        if row is None:
            return None
        return Connection(
            preview=row["preview"],
            label=row["label"],
            read_at=row["read_at"],
            refused_at=row["refused_at"],
            encrypted=str(row["token"]).startswith(_ENCRYPTED),
        )

    def token(self) -> str | None:
        row = self.connection.execute("SELECT token FROM auto_onboarding_connection").fetchone()
        return None if row is None else decrypt(str(row["token"]))

    def save_token(self, token: str, label: str) -> None:
        """Keep a token the platform has just accepted, in place of any other."""
        with self.connection:
            self.connection.execute(
                "INSERT INTO auto_onboarding_connection (id, token, preview, label, read_at, "
                "refused_at) VALUES (1, ?, ?, ?, ?, NULL) "
                "ON CONFLICT(id) DO UPDATE SET token = excluded.token, "
                "preview = excluded.preview, label = excluded.label, "
                "read_at = excluded.read_at, refused_at = NULL",
                (encrypt(token), preview_of(token), label, _now()),
            )

    def accepted(self, label: str) -> None:
        """The platform answered for the saved token: note when, and what it calls it."""
        with self.connection:
            self.connection.execute(
                "UPDATE auto_onboarding_connection SET label = ?, read_at = ?, refused_at = NULL",
                (label, _now()),
            )

    def refused(self) -> None:
        """The platform no longer accepts the saved token. It stays, to be replaced."""
        with self.connection:
            self.connection.execute(
                "UPDATE auto_onboarding_connection SET refused_at = ?", (_now(),)
            )

    def preset(self) -> tuple[Preset, int]:
        """What was chosen, and how many times it has been saved."""
        row = self.connection.execute(
            "SELECT value, version FROM auto_onboarding_preset"
        ).fetchone()
        if row is None:
            return Preset(), 0
        try:
            return Preset.model_validate_json(row["value"]), int(row["version"])
        except ValueError:
            # Written by a version whose choices this one cannot read: start over
            # rather than refuse to open the screen.
            return Preset(), int(row["version"])

    def save_preset(self, preset: Preset) -> int:
        with self.connection:
            self.connection.execute(
                "INSERT INTO auto_onboarding_preset (id, value, version, updated_at) "
                "VALUES (1, ?, 1, ?) "
                "ON CONFLICT(id) DO UPDATE SET value = excluded.value, "
                "version = auto_onboarding_preset.version + 1, updated_at = excluded.updated_at",
                (preset.model_dump_json(), _now()),
            )
            row = self.connection.execute("SELECT version FROM auto_onboarding_preset").fetchone()
        return int(row["version"])

    # --- authorizations ---------------------------------------------------------

    _COLUMNS = (
        "id, profile_id, email, job_id, status, outcome, error_code, account_id, "
        "account_email, node, proxy, expires_at, cooldown_until, created_at"
    )

    def _authorization(self, row: sqlite3.Row | None) -> Authorization | None:
        return None if row is None else Authorization(**{key: row[key] for key in row.keys()})

    def latest(self, profile_id: str, email: str) -> Authorization | None:
        """The last authorization asked for this profile while it had this address."""
        return self._authorization(
            self.connection.execute(
                f"SELECT {self._COLUMNS} FROM auto_onboarding_authorization "
                "WHERE profile_id = ? AND email = ? ORDER BY id DESC LIMIT 1",
                (profile_id, _address(email)),
            ).fetchone()
        )

    def latest_of(self, profile_ids: list[str]) -> dict[str, Authorization]:
        """Every listed profile's last authorization, whatever address it was for."""
        found: dict[str, Authorization] = {}
        for start in range(0, len(profile_ids), 500):
            part = profile_ids[start : start + 500]
            marks = ",".join("?" * len(part))
            for row in self.connection.execute(
                f"SELECT {self._COLUMNS} FROM auto_onboarding_authorization WHERE id IN ("
                "SELECT MAX(id) FROM auto_onboarding_authorization "
                f"WHERE profile_id IN ({marks}) GROUP BY profile_id)",
                part,
            ):
                found[row["profile_id"]] = Authorization(**{key: row[key] for key in row.keys()})
        return found

    def open_authorizations(self) -> list[Authorization]:
        rows = self.connection.execute(
            f"SELECT {self._COLUMNS} FROM auto_onboarding_authorization "
            "WHERE status IN ('pending', 'working') ORDER BY id"
        ).fetchall()
        return [Authorization(**{key: row[key] for key in row.keys()}) for row in rows]

    def begin(
        self,
        profile_id: str,
        email: str,
        job_id: str,
        node: str | None,
        proxy: str | None,
        expires_at: str | None,
    ) -> Authorization:
        """Note an authorization the platform has just made a link for."""
        now = _now()
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO auto_onboarding_authorization (profile_id, email, job_id, status, "
                "node, proxy, expires_at, created_at, updated_at) "
                "VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?)",
                (profile_id, _address(email), job_id, node, proxy, expires_at, now, now),
            )
        found = self._authorization(
            self.connection.execute(
                f"SELECT {self._COLUMNS} FROM auto_onboarding_authorization WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        )
        assert found is not None
        return found

    def update(self, authorization: Authorization, **values: str | None) -> Authorization:
        """Write what the platform now says of an authorization."""
        allowed = {
            "status",
            "outcome",
            "error_code",
            "account_id",
            "account_email",
            "expires_at",
            "cooldown_until",
        }
        assert set(values) <= allowed, set(values) - allowed
        assignments = ", ".join(f"{name} = ?" for name in values)
        with self.connection:
            self.connection.execute(
                f"UPDATE auto_onboarding_authorization SET {assignments}, updated_at = ? "
                "WHERE id = ?",
                (*values.values(), _now(), authorization.id),
            )
        found = self._authorization(
            self.connection.execute(
                f"SELECT {self._COLUMNS} FROM auto_onboarding_authorization WHERE id = ?",
                (authorization.id,),
            ).fetchone()
        )
        assert found is not None
        return found

    # --- what is onboarded ------------------------------------------------------

    def onboarded(self) -> set[tuple[str, str]]:
        """Every (profile, address) whose account is on the platform.

        Told by profile and address together: a profile given another address
        is another account, and may be onboarded again.
        """
        return {
            (row["profile_id"], row["email"])
            for row in self.connection.execute(
                "SELECT profile_id, email FROM auto_onboarding_onboarded"
            )
        }

    def is_onboarded(self, profile_id: str, email: str) -> bool:
        return (
            self.connection.execute(
                "SELECT 1 FROM auto_onboarding_onboarded WHERE profile_id = ? AND email = ?",
                (profile_id, _address(email)),
            ).fetchone()
            is not None
        )

    def mark_onboarded(self, profile_id: str, email: str, account_id: str | None) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO auto_onboarding_onboarded (profile_id, email, account_id, source, "
                "onboarded_at) VALUES (?, ?, ?, 'authorization', ?) "
                "ON CONFLICT(profile_id, email) DO NOTHING",
                (profile_id, _address(email), account_id, _now()),
            )
