"""Order lifecycle: draft -> confirmed -> executing -> provisioned|failed
(+ cancelled from draft/confirmed, never from executing - a submit is
irreversible in the real world; + awaiting_payment when the provider holds
the server behind an unpaid order).

`mode` is fixed when the order is drafted: 'real' when it names an account
whose purchases are enabled, else 'prototype'. Prototype execution simulates
provisioning (placeholder id, explicit audit wording, NO row in the servers
cache - the fleet is provider-reported truth and never holds invented
servers); real execution calls the account adapter's provision() and buys
the requested extra IPs on the new server.
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal

from . import audit, db


class OrderError(Exception):
    """Safe to show in the UI."""


def _publish(event: str, data: dict) -> None:
    from . import sync
    sync.publish(event, data)


def _get(order_id: int) -> dict | None:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    return dict(row) if row else None


def _transition(order_id: int, status: str, detail: str | None = None,
                allowed_from: tuple[str, ...] | None = None) -> None:
    """Atomic status change: the WHERE clause is the guard, so two concurrent
    executes (or execute vs cancel) cannot both win - exactly one UPDATE
    matches. allowed_from=None skips the guard (internal use)."""
    with db.connect() as conn:
        cur = conn.execute(
            "UPDATE orders SET status=?, updated_at=? WHERE id=?"
            + (" AND status IN (%s)" % ",".join("?" * len(allowed_from)) if allowed_from else ""),
            (status, db.now(), order_id, *allowed_from) if allowed_from
            else (status, db.now(), order_id))
        if cur.rowcount == 0:
            raise OrderError(f"cannot set status {status} - the order moved on")
        conn.execute("INSERT INTO order_events(order_id, status, detail, created_at) "
                     "VALUES(?,?,?,?)",
                     (order_id, status, detail, db.now()))


def create_order(user_id: int, adapter: str, plan_name: str, location: str,
                 options: dict) -> int:
    """Draft order: validate the plan exists in the catalog at that location,
    snapshot it (price honesty: an order shows what was true when placed),
    estimate the monthly total."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT canonical, source FROM plan_catalog "
            "WHERE adapter=? AND plan_name=? AND location=?",
            (adapter, plan_name, location)).fetchone()
    if not row:
        raise OrderError(f"no plan {plan_name!r} at {location!r} in the {adapter} catalog")

    plan = json.loads(row["canonical"])
    extra_ips = int(options.get("extra_ips", 0))
    if extra_ips < 0:
        raise OrderError("extra_ips must be >= 0")
    ip_offer = plan.get("extra_ip") or {}
    if extra_ips > 0 and not ip_offer:
        raise OrderError("this plan does not offer additional IPs")
    if ip_offer.get("limit") is not None and extra_ips > int(ip_offer["limit"]):
        raise OrderError(f"this plan allows at most {ip_offer['limit']} additional IPs")

    mode, account_id = "prototype", options.get("account_id")
    if account_id is not None:
        from . import accounts
        account = accounts.get_account(int(account_id))
        if not account:
            raise OrderError(f"no account {account_id}")
        if account["adapter"] != adapter:
            raise OrderError(f"account {account['name']} is a {account['adapter']} account, "
                             f"not {adapter}")
        if account["purchases_enabled"]:
            cls = accounts.ADAPTERS.get(adapter)
            if not hasattr(cls, "provision"):
                raise OrderError(f"{adapter} server ordering isn't wired in the panel yet - "
                                 "order in the provider's own panel")
            mode = "real"
        account_id = account["id"]

    # Estimate: plan monthly + extra IP prices where published. Unpublished
    # IP price = partial estimate, labeled as such (never invented).
    # price_monthly can be stored as null (e.g. Gcore pricing BFF down) - the
    # default in .get() only fires when the KEY is absent, so null would raise
    # AttributeError and 500. A plan with no price cannot be honestly estimated:
    # refuse as a clean OrderError, never invent a 0.
    price_monthly = plan.get("price_monthly") or {}
    if price_monthly.get("amount") in (None, ""):
        raise OrderError(f"plan {plan_name!r} has no published monthly price "
                         "right now - try again after the next catalog sync")
    monthly = Decimal(price_monthly.get("amount", "0") or 0)
    ip_price = (ip_offer.get("price") or {}).get("amount")
    currency = (plan.get("price_monthly") or {}).get("currency", "EUR")
    est = monthly
    if ip_price is not None and extra_ips:
        est += Decimal(str(ip_price)) * extra_ips
    partial = ip_price is None and extra_ips > 0
    estimated = {"amount": str(est), "currency": currency, "partial": partial}

    ts = db.now()
    with db.connect() as conn:
        cur = conn.execute(
            """INSERT INTO orders(mode, status, adapter, account_id, plan_name, location,
                                  options, plan_snapshot, estimated_monthly, requested_by,
                                  created_at, updated_at)
               VALUES(?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (mode, adapter, account_id, plan_name, location,
             json.dumps(options), json.dumps(plan), json.dumps(estimated),
             user_id, ts, ts))
        order_id = cur.lastrowid
        conn.execute("INSERT INTO order_events(order_id, status, detail, created_at) "
                     "VALUES(?, 'draft', ?, ?)",
                     (order_id, f"order drafted from {row['source']} catalog", ts))
    audit.record(user_id, "order.create",
                 f"order/{order_id}/{adapter}/{plan_name}/{location}",
                 before=None, after={"estimated_monthly": estimated})
    _publish("order", {"order_id": order_id, "status": "draft"})
    return order_id


def confirm(order_id: int, user_id: int) -> None:
    o = _get(order_id) or _raise_not_found(order_id)
    if o["status"] != "draft":
        raise OrderError(f"cannot confirm an order in status {o['status']}")
    _transition(order_id, "confirmed", "order confirmed by operator", ("draft",))
    audit.record(user_id, "order.confirm", f"order/{order_id}")
    _publish("order", {"order_id": order_id, "status": "confirmed"})


def cancel(order_id: int, user_id: int) -> None:
    o = _get(order_id) or _raise_not_found(order_id)
    if o["status"] not in ("draft", "confirmed"):
        raise OrderError(f"cannot cancel an order in status {o['status']} - "
                         "executing orders are already submitted")
    _transition(order_id, "cancelled", "cancelled by operator", ("draft", "confirmed"))
    audit.record(user_id, "order.cancel", f"order/{order_id}")
    _publish("order", {"order_id": order_id, "status": "cancelled"})


def execute(order_id: int, user_id: int) -> None:
    """confirmed -> executing -> (async) provisioned|failed."""
    o = _get(order_id) or _raise_not_found(order_id)
    if o["status"] != "confirmed":
        raise OrderError(f"cannot execute an order in status {o['status']}")
    _transition(order_id, "executing", "provisioning started", ("confirmed",))
    _publish("order", {"order_id": order_id, "status": "executing"})
    # Spawn on the app loop (threadpool routes have none of their own).
    # No loop at all (tests with start_all disabled): run to completion inline
    # so the order still finishes instead of hanging in "executing".
    from . import sync
    loop = sync._loop
    coro = _execute_task(order_id, user_id, o)
    if loop is not None and loop.is_running():
        loop.call_soon_threadsafe(lambda: asyncio.create_task(coro, name=f"order-{order_id}"))
    else:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # no loop anywhere (plain test context): schedule on a fresh loop
            # thread so the prototype executor still completes
            import threading
            def _run():
                asyncio.run(coro)
            threading.Thread(target=_run, name=f"order-{order_id}", daemon=True).start()
        else:
            asyncio.create_task(coro, name=f"order-{order_id}")


async def _execute_task(order_id: int, user_id: int, o: dict) -> None:
    try:
        if o["mode"] == "prototype":
            # Visible intermediate state (design.md 6) - the operator watches
            # executing -> provisioned with a clear audit trail that NOTHING
            # was created at the provider.
            await asyncio.sleep(3)
            pid = f"proto-{order_id}"
            with db.connect() as conn:
                cur = conn.execute(
                    "UPDATE orders SET status='provisioned', resulting_provider_id=?, "
                    "updated_at=? WHERE id=? AND status='executing'",
                    (pid, db.now(), order_id))
                if cur.rowcount == 0:
                    return  # the order moved on while we slept (cancel raced in) - stay quiet
                conn.execute(
                    "INSERT INTO order_events(order_id, status, detail, created_at) "
                    "VALUES(?, 'provisioned', ?, ?)",
                    (order_id, "prototype order - no server was created at the provider; "
                               f"placeholder id {pid}", db.now()))
            audit.record(user_id, "order.provisioned", f"order/{order_id}",
                         after={"provider_id": pid, "mode": "prototype",
                                "detail": "prototype - no real provisioning"})
        else:
            if await _execute_real(order_id, user_id, o) == "awaiting_payment":
                return
        _publish("order", {"order_id": order_id, "status": "provisioned"})
    except Exception as e:  # noqa: BLE001 - failures are states, not crashes
        msg = f"{type(e).__name__}: {e}"[:500]
        with db.connect() as conn:
            cur = conn.execute(
                "UPDATE orders SET status='failed', updated_at=? WHERE id=? AND status='executing'",
                (db.now(), order_id))
            failed = cur.rowcount > 0
            if failed:
                conn.execute("INSERT INTO order_events(order_id, status, detail, created_at) "
                             "VALUES(?, 'failed', ?, ?)", (order_id, msg, db.now()))
        # outside the transaction: audit opens its own connection, and doing
        # that while this one holds the write lock deadlocked into a rollback
        # (the order then sat in 'executing' forever)
        if failed:
            audit.record(user_id, "order.failed", f"order/{order_id}", after={"error": msg})
            _publish("order", {"order_id": order_id, "status": "failed"})


async def _execute_real(order_id: int, user_id: int, o: dict) -> str:
    """adapter.provision -> provisioned (provider_id recorded, extra IPs
    bought on the new server) or awaiting_payment (pay URL recorded)."""
    from . import accounts, sync
    from .adapters.base import AdapterError, PaymentRequired
    account = accounts.get_account(o["account_id"]) if o["account_id"] else None
    if not account:
        raise OrderError("the order's account no longer exists")
    if not account["purchases_enabled"]:
        raise OrderError("purchases were disabled for this account after the order was "
                         "drafted - nothing was sent to the provider")
    options = json.loads(o["options"])
    adapter = accounts.build_adapter(account)
    try:
        try:
            pid = await adapter.provision(o["plan_name"], o["location"], options)
        except PaymentRequired as e:
            with db.connect() as conn:
                conn.execute("UPDATE orders SET provider_ref=?, pay_url=? WHERE id=?",
                             (e.order_ref, e.pay_url, order_id))
            _transition(order_id, "awaiting_payment",
                        f"provider order {e.order_ref} created unpaid - pay it to start "
                        "delivery", ("executing",))
            audit.record(user_id, "order.awaiting_payment", f"order/{order_id}",
                         after={"provider_ref": e.order_ref, "pay_url": e.pay_url})
            _publish("order", {"order_id": order_id, "status": "awaiting_payment"})
            return "awaiting_payment"
        notes = []
        for n in range(int(options.get("extra_ips") or 0)):
            try:
                ip = await adapter.add_ip(pid)
                notes.append(ip.address)
            except AdapterError as e:  # the server exists - report, don't fail it
                notes.append(f"extra IP {n + 1} failed: {e}")
                break
    finally:
        try:
            await adapter.close()
        except Exception:  # noqa: BLE001
            pass
    with db.connect() as conn:
        cur = conn.execute(
            "UPDATE orders SET status='provisioned', resulting_provider_id=?, updated_at=? "
            "WHERE id=? AND status='executing'", (pid, db.now(), order_id))
        if cur.rowcount:
            conn.execute("INSERT INTO order_events(order_id, status, detail, created_at) "
                         "VALUES(?, 'provisioned', ?, ?)",
                         (order_id, f"server {pid} created at the provider"
                          + (f"; extra IPs: {', '.join(notes)}" if notes else ""), db.now()))
    audit.record(user_id, "order.provisioned", f"order/{order_id}",
                 after={"provider_id": pid, "mode": "real", "extra_ips": notes})
    sync.request_sync(account["id"])
    return "provisioned"


def list_orders() -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT o.*, u.username FROM orders o
               LEFT JOIN users u ON u.id = o.requested_by
               ORDER BY o.id DESC LIMIT 200""").fetchall()
    return [dict(r) for r in rows]


def get_order(order_id: int) -> dict | None:
    o = _get(order_id)
    if not o:
        return None
    with db.connect() as conn:
        events = conn.execute(
            "SELECT status, detail, created_at FROM order_events "
            "WHERE order_id=? ORDER BY id", (order_id,)).fetchall()
    o["events"] = [dict(e) for e in events]
    return o


def _raise_not_found(order_id: int) -> None:
    raise OrderError(f"no order {order_id}")
