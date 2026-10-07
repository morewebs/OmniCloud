"""The IP-change API end to end: an operator's script holding an ip_change
token swaps a server's extra IP, against the BILLmanager panel double."""
import pytest
from fastapi.testclient import TestClient

from server import accounts, db, sync
from server.adapters import gcore_hosting as gh
from server.adapters.gcore_hosting import GcoreHostingAdapter
from server.main import create_app

from conftest import mock_gcore_hosting_transport

HDRS = {"X-Requested-With": "XMLHttpRequest"}
EXTRA = "203.0.113.11"     # srv-ams-01's swappable IP (fixture)
PRIMARY = "203.0.113.10"   # srv-ams-01's main IP


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


async def _account(client, purchases=True):
    aid = client.post("/api/accounts", headers=HDRS, json={
        "adapter": "gcore_hosting", "name": "panel-a7f3",
        "fields": {"url": "https://panel.example.test/billmgr",
                   "username": "ops@example.test", "password": "panel-pw-0000"}}).json()["id"]
    if purchases:
        client.patch(f"/api/accounts/{aid}", headers=HDRS, json={"purchases_enabled": True})
    await sync.sync_account_now(aid)
    return aid


def _script_token(client):
    """What the operator's server holds: an ip_change-scoped admin token,
    and no session cookie."""
    tok = client.post("/api/auth/tokens", headers=HDRS,
                      json={"name": "rotator", "scope": "ip_change"}).json()["token"]
    client.cookies.clear()
    return {"Authorization": f"Bearer {tok}"}


async def test_script_changes_its_extra_ip(client, monkeypatch):
    calls, _ = _panel(monkeypatch)
    aid = await _account(client)
    b = _script_token(client)

    info = client.get(f"/api/ips/{EXTRA}", headers=b).json()
    assert info["changeable"] and not info["primary"]
    assert info["cost"]["per"] == "purchase" and info["daily_cap"] == 10

    r = client.post(f"/api/ips/{EXTRA}/change", headers=b)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["old_ip"] == EXTRA and res["new_ip"] == "203.0.113.30"
    assert res["old_released"] is True
    # exactly one purchase went to the panel
    assert sum(1 for c in calls if c[1] == "service.ip.edit" and c[2].get("sok") == "ok") == 1

    # the fleet cache follows: the new IP is findable, the old one gone
    assert client.get("/api/ips/203.0.113.30", headers=b).json()["changeable"]
    assert client.get(f"/api/ips/{EXTRA}", headers=b).status_code == 404

    # a dropped connection can collect the outcome from the action row
    act = client.get(f"/api/actions/{res['action_id']}", headers=b).json()
    assert act["status"] == "done" and act["result"]["new_ip"] == "203.0.113.30"

    with db.connect() as conn:
        o = conn.execute("SELECT * FROM orders WHERE kind='ip'").fetchone()
    assert o["mode"] == "real" and o["status"] == "provisioned"
    assert o["resulting_provider_id"] == "203.0.113.30" and o["target_provider_id"] == "5101"

    # and the script's token still can't reach anything else
    assert client.get("/api/fleet", headers=b).status_code == 403


async def test_primary_ip_is_never_changed(client, monkeypatch):
    calls, _ = _panel(monkeypatch)
    await _account(client)
    b = _script_token(client)
    r = client.post(f"/api/ips/{PRIMARY}/change", headers=b)
    assert r.status_code == 409 and "primary" in r.json()["detail"]
    assert not any(c[1] in ("service.ip.edit", "service.ip.delete") for c in calls)


async def test_purchases_disabled_refuses_before_any_provider_call(client, monkeypatch):
    calls, _ = _panel(monkeypatch)
    await _account(client, purchases=False)
    n = len(calls)
    r = client.post(f"/api/ips/{EXTRA}/change", headers=_script_token(client))
    assert r.status_code == 403 and "purchases are disabled" in r.json()["detail"]
    assert len(calls) == n


async def test_daily_cap_stops_a_looping_script(client, monkeypatch):
    _panel(monkeypatch)
    aid = await _account(client)
    client.put("/api/settings", headers=HDRS, json={f"ip_change_daily_cap:{aid}": "1"})
    b = _script_token(client)
    new = client.post(f"/api/ips/{EXTRA}/change", headers=b).json()["new_ip"]
    r = client.post(f"/api/ips/{new}/change", headers=b)
    assert r.status_code == 429
    assert r.json()["detail"]["used"] == 1 and r.json()["detail"]["cap"] == 1


async def test_unknown_ip_is_404(client, monkeypatch):
    _panel(monkeypatch)
    await _account(client)
    assert client.post("/api/ips/198.51.100.1/change",
                       headers=_script_token(client)).status_code == 404


async def test_ip_behind_a_payment_returns_202_with_the_pay_link(client, monkeypatch):
    _panel(monkeypatch, payment_required=True)
    await _account(client)
    r = client.post(f"/api/ips/{EXTRA}/change", headers=_script_token(client))
    assert r.status_code == 202
    assert r.json()["status"] == "awaiting_payment" and r.json()["provider_ref"] == "BO-77"
    assert r.json()["old_released"] is False  # new-first: the old IP stays until paid
    with db.connect() as conn:
        o = conn.execute("SELECT status, pay_url FROM orders WHERE kind='ip'").fetchone()
    assert o["status"] == "awaiting_payment" and o["pay_url"].startswith("https://")


