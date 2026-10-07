"""Provider accounts + credential CRUD and the adapter registry."""
from __future__ import annotations

import json

from . import config, db, secrets
from .adapters.base import Capability, ProviderAdapter
from .adapters.gcore import GcoreAdapter
from .adapters.gcore_hosting import GcoreHostingAdapter
from .adapters.hetzner import HetznerAdapter
from .adapters.leaseweb import LeasewebAdapter
from .adapters.lightnode import LightNodeAdapter
from .adapters.netlen import NetlenAdapter
from .adapters.ovh import OvhAdapter
from .adapters.tube import TubeAdapter

# Registry: adapters that ship. Roadmap adapters slot in here only.
ADAPTERS: dict[str, type[ProviderAdapter]] = {
    HetznerAdapter.key: HetznerAdapter,
    LeasewebAdapter.key: LeasewebAdapter,
    OvhAdapter.key: OvhAdapter,
    GcoreAdapter.key: GcoreAdapter,
    GcoreHostingAdapter.key: GcoreHostingAdapter,
    NetlenAdapter.key: NetlenAdapter,
    TubeAdapter.key: TubeAdapter,
    LightNodeAdapter.key: LightNodeAdapter,
}


def adapter_info() -> list[dict]:
    return [
        {
            "key": cls.key,
            "display_name": cls.display_name,
            "capabilities": sorted(c.value for c in cls.capabilities),
            "credential_fields": [f.model_dump() for f in cls.credential_fields],
            # real server orders: provision() exists for this provider
            "orders": hasattr(cls, "provision"),
        }
        for cls in ADAPTERS.values()
    ]


def pack_credential(adapter: str, token: str | None,
                    fields: dict[str, str] | None) -> tuple[str, str]:
    """(secret to encrypt, last4 to display). Single-token adapters store the
    token as-is (unchanged from v3); multi-field adapters store one JSON
    object. last4 comes from the identifying non-secret field - the last
    non-secret one in the schema (forms list url, then username) - never
    from a password."""
    schema = ADAPTERS[adapter].credential_fields
    fields = fields or {}
    if len(schema) == 1 and schema[0].name == "token":
        secret = token or fields.get("token") or ""
        if not secret.strip():
            raise ValueError("API token is required")
        return secret, secret[-4:]
    values: dict[str, str] = {}
    for f in schema:
        v = fields.get(f.name)
        if v is None or not str(v).strip():
            v = f.default
        if v is None or not str(v).strip():
            raise ValueError(f"{f.label} is required")
        # secrets keep their exact bytes (a password may end in a space)
        values[f.name] = str(v) if f.secret else str(v).strip()
    ident = next((values[f.name] for f in reversed(schema) if not f.secret), None)
    return json.dumps(values), (ident or values[schema[-1].name])[-4:]


def list_accounts() -> list[dict]:
    """Account rows for the API. Never includes the token, only metadata."""
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT a.id, a.adapter, a.name, a.enabled, a.purchases_enabled, a.created_at,
                      c.last4, c.scope, c.created_at AS cred_created, c.last_used_at,
                      s.last_success_at, s.last_error
               FROM accounts a
               LEFT JOIN credentials c ON c.account_id = a.id
               LEFT JOIN sync_state s ON s.account_id = a.id
               ORDER BY a.id"""
        ).fetchall()
    return [dict(row) for row in rows]


def create_account(adapter: str, name: str, token: str | None = None,
                   scope: str | None = None,
                   fields: dict[str, str] | None = None) -> int:
    if adapter not in ADAPTERS:
        raise ValueError(f"unknown adapter: {adapter}")
    secret, last4 = pack_credential(adapter, token, fields)
    ct = secrets.encrypt(secret)
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO accounts(adapter, name, created_at) VALUES(?,?,?)",
            (adapter, name, db.now()),
        )
        account_id = cur.lastrowid
        conn.execute(
            "INSERT INTO credentials(account_id, ciphertext, last4, scope, created_at) "
            "VALUES(?,?,?,?,?)",
            (account_id, ct, last4, scope, db.now()),
        )
        conn.execute("INSERT OR IGNORE INTO sync_state(account_id) VALUES(?)", (account_id,))
    return account_id


def delete_account(account_id: int) -> bool:
    with db.connect() as conn:
        # orders are the operator's legal trail (kept forever - see the
        # retention sweeper) and their account_id is deliberately NOT ON
        # DELETE CASCADE, so an account with orders would hit the FK and be
        # undeletable. Detach the reference instead: the order history
        # survives (account_id NULL), the account and its credentials/
        # servers/actions rows go.
        conn.execute("UPDATE orders SET account_id=NULL WHERE account_id=?",
                     (account_id,))
        cur = conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        return cur.rowcount > 0


def set_enabled(account_id: int, enabled: bool) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE accounts SET enabled=? WHERE id=?", (int(enabled), account_id))


def set_purchases_enabled(account_id: int, enabled: bool) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE accounts SET purchases_enabled=? WHERE id=?",
                     (int(enabled), account_id))


def get_account(account_id: int) -> dict | None:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT a.id, a.adapter, a.name, a.enabled, a.purchases_enabled "
            "FROM accounts a WHERE a.id=?",
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


def interval_for(account_id: int) -> float:
    """Sync interval in minutes, from settings; the env default feeds the
    DB default (OMNICLOUD_SYNC_INTERVAL_MIN was previously dead config)."""
    default = db.get_setting("sync_default_interval") or str(config.DEFAULT_SYNC_INTERVAL_MIN)
    raw = db.get_setting(f"sync_interval:{account_id}") or default
    try:
        return max(1.0, float(raw))
    except ValueError:
        return float(config.DEFAULT_SYNC_INTERVAL_MIN)
