"""Production-hardness tests: thread-safety, no-network guarantee, races,
auth hardening. Each maps to a defect found in the production-readiness
audit."""
import asyncio
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient


async def test_publish_from_thread_is_threadsafe():
    """publish() called from a threadpool thread (sync def routes) must hop
    onto the app loop instead of mutating queue waiters off-loop."""
    from server import sync

    # this test runs on the loop pytest-asyncio provides; register it as the app loop
    sync._loop = asyncio.get_running_loop()
    q = await sync.subscribe()
    try:
        # call publish() from a plain thread with NO running loop
        t = threading.Thread(target=lambda: sync.publish("order", {"id": 1}))
        t.start(); t.join()
        # give the call_soon_threadsafe hop a beat to run
        await asyncio.sleep(0.05)
        payload = q.get_nowait()
        assert '"order"' in payload
    finally:
        sync.unsubscribe(q)
        sync._loop = None


def test_testclient_never_touches_the_network(monkeypatch):
    """Guard: with outbound HTTP counted AND poisoned, the whole app (startup
    included) must not make a single real request. Counting matters: the
    catalog loop swallows Exception subclasses, so a raise-only poison can
    pass silently - the counter cannot lie."""
    from server.main import create_app

    calls = []

    real_client_send = httpx.Client.send
    real_async_send = httpx.AsyncClient.send

    def poison(self, request, **kw):
        # testserver = the TestClient's own ASGI transport, not real network
        if "testserver" in str(request.url):
            return (real_async_send if isinstance(self, httpx.AsyncClient)
                    else real_client_send)(self, request, **kw)
        calls.append(str(request.url))
        raise AssertionError(f"outbound HTTP during test: {request.url}")

    monkeypatch.setattr(httpx.AsyncClient, "send", poison)
    monkeypatch.setattr(httpx.Client, "send", poison)

    app = create_app()
    with TestClient(app) as c:
        # startup ran catalog.start() and sync.start_all()
        assert c.get("/api/auth/status").status_code == 200
        c.post("/api/auth/setup", json={"username": "admin", "password": "pw123456"},
               headers={"X-Requested-With": "XMLHttpRequest"})
        c.post("/api/auth/login", json={"username": "admin", "password": "pw123456"},
               headers={"X-Requested-With": "XMLHttpRequest"})
        assert c.get("/api/fleet", headers={"X-Requested-With": "XMLHttpRequest"}).status_code == 200
        # give the catalog loop's startup pass a beat to (not) fire
        time.sleep(0.5)
    assert calls == [], f"test made outbound HTTP: {calls[:3]}"


def test_double_execute_race_only_one_wins(uid):
    """Two concurrent executes: exactly one wins; the loser gets a clear
    OrderError. The atomic UPDATE is the guard."""
    from server import catalog, orders
    from server.adapters.base import IpOffer, Money, Plan
    catalog.store("fake", [Plan(adapter="fake", name="plan-a", location="l",
                                price_monthly=Money(amount="1.00", currency="EUR"),
                                billing_model="x")], "live", None)
    oid = orders.create_order(uid, "fake", "plan-a", "l", {})
    orders.confirm(oid, uid)

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        futs = [ex.submit(orders.execute, oid, uid) for _ in range(2)]
        outcomes = []
        for f in futs:
            try:
                f.result()
                outcomes.append("ok")
            except orders.OrderError:
                outcomes.append("rejected")  # the loser sees a clear message
    # both threads entered execute() believing the order was confirmed; the
    # pre-check or the atomic UPDATE must reject exactly one of them
    # ...but only ONE executor transitioned to executing
    from server import db
    with db.connect() as conn:
        evs = conn.execute(
            "SELECT status FROM order_events WHERE order_id=? AND status='executing'",
            (oid,)).fetchall()
    assert len(evs) == 1, f"{len(evs)} executing events - the race leaked through"


HDRS = {"X-Requested-With": "XMLHttpRequest"}