async def test_slow_acquisition_keeps_the_old_ip_and_says_an_order_is_out(client, monkeypatch):
    monkeypatch.setattr(gh, "POLL_BUDGET_S", 0)
    calls, state = _panel(monkeypatch, ip_appears_after=99)
    await _account(client)
    r = client.post(f"/api/ips/{EXTRA}/change", headers=_script_token(client))
    assert r.status_code == 502
    d = r.json()["detail"]
    # the old IP stays; the caller is told an order may still land
    assert d["old_released"] is False and "may still appear" in d["message"]
    assert not any(c[1] == "service.ip.delete" for c in calls)
    with db.connect() as conn:
        assert conn.execute("SELECT status FROM orders WHERE kind='ip'").fetchone()[0] == "failed"


async def test_refused_acquisition_changes_nothing(client, monkeypatch):
    calls, _ = _panel(monkeypatch)
    await _account(client)

    async def no_ips(self, server_id):
        from server.adapters.base import AdapterError
        raise AdapterError("no free IPs in this pool")
    monkeypatch.setattr(GcoreHostingAdapter, "add_ip", no_ips)
    r = client.post(f"/api/ips/{EXTRA}/change", headers=_script_token(client))
    assert r.status_code == 502 and "nothing changed" in r.json()["detail"]["message"]
    assert not any(c[1] == "service.ip.delete" for c in calls)


async def test_release_first_for_servers_at_their_ip_cap(client, monkeypatch):
    calls, _ = _panel(monkeypatch)
    await _account(client)
    r = client.post(f"/api/ips/{EXTRA}/change", headers=_script_token(client),
                    json={"release_first": True})
    assert r.status_code == 200
    funcs = [c[1] for c in calls if c[1] in ("service.ip.delete", "service.ip.edit")
             and c[2].get("sok") == "ok"]
    assert funcs == ["service.ip.delete", "service.ip.edit"]


async def test_viewer_token_cannot_spend_money(client, monkeypatch):
    _panel(monkeypatch)
    await _account(client)
    client.post("/api/users", headers=HDRS,
                json={"username": "view", "password": "pw123456", "role": "viewer"})
    client.post("/api/auth/logout", headers=HDRS)
    client.post("/api/auth/login", headers=HDRS,
                json={"username": "view", "password": "pw123456"})
    b = _script_token(client)
    assert client.post(f"/api/ips/{EXTRA}/change", headers=b).status_code == 403


async def test_add_and_release_from_the_ui_routes(client, monkeypatch):
    _panel(monkeypatch)
    aid = await _account(client)
    r = client.post(f"/api/servers/{aid}/5101/ips", headers=HDRS)
    assert r.status_code == 200 and r.json()["new_ip"] == "203.0.113.30"
    r = client.delete(f"/api/servers/{aid}/5101/ips/203.0.113.30", headers=HDRS)
    assert r.status_code == 200
    assert client.delete(f"/api/servers/{aid}/5101/ips/{PRIMARY}",
                         headers=HDRS).status_code == 409
    actions = [e["action"] for e in client.get("/api/audit").json()]
    assert "ip.add" in actions and "ip.release" in actions


async def test_generic_action_route_refuses_ip_kinds(client, monkeypatch):
    _panel(monkeypatch)
    aid = await _account(client)
    r = client.post(f"/api/servers/{aid}/5101/actions", headers=HDRS,
                    json={"kind": "ip_change", "params": {}})
    assert r.status_code == 400


async def test_ip_read_token_only_looks_ips_up(client, monkeypatch):
    """What the changing server itself holds: it reads its own IP list
    (gateway + prefix included) and can neither change nor release one."""
    calls, _ = _panel(monkeypatch)
    await _account(client)
    tok = client.post("/api/auth/tokens", headers=HDRS,
                      json={"name": "reconciler", "scope": "ip_read"}).json()
    assert tok["scope"] == "ip_read"
    client.cookies.clear()
    b = {"Authorization": f"Bearer {tok['token']}"}
    info = client.get(f"/api/ips/{PRIMARY}", headers=b)
    assert info.status_code == 200 and info.json()["primary"]
    assert {i["address"] for i in info.json()["ips"]} == {PRIMARY, EXTRA}
    assert client.post(f"/api/ips/{EXTRA}/change", headers=b).status_code == 403
    assert client.get("/api/actions/1", headers=b).status_code == 403
    assert client.get("/api/fleet", headers=b).status_code == 403
    assert not any(c[1] in ("service.ip.edit", "service.ip.delete") for c in calls)


async def test_describe_caches_the_cost_lookup(client, monkeypatch):
    """A server polling its own IP list must not log in to the panel on
    every poll: the cost (a live provider call) is cached."""
    calls, _ = _panel(monkeypatch)
    await _account(client)
    b = _script_token(client)
    client.get(f"/api/ips/{PRIMARY}", headers=b)
    n = len(calls)
    for _ in range(3):
        assert client.get(f"/api/ips/{PRIMARY}", headers=b).status_code == 200
    assert len(calls) == n
