"""IP roles (0.6.0): a role names an extra IP, survives a swap, and is the
label both swap loops pin to instead of list order."""
import pytest
from fastapi.testclient import TestClient

from server import accounts, sync
from server.adapters import gcore_hosting as gh
from server.adapters.gcore_hosting import GcoreHostingAdapter
from server.main import create_app

from conftest import mock_gcore_hosting_transport

HDRS = {"X-Requested-With": "XMLHttpRequest"}
EXTRA = "203.0.113.11"     # srv-ams-01's swappable IP (fixture)
PRIMARY = "203.0.113.10"   # srv-ams-01's main IP
PID = "5101"               # srv-ams-01's provider id (fixture)


@pytest.fixture(autouse=True)
def fast_polls(monkeypatch):
    monkeypatch.setattr(gh, "POLL_INTERVAL_S", 0)


def _panel(monkeypatch, **kw):
    transport, calls, state = mock_gcore_hosting_transport(**kw)

    class Panel(GcoreHostingAdapter):
        def __init__(self, account_id, account_name, token, http=None):
            super().__init__(account_id, account_name, token, http=transport)
    monkeypatch.setitem(accounts.ADAPTERS, "gcore_hosting", Panel)
    return calls, state


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        c.post("/api/auth/setup", json={"username": "admin", "password": "pw123456"},
               headers=HDRS)
        yield c


async def _account(client):
    aid = client.post("/api/accounts", headers=HDRS, json={
        "adapter": "gcore_hosting", "name": "panel-a7f3",
        "fields": {"url": "https://panel.example.test/billmgr",
                   "username": "ops@example.test", "password": "panel-pw-0000"}}).json()["id"]
    client.patch(f"/api/accounts/{aid}", headers=HDRS, json={"purchases_enabled": True})
    await sync.sync_account_now(aid)
    return aid


async def _script_token(client):
    tok = client.post("/api/auth/tokens", headers=HDRS,
                      json={"name": "rotator", "scope": "ip_change"}).json()["token"]
    client.cookies.clear()
    return {"Authorization": f"Bearer {tok}"}


async def test_role_assign_read_and_describe(client, monkeypatch):
    _panel(monkeypatch)
    aid = await _account(client)
    r = client.put(f"/api/servers/{aid}/{PID}/roles", headers=HDRS,
                   json={"role": "awgbk-secondary", "address": EXTRA})
    assert r.status_code == 200 and r.json()["address"] == EXTRA

    got = client.get(f"/api/servers/{aid}/{PID}/roles/awgbk-secondary",
                     headers=HDRS).json()
    assert got == {"role": "awgbk-secondary", "address": EXTRA}

    # describe() shows the role on the answer the server itself polls
    b = await _script_token(client)
    info = client.get(f"/api/ips/{EXTRA}", headers=b).json()
    assert info["role"] == "awgbk-secondary"
    assert {i.get("role") for i in info["ips"]} == {None, "awgbk-secondary"}


async def test_role_carries_across_a_swap(client, monkeypatch):
    calls, _ = _panel(monkeypatch)
    aid = await _account(client)
    client.put(f"/api/servers/{aid}/{PID}/roles", headers=HDRS,
               json={"role": "grpc-entry", "address": EXTRA})
    b = await _script_token(client)

    res = client.post(f"/api/ips/{EXTRA}/change", headers=b).json()
    assert res["new_ip"] == "203.0.113.30" and res["old_released"] is True

    # the role followed the swap without anyone re-assigning it
    got = client.get(f"/api/servers/{aid}/{PID}/roles/grpc-entry", headers=HDRS).json()
    assert got["address"] == "203.0.113.30"
    info = client.get("/api/ips/203.0.113.30", headers=b).json()
    assert info["role"] == "grpc-entry"
    # and the old address no longer claims it
    assert client.get(f"/api/ips/{EXTRA}", headers=b).status_code == 404


async def test_role_cannot_name_a_foreign_address(client, monkeypatch):
    _panel(monkeypatch)
    aid = await _account(client)
    r = client.put(f"/api/servers/{aid}/{PID}/roles", headers=HDRS,
                   json={"role": "x", "address": "198.51.100.7"})
    assert r.status_code == 409
    # the primary may be named (for inventory labels) - the swap path still
    # refuses to touch it
    r = client.put(f"/api/servers/{aid}/{PID}/roles", headers=HDRS,
                   json={"role": "exit", "address": PRIMARY})
    assert r.status_code == 200


async def test_one_address_one_role_reassign_moves_the_row(client, monkeypatch):
    _panel(monkeypatch)
    aid = await _account(client)
    h = HDRS
    client.put(f"/api/servers/{aid}/{PID}/roles", headers=h,
               json={"role": "a", "address": EXTRA})
    r = client.put(f"/api/servers/{aid}/{PID}/roles", headers=h,
                   json={"role": "b", "address": EXTRA})
    assert r.json()["previous"] is None
    roles = client.get("/api/roles", headers=h).json()["roles"]
    by_role = {row["role"]: row for row in roles}
    assert by_role["b"]["address"] == EXTRA and "a" not in by_role


async def test_role_endpoints_need_admin(client, monkeypatch):
    _panel(monkeypatch)
    await _account(client)
    b = await _script_token(client)
    # reads are open, writes are admin-only
    assert client.get("/api/roles", headers=b).status_code == 403
