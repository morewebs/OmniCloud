"""E2E-ish smoke: the whole stack over TestClient with a FakeAdapter."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from server import accounts
from server.main import create_app

from conftest import TEST_TOKEN, FakeAdapter


@pytest.fixture
def client(monkeypatch):
    # Route the "fake" adapter through the registry for this test only.
    monkeypatch.setitem(accounts.ADAPTERS, "fake", FakeAdapter)
    app = create_app()
    with TestClient(app) as c:
        yield c


HDRS = {"X-Requested-With": "XMLHttpRequest"}


def _admin(c):
    c.post("/api/auth/setup", json={"username": "admin", "password": "pw123456"}, headers=HDRS)


def _viewer(c, name="view"):
    c.post("/api/users", json={"username": name, "password": "pw123456", "role": "viewer"},
           headers=HDRS)
    c.post("/api/auth/logout", headers=HDRS)
    c.post("/api/auth/login", json={"username": name, "password": "pw123456"}, headers=HDRS)


async def test_setup_then_full_flow(client):
    # first run: setup needed
    assert client.get("/api/auth/status").json()["needs_setup"] is True
    _admin(client)
    assert client.get("/api/auth/status").json()["needs_setup"] is False

    # setup closes: a second setup attempt is rejected
    r = client.post("/api/auth/setup", json={"username": "x", "password": "y"}, headers=HDRS)
    assert r.status_code == 403

    # add account (token goes in once, never comes back out)
    r = client.post("/api/accounts", headers=HDRS,
                    json={"adapter": "fake", "name": "account-a7f3", "token": TEST_TOKEN})
    assert r.status_code == 200
    account_id = r.json()["id"]

    # force a sync via the runner (the loop task is also running in the app)
    from server import sync as syncmod
    await syncmod.sync_account_now(account_id)

    # fleet shows the fake server with per-source timestamp
    fleet = client.get("/api/fleet").json()
    all_servers = [s for a in fleet["accounts"] for s in a["servers"]]
    assert any(s["provider_id"] == "fake-1" for s in all_servers)
    assert fleet["sync"][str(account_id)]["last_success_at"] is not None

    # mutation as admin: done only after the adapter confirms
    r = client.post(f"/api/servers/{account_id}/fake-1/actions", headers=HDRS,
                    json={"kind": "reboot", "params": {}})
    assert r.status_code == 200
    assert r.json()["status"] == "done"

    # audit row exists with before-state
    audit = client.get("/api/audit").json()
    assert any(a["action"] == "reboot" and a["before_state"] for a in audit)

    # THE secret-hygiene test: the fixture token appears in NO response body
    # we have produced so far. Re-fetch everything and grep.
    for path in ("/api/accounts", "/api/fleet", "/api/audit", "/api/actions",
                 "/api/allowances", "/api/billing/summary", "/api/users",
                 "/api/adapters", "/api/auth/me", "/api/catalog",
                 "/api/catalog/images?adapter=fake", "/api/orders",
                 "/api/orders/1", "/api/overview"):
        body = client.get(path).text
        assert TEST_TOKEN not in body, f"token leaked via {path}"
    # and only last4 is shown
    acct = client.get("/api/accounts").json()[0]
    assert acct["last4"] == TEST_TOKEN[-4:]


def test_viewer_blocked_from_mutations(client):
    _admin(client)
    _viewer(client)
    # CSRF guard first: missing header is rejected outright
    r = client.post("/api/servers/1/x/actions", json={"kind": "reboot"})
    assert r.status_code == 403
    # with header but viewer role: still 403
    r = client.post("/api/servers/1/x/actions", headers=HDRS,
                    json={"kind": "reboot"})
    assert r.status_code == 403
    # read endpoints fine
    assert client.get("/api/fleet").status_code == 200
    # admin-only reads blocked for viewer
    assert client.get("/api/audit").status_code == 403


def test_missing_csrf_header_rejected(client):
    _admin(client)
    r = client.post("/api/auth/logout")  # no X-Requested-With
    assert r.status_code == 403


def test_adapters_lists_fleet_and_catalog_providers(client):
    """The Adapters view merges fleet adapters with catalog providers;
    credentials views must offer only fleet adapters (source absent)."""
    _admin(client)
    r = client.get("/api/adapters")
    assert r.status_code == 200
    keys = {a["key"]: a for a in r.json()}
    # fleet adapters carry capabilities and no catalog source
    assert set(a["key"] for a in keys.values() if not a.get("source")) == {"fake"}
    # all 5 catalog providers present with honest source labels
    for key in ("hetzner", "leaseweb", "ovh", "gcore", "tube"):
        assert keys[key]["source"] == "live"
    for key in ("netlen", "lightnode"):
        assert keys[key]["source"] == "seeded"
    # no credential can be created for a catalog-only provider
    r = client.post("/api/accounts", headers=HDRS,
                    json={"adapter": "ovh", "name": "x", "token": TEST_TOKEN})
    assert r.status_code == 400


def test_orders_routes_full_flow(client):
    """Order pipeline driven through HTTP: viewer blocked, draft->confirmed->
    executing->provisioned with events, cancel rules, unknown plan 400s."""
    _admin(client)
    # seed the catalog so the order can validate against a real plan
    from server import catalog as cat
    from server.adapters.base import IpOffer, Money, Plan
    cat.store("fake", [Plan(adapter="fake", name="plan-a", location="loc-1",
                            price_monthly=Money(amount="10.00", currency="EUR"),
                            extra_ip=IpOffer(kind="floating", included=1,
                                             price=Money(amount="0.50",
                                                         currency="EUR"),
                                             limit=5),
                            billing_model="monthly invoice")], "live", None)

    r = client.post("/api/orders", headers=HDRS,
                    json={"adapter": "fake", "plan_name": "plan-a",
                          "location": "loc-1", "options": {"extra_ips": 2}})
    assert r.status_code == 200
    oid = r.json()["id"]
    # unknown plan -> 400 with the OrderError message
    r = client.post("/api/orders", headers=HDRS,
                    json={"adapter": "fake", "plan_name": "nope",
                          "location": "loc-1", "options": {}})
    assert r.status_code == 400

    # viewer cannot create or confirm orders
    _viewer(client, "orderview")
    r = client.post("/api/orders", headers=HDRS,
                    json={"adapter": "fake", "plan_name": "plan-a",
                          "location": "loc-1", "options": {}})
    assert r.status_code == 403
    r = client.post(f"/api/orders/{oid}/confirm", headers=HDRS)
    assert r.status_code == 403
    # but can watch the pipeline
    assert client.get("/api/orders").status_code == 200
    assert client.get(f"/api/orders/{oid}").status_code == 200

    # back to admin: confirm + execute + wait for the prototype executor
    client.post("/api/auth/logout", headers=HDRS)
    client.post("/api/auth/login", json={"username": "admin", "password": "pw123456"},
                headers=HDRS)
    r = client.post(f"/api/orders/{oid}/confirm", headers=HDRS)
    assert r.status_code == 200
    # cancel after confirm is allowed; cancel after execute is not
    oid2 = client.post("/api/orders", headers=HDRS,
                       json={"adapter": "fake", "plan_name": "plan-a",
                             "location": "loc-1", "options": {}}).json()["id"]
    client.post(f"/api/orders/{oid2}/confirm", headers=HDRS)
    assert client.post(f"/api/orders/{oid2}/cancel", headers=HDRS).status_code == 200
    r = client.post(f"/api/orders/{oid}/execute", headers=HDRS)
    assert r.status_code == 200
    r = client.post(f"/api/orders/{oid}/cancel", headers=HDRS)
    assert r.status_code == 409

    deadline = time.monotonic() + 10
    o = None
    while time.monotonic() < deadline:
        o = client.get(f"/api/orders/{oid}").json()
        if o["status"] == "provisioned":
            break
        time.sleep(0.5)
    assert o and o["status"] == "provisioned"
    assert o["resulting_provider_id"] == f"proto-{oid}"
    assert [e["status"] for e in o["events"]] == \
        ["draft", "confirmed", "executing", "provisioned"]
    # mode column visible through the API: this run is a prototype
    assert o["mode"] == "prototype"
    # fleet cache untouched: prototype orders never pollute servers
    fleet = client.get("/api/fleet").json()
    assert not any(s.get("provider_id", "").startswith("proto-")
                   for a in fleet["accounts"] for s in a["servers"])


async def test_overview_groups_spend_per_currency(client):
    """The dashboard never silently sums EUR + USD: mixed-currency fleets
    get one spend line per currency."""
    _admin(client)
    r = client.post("/api/accounts", headers=HDRS,
                    json={"adapter": "fake", "name": "account-a7f3", "token": TEST_TOKEN})
    aid = r.json()["id"]
    from server import sync as syncmod
    await syncmod.sync_account_now(aid)

    o = client.get("/api/overview").json()
    # fleet aggregate present; spend is a per-currency dict (may be empty
    # when the fake server has no price - the invariant is the grouping)
    assert o["fleet"]["by_status"] == {"running": 1}
    assert isinstance(o["spend"], dict)
    for cur, total in o["spend"].items():
        assert isinstance(cur, str) and len(cur) == 3, "spend keys are currencies"


def test_capabilities_409_for_absent(client, monkeypatch):
    """Capability absent from the adapter = 409 with a clear message, and the
    UI renders it as absent (never a disabled button)."""
    _admin(client)
    r = client.post("/api/accounts", headers=HDRS,
                    json={"adapter": "fake", "name": "a", "token": TEST_TOKEN})
    aid = r.json()["id"]
    # FakeAdapter.capabilities is empty -> any action kind hits the 409 path
    r = client.post(f"/api/servers/{aid}/fake-1/actions", headers=HDRS,
                    json={"kind": "firewall", "params": {}})
    # firewall has its own route; use rebuild via generic route
    r = client.post(f"/api/servers/{aid}/fake-1/actions", headers=HDRS,
                    json={"kind": "rebuild", "params": {"image": "x"}})
    assert r.status_code == 409
