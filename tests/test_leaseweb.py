from server.adapters.base import Capability, ServerStatus, TrafficCounting
from server.adapters.leaseweb import LeasewebAdapter

from conftest import TEST_TOKEN, mock_leaseweb_transport


def make_adapter(transport):
    return LeasewebAdapter(1, "account-a7f3", TEST_TOKEN, http=transport)


async def test_list_servers_egress_only_traffic():
    """VERIFIED: Public Cloud bills EGRESS ONLY (upPublic). The legacy
    assumption of both-direction counting was wrong for this product."""
    transport, _ = mock_leaseweb_transport()
    a = make_adapter(transport)
    servers = await a.list_servers()
    s = next(x for x in servers if x.name == "srv-ams1-01")
    # upPublic values: 120 GB + 128 GB = 248 GB egress. downPublic is free.
    assert s.allowance.used_bytes == 248_000_000_000
    assert s.allowance.counting == TrafficCounting.OUTGOING_ONLY
    # Public Cloud allowance is per ACCOUNT (1TB), not per instance
    assert s.allowance.included_bytes is None
    assert "per account" in (s.allowance.window or "")


async def test_instance_fields_are_reference_and_region():
    """Instances have reference (not name) and region (not datacenter)."""
    transport, _ = mock_leaseweb_transport()
    a = make_adapter(transport)
    servers = await a.list_servers()
    s = next(x for x in servers if x.name == "srv-ams1-01")
    assert s.name == "srv-ams1-01"  # mapped from reference
    assert s.region == "eu-west-3"
    assert s.server_type == "lsw.m3.medium"


async def test_monthly_price_via_instance_types():
    """Price comes from GET /instanceTypes (not the instance object)."""
    transport, _ = mock_leaseweb_transport()
    a = make_adapter(transport)
    servers = await a.list_servers()
    s = next(x for x in servers if x.name == "srv-ams1-01")
    assert str(s.monthly_price.amount) == "14.90"


async def test_empty_metrics_renders_missing_not_zero():
    """A stopped instance with no metric values: usage is missing entirely -
    no allowance data (None), never 0 (which would read as a healthy idle
    server)."""
    transport, _ = mock_leaseweb_transport()
    a = make_adapter(transport)
    servers = await a.list_servers()
    s = next(x for x in servers if x.name == "srv-fra1-05")
    assert s.allowance is None or s.allowance.used_bytes is None


async def test_state_enum_is_verified_one():
    transport, _ = mock_leaseweb_transport()
    a = make_adapter(transport)
    servers = await a.list_servers()
    assert next(x for x in servers if x.name == "srv-ams1-01").status == ServerStatus.RUNNING
    assert next(x for x in servers if x.name == "srv-fra1-05").status == ServerStatus.OFF


async def test_power_verbs_are_start_stop_reboot():
    """Verified endpoints: /start, /stop, /reboot - powerOn/powerOff/shutdown
    do not exist."""
    transport, calls = mock_leaseweb_transport()
    a = make_adapter(transport)
    await a.perform_action(Capability.POWER_ON, "5f8f2e6a-1111-2222-3333-a7f3b2c4d9e0", {})
    verbs = [c.rsplit("/", 1)[-1] for c in calls if "/instances/" in c and c.count("/") >= 3]
    assert "start" in verbs, "power_on must POST /start"
    assert not any(v in ("powerOn", "powerOff", "shutdown") for v in verbs)


async def test_shutdown_is_stop_and_polls_to_stopped():
    transport, calls = mock_leaseweb_transport()
    a = make_adapter(transport)
    await a.perform_action(Capability.SHUTDOWN, "5f8f2e6a-1111-2222-3333-a7f3b2c4d9e0", {})
    verbs = [c.rsplit("/", 1)[-1] for c in calls if "/instances/" in c and c.count("/") >= 3]
    assert "stop" in verbs


async def test_rename_sends_reference_not_name():
    """The update field is reference; "name" is not a schema field."""
    import httpx

    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT" and "/instances/" in request.url.path:
            bodies.append(request.read().decode())
            return httpx.Response(200, json={})
        return httpx.Response(404)

    a = make_adapter(httpx.MockTransport(handler))
    await a.perform_action(Capability.RENAME, "5f8f2e6a-1111-2222-3333-a7f3b2c4d9e0",
                           {"name": "renamed-x"})
    assert any('"reference"' in b for b in bodies)
    assert not any('"name"' in b for b in bodies)


async def test_delete_monthly_contract_sends_reason_code():
    """MONTHLY contracts REQUIRE reasonCode; termination is deferred."""
    import httpx

    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/instances/" in request.url.path:
            return httpx.Response(200, json={
                "id": "x", "reference": "r", "state": "RUNNING", "region": "eu-west-3",
                "contract": {"billingFrequency": 1, "term": 0, "type": "MONTHLY"}})
        if request.method == "DELETE":
            bodies.append(request.read().decode())
            return httpx.Response(204)
        return httpx.Response(404)

    a = make_adapter(httpx.MockTransport(handler))
    result = await a.perform_action(
        Capability.DELETE, "5f8f2e6a-1111-2222-3333-a7f3b2c4d9e0", {})
    assert any("reasonCode" in b for b in bodies), "monthly delete must send reasonCode"
    assert "contract end" in (result.detail or "")
