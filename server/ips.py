"""IP management across providers, and the IP-change API's engine.

The contract the operator's own server/script calls (docs/ip-change.md):
POST /api/ips/{ip}/change releases that swappable IP, acquires a fresh one on
the same server and returns it. Testing the new address (DPI filtering) is
the caller's job - OmniCloud never judges an IP and never rotates on a
schedule. The server's primary IP is never touched.

A change spends money (a fresh IP purchase at most providers), so every
acquisition passes the same guards: the account's purchases_enabled toggle,
a per-account daily cap (a looping script can't run up a bill), and one
change at a time per server. Each acquisition writes an orders row
(kind=ip) - the purchase trail - plus the usual action row and audit entry.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
from datetime import datetime, timedelta, timezone

from . import accounts, audit, db
from .adapters.base import (ActionTimeout, AdapterError, Capability, IpAddress, IpCost,
                            PaymentRequired)

DEFAULT_DAILY_CAP = 10

# describe()'s cost lookup is a live provider call; a server polling its own
# IP list must not log in to the provider panel on every poll
COST_TTL_S = 3600

_locks: dict[tuple[int, str], asyncio.Lock] = {}
_cost_cache: dict[tuple[int, str], tuple[float, IpCost | None]] = {}


class IpError(Exception):
    """Refusal with the HTTP status the route returns. Safe to show."""
    def __init__(self, http_status: int, message: str, **extra):
        super().__init__(message)
        self.status = http_status
        self.extra = extra


def find_owner(address: str) -> tuple[dict, dict] | None:
    """(account row, cached canonical server) carrying `address`, from the
    synced fleet cache - the caller only needs to know its own IP."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT s.account_id, s.canonical FROM servers s "
            "JOIN accounts a ON a.id = s.account_id WHERE s.canonical LIKE ?",
            (f'%"{address}"%',)).fetchall()
    for row in rows:
        s = json.loads(row["canonical"])
        if address in {ip.get("address") for ip in s.get("ips") or []} \
                or s.get("ipv4") == address:
            return accounts.get_account(row["account_id"]), s
    return None


def _cached_ip(server: dict, address: str) -> dict | None:
    for ip in server.get("ips") or []:
        if ip.get("address") == address:
            return ip
    if server.get("ipv4") == address:
        # an adapter that reports only ipv4 (no ips list): that address is
        # the server's own - treat it as primary, never release it
        return {"address": address, "primary": True}
    return None


def daily_cap(account_id: int) -> int:
    raw = (db.get_setting(f"ip_change_daily_cap:{account_id}")
           or db.get_setting("ip_change_daily_cap") or str(DEFAULT_DAILY_CAP))
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_DAILY_CAP


def acquisitions_last_24h(account_id: int) -> int:
    """Every change/add attempt counts, whatever its outcome: a failed
    attempt may still have bought something, and a predictable count is
    what a script author can plan around."""
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    with db.connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM actions WHERE account_id=? AND kind IN (?, ?) "
            "AND created_at >= ?",
            (account_id, Capability.IP_CHANGE.value, Capability.IP_ADD.value, since),
        ).fetchone()[0]


def _caps(account: dict) -> frozenset:
    cls = accounts.ADAPTERS.get(account["adapter"])
    return cls.capabilities if cls else frozenset()


def _guard_purchase(account: dict, cap: Capability) -> None:
    if cap not in _caps(account):
        raise IpError(409, f"{account['adapter']} has no API for {cap.value.replace('_', ' ')}")
    if not account.get("purchases_enabled"):
        raise IpError(403, f"purchases are disabled for account {account['name']} - "
                           "an admin enables them under Credentials")
    cap_n = daily_cap(account["id"])
    used = acquisitions_last_24h(account["id"])
    if used >= cap_n:
        raise IpError(429, f"daily IP acquisition cap reached ({used}/{cap_n} in the "
                           "last 24h) - raise it in Settings if this is intended",
                      used=used, cap=cap_n)


def _cached_server(account_id: int, provider_id: str) -> dict:
    with db.connect() as conn:
        row = conn.execute("SELECT canonical FROM servers WHERE account_id=? AND provider_id=?",
                           (account_id, provider_id)).fetchone()
    if not row:
        raise IpError(404, "no such server in the synced fleet")
    return json.loads(row["canonical"])


