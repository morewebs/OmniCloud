"""Sync service: per-account background loop, SSE hub, action runner.

One asyncio task per enabled account; a broken account never blocks another.
Intervals are read from settings each cycle (editable in Settings UI, no
restart). Actions run as tasks: insert row -> run adapter action (which
confirms via the provider's own view) -> audit (before/after) -> force sync.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import traceback

from . import accounts, audit, db
from .adapters.base import AdapterError, Capability, ProviderAdapter

log = logging.getLogger("omnicloud.sync")

# SSE hub: one asyncio.Queue per connected client.
_subscribers: set[asyncio.Queue] = set()

_tasks: dict[int, asyncio.Task] = {}
_sync_events: dict[int, asyncio.Event] = {}  # account_id -> "sync now requested"
_loop: asyncio.AbstractEventLoop | None = None  # the app's main loop, set at startup


def publish(event: str, data: dict | None = None) -> None:
    """Fan out an SSE event; dropped for slow consumers (they refetch on reconnect).

    Thread-safe: sync `def` routes run in FastAPI's threadpool, and touching
    asyncio queue waiter futures from off-loop threads corrupts them - so
    when called from a thread (no running loop), hop onto the app loop.
    """
    payload = json.dumps({"event": event, "data": data or {}})
    try:
        on_loop = asyncio.get_running_loop() is _loop
    except RuntimeError:
        on_loop = False
    if not on_loop:
        if _loop is not None and _loop.is_running():
            _loop.call_soon_threadsafe(_fanout, payload)
        return
    _fanout(payload)


def _fanout(payload: str) -> None:
    for q in list(_subscribers):
        with contextlib.suppress(asyncio.QueueFull):
            q.put_nowait(payload)


async def subscribe() -> asyncio.Queue:
    q = asyncio.Queue(maxsize=16)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


# -- sync loop ---------------------------------------------------------------

async def _sync_once(account: dict, adapter: ProviderAdapter) -> None:
    servers = await adapter.list_servers()
    ts = db.now()
    with db.connect() as conn:
        for s in servers:
            row = s.model_dump(mode="json")
            conn.execute(
                """INSERT INTO servers(account_id, provider_id, canonical, first_seen_at, last_seen_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(account_id, provider_id) DO UPDATE SET
                     canonical=excluded.canonical, last_seen_at=excluded.last_seen_at""",
                (account["id"], s.provider_id, json.dumps(row), ts, ts),
            )
            # traffic history: one row per server per day (upsert takes max).
            if s.allowance and s.allowance.used_bytes is not None:
                day = ts[:10]
                counting = (s.allowance.counting.value
                            if s.allowance.counting else "unknown")
                conn.execute(
                    """INSERT INTO traffic_history(account_id, provider_id, day, bytes_used, counting)
                       VALUES(?,?,?,?,?)
                       ON CONFLICT(account_id, provider_id, day) DO UPDATE SET
                         bytes_used=max(bytes_used, excluded.bytes_used)""",
                    (account["id"], s.provider_id, day, s.allowance.used_bytes, counting),
                )
        # servers no longer present keep their last cache row (never silently
        # dropped - fleet view joins on cache, and delete actions prune them).
        conn.execute(
            "UPDATE sync_state SET last_success_at=?, last_attempt_at=?, last_error=NULL WHERE account_id=?",
            (ts, ts, account["id"]),
        )
    publish("servers_updated", {"account_id": account["id"]})


async def sync_account_now(account_id: int) -> dict:
    """Explicit refresh (Refresh button / POST /accounts/{id}/sync). Failures
    are recorded + published so connected clients see the error, not silence."""
    account = accounts.get_account(account_id)
    if not account:
        raise ValueError("no such account")
    adapter = accounts.build_adapter(account)
    try:
        await _sync_once(account, adapter)
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        msg = f"{type(e).__name__}: {e}"[:500]
        with db.connect() as conn:
            conn.execute(
                "UPDATE sync_state SET last_attempt_at=?, last_error=? WHERE account_id=?",
                (db.now(), msg, account_id),
            )
        publish("sync_error", {"account_id": account_id, "error": msg})
        raise
    finally:
        with contextlib.suppress(Exception):
            await adapter.close()


async def _sync_loop(account_id: int) -> None:
    while True:
        account = accounts.get_account(account_id)
        if not account or not account["enabled"]:
            return
        wake = _sync_events.get(account_id)
        interval_min = accounts.interval_for(account_id, account["adapter"])
        try:
            with db.connect() as conn:
                conn.execute(
                    "UPDATE sync_state SET last_attempt_at=? WHERE account_id=?",
                    (db.now(), account_id),
                )
            adapter = accounts.build_adapter(account)
            try:
                await _sync_once(account, adapter)
            finally:
                with contextlib.suppress(Exception):
                    await adapter.close()
        except Exception as e:  # noqa: BLE001 - one account's error must not kill the loop
            msg = f"{type(e).__name__}: {e}"[:500]
            with db.connect() as conn:
                conn.execute(
                    "UPDATE sync_state SET last_error=?, last_attempt_at=? WHERE account_id=?",
                    (msg, db.now(), account_id),
                )
            publish("sync_error", {"account_id": account_id, "error": msg})
            log.warning("sync failed for account %s: %s", account_id, msg)
        # sleep the interval, but wake early if a sync-now was requested
        try:
            if wake is None:
                await asyncio.sleep(interval_min * 60)
            else:
                try:
                    await asyncio.wait_for(wake.wait(), timeout=interval_min * 60)
                    wake.clear()
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            return


def start_all() -> None:
    """Spawn a loop task per enabled account. Called from app lifespan."""
    global _loop
    _loop = asyncio.get_running_loop()
    for acct in accounts.list_accounts():
        if acct["enabled"]:
            _spawn(acct["id"])


def _spawn(account_id: int) -> None:
    old = _tasks.get(account_id)
    if old and not old.done():
        old.cancel()
    ev = asyncio.Event()
    _sync_events[account_id] = ev
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop (tests with start_all disabled) - nothing to spawn on
    # threadpool routes (def endpoints) have no running loop: use the app loop.
    if _loop is not None and _loop.is_running():
        _loop.call_soon_threadsafe(
            lambda: _tasks.__setitem__(
                account_id, asyncio.create_task(_sync_loop(account_id), name=f"sync-{account_id}"))
        )
    else:
        _tasks[account_id] = asyncio.create_task(
            _sync_loop(account_id), name=f"sync-{account_id}")


def restart_account(account_id: int) -> None:
    """Start (or restart) the loop for an account after create/enable/settings change."""
    _spawn(account_id)


def stop_account(account_id: int) -> None:
    t = _tasks.pop(account_id, None)
    _sync_events.pop(account_id, None)
    if t:
        t.cancel()


def stop_all() -> None:
    for aid in list(_tasks):
        stop_account(aid)


def request_sync(account_id: int) -> None:
    ev = _sync_events.get(account_id)
    if ev:
        ev.set()


# -- action runner ------------------------------------------------------------

async def run_action(account_id: int, provider_id: str, kind: str,
                     user_id: int, params: dict) -> int:
    """Insert in_progress row, run, record outcome + audit + force sync."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT canonical FROM servers WHERE account_id=? AND provider_id=?",
            (account_id, provider_id),
        ).fetchone()
        before = json.loads(row["canonical"]) if row else None
        cur = conn.execute(
            """INSERT INTO actions(account_id, provider_id, kind, requested_by, status, created_at)
               VALUES(?,?,?,?, 'in_progress', ?)""",
            (account_id, provider_id, kind, user_id, db.now()),
        )
        action_id = cur.lastrowid

    publish("action", {"account_id": account_id, "kind": kind, "status": "in_progress"})
    try:
        account = accounts.get_account(account_id)
        if not account:
            raise ValueError("no such account")
        adapter = accounts.build_adapter(account)
        try:
            cap = Capability(kind)
            result = await adapter.perform_action(cap, provider_id, params)
            if cap is Capability.FIREWALL:
                pass  # handled via run_firewall
        finally:
            with contextlib.suppress(Exception):
                await adapter.close()
        after = None
        if cap is not Capability.DELETE:
            try:
                a2 = accounts.build_adapter(account)
                fresh = await a2.get_server(provider_id)
                after = fresh.model_dump(mode="json")
                with contextlib.suppress(Exception):
                    await a2.close()
            except Exception:  # noqa: BLE001 - after-state is best-effort
                pass
        with db.connect() as conn:
            conn.execute(
                "UPDATE actions SET status='done', completed_at=?, detail=? WHERE id=?",
                (db.now(), result.detail, action_id),
            )
        audit.record(user_id, kind, f"{account['adapter']}/{account['name']}/{provider_id}",
                     before=before, after=after)
        if cap is Capability.DELETE:
            with db.connect() as conn:
                conn.execute(
                    "DELETE FROM servers WHERE account_id=? AND provider_id=?",
                    (account_id, provider_id),
                )
        else:
            with contextlib.suppress(Exception):
                await sync_account_now(account_id)
        publish("action", {"account_id": account_id, "kind": kind, "status": "done"})
        return action_id
    except Exception as e:  # noqa: BLE001
        msg = f"{type(e).__name__}: {e}"[:500]
        with db.connect() as conn:
            conn.execute(
                "UPDATE actions SET status='failed', completed_at=?, detail=? WHERE id=?",
                (db.now(), msg, action_id),
            )
        publish("action", {"account_id": account_id, "kind": kind, "status": "failed"})
        # do NOT re-raise: the failure is recorded; the route reads the row and
        # returns a 502 with this message. Re-raising made that path dead code.
        return action_id
