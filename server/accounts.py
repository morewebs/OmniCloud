"""Provider accounts + credential CRUD and the adapter registry."""
from __future__ import annotations

from . import config, db, secrets
from .adapters.base import Capability, ProviderAdapter
from .adapters.hetzner import HetznerAdapter
from .adapters.leaseweb import LeasewebAdapter

# Registry: adapters that ship. Roadmap adapters slot in here only.
ADAPTERS: dict[str, type[ProviderAdapter]] = {
    HetznerAdapter.key: HetznerAdapter,
    LeasewebAdapter.key: LeasewebAdapter,
}


def adapter_info() -> list[dict]:
    return [
        {
            "key": cls.key,
            "display_name": cls.display_name,
            "capabilities": sorted(c.value for c in cls.capabilities),
        }
        for cls in ADAPTERS.values()
    ]


def list_accounts() -> list[dict]:
    """Account rows for the API. Never includes the token, only metadata."""
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT a.id, a.adapter, a.name, a.enabled, a.created_at,
                      c.last4, c.scope, c.created_at AS cred_created, c.last_used_at,
                      s.last_success_at, s.last_error
               FROM accounts a
               LEFT JOIN credentials c ON c.account_id = a.id
               LEFT JOIN sync_state s ON s.account_id = a.id
               ORDER BY a.id"""
        ).fetchall()
    return [dict(row) for row in rows]


def create_account(adapter: str, name: str, token: str, scope: str | None = None) -> int:
    if adapter not in ADAPTERS:
        raise ValueError(f"unknown adapter: {adapter}")
    ct = secrets.encrypt(token)
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO accounts(adapter, name, created_at) VALUES(?,?,?)",
            (adapter, name, db.now()),
        )
        account_id = cur.lastrowid
        conn.execute(
            "INSERT INTO credentials(account_id, ciphertext, last4, scope, created_at) "
            "VALUES(?,?,?,?,?)",
            (account_id, ct, token[-4:], scope, db.now()),
        )
        conn.execute("INSERT OR IGNORE INTO sync_state(account_id) VALUES(?)", (account_id,))
    return account_id


def delete_account(account_id: int) -> bool:
    with db.connect() as conn:
        cur = conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        return cur.rowcount > 0


def set_enabled(account_id: int, enabled: bool) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE accounts SET enabled=? WHERE id=?", (int(enabled), account_id))


def get_account(account_id: int) -> dict | None:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT a.id, a.adapter, a.name, a.enabled FROM accounts a WHERE a.id=?",
            (account_id,),
        ).fetchone()
    return dict(row) if row else None


def build_adapter(account: dict) -> ProviderAdapter:
    """Instantiate the account's adapter with its decrypted token."""
    cls = ADAPTERS[account["adapter"]]
    with db.connect() as conn:
        row = conn.execute(
            "SELECT ciphertext FROM credentials WHERE account_id=?", (account["id"],)
        ).fetchone()
    if not row:
        raise ValueError(f"account {account['name']} has no stored credential")
    token = secrets.decrypt(row["ciphertext"])
    with db.connect() as conn:
        conn.execute("UPDATE credentials SET last_used_at=? WHERE account_id=?",
                     (db.now(), account["id"]))
    return cls(account["id"], account["name"], token)


def interval_for(account_id: int, adapter_key: str = "") -> float:
    """Sync interval in minutes, from settings; the env default feeds the
    DB default (OMNICLOUD_SYNC_INTERVAL_MIN was previously dead config)."""
    default = db.get_setting("sync_default_interval") or str(config.DEFAULT_SYNC_INTERVAL_MIN)
    raw = db.get_setting(f"sync_interval:{account_id}") or default
    try:
        return max(1.0, float(raw))
    except ValueError:
        return float(config.DEFAULT_SYNC_INTERVAL_MIN)
