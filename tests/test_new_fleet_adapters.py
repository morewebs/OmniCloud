"""Netlen, Tube-hosting and LightNode fleet adapters against doubles built
from each provider's documented response shapes (no network)."""
import json

import httpx
import pytest

from server.adapters import lightnode, netlen, tube
from server.adapters.base import AdapterError, Capability, ServerStatus
from server.adapters.lightnode import LightNodeAdapter
from server.adapters.netlen import NetlenAdapter
from server.adapters.tube import TubeAdapter

from conftest import TEST_TOKEN


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    for mod in (netlen, lightnode, tube):
        monkeypatch.setattr(mod, "POLL_INTERVAL_S", 0)


# -- Netlen --------------------------------------------------------------------

def netlen_mock(*, add_error=None):
    srv = {"id": "NET10231", "name": "web01", "status": "active", "power_state": "running",
           "location_id": 3, "specs": {"cpu_cores": 2, "ram_mb": 4096, "storage_gb": 60},
           "network": {"ipv4": {"address": "203.0.113.40"},
                       "extra_ips": [{"address": "203.0.113.41", "version": 4}]},
           "plan": {"id": 6, "name": "VDS-3"},
           "billing": {"amount": "7.99", "currency": "USD", "cycle": "monthly",
                       "next_billing_at": "2026-11-13T00:00:00Z", "deletable": True},
           "created_at": "2026-08-13T10:00:00Z"}
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        p, m = req.url.path.removeprefix("/v2"), req.method
        calls.append((m, p))
        assert req.headers["Authorization"] == f"Bearer {TEST_TOKEN}"
        if p == "/servers":
            lst = dict(srv, power_state=None)  # list rows carry no power_state
            return httpx.Response(200, json={"data": [lst], "meta": {"pagination": {
                "page": 1, "per_page": 100, "total": 1, "total_pages": 1}}})
        if p == "/servers/NET10231":
            return httpx.Response(200, json={"data": srv})
        if p.startswith("/servers/NET10231/actions/"):
            verb = p.rsplit("/", 1)[1]
            srv["power_state"] = {"stop": "stopped"}.get(verb, "running")
            return httpx.Response(202, json={"data": {"operation": {"id": "op_1", "status": "queued"}}})
        if p == "/operations/op_1":
            return httpx.Response(200, json={"data": {"operation": {"id": "op_1", "status": "completed"}}})
        if p == "/servers/NET10231/ips" and m == "POST":
            if add_error:
                return httpx.Response(422, json={"error": {"code": add_error, "message": "no"}})
            srv["network"]["extra_ips"].append({"address": "203.0.113.42", "version": 4})
            return httpx.Response(201, json={"data": {"address": "203.0.113.42", "version": 4,
                                                      "is_primary": False}})
        if p == "/servers/NET10231/addons":
            return httpx.Response(200, json={"data": {"extra_ip": {"ipv4": {
                "available": True, "price": {"amount": "3.00", "currency": "USD"}}}}})
        if p == "/billing/balance":
            return httpx.Response(200, json={"data": {"balance": {"amount": "42.50",
                                                                  "currency": "USD"}}})
        return httpx.Response(404, json={"error": {"code": "RESOURCE_NOT_FOUND"}})
    return httpx.MockTransport(handler), calls


async def test_netlen_servers_ips_and_power_state_from_detail():
    t, _ = netlen_mock()
    [s] = await NetlenAdapter(1, "a", TEST_TOKEN, http=t).list_servers()
    assert s.status is ServerStatus.RUNNING and s.ipv4 == "203.0.113.40"
    assert [(i.address, i.primary) for i in s.ips] == [("203.0.113.40", True),
                                                      ("203.0.113.41", False)]
    assert str(s.monthly_price.amount) == "7.99" and s.server_type == "VDS-3"


async def test_netlen_power_waits_for_operation_then_power_state():
    t, calls = netlen_mock()
    res = await NetlenAdapter(1, "a", TEST_TOKEN, http=t).perform_action(
        Capability.POWER_OFF, "NET10231", {})
    assert "stopped" in res.detail and ("GET", "/operations/op_1") in calls


