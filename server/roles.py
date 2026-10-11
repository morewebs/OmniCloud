"""IP roles: operator labels for a server's extra IPs that survive a swap.

A server with two swappable IPs and two automated loops needs each side to
know which IP is whose. The provider only says "extra IP" - the meaning lives
here. A role is one row per (account, provider_id, role), re-keyed to the
current address on every swap, so `role_of("awgbk-secondary")` stays stable
while the address underneath churns.

The canonical cache is the provider's truth; a role row whose address is no
longer on the server (released by hand, provider-side swap we did not drive)
is stale and reported as such rather than silently followed.
"""
from __future__ import annotations

from . import db


def _now() -> str:
    return db.now()


def assign(account_id: int, provider_id: str, role: str, address: str,
           user_id: int | None = None) -> dict:
    """Point a role at an address (admin/UI path). Replaces any previous
    holder; one role names one address."""
    with db.connect() as conn:
        old = conn.execute(
            "SELECT address FROM ip_roles WHERE account_id=? AND provider_id=? AND role=?",
            (account_id, provider_id, role)).fetchone()
        ts = _now()
        conn.execute(
            "INSERT INTO ip_roles(address, account_id, provider_id, role, set_by, "
            "created_at, updated_at) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(address) DO UPDATE SET "
            "account_id=excluded.account_id, provider_id=excluded.provider_id, "
            "role=excluded.role, set_by=excluded.set_by, updated_at=excluded.updated_at",
            (address, account_id, provider_id, role, user_id, ts, ts))
        # one address cannot carry two roles, and one role cannot point at
        # two addresses: drop every other row naming this role or address
        conn.execute("DELETE FROM ip_roles WHERE account_id=? AND provider_id=? AND role=? "
                     "AND address<>?", (account_id, provider_id, role, address))
        conn.execute("DELETE FROM ip_roles WHERE address=? AND NOT (account_id=? AND "
                     "provider_id=? AND role=?)", (address, account_id, provider_id, role))
    return {"role": role, "address": address, "account_id": account_id,
            "provider_id": provider_id, "previous": old["address"] if old else None}


def carry(account_id: int, provider_id: str, old: str, new: str) -> None:
    """Re-key a role after a swap (ip-change path, no user involved)."""
    with db.connect() as conn:
        conn.execute("UPDATE ip_roles SET address=?, updated_at=? "
                     "WHERE account_id=? AND provider_id=? AND address=?",
                     (new, _now(), account_id, provider_id, old))


def address_of(account_id: int, provider_id: str, role: str) -> str | None:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT address FROM ip_roles WHERE account_id=? AND provider_id=? AND role=?",
            (account_id, provider_id, role)).fetchone()
        return row["address"] if row else None


def list_roles() -> list[dict]:
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT address, account_id, provider_id, role, created_at, updated_at "
            "FROM ip_roles ORDER BY account_id, provider_id, role").fetchall()]


def role_of(address: str) -> dict | None:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT address, account_id, provider_id, role, updated_at "
            "FROM ip_roles WHERE address=?", (address,)).fetchone()
        return dict(row) if row else None


def annotate(ips: list[dict], account_id: int, provider_id: str) -> list[dict]:
    """Attach each address's role (if any) as `role` on a describe() answer."""
    with db.connect() as conn:
        rows = {r["address"]: r["role"] for r in conn.execute(
            "SELECT address, role FROM ip_roles WHERE account_id=? AND provider_id=?",
            (account_id, provider_id)).fetchall()}
    for ip in ips:
        ip.setdefault("role", rows.get(ip.get("address")))
    return ips