def _start_action(account_id: int, provider_id: str, cap: Capability, user_id: int) -> int:
    with db.connect() as conn:
        return conn.execute(
            "INSERT INTO actions(account_id, provider_id, kind, requested_by, status, created_at) "
            "VALUES(?,?,?,?, 'in_progress', ?)",
            (account_id, provider_id, cap.value, user_id, db.now())).lastrowid


def _finish_action(action_id: int, status: str, detail: str, result: dict | None) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE actions SET status=?, completed_at=?, detail=?, result=? WHERE id=?",
                     (status, db.now(), detail[:500],
                      json.dumps(result) if result is not None else None, action_id))


def _record_order(account: dict, server: dict, user_id: int, status: str,
                  cost: IpCost | None, *, new_ip: str | None = None,
                  replaces: str | None = None, provider_ref: str | None = None,
                  pay_url: str | None = None, detail: str = "") -> int:
    """The purchase trail: one orders row per IP acquisition attempt."""
    ts = db.now()
    estimate = {"partial": cost is None or cost.price is None,
                "per": cost.per if cost else None,
                "amount": str(cost.price.amount) if cost and cost.price else None,
                "currency": cost.price.currency if cost and cost.price else None}
    with db.connect() as conn:
        oid = conn.execute(
            """INSERT INTO orders(mode, status, kind, adapter, account_id, plan_name, location,
                   options, plan_snapshot, estimated_monthly, resulting_provider_id,
                   target_provider_id, provider_ref, pay_url, requested_by, created_at, updated_at)
               VALUES('real', ?, 'ip', ?, ?, 'extra-ip', ?, ?, '{}', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (status, account["adapter"], account["id"], server.get("region") or "",
             json.dumps({"server": server.get("name"), "replaces": replaces}),
             json.dumps(estimate), new_ip, server["provider_id"], provider_ref, pay_url,
             user_id, ts, ts)).lastrowid
        conn.execute("INSERT INTO order_events(order_id, status, detail, created_at) "
                     "VALUES(?,?,?,?)", (oid, status, detail or None, ts))
    return oid


async def _resync(account_id: int) -> None:
    from . import sync
    with contextlib.suppress(Exception):
        await sync.sync_account_now(account_id)


def _publish(account_id: int, kind: str, status: str) -> None:
    from . import sync
    sync.publish("action", {"account_id": account_id, "kind": kind, "status": status})


async def _cached_cost(account: dict, provider_id: str) -> IpCost | None:
    key = (account["id"], provider_id)
    hit = _cost_cache.get(key)
    if hit and time.monotonic() - hit[0] < COST_TTL_S:
        return hit[1]
    adapter = accounts.build_adapter(account)
    try:
        cost = await adapter.ip_cost(provider_id)
    except AdapterError:
        cost = None
    finally:
        with contextlib.suppress(Exception):
            await adapter.close()
    _cost_cache[key] = (time.monotonic(), cost)
    return cost


async def describe(address: str) -> dict:
    """GET /api/ips/{ip}: who owns it, is it swappable, what a change costs."""
    found = find_owner(address)
    if not found:
        raise IpError(404, f"{address} is not on any synced server")
    account, server = found
    ip = _cached_ip(server, address) or {}
    caps = _caps(account)
    cost = None
    if Capability.IP_ADD in caps:
        cost = await _cached_cost(account, server["provider_id"])
    return {
        "address": address,
        "account_id": account["id"], "account": account["name"],
        "adapter": account["adapter"],
        "provider_id": server["provider_id"], "server": server.get("name"),
        "primary": bool(ip.get("primary")),
        "changeable": (Capability.IP_CHANGE in caps and not ip.get("primary")),
        "purchases_enabled": bool(account.get("purchases_enabled")),
        "cost": cost.model_dump(mode="json") if cost else None,
        "acquisitions_last_24h": acquisitions_last_24h(account["id"]),
        "daily_cap": daily_cap(account["id"]),
        "ips": server.get("ips") or [],
    }


async def change(address: str, user_id: int, release_first: bool = False) -> dict:
    """Swap one swappable IP for a fresh one on the same server.

    Default order is new-first (acquire, then release the old one), so the
    server is never without its swappable address and a failed acquisition
    changes nothing. release_first is for servers at their IP cap (a plan
    allowing one extra IP can't hold two at once)."""
    found = find_owner(address)
    if not found:
        raise IpError(404, f"{address} is not on any synced server")
    account, server = found
    ip = _cached_ip(server, address)
    if ip is None or ip.get("primary"):
        raise IpError(409, f"{address} is {server.get('name')}'s primary IP - "
                           "only its extra IPs are changeable")
    if Capability.IP_CHANGE not in _caps(account):
        raise IpError(409, f"{account['adapter']} has no API to change an IP")
    _guard_purchase(account, Capability.IP_CHANGE)

    lock = _locks.setdefault((account["id"], server["provider_id"]), asyncio.Lock())
    if lock.locked():
        raise IpError(409, f"an IP operation is already running on {server.get('name')}")
    async with lock:
        return await _change_locked(account, server, address, user_id, release_first)


async def _change_locked(account: dict, server: dict, old: str, user_id: int,
                         release_first: bool) -> dict:
    pid = server["provider_id"]
    action_id = _start_action(account["id"], pid, Capability.IP_CHANGE, user_id)
    _publish(account["id"], Capability.IP_CHANGE.value, "in_progress")
    adapter = accounts.build_adapter(account)
    cost = None
    new: IpAddress | None = None
    old_released = False
    outcome = "failed"
    try:
        with contextlib.suppress(AdapterError):
            cost = await adapter.ip_cost(pid)
        try:
            if release_first:
                await adapter.release_ip(pid, old)
                old_released = True
            new = await adapter.add_ip(pid)
            if not old_released:
                await adapter.release_ip(pid, old)
                old_released = True
        except PaymentRequired as e:
            oid = _record_order(account, server, user_id, "awaiting_payment", cost,
                                replaces=old, provider_ref=e.order_ref, pay_url=e.pay_url,
                                detail=str(e))
            result = {"status": "awaiting_payment", "old_ip": old,
                      "old_released": old_released, "order_id": oid,
                      "provider_ref": e.order_ref, "pay_url": e.pay_url,
                      "action_id": action_id}
            _finish_action(action_id, "done", f"{old}: replacement awaits payment "
                                              f"(order {e.order_ref})", result)
            outcome = "done"
            return result
        except AdapterError as e:
            if new is not None:
                # acquired but the old one wouldn't release: the new IP is
                # live and usable, the old one still bills - say both
                oid = _record_order(account, server, user_id, "provisioned", cost,
                                    new_ip=new.address, replaces=old,
                                    detail=f"old IP not released: {e}")
                result = {"status": "done", "old_ip": old, "new_ip": new.address,
                          "old_released": False, "warning": f"old IP not released: {e}",
                          "order_id": oid, "action_id": action_id, "cost": _cost(cost)}
                _finish_action(action_id, "done", f"{old} -> {new.address} "
                                                  f"(old NOT released: {e})", result)
                audit.record(user_id, "ip.change",
                             f"{account['adapter']}/{account['name']}/{pid}",
                             before={"ip": old}, after={"ip": new.address, "old_still_attached": old})
                outcome = "done"
                return result
            if isinstance(e, ActionTimeout):
                # the order went in but no address showed up in time: it may
                # still be assigned (and bill) - never report "nothing changed"
                _record_order(account, server, user_id, "failed", cost, replaces=old,
                              detail=f"ordered, address not assigned in time: {e}")
            elif old_released:
                _record_order(account, server, user_id, "failed", cost, replaces=old,
                              detail=f"old IP released, acquisition failed: {e}")
            raise
        oid = _record_order(account, server, user_id, "provisioned", cost,
                            new_ip=new.address, replaces=old)
        result = {"status": "done", "old_ip": old, "new_ip": new.address,
                  "old_released": True, "server": server.get("name"),
                  "provider_id": pid, "account": account["name"],
                  "order_id": oid, "action_id": action_id, "cost": _cost(cost)}
        _finish_action(action_id, "done", f"{old} -> {new.address}", result)
        audit.record(user_id, "ip.change",
                     f"{account['adapter']}/{account['name']}/{pid}",
                     before={"ip": old}, after={"ip": new.address})
        outcome = "done"
        return result
    except AdapterError as e:
        if isinstance(e, ActionTimeout):
            state = ("an IP was ordered but not assigned in time - it may still "
                     "appear (and bill); the next sync shows it")
        elif old_released:
            state = "old IP released, no replacement acquired"
        else:
            state = "nothing changed"
        result = {"status": "failed", "old_ip": old, "old_released": old_released,
                  "error": str(e), "action_id": action_id}
        _finish_action(action_id, "failed", f"{old}: {e} ({state})", result)
        raise IpError(502, f"{e} ({state})", **result)
    finally:
        with contextlib.suppress(Exception):
            await adapter.close()
        await _resync(account["id"])
        _publish(account["id"], Capability.IP_CHANGE.value, outcome)


def _cost(cost: IpCost | None) -> dict | None:
    return cost.model_dump(mode="json") if cost else None


async def add(account_id: int, provider_id: str, user_id: int) -> dict:
    """Acquire one more swappable IP (setup for a server that has none yet)."""
    account = accounts.get_account(account_id)
    if not account:
        raise IpError(404, "no such account")
    server = _cached_server(account_id, provider_id)
    _guard_purchase(account, Capability.IP_ADD)
    lock = _locks.setdefault((account_id, provider_id), asyncio.Lock())
    if lock.locked():
        raise IpError(409, f"an IP operation is already running on {server.get('name')}")
    async with lock:
        action_id = _start_action(account_id, provider_id, Capability.IP_ADD, user_id)
        adapter = accounts.build_adapter(account)
        cost = None
        try:
            with contextlib.suppress(AdapterError):
                cost = await adapter.ip_cost(provider_id)
            new = await adapter.add_ip(provider_id)
        except PaymentRequired as e:
            oid = _record_order(account, server, user_id, "awaiting_payment", cost,
                                provider_ref=e.order_ref, pay_url=e.pay_url, detail=str(e))
            result = {"status": "awaiting_payment", "order_id": oid,
                      "provider_ref": e.order_ref, "pay_url": e.pay_url,
                      "action_id": action_id}
            _finish_action(action_id, "done", f"IP order {e.order_ref} awaits payment", result)
            return result
        except AdapterError as e:
            _finish_action(action_id, "failed", str(e), {"status": "failed", "error": str(e)})
            raise IpError(502, str(e))
        finally:
            with contextlib.suppress(Exception):
                await adapter.close()
            await _resync(account_id)
        oid = _record_order(account, server, user_id, "provisioned", cost, new_ip=new.address)
        result = {"status": "done", "new_ip": new.address, "order_id": oid,
                  "action_id": action_id, "cost": _cost(cost)}
        _finish_action(action_id, "done", f"added {new.address}", result)
        audit.record(user_id, "ip.add", f"{account['adapter']}/{account['name']}/{provider_id}",
                     after={"ip": new.address})
        _publish(account_id, Capability.IP_ADD.value, "done")
        return result


async def release(account_id: int, provider_id: str, address: str, user_id: int) -> dict:
    """Give back one swappable IP. Frees money, so no purchase guard - but
    the primary is refused exactly like a change."""
    account = accounts.get_account(account_id)
    if not account:
        raise IpError(404, "no such account")
    server = _cached_server(account_id, provider_id)
    if Capability.IP_RELEASE not in _caps(account):
        raise IpError(409, f"{account['adapter']} has no API to release an IP")
    ip = _cached_ip(server, address)
    if ip is None:
        raise IpError(404, f"{address} is not on {server.get('name')}")
    if ip.get("primary"):
        raise IpError(409, f"{address} is {server.get('name')}'s primary IP - never released")
    lock = _locks.setdefault((account_id, provider_id), asyncio.Lock())
    if lock.locked():
        raise IpError(409, f"an IP operation is already running on {server.get('name')}")
    async with lock:
        action_id = _start_action(account_id, provider_id, Capability.IP_RELEASE, user_id)
        adapter = accounts.build_adapter(account)
        try:
            await adapter.release_ip(provider_id, address)
        except AdapterError as e:
            _finish_action(action_id, "failed", str(e), {"status": "failed", "error": str(e)})
            raise IpError(502, str(e))
        finally:
            with contextlib.suppress(Exception):
                await adapter.close()
            await _resync(account_id)
        result = {"status": "done", "released": address, "action_id": action_id}
        _finish_action(action_id, "done", f"released {address}", result)
        audit.record(user_id, "ip.release", f"{account['adapter']}/{account['name']}/{provider_id}",
                     before={"ip": address})
        _publish(account_id, Capability.IP_RELEASE.value, "done")
        return result
