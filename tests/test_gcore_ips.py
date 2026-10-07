"""Gcore Cloud extra IPs = reserved public IPs: reserve -> attach -> confirm,
detach -> delete; an IP that fails to attach is deleted at once (it bills
per minute while it exists, attached or not)."""
import copy
import json
import re

import httpx
import pytest

from server.adapters import gcore
from server.adapters.base import AdapterError
from server.adapters.gcore import GcoreAdapter

from conftest import TEST_TOKEN, fixture

PID = "101:7:aaaa1111-2222-3333-4444-555555555555"
IID = "aaaa1111-2222-3333-4444-555555555555"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(gcore, "POLL_INTERVAL_S", 0)


def mock_reserved(*, attach_fails=False, task_names_port=True, cleanup_fails=False):
    """Instance 'web' (primary floating 203.0.113.10) plus one attached
    reserved IP 203.0.113.50. New reservations get 203.0.113.60+."""
    inst = copy.deepcopy(fixture("gcore/instance_web.json"))
    inst["addresses"]["external_net"].append({"addr": "203.0.113.50", "type": "fixed"})
    reserved = {"port-50": {"port_id": "port-50", "fixed_ip_address": "203.0.113.50",
                            "is_external": True}}
    tasks: dict[str, dict] = {}
    state = {"n": 60, "calls": []}

    def task(created=None):
        tid = f"task-{len(tasks)}"
        tasks[tid] = {"id": tid, "state": "FINISHED", "created_resources": created or {}}
        return httpx.Response(200, json={"tasks": [tid]})

    def handler(req: httpx.Request) -> httpx.Response:
        path, m = req.url.path, req.method
        state["calls"].append((m, path))
        if path.startswith("/cloud/v1/tasks/"):
            return httpx.Response(200, json=tasks[path.rsplit("/", 1)[1]])
        if path == f"/cloud/v1/instances/101/7/{IID}" and m == "GET":
            return httpx.Response(200, json=inst)
        if path == "/cloud/v1/reserved_fixed_ips/101/7" and m == "GET":
            return httpx.Response(200, json={"count": len(reserved),
                                             "results": list(reserved.values())})
        if path == "/cloud/v1/reserved_fixed_ips/101/7" and m == "POST":
            assert json.loads(req.content)["type"] == "external"
            port = f"port-{state['n']}"
            reserved[port] = {"port_id": port, "fixed_ip_address": f"203.0.113.{state['n']}",
                              "is_external": True}
            state["n"] += 1
            return task({"ports": [port]} if task_names_port else {})
        mm = re.match(r"^/cloud/v1/reserved_fixed_ips/101/7/(.+)$", path)
        if mm:
            port = mm.group(1)
            if m == "GET":
                return (httpx.Response(200, json=reserved[port]) if port in reserved
                        else httpx.Response(404, json={"message": "not found"}))
            if m == "DELETE":
                if cleanup_fails:
                    return httpx.Response(500, json={"message": "busy"})
                reserved.pop(port, None)
                return task()
        if path.endswith("/attach_interface"):
            if attach_fails:
                return httpx.Response(409, json={"message": "port limit reached"})
            port = json.loads(req.content)["port_id"]
            inst["addresses"]["external_net"].append(
                {"addr": reserved[port]["fixed_ip_address"], "type": "fixed"})
            return task()
        if path.endswith("/detach_interface"):
            ip = json.loads(req.content)["ip_address"]
            inst["addresses"]["external_net"] = [
                a for a in inst["addresses"]["external_net"] if a["addr"] != ip]
            return task()
        if path == "/cloud/v1/pricing/101/7/reserved_fixed_ips":
            return httpx.Response(200, json={"price_per_hour": 0.0038, "currency_code": "EUR"})
        if path.startswith("/cloud/v1/pricing/"):
            return httpx.Response(404, json={})
        return httpx.Response(404, json={"message": f"unmocked {m} {path}"})

    return httpx.MockTransport(handler), state, reserved, inst


async def test_reserved_ip_is_swappable_and_the_vms_own_ip_is_primary():
    t, _, _, _ = mock_reserved()
    s = await GcoreAdapter(1, "acct", TEST_TOKEN, http=t).get_server(PID)
    ips = {i.address: i for i in s.ips}
    assert ips["203.0.113.10"].primary and not ips["203.0.113.50"].primary
    assert ips["203.0.113.50"].kind == "reserved"
    assert "10.0.10.5" not in ips  # private fixed address is not a public IP


async def test_add_ip_reserves_attaches_and_confirms():
    t, state, reserved, inst = mock_reserved()
    ip = await GcoreAdapter(1, "acct", TEST_TOKEN, http=t).add_ip(PID)
    assert ip.address == "203.0.113.60" and ip.kind == "reserved"
    assert "203.0.113.60" in {a["addr"] for a in inst["addresses"]["external_net"]}
    assert [c for c in state["calls"] if c == ("POST", "/cloud/v1/reserved_fixed_ips/101/7")] \
        == [("POST", "/cloud/v1/reserved_fixed_ips/101/7")]  # reserved exactly once


async def test_failed_attach_deletes_the_reserved_ip_so_it_stops_billing():
    t, state, reserved, _ = mock_reserved(attach_fails=True)
    with pytest.raises(AdapterError, match="409"):
        await GcoreAdapter(1, "acct", TEST_TOKEN, http=t).add_ip(PID)
    assert set(reserved) == {"port-50"}  # the new reservation is gone
    assert ("DELETE", "/cloud/v1/reserved_fixed_ips/101/7/port-60") in state["calls"]


async def test_failed_cleanup_says_so_loudly():
    t, _, _, _ = mock_reserved(attach_fails=True, cleanup_fails=True)
    with pytest.raises(AdapterError, match="could NOT be deleted"):
        await GcoreAdapter(1, "acct", TEST_TOKEN, http=t).add_ip(PID)


async def test_release_detaches_and_deletes():
    t, state, reserved, inst = mock_reserved()
    await GcoreAdapter(1, "acct", TEST_TOKEN, http=t).release_ip(PID, "203.0.113.50")
    assert "port-50" not in reserved
    assert "203.0.113.50" not in {a["addr"] for a in inst["addresses"]["external_net"]}


async def test_release_refuses_the_vms_own_address():
    t, _, _, _ = mock_reserved()
    with pytest.raises(AdapterError, match="not a reserved IP"):
        await GcoreAdapter(1, "acct", TEST_TOKEN, http=t).release_ip(PID, "203.0.113.10")


async def test_ip_cost_is_per_hour():
    t, _, _, _ = mock_reserved()
    cost = await GcoreAdapter(1, "acct", TEST_TOKEN, http=t).ip_cost(PID)
    assert cost.per == "hour" and str(cost.price.amount) == "0.0038"
    assert "per minute" in cost.note
