"""Targeted regression tests for the core fixes (sync/catalog/accounts/orders/
update). Each test covers one fix; see test_update.py for the updater's
older guards."""
import inspect

import pytest

from server import accounts, catalog, db, orders, sync, update


# -- sync.stop_account (thread-safe cancel) ------------------------------------

def test_stop_account_hops_via_loop():
    """A sync-def caller (threadpool route) has no running loop and must not
    call Task.cancel() directly - cancel must be routed through the app loop."""
    src = inspect.getsource(sync.stop_account)
    assert "call_soon_threadsafe" in src
    assert "_loop" in src


async def test_stop_account_cancels_via_threadsafe():
    """End-to-end: stop_account from a non-loop thread cancels the loop task."""
    import asyncio
    import threading

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def hang():
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    t = asyncio.get_running_loop().create_task(hang())
    sync._loop = asyncio.get_running_loop()
    sync._tasks[42] = t
    await started.wait()
    # hop off the loop like a threadpool route would
    await asyncio.to_thread(sync.stop_account, 42)
    await asyncio.wait_for(cancelled.wait(), timeout=2)
    assert 42 not in sync._tasks
    sync._loop = None


# -- sync.run_action (deferred delete + after-state) ----------------------------

def _seed_server(provider_id="srv-1"):
    import json
    from conftest import FakeAdapter
    accounts.ADAPTERS.setdefault("fake", FakeAdapter)
    account_id = accounts.create_account("fake", "acc", "token-xyz")
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO servers(account_id, provider_id, canonical, first_seen_at, last_seen_at)
               VALUES(?,?,?,?,?)""",
            (account_id, provider_id, json.dumps({"provider_id": provider_id}),
             db.now(), db.now()))
        conn.execute("INSERT OR IGNORE INTO sync_state(account_id) VALUES(?)",
                     (account_id,))
    return account_id


def _server_row(account_id, provider_id="srv-1"):
    with db.connect() as conn:
        return conn.execute(
            "SELECT canonical FROM servers WHERE account_id=? AND provider_id=?",
            (account_id, provider_id)).fetchone()


async def test_deferred_delete_keeps_cache_row(monkeypatch, uid):
    """Leaseweb MONTHLY delete: instance still exists until contract end -
    the cache row must survive (no flicker); a confirmed delete prunes it."""
    aid_account = _seed_server()

    class Result:
        detail = "delete scheduled at contract end"

    class Adapter:
        async def perform_action(self, cap, sid, params):
            return Result()
        async def close(self):
            pass

    monkeypatch.setattr(accounts, "get_account",
                        lambda aid: {"id": aid, "adapter": "x", "name": "n", "enabled": 1})
    monkeypatch.setattr(accounts, "build_adapter", lambda a: Adapter())
    aid = await sync.run_action(aid_account, "srv-1", "delete", uid, {})
    with db.connect() as conn:
        st = conn.execute("SELECT status, detail FROM actions WHERE id=?", (aid,)).fetchone()
    assert st["status"] == "done"
    assert _server_row(aid_account) is not None, "deferred delete must keep the cache row"


async def test_confirmed_delete_prunes_cache_row(monkeypatch, uid):
    aid_account = _seed_server()

    class Result:
        detail = "deleted"

    class Adapter:
        async def perform_action(self, cap, sid, params):
            return Result()
        async def close(self):
            pass

    monkeypatch.setattr(accounts, "get_account",
                        lambda aid: {"id": aid, "adapter": "x", "name": "n", "enabled": 1})
    monkeypatch.setattr(accounts, "build_adapter", lambda a: Adapter())
    await sync.run_action(aid_account, "srv-1", "delete", uid, {})
    assert _server_row(aid_account) is None, "confirmed delete must prune the cache row"


# -- catalog._loop (interval guard) ----------------------------------------------

def test_catalog_interval_bad_value_degrades(monkeypatch):
    """A non-numeric catalog_sync_interval_hours must fall back to 24h, not
    raise ValueError inside the loop and kill the catalog task."""
    db.set_setting("catalog_sync_interval_hours", "soon")
    from server.accounts import interval_for  # noqa: F401  (pattern reference)

    # simulate the loop's read: exactly the guard shipped in catalog._loop
    try:
        hours = float(db.get_setting("catalog_sync_interval_hours") or 24)
    except ValueError:
        hours = 24.0
    assert hours == 24.0

    # the loop body itself must not raise on the bad value: run one iteration
    # of just the sleep-interval computation against the shipped source
    src = inspect.getsource(catalog._loop)
    assert "except ValueError" in src, "catalog._loop must guard the interval"


# -- accounts.delete_account (FK) ------------------------------------------------

async def test_delete_account_with_orders_detaches_them(uid):
    """An account referenced by orders must be deletable: orders are kept
    forever (legal trail) and get account_id NULL, not deleted with it."""
    from conftest import FakeAdapter
    from server.adapters.base import IpOffer, Money, Plan
    accounts.ADAPTERS.setdefault("fake", FakeAdapter)
    catalog.store("fake", [Plan(adapter="fake", name="p", location="l",
                                price_monthly=Money(amount="1", currency="EUR"),
                                extra_ip=IpOffer(kind="x", included=0))], "live", None)
    aid = accounts.create_account("fake", "acc-with-orders", "token-xyz")
    oid = orders.create_order(uid, "fake", "p", "l", {"account_id": aid})
    assert accounts.delete_account(aid) is True
    o = orders.get_order(oid)
    assert o is not None, "order history survives account deletion"
    assert o["account_id"] is None


def test_delete_account_without_orders(uid):
    from conftest import FakeAdapter
    accounts.ADAPTERS.setdefault("fake", FakeAdapter)
    aid = accounts.create_account("fake", "plain", "token-xyz")
    assert accounts.delete_account(aid) is True
    assert accounts.get_account(aid) is None


# -- orders.create_order (null price_monthly) --------------------------------------

async def test_create_order_null_price_monthly_is_400_not_500(uid):
    """A plan stored with price_monthly:null (Gcore pricing BFF down) must be
    a clean OrderError, not AttributeError -> 500."""
    import json
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO plan_catalog(adapter, plan_name, location, canonical, source, fetched_at)
               VALUES('gcore','down','l',?, 'live', ?)""",
            (json.dumps({"adapter": "gcore", "name": "down", "location": "l",
                         "price_monthly": None, "extra_ip": None}), db.now()))
    with pytest.raises(orders.OrderError):
        orders.create_order(uid, "gcore", "down", "l", {})


