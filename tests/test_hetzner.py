import pytest

from server.adapters.base import Capability, ServerStatus, TrafficCounting
from server.adapters.hetzner import HetznerAdapter

from conftest import TEST_TOKEN, mock_hetzner_transport


def make_adapter(transport):
    return HetznerAdapter(1, "account-a7f3", TEST_TOKEN, http=transport)


async def test_list_servers_canonical_mapping():
    transport, calls = mock_hetzner_transport()
    a = make_adapter(transport)
    servers = await a.list_servers()
    assert len(servers) == 2
    s = servers[0]
    assert s.provider_id == "11111111"
    assert s.name == "srv-fsn1-01"
    assert s.status == ServerStatus.RUNNING
    assert s.ipv4 == "203.0.113.10"
    # location is top-level now (datacenter removed from the API)
    assert s.region == "fsn1 / DE"
    assert s.server_type == "cx22"
    assert s.labels == {"alias": "edge-a"}


async def test_allowance_is_server_field_not_catalog():
    """The server object's own included_traffic is authoritative (docs);
    the per-location catalog is only the fallback."""
    transport, _ = mock_hetzner_transport()
    a = make_adapter(transport)
    servers = await a.list_servers()
    assert servers[0].allowance.included_bytes == 2199102253056
    assert servers[0].allowance.used_bytes == 512000000000
    assert servers[0].allowance.counting == TrafficCounting.OUTGOING_ONLY


async def test_price_per_location_join():
    """Monthly price from server_type.prices[] joined by (type, location)."""
    transport, _ = mock_hetzner_transport()
    a = make_adapter(transport)
    servers = await a.list_servers()
    assert str(servers[0].monthly_price.amount) == "3.92"  # fsn1 gross
    # overage price from prices[].price_per_tb_traffic, not a constant
    assert str(servers[0].allowance.overage_price.amount).startswith("1.19")


async def test_zero_traffic_is_measured_zero_not_hidden():
    """A server reporting 0 outgoing bytes IS a measured zero - must stay 0,
    never become None/'-'."""
    transport, _ = mock_hetzner_transport()
    a = make_adapter(transport)
    servers = await a.list_servers()
    assert servers[1].allowance.used_bytes == 0


async def test_overage_projection_uses_api_price():
    transport, _ = mock_hetzner_transport()
    a = make_adapter(transport)
    servers = await a.list_servers()
    s = servers[0]  # 512 GB used, 21.99 TB included -> no overage
    assert s.allowance.projected_overage_cost is None


async def test_poweron_polls_until_success_not_finished():
    """Verified action enum: running|success|error. Done only at success."""
    transport, calls = mock_hetzner_transport()
    a = make_adapter(transport)
    result = await a.perform_action(Capability.POWER_ON, "11111112", {})
    assert "success" in (result.detail or "")
    assert any("/actions/" in c for c in calls), "must poll the action"


async def test_429_backoff_retry():
    ok = {"servers": [], "meta": {"pagination": {"last_page": 1}}}
    transport, calls = mock_hetzner_transport(
        responses={"/servers": [429, 429, ok]})
    a = make_adapter(transport)
    servers = await a.list_servers()
    assert servers == []
    assert calls.count("/servers") >= 3


async def test_rename_confirms_provider_view():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT" and request.url.path.endswith("/servers/11111111"):
            return httpx.Response(200, json={"server": {"id": 11111111, "name": "renamed-x"}})
        return httpx.Response(404)

    a = make_adapter(httpx.MockTransport(handler))
    result = await a.perform_action(Capability.RENAME, "11111111", {"name": "renamed-x"})
    assert result.detail == "renamed"


async def test_get_server_unwraps_envelope():
    """GET /servers/{id} returns {server: {...}} - the adapter must unwrap."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/servers/11111111" in request.url.path \
                and "/actions" not in request.url.path:
            data = {"server": {"id": 11111111, "name": "srv-fsn1-01", "status": "running",
                               "public_net": {"ipv4": {"ip": "203.0.113.10"}},
                               "server_type": {"name": "cx22"},
                               "location": {"name": "fsn1", "country": "DE"},
                               "included_traffic": 1000, "outgoing_traffic": 100}}
            return httpx.Response(200, json=data)
        if "/server_types" in request.url.path:
            return httpx.Response(200, json={"server_types": [], "meta":
                                            {"pagination": {"last_page": 1}}})
        return httpx.Response(404)

    a = make_adapter(httpx.MockTransport(handler))
    s = await a.get_server("11111111")
    assert s.name == "srv-fsn1-01"
    assert s.allowance.included_bytes == 1000


async def test_firewall_refuses_zero_inbound_rules():
    """Firewalls default in=DROP: a firewall with no inbound allow rule blocks
    all inbound traffic - the adapter must refuse to create one."""
    from server.adapters.base import AdapterError

    transport, _ = mock_hetzner_transport()
    a = make_adapter(transport)
    with pytest.raises(AdapterError, match="inbound"):
        await a.apply_firewall("11111111", rules=[], attach=True)


async def test_list_firewalls_marks_applied_servers():
    """Firewalls are shared batches: the list reports which servers each
    governs so the UI can mark already-attached ones."""
    transport, _ = mock_hetzner_transport()
    a = make_adapter(transport)
    fws = await a.list_firewalls()
    assert len(fws) == 2
    shared = next(f for f in fws if f["name"] == "batch-edge-fs")
    assert shared["applied_to_count"] == 2
    assert 4211111 in shared["applied_server_ids"]
    assert shared["rules"] == 3


async def test_attach_firewall_sends_apply_to_and_polls():
    """Attach = POST apply_to_resources with {apply_to: [...]} (201,
    {actions: [...]}) -> poll actions until success."""
    transport, calls = mock_hetzner_transport()
    a = make_adapter(transport)
    await a.attach_firewall(1710054, "11111112")
    assert any("apply_to_resources" in c for c in calls)
    assert any("/actions/" in c for c in calls), "must poll the action"


async def test_detach_firewall_sends_remove_from():
    """Verified body key: remove_from (NOT remove_from_resources)."""
    import httpx

    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "/firewalls/" in request.url.path and "/actions/" in request.url.path \
                and request.method == "POST":
            bodies.append(request.read().decode())
            return httpx.Response(201, json={"actions": [{"id": 999002, "status": "running"}]})
        if "/actions/" in request.url.path and request.method == "GET":
            return httpx.Response(200, json={"action": {"id": 999002, "status": "success"}})
        return httpx.Response(404)

    a = make_adapter(httpx.MockTransport(handler))
    await a.detach_firewall(1710054, "11111112")
    assert any('"remove_from"' in b for b in bodies), \
        "detach must send remove_from, not remove_from_resources"
    assert not any('"remove_from_resources"' in b for b in bodies)