async def test_netlen_add_ip_charges_once_and_cannot_change():
    t, calls = netlen_mock()
    a = NetlenAdapter(1, "a", TEST_TOKEN, http=t)
    ip = await a.add_ip("NET10231")
    assert ip.address == "203.0.113.42"
    assert calls.count(("POST", "/servers/NET10231/ips")) == 1
    assert Capability.IP_CHANGE not in a.capabilities  # no release endpoint exists
    cost = await a.ip_cost("NET10231")
    assert str(cost.price.amount) == "3.00"


async def test_netlen_insufficient_balance_is_a_clear_error():
    t, _ = netlen_mock(add_error="INSUFFICIENT_BALANCE")
    with pytest.raises(AdapterError, match="INSUFFICIENT_BALANCE"):
        await NetlenAdapter(1, "a", TEST_TOKEN, http=t).add_ip("NET10231")


async def test_netlen_billing():
    t, _ = netlen_mock()
    b = await NetlenAdapter(1, "a", TEST_TOKEN, http=t).get_billing()
    assert str(b.balance.amount) == "42.50" and "invoices" in b.not_exposed
    assert b.renewals[0].date.isoformat().startswith("2026-11-13")


# -- LightNode -----------------------------------------------------------------

def lightnode_mock():
    inst = {"ecsResourceUUID": "ecs-r600009d0myg", "instanceName": "edge-1",
            "ecsStatus": "STARTED", "ecsPendingStatus": "NONE",
            "publicIpAddress": "203.0.113.60", "secondaryPublicIpInfoList": ["203.0.113.61"],
            "regionCode": "tr-ist-1", "zoneCode": "tr-ist-1-a", "cpu": 2, "memory": 2,
            "freeFlow": 1000, "usedFlow": 250.5, "createTime": "2026-03-12 10:15:17"}
    task = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["x-open-token"] == TEST_TOKEN
        p = req.url.path
        if p == "/region/list":
            return httpx.Response(200, json={"regions": [
                {"regionCode": "tr-ist-1", "regionName": "Istanbul",
                 "zones": [{"zoneCode": "tr-ist-1-a", "zoneName": "A"}]},
                {"regionCode": "hk-1", "regionName": "HK",
                 "zones": [{"zoneCode": "hk-1-a", "zoneName": "A"}]}]})
        if p == "/instance/list":
            rows = [inst] if req.url.params["zoneCode"] == "tr-ist-1-a" else []
            return httpx.Response(202, json={"instances": rows, "rowCount": len(rows),
                                             "success": True, "httpStatus": 202})
        if p == "/instance/detail":
            return httpx.Response(202, json={"instance": inst})
        if p in ("/instance/stop", "/instance/start", "/instance/reboot"):
            assert json.loads(req.content) == {"ecsResourceUUID": "ecs-r600009d0myg"}
            inst["ecsStatus"] = "STOPPED" if p.endswith("stop") else "STARTED"
            return httpx.Response(202, json={"asyncTaskUUID": "cm1", "httpStatus": 202})
        if p == "/asynctask/getResult":
            task["n"] += 1
            done = task["n"] > 1
            return httpx.Response(202, json={"asyncTaskInfo": {
                "taskStatus": "FINISHED" if done else "PROCESSING",
                "processResult": "SUCCESS" if done else "RETRY"}})
        return httpx.Response(404, json={})
    return httpx.MockTransport(handler)


async def test_lightnode_lists_zone_by_zone_with_quota_and_ips():
    [s] = await LightNodeAdapter(1, "a", TEST_TOKEN, http=lightnode_mock()).list_servers()
    assert s.provider_id == "ecs-r600009d0myg" and s.status is ServerStatus.RUNNING
    assert s.allowance.included_bytes == 1000 * 10**9 and s.allowance.used_bytes == 250_500_000_000
    assert [i.primary for i in s.ips] == [True, False]
    assert Capability.IP_ADD not in LightNodeAdapter.capabilities  # no IP API