def test_login_lockout_after_five_failures(client):
    """5 bad logins -> 429 lockout, not unlimited brute force."""
    client.post("/api/auth/setup", json={"username": "admin", "password": "pw123456"},
                headers=HDRS)
    client.post("/api/auth/logout", headers=HDRS)
    for _ in range(5):
        r = client.post("/api/auth/login",
                        json={"username": "admin", "password": "wrong"},
                        headers=HDRS)
        assert r.status_code == 401
    r = client.post("/api/auth/login",
                    json={"username": "admin", "password": "pw123456"},
                    headers=HDRS)
    assert r.status_code == 429
    assert "try again" in r.json()["detail"]


def test_login_timing_does_not_reveal_usernames(client):
    """Absent-user logins burn the same scrypt cost as real ones. Distinct
    usernames dodge the rate limiter (a locked-out request returns instantly
    and would fake the timing)."""
    client.post("/api/auth/setup", json={"username": "admin", "password": "pw123456"},
                headers=HDRS)
    client.post("/api/auth/logout", headers=HDRS)
    def attempt(u):
        t0 = time.perf_counter()
        client.post("/api/auth/login", json={"username": u, "password": "wrongpw"},
                    headers=HDRS)
        return time.perf_counter() - t0
    # real user, absent user - each measured 3x with fresh usernames
    real = min(attempt("admin") for _ in range(3))
    fake = min(attempt(f"no-such-user-{i}") for i in range(3))
    assert abs(real - fake) < 0.15, f"timing gap {real - fake:.3f}s reveals usernames"


def test_password_change_kills_sessions(client):
    client.post("/api/auth/setup", json={"username": "admin", "password": "pw123456"},
                headers=HDRS)
    r = client.patch("/api/auth/me",
                     json={"current_password": "pw123456", "new_password": "newpw999"},
                     headers=HDRS)
    assert r.status_code == 200
    # the old session cookie no longer authenticates
    assert client.get("/api/fleet", headers=HDRS).status_code == 401


def test_concurrent_setup_creates_exactly_one_admin():
    """TOCTOU-safe first-run: two parallel setups, one wins."""
    import concurrent.futures
    from server import auth
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        futs = [ex.submit(auth.create_first_admin, f"admin{i}", "pw123456")
                for i in range(2)]
        results = [f.result() for f in futs]
    assert sum(1 for r in results if r is not None) == 1
    assert auth.user_count() == 1


def test_expired_sessions_swept_on_login(client):
    """login() sweeps expired rows; sessions table never grows unbounded."""
    from server import db
    client.post("/api/auth/setup", json={"username": "admin", "password": "pw123456"},
                headers=HDRS)
    with db.connect() as conn:
        conn.execute("INSERT INTO sessions(token_hash, user_id, created_at, expires_at) "
                     "VALUES('dead1', 1, '2000-01-01', '2000-01-02'), "
                     "('dead2', 1, '2000-01-01', '2000-01-02')")
    client.post("/api/auth/login",
                json={"username": "admin", "password": "pw123456"}, headers=HDRS)
    with db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM sessions WHERE token_hash IN "
                         "('dead1','dead2')").fetchone()[0]
    assert n == 0


@pytest.fixture
def client(monkeypatch):
    from server import accounts as accounts_mod, api as api_mod
    from server.main import create_app
    from conftest import FakeAdapter
    monkeypatch.setitem(accounts_mod.ADAPTERS, "fake", FakeAdapter)
    api_mod._login_fails.clear()  # locks are per-process state; never leak between tests
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_path_traversal_blocked(client):
    """Static route must never serve anything outside server/static."""
    for evil in ("/..%2f..%2f..%2fserver%2fsecrets.py",
                 "/%2e%2e/%2e%2e/server/secrets.py",
                 "/../../server/secrets.py"):
        r = client.get(evil)
        # falls back to the SPA index (client route) - never the file body
        assert b"scrypt" not in r.content
        assert b"MASTER_KEY" not in r.content


