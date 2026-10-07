"""Hetzner extra IPs = Floating IPs: created assigned, confirmed on the
provider's view, deleted to release; the Primary IP is never touched."""
import json

import httpx
import pytest

from server.adapters import hetzner
from server.adapters.base import AdapterError
from server.adapters.hetzner import HetznerAdapter

from conftest import TEST_TOKEN, fixture


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(hetzner, "POLL_INTERVAL", 0)
    monkeypatch.setattr(hetzner, "_catalog", None)


def mock_floating(*, assign_lands_elsewhere=False):
    floating = {7001: {"id": 7001, "ip": "203.0.113.70", "type": "ipv4", "server": 11111111}}
    state = {"next": 7002, "calls": []}

    def handler(req: httpx.Request) -> httpx.Response:
        path, m = req.url.path, req.method
        state["calls"].append((m, path))
        if path == "/v1/servers" and m == "GET":
            return httpx.Response(200, json=fixture("hetzner/servers_p1.json"))
        if path == "/v1/servers/11111111":
            srv = fixture("hetzner/servers_p1.json")["servers"][0]
            return httpx.Response(200, json={"server": srv})
        if path == "/v1/server_types":
            return httpx.Response(200, json=fixture("hetzner/server_types_p1.json"))
        if path == "/v1/floating_ips" and m == "GET":
            return httpx.Response(200, json={"floating_ips": list(floating.values()),
                                             "meta": {"pagination": {"last_page": 1}}})
        if path == "/v1/floating_ips" and m == "POST":
            body = json.loads(req.content)
            fid = state["next"]
            state["next"] += 1
            target = 999 if assign_lands_elsewhere else body["server"]
            floating[fid] = {"id": fid, "ip": f"203.0.113.{fid - 7000 + 70}",
                             "type": "ipv4", "server": target}
            return httpx.Response(201, json={"floating_ip": floating[fid],
                                             "action": {"id": 55, "status": "running"}})
        if path.startswith("/v1/floating_ips/"):
            fid = int(path.rsplit("/", 1)[1])
            if m == "GET":
                return (httpx.Response(200, json={"floating_ip": floating[fid]})
                        if fid in floating else httpx.Response(404, json={}))
            if m == "DELETE":
                floating.pop(fid, None)
                return httpx.Response(204)
        if path == "/v1/actions/55":
            return httpx.Response(200, json={"action": {"id": 55, "status": "success"}})
        if path == "/v1/pricing":
            return httpx.Response(200, json={"pricing": {"currency": "EUR", "floating_ips": [
                {"type": "ipv4", "prices": [{"location": "fsn1",
                                             "price_monthly": {"net": "3.00", "gross": "3.57"}}]}]}})
        return httpx.Response(404, json={"error": {"code": "not_found"}})

    return httpx.MockTransport(handler), state, floating


async def test_floating_ip_is_listed_as_swappable_primary_is_not():
    t, _, _ = mock_floating()
    servers = {s.provider_id: s for s in await HetznerAdapter(1, "a", TEST_TOKEN, http=t).list_servers()}
    ips = {i.address: i for i in servers["11111111"].ips}
    assert ips["203.0.113.10"].primary and ips["203.0.113.10"].kind == "primary"
    assert not ips["203.0.113.70"].primary and ips["203.0.113.70"].kind == "floating"
    assert [i.address for i in servers["11111112"].ips] == ["203.0.113.11"]


async def test_add_creates_assigned_and_confirms():
    t, state, floating = mock_floating()
    ip = await HetznerAdapter(1, "a", TEST_TOKEN, http=t).add_ip("11111111")
    assert ip.address == "203.0.113.72" and ip.provider_ip_id == "7002"
    assert state["calls"].count(("POST", "/v1/floating_ips")) == 1


async def test_add_that_lands_on_another_server_is_rolled_back():
    t, state, floating = mock_floating(assign_lands_elsewhere=True)
    with pytest.raises(AdapterError, match="not assigned"):
        await HetznerAdapter(1, "a", TEST_TOKEN, http=t).add_ip("11111111")
    assert 7002 not in floating  # deleted again - it would bill monthly


async def test_release_deletes_floating_and_refuses_primary():
    t, _, floating = mock_floating()
    a = HetznerAdapter(1, "a", TEST_TOKEN, http=t)
    await a.release_ip("11111111", "203.0.113.70")
    assert 7001 not in floating
    with pytest.raises(AdapterError, match="primary"):
        await a.release_ip("11111111", "203.0.113.10")


async def test_ip_cost_per_location_monthly_gross():
    t, _, _ = mock_floating()
    cost = await HetznerAdapter(1, "a", TEST_TOKEN, http=t).ip_cost("11111111")
    assert cost.per == "month" and str(cost.price.amount) == "3.57"
    assert cost.price.vat_inclusive is True