async def test_lightnode_power_polls_task_then_confirms_status():
    res = await LightNodeAdapter(1, "a", TEST_TOKEN, http=lightnode_mock()).perform_action(
        Capability.POWER_OFF, "ecs-r600009d0myg", {})
    assert "off" in res.detail


# -- Tube-hosting ----------------------------------------------------------------

CRED = json.dumps({"mail": "ops@example.test", "password": "tube-pw-0000"})


def tube_mock(*, expire_once=False):
    state = {"status": "running", "logins": 0, "expire": expire_once}

    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/login":
            body = json.loads(req.content)
            assert body["mail"] == "ops@example.test" and body["device"]["type"] == "WEB"
            state["logins"] += 1
            return httpx.Response(200, json={"accessToken": f"jwt-{state['logins']}",
                                             "refreshToken": "r"})
        if not req.headers.get("Authorization", "").startswith("Bearer jwt-"):
            return httpx.Response(401)
        if state["expire"]:
            state["expire"] = False
            return httpx.Response(401)
        if p == "/servicegroups/currents":
            return httpx.Response(200, json=[{
                "id": 70, "metaData": {"status": "ACTIVE", "endDate": "2026-11-01T00:00:00Z"},
                "groupData": {"endDate": "2026-11-01T00:00:00Z", "services": [
                    {"id": 501, "name": "kvm-ams-1", "type": "VPS", "serviceGroupId": 70,
                     "price": 500, "runtime": "P1M", "startDate": "2026-09-01T00:00:00Z",
                     "endDate": "2026-11-01T00:00:00Z"},
                    {"id": 502, "name": "ip-bundle", "type": "IPV4BUNDLE", "serviceGroupId": 70}]}}])
        if p == "/vps/501":
            return httpx.Response(200, json={"id": 501, "coreCount": 2, "memory": 4096,
                                             "vpsType": "KVM",
                                             "primaryIPv4": {"ipv4": {"ipv4": "203.0.113.90"}}})
        if p == "/vps/501/status":
            return httpx.Response(200, json={"status": state["status"]})
        if p in ("/vps/501/stop", "/vps/501/start", "/vps/501/restart", "/vps/501/shutdown"):
            state["status"] = "stopped" if p.endswith(("stop", "shutdown")) else "running"
            return httpx.Response(200, text="ok")
        if p == "/vps/501/password":
            return httpx.Response(200, text="ok")
        if p == "/me":
            return httpx.Response(200, json={"id": 1, "balance": 1234})
        if p == "/payments/invoices":
            return httpx.Response(200, json=[{"id": 9, "time": "2026-10-01T00:00:00Z",
                                              "finished": True,
                                              "items": [{"unitPrice": 500, "quantity": 1}]}])
        return httpx.Response(404)
    return httpx.MockTransport(handler), state


async def test_tube_lists_vps_services_only():
    t, _ = tube_mock()
    [s] = await TubeAdapter(1, "a", CRED, http=t).list_servers()
    assert s.provider_id == "501" and s.ipv4 == "203.0.113.90"
    assert s.status is ServerStatus.RUNNING
    assert {f.label: f.value for f in s.facets}["price (panel)"] == "5.00 EUR / P1M"


async def test_tube_relogs_once_on_401_and_powers_off():
    t, state = tube_mock(expire_once=True)
    a = TubeAdapter(1, "a", CRED, http=t)
    res = await a.perform_action(Capability.POWER_OFF, "501", {})
    assert "stopped" in res.detail and state["logins"] == 2


async def test_tube_billing_reads_cents_balance():
    t, _ = tube_mock()
    b = await TubeAdapter(1, "a", CRED, http=t).get_billing()
    assert str(b.balance.amount) == "12.34" and b.invoices[0].total.amount == 5
    assert b.renewals and b.renewals[0].provider_id == "501"