def test_failed_action_returns_502_with_provider_message(client):
    """A provider-side failure returns 502 + the recorded message, not 500."""
    client.post("/api/auth/setup", json={"username": "admin", "password": "pw123456"},
                headers=HDRS)
    r = client.post("/api/accounts", headers=HDRS,
                    json={"adapter": "fake", "name": "a", "token": "fixture-token"})
    aid = r.json()["id"]
    # FakeAdapter.perform_action raises for an unknown server id
    r = client.post(f"/api/servers/{aid}/nonexistent/actions", headers=HDRS,
                    json={"kind": "reboot", "params": {}})
    assert r.status_code == 502
    assert r.json()["detail"]


def test_health_checks_the_database(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


async def test_partial_catalog_is_not_a_success():
    """One region 500ing must NOT silently shrink the catalog: the adapter
    raises, the store is skipped, the previous plans stay with a recorded
    error."""
    import httpx
    from server import catalog
    from server.adapters.base import AdapterError, Money, Plan
    from server.adapters.gcore import GcoreCatalogAdapter

    good = {"count": 1, "results": [
        {"name": "g2s-shared-1-1-25", "vcpus": 1, "ram": 1, "disk": 25}]}
    calls = {"regions": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/regions"):
            calls["regions"] += 1
            return httpx.Response(200, json={"count": 2, "results": [
                {"id": 7, "name": "Frankfurt", "technical_name": "FRN-2", "country": "DE"},
                {"id": 9, "name": "Down", "technical_name": "DOWN-1", "country": "XX"}]})
        if "basic_vms/flavors" in url:
            if "region_id=9" in url:
                return httpx.Response(500)  # one region is down
            return httpx.Response(200, json=good)
        if "vcc-items" in url:
            return httpx.Response(200, json=[
                {"name": "g2s-shared-1-1-25", "vmType": "standard",
                 "priceMinute": "0.00107"}])
        return httpx.Response(404)

    a = GcoreCatalogAdapter(http=httpx.MockTransport(handler))
    # seed a previous good catalog
    catalog.store("gcore", [Plan(adapter="gcore", name="old-plan", location="FRN-2",
                                price_monthly=Money(amount="1.00", currency="USD"))],
                  "live", None)
    with pytest.raises(AdapterError, match="partial catalog"):
        plans = await a.list_plans()
        catalog.store("gcore", plans, "live", None)
    await a.close()
    # the previous catalog survived
    data = catalog.read()
    rows = [r for r in data["plans"] if r["adapter"] == "gcore"]
    assert [r["plan_name"] for r in rows] == ["old-plan"]


async def test_billing_summary_groups_per_currency(client):
    """A USD-billed server never sums into a EUR line."""
    client.post("/api/auth/setup", json={"username": "admin", "password": "pw123456"},
                headers=HDRS)
    r = client.post("/api/accounts", headers=HDRS,
                    json={"adapter": "fake", "name": "a", "token": "fixture-token"})
    assert r.status_code == 200, r.text
    from server import db
    # one EUR server, one USD server
    import json as _json
    with db.connect() as conn:
        for sid, cur, amt in (("fake-1", "EUR", 10.0), ("fake-2", "USD", 5.0)):
            conn.execute(
                "INSERT INTO servers(account_id, provider_id, canonical, last_seen_at, first_seen_at) "
                "VALUES(1, ?, ?, '2026-01-01T00:00:00', '2026-01-01T00:00:00')",
                (sid, _json.dumps({
                    "provider_id": sid, "name": f"srv-{sid}", "adapter": "fake",
                    "account_id": 1, "status": "running",
                    "monthly_price": {"amount": str(amt), "currency": cur}})))
    summary = client.get("/api/billing/summary", headers=HDRS).json()
    curs = sorted(s["currency"] for s in summary)
    assert curs == ["EUR", "USD"], f"mixed currencies were collapsed: {summary}"
    for s in summary:
        assert isinstance(s["monthly_base"], float)


def test_startup_recovery_fails_stranded_inflight(client, uid):
    """A crash (or exit-78 update) mid-action/order must not leave rows stuck
    in_progress/executing forever - the next startup fails them visibly."""
    from server import db
    ts = db.now()
    with db.connect() as conn:
        conn.execute("INSERT INTO accounts(id, adapter, name, enabled, created_at) "
                     "VALUES(1, 'fake', 'acct-a', 1, ?)", (ts,))
        conn.execute(
            "INSERT INTO actions(account_id, provider_id, kind, requested_by, "
            "status, created_at) VALUES(1, 'fake-1', 'reboot', ?, 'in_progress', ?)",
            (uid, ts))
        conn.execute(
            "INSERT INTO orders(mode, status, adapter, plan_name, location, options, "
            "plan_snapshot, estimated_monthly, requested_by, created_at, updated_at) "
            "VALUES('prototype', 'executing', 'fake', 'p', 'l', '{}', '{}', '{}', ?, ?, ?)",
            (uid, ts, ts))
    # run one startup (recovery lives in the app's startup hook)
    from server.main import create_app
    with TestClient(create_app()) as c2:
        c2.get("/api/auth/status")
    with db.connect() as conn:
        a = conn.execute("SELECT status, detail FROM actions "
                         "WHERE status='failed'").fetchone()
        assert a and a["detail"] == "interrupted by restart"
        o = conn.execute("SELECT status FROM orders WHERE status='failed'").fetchone()
        assert o, "executing order not failed by recovery"
        ev = conn.execute("SELECT detail FROM order_events WHERE status='failed'").fetchone()
        assert ev and ev["detail"] == "interrupted by restart"


def test_empty_token_and_name_rejected(client):
    """No zero-length credentials/account names at the trust boundary."""
    HDRS = {"X-Requested-With": "XMLHttpRequest"}
    client.post("/api/auth/setup", json={"username": "admin", "password": "pw123456"},
                headers=HDRS)
    r = client.post("/api/accounts", headers=HDRS,
                    json={"adapter": "fake", "name": "", "token": "x"})
    assert r.status_code == 422
    r = client.post("/api/accounts", headers=HDRS,
                    json={"adapter": "fake", "name": "a", "token": ""})
    assert r.status_code == 422


def test_short_passwords_rejected_everywhere(client):
    """8-char floor on setup, user-create, change-password, user-patch."""
    HDRS = {"X-Requested-With": "XMLHttpRequest"}
    # setup path (fresh app -> no admin yet)
    r = client.post("/api/auth/setup", json={"username": "a", "password": "short"},
                    headers=HDRS)
    assert r.status_code == 400, r.text
    # now create the admin and test the other three paths
    client.post("/api/auth/setup", json={"username": "admin", "password": "pw123456"},
                headers=HDRS)
    r = client.post("/api/users", json={"username": "x", "password": "short"},
                    headers=HDRS)
    assert r.status_code == 400, r.text
    r = client.patch("/api/auth/me", json={"current_password": "pw123456",
                                           "new_password": "short"}, headers=HDRS)
    assert r.status_code == 400, r.text
    r = client.patch("/api/users/1", json={"password": "short"}, headers=HDRS)
    assert r.status_code == 400, r.text


def test_firewall_attach_on_incapable_adapter_is_409(client, uid):
    """FakeAdapter has no FIREWALL capability: attach/detach must 409, not 500."""
    HDRS = {"X-Requested-With": "XMLHttpRequest"}
    # uid fixture already created the admin 'ordop' - sign in as them
    client.post("/api/auth/login", json={"username": "ordop", "password": "pw123456"},
                headers=HDRS)
    client.post("/api/accounts", headers=HDRS,
                json={"adapter": "fake", "name": "a", "token": "fixture-token"})
    r = client.post("/api/accounts/1/firewalls/1/attach/fake-1", headers=HDRS)
    assert r.status_code == 409, r.text
    assert "does not support" in r.json()["detail"]
    r = client.post("/api/accounts/1/firewalls/1/detach/fake-1", headers=HDRS)
    assert r.status_code == 409, r.text
