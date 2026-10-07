"""Per-account billing snapshots: balance, invoices, unpaid orders, renewals.

Each account's sync loop refreshes its snapshot on its own (slower) timer -
billing changes daily, not every five minutes, and every provider's billing
API is N+1-shaped. A billing failure is recorded on the snapshot, never on
the account's server sync. What a provider's API lacks is listed in the
snapshot's not_exposed (rendered "not exposed", never zero).

The same pass moves IP/server orders out of awaiting_payment once the
provider reports them delivered or cancelled.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from . import accounts, db
from .adapters.base import ProviderAdapter

log = logging.getLogger("omnicloud.billing")

DEFAULT_INTERVAL_MIN = 60
RENEWAL_WARN_DAYS = 14


def interval_min() -> float:
    try:
        return max(5.0, float(db.get_setting("billing_interval_min") or DEFAULT_INTERVAL_MIN))
    except ValueError:
        return float(DEFAULT_INTERVAL_MIN)


def is_due(account_id: int) -> bool:
    with db.connect() as conn:
        row = conn.execute("SELECT fetched_at, last_error FROM billing_snapshots "
                           "WHERE account_id=?", (account_id,)).fetchone()
    if not row or not row["fetched_at"]:
        return True
    last = datetime.fromisoformat(row["fetched_at"])
    return datetime.now(timezone.utc) - last >= timedelta(minutes=interval_min())


async def refresh(account: dict, adapter: ProviderAdapter) -> dict | None:
    """One billing pass with an already-built adapter. Never raises: the
    error lands on the snapshot row."""
    from . import sync
    if not _supports(type(adapter)):
        return None
    ts = db.now()
    try:
        snap = await adapter.get_billing()
    except Exception as e:  # noqa: BLE001 - billing must never break a sync
        msg = f"{type(e).__name__}: {e}"[:500]
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO billing_snapshots(account_id, fetched_at, last_error) VALUES(?,?,?) "
                "ON CONFLICT(account_id) DO UPDATE SET fetched_at=excluded.fetched_at, "
                "last_error=excluded.last_error", (account["id"], ts, msg))
        log.warning("billing refresh failed for account %s: %s", account["id"], msg)
        sync.publish("billing_updated", {"account_id": account["id"]})
        return None
    canonical = json.dumps(snap.model_dump(mode="json")) if snap is not None else None
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO billing_snapshots(account_id, canonical, fetched_at, last_error) "
            "VALUES(?,?,?,NULL) ON CONFLICT(account_id) DO UPDATE SET "
            "canonical=excluded.canonical, fetched_at=excluded.fetched_at, last_error=NULL",
            (account["id"], canonical, ts))
    await _settle_awaiting_orders(account, adapter)
    sync.publish("billing_updated", {"account_id": account["id"]})
    return json.loads(canonical) if canonical else None


async def refresh_if_due(account: dict, adapter: ProviderAdapter) -> None:
    if is_due(account["id"]):
        await refresh(account, adapter)


async def refresh_now(account_id: int) -> dict | None:
    account = accounts.get_account(account_id)
    if not account:
        raise ValueError("no such account")
    adapter = accounts.build_adapter(account)
    try:
        return await refresh(account, adapter)
    finally:
        try:
            await adapter.close()
        except Exception:  # noqa: BLE001
            pass


async def _settle_awaiting_orders(account: dict, adapter: ProviderAdapter) -> None:
    """awaiting_payment -> provisioned/cancelled when the provider says so."""
    from . import orders
    with db.connect() as conn:
        rows = conn.execute("SELECT id, provider_ref FROM orders WHERE account_id=? "
                            "AND status='awaiting_payment' AND provider_ref IS NOT NULL",
                            (account["id"],)).fetchall()
    if not rows or not hasattr(adapter, "order_status"):
        return
    for row in rows:
        try:
            state = await adapter.order_status(row["provider_ref"])
        except Exception:  # noqa: BLE001 - next pass retries
            continue
        try:
            if state == "delivered":
                orders._transition(row["id"], "provisioned",
                                   "provider reports the order delivered",
                                   allowed_from=("awaiting_payment",))
            elif state == "cancelled":
                orders._transition(row["id"], "cancelled",
                                   "provider cancelled or expired the unpaid order",
                                   allowed_from=("awaiting_payment",))
        except orders.OrderError:
            pass


def snapshots() -> list[dict]:
    """One row per account (billing or not): the Billing page's cards."""
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT a.id AS account_id, a.adapter, a.name, a.enabled,
                      b.canonical, b.fetched_at, b.last_error
               FROM accounts a LEFT JOIN billing_snapshots b ON b.account_id = a.id
               ORDER BY a.id""").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["billing"] = json.loads(d.pop("canonical")) if d.get("canonical") else None
        d["supported"] = _supports(accounts.ADAPTERS.get(r["adapter"]))
        d["low_balance_threshold"] = db.get_setting(f"billing_low_balance:{r['account_id']}")
        out.append(d)
    return out


def _supports(cls) -> bool:
    """Adapters are duck-typed: billing = a get_billing that isn't the
    base class's None-returning default."""
    fn = getattr(cls, "get_billing", None) if cls else None
    return fn is not None and fn is not ProviderAdapter.get_billing


def _when(s: str) -> datetime:
    """Provider dates are often date-only ("2026-11-01" -> naive): read
    those as UTC so they compare with now."""
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _amount(m: dict | None) -> Decimal | None:
    try:
        return Decimal(str(m["amount"])) if m else None
    except (KeyError, InvalidOperation):
        return None


def alerts(now: datetime | None = None) -> list[dict]:
    """Overview alerts: money the operator owes or is about to run out of.
    Shaped like the sync alert (account_id + error text) so the overview
    renders them without a new pattern."""
    now = now or datetime.now(timezone.utc)
    soon = now + timedelta(days=RENEWAL_WARN_DAYS)
    out = []
    for snap in snapshots():
        b = snap["billing"]
        if not b:
            continue
        aid = snap["account_id"]
        for inv in b.get("invoices") or []:
            open_amt = _amount(inv.get("open_amount"))
            if open_amt is None or open_amt <= 0:
                continue
            due = inv.get("due_date")
            overdue = bool(due) and _when(due) < now
            out.append({"kind": "billing", "account_id": aid,
                        "error": f"invoice {inv['id']} {'OVERDUE' if overdue else 'unpaid'}: "
                                 f"{open_amt} {inv['open_amount']['currency']} open"
                                 + (f", due {due[:10]}" if due else "")})
        for o in b.get("unpaid_orders") or []:
            out.append({"kind": "billing", "account_id": aid,
                        "error": f"order {o['id']} awaits payment"
                                 + (f" ({_amount(o.get('total'))} {o['total']['currency']})"
                                    if o.get("total") else "")})
        for r in b.get("renewals") or []:
            if r.get("date") and r.get("auto") is not True \
                    and _when(r["date"]) <= soon:
                out.append({"kind": "billing", "account_id": aid,
                            "error": f"{r['name']} expires {r['date'][:10]} "
                                     "and does not auto-renew"})
        threshold = snap.get("low_balance_threshold")
        bal = _amount(b.get("balance"))
        if threshold and bal is not None:
            try:
                if bal < Decimal(threshold):
                    out.append({"kind": "billing", "account_id": aid,
                                "error": f"balance {bal} {b['balance']['currency']} is below "
                                         f"the {threshold} alert threshold"})
            except InvalidOperation:
                pass
    return out