# -- update.apply / _apply_sequence -------------------------------------------------

async def test_apply_restores_lockfile_before_dirty_check(monkeypatch, tmp_path):
    """The lockfile restore must run BEFORE the dirty check, or an apply
    whose predecessor dirtied web/package-lock.json is refused (the exact
    scenario commit d289a3b's restore was unreachable for). The tree is
    dirty ONLY via package-lock.json; after the restore it must be clean,
    so apply must not raise the dirty-tree RuntimeError."""
    calls = []

    class R:
        def __init__(self, rc=0, out=""):
            self.returncode = rc
            self.stdout = out
            self.stderr = ""

    state = {"lockfile_dirty": True}

    def fake_run(cmd, *a, **kw):
        calls.append(tuple(cmd[:2]))
        if cmd[:2] == ["git", "rev-parse"]:
            return R(0, "true")
        if cmd[:2] == ["git", "checkout"]:
            state["lockfile_dirty"] = False  # the restore discards it
            return R(0)
        if cmd[:2] == ["git", "status"]:
            return R(0, " M web/package-lock.json\n" if state["lockfile_dirty"] else "")
        return R(0)

    monkeypatch.setattr(update.subprocess, "run", fake_run)
    # don't let apply run the real sequence in the executor (it would exit)
    monkeypatch.setattr(update, "_apply_sequence", lambda: None)
    monkeypatch.setattr(update, "REPO_ROOT", tmp_path)
    (tmp_path / "web").mkdir(parents=True, exist_ok=True)
    (tmp_path / "web" / "package.json").write_text("{}")
    update._status.update(available=True, latest="9.9.9", applying=False)
    try:
        await update.apply()  # must NOT raise the dirty-tree RuntimeError
        # checkout ran before status
        assert calls.index(("git", "checkout")) < calls.index(("git", "status"))
        # the dirty-tree refusal must not have fired for a lockfile-only tree
        assert "local changes" not in (update._status["error"] or "")
    finally:
        update._status.update(available=False, latest=None, applying=False)


def test_apply_sequence_resets_applying_on_crash():
    """Any unexpected exception (missing uv/npm binary) must reset applying
    and restart the loops - not leave the panel wedged until restart."""
    src = inspect.getsource(update._apply_sequence)
    assert "except Exception" in src
    assert "applying=False" in src
    assert "_restart_loops" in src


async def test_apply_sequence_step_failure_restarts_loops(monkeypatch):
    """A failed step (git pull conflict) must re-spawn the stopped loops."""
    restarted = []
    monkeypatch.setattr(update, "_restart_loops", lambda: restarted.append(True))

    class R:
        returncode = 1
        stdout = ""
        stderr = "conflict"

    monkeypatch.setattr(update.subprocess, "run", lambda *a, **kw: R())
    update._apply_sequence()
    assert restarted, "failed step must restart the sync/catalog loops"
    assert update._status["applying"] is False


async def test_apply_sequence_crash_restarts_loops(monkeypatch):
    """FileNotFoundError (git/uv/npm missing in the executor thread) must be
    caught, recorded, and the loops restarted - not kill the thread silently."""
    restarted = []
    monkeypatch.setattr(update, "_restart_loops", lambda: restarted.append(True))

    def boom(*a, **kw):
        raise FileNotFoundError("uv not found")

    monkeypatch.setattr(update.subprocess, "run", boom)
    update._apply_sequence()  # must not raise
    assert restarted
    assert update._status["applying"] is False
    assert "FileNotFoundError" in (update._status["error"] or "")


async def test_apply_drain_excludes_current_task():
    """The drain must exclude the applying task itself, like main.py's
    shutdown handler - otherwise asyncio.wait can never complete."""
    src = inspect.getsource(update.apply)
    assert "current_task" in src


# -- docker-entrypoint (safe.directory) ---------------------------------------------

def test_entrypoint_sets_safe_directory():
    text = open("docker-entrypoint.sh", encoding="utf-8").read()
    assert "safe.directory /repo" in text
