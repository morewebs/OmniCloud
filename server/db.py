"""SQLite via stdlib sqlite3. One fresh connection per operation, WAL, short transactions.

ponytail: no migrations - CREATE TABLE IF NOT EXISTS + schema_version row; real
migration tool only when a schema change actually ships (v2).
"""
import sqlite3
from contextlib import AbstractContextManager, closing, contextmanager
from datetime import datetime, timezone

from . import config

SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin','viewer')),
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    adapter TEXT NOT NULL,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS credentials (
    account_id INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    ciphertext BLOB NOT NULL,
    last4 TEXT NOT NULL,
    scope TEXT,
    created_at TEXT NOT NULL,
    last_used_at TEXT
);
CREATE TABLE IF NOT EXISTS servers (
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    provider_id TEXT NOT NULL,
    canonical TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (account_id, provider_id)
);
CREATE TABLE IF NOT EXISTS traffic_history (
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    provider_id TEXT NOT NULL,
    day TEXT NOT NULL,
    bytes_used INTEGER NOT NULL,
    counting TEXT NOT NULL,
    PRIMARY KEY (account_id, provider_id, day)
);
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    provider_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    requested_by INTEGER NOT NULL REFERENCES users(id),
    status TEXT NOT NULL CHECK(status IN ('in_progress','done','failed')),
    detail TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    before_state TEXT,
    after_state TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_state (
    account_id INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    last_success_at TEXT,
    last_attempt_at TEXT,
    last_error TEXT,
    in_flight INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plan_catalog (
    adapter TEXT NOT NULL,
    plan_name TEXT NOT NULL,
    location TEXT NOT NULL,
    canonical TEXT NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('live','seeded')),
    last_verified TEXT,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (adapter, plan_name, location)
);
CREATE TABLE IF NOT EXISTS catalog_state (
    adapter TEXT PRIMARY KEY,
    last_success_at TEXT,
    last_attempt_at TEXT,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    mode TEXT NOT NULL CHECK(mode IN ('prototype','real')),
    status TEXT NOT NULL CHECK(status IN
        ('draft','confirmed','executing','provisioned','failed','cancelled')),
    adapter TEXT NOT NULL,
    account_id INTEGER REFERENCES accounts(id),
    plan_name TEXT NOT NULL,
    location TEXT NOT NULL,
    options TEXT NOT NULL,
    plan_snapshot TEXT NOT NULL,
    estimated_monthly TEXT NOT NULL,
    resulting_provider_id TEXT,
    requested_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS order_events (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_tokens (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_used_at TEXT
);
INSERT OR IGNORE INTO settings(key, value) VALUES ('schema_version', '3');
"""


def now() -> str:
    """UTC ISO timestamp, the one format used everywhere in the DB."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> AbstractContextManager[sqlite3.Connection]:
    """A fresh connection with sane pragmas. Use as a context manager - it
    commits/rolls back AND closes (sqlite's plain `with` leaves the handle
    open to GC)."""
    @contextmanager
    def _cm():
        conn = sqlite3.connect(config.DB_PATH, timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()  # sqlite's own `with` did this; keep the semantics
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
    return _cm()


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        row = conn.execute("SELECT value FROM settings WHERE key='schema_version'").fetchone()
        if row and int(row[0]) > SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema v{row[0]} is newer than this build (v{SCHEMA_VERSION})."
            )


def get_setting(key: str, default: str | None = None) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default


def set_setting(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO settings(key, value) VALUES(?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
