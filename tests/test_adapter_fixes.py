"""Regression tests for the adapter data-honesty fixes (one per fix).
Each test asserts the honest behavior, never the old invented/silent one."""
import httpx
import pytest

from server.adapters.base import AdapterError, Capability
from server.adapters.gcore import GcoreCatalogAdapter
from server.adapters.hetzner import HetznerAdapter
from server.adapters.leaseweb import LeasewebAdapter
from server.adapters.ovh import OvhAdapter

from conftest import TEST_TOKEN


@pytest.fixture(autouse=True)
def _reset_module_caches():
    """These adapters cache in module globals (shared across instances) -
    a test that populates them must not poison later tests in the same run."""
    import server.adapters.hetzner as h
    import server.adapters.leaseweb as lw
    import server.adapters.ovh as o
    saved = (h._catalog, dict(lw._instance_types), dict(lw._instance_types_ts),
             o._region_pricings)
    yield
    h._catalog, lw._instance_types, lw._instance_types_ts, o._region_pricings = \
        saved[0], dict(saved[1]), dict(saved[2]), saved[3]


# -- leaseweb ---------------------------------------------------------------

async def test_leaseweb_prices_keyed_by_region():
    """Prices are per-region: the same type name must yield different prices
    for different regions (a name-only cache poisoned all regions)."""
    types_by_region = {
        "eu-west-3": {"instanceTypes": [
            {"name": "lsw.m3.medium", "prices": {"monthly": "14.90"}}]},
        "us-east-1": {"instanceTypes": [
            {"name": "lsw.m3.medium", "prices": {"monthly": "20.50"}}]},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        region = request.url.params.get("region")
        return httpx.Response(200, json=types_by_region[region])

    a = LeasewebAdapter(1, "acct", TEST_TOKEN, http=httpx.MockTransport(handler))
    p_eu = await a._price_for("eu-west-3", "lsw.m3.medium")
    p_us = await a._price_for("us-east-1", "lsw.m3.medium")
    assert str(p_eu.amount) == "14.90"
    assert str(p_us.amount) == "20.50"  # not the eu-west-3 cache hit
    await a.close()


async def test_leaseweb_price_failure_not_cached():
    """A transient /instanceTypes failure returns None but must NOT poison
    the next call - the retry after recovery gets the real price."""
    fail = {"fail": True}
    region = "ca-central-1"  # unique: the module cache is shared across tests

    def handler(request: httpx.Request) -> httpx.Response:
        if fail["fail"]:
            return httpx.Response(500, json={})
        return httpx.Response(200, json={"instanceTypes": [
            {"name": "lsw.m3.medium", "prices": {"monthly": "14.90"}}]})

    a = LeasewebAdapter(1, "acct", TEST_TOKEN, http=httpx.MockTransport(handler))
    assert await a._price_for(region, "lsw.m3.medium") is None
    fail["fail"] = False
    p = await a._price_for(region, "lsw.m3.medium")
    assert p is not None and str(p.amount) == "14.90"
    await a.close()


async def test_leaseweb_delete_scheduled_not_unknown():
    """DELETE_SCHEDULED (monthly-contract termination, running until
    contract end) must not map to UNKNOWN - that would fire a false
    'down' alert for a still-running server."""
    from server.adapters.base import ServerStatus

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"instances": [
            {"id": "x", "reference": "r", "state": "DELETE_SCHEDULED",
             "region": "eu-west-3", "type": "lsw.m3.medium"}]})

    a = LeasewebAdapter(1, "acct", TEST_TOKEN, http=httpx.MockTransport(handler))
    # _server reads metrics + instanceTypes, both 404 -> degrade, no crash
    servers = await a.list_servers()
    assert servers[0].status is ServerStatus.RUNNING
    await a.close()


async def test_leaseweb_regions_outage_raises_not_one_region_catalog():
    """A /regions outage must raise, never fall back to a hardcoded
    one-region catalog stored as a fresh full success."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    a = LeasewebAdapter(1, "acct", TEST_TOKEN, http=httpx.MockTransport(handler))
    with pytest.raises(AdapterError):
        await a._regions()
    await a.close()


# -- ovh ----------------------------------------------------------------------

def _ovh_delete_transport(delete_status: int | None, get_status: int):
    """GET returns get_status always; the delete-poll GET is the same path.
    delete_status=None means the DELETE call itself is fine (poll GET decides)."""
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/auth/time"):
            return httpx.Response(200, json=int(__import__("time").time()))
        if request.method == "DELETE":
            return httpx.Response(200, json={})
        # the post-delete confirm GET
        return httpx.Response(get_status, json={})
    return httpx.MockTransport(handler)


async def test_ovh_cloud_delete_404_confirms():
    a = OvhAdapter(1, "acct", "ak:as:ck",
                  http=_ovh_delete_transport(None, 404))
    res = await a.perform_action(
        Capability.DELETE, "cloud:proj:22222222-1111-1111-1111-111111111111", {})
    assert "deleted" in (res.detail or "")
    await a.close()


async def test_ovh_cloud_delete_500_reraises_not_success():
    """A persistent 5xx during the delete poll must raise - treating any
    AdapterError as 'deleted' hides auth failures and 5xx as successes."""
    a = OvhAdapter(1, "acct", "ak:as:ck",
                  http=_ovh_delete_transport(None, 500))
    with pytest.raises(AdapterError):
        await a.perform_action(
            Capability.DELETE, "cloud:proj:22222222-1111-1111-1111-111111111111", {})
    await a.close()


async def test_ovh_cloud_stop_accepts_stopped_enum():
    """InstanceStatusEnum contains STOPPED too - a stop landing there must
    confirm, not time out as a failed action."""
    state = {"status": "STOPPED"}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/auth/time"):
            return httpx.Response(200, json=int(__import__("time").time()))
        if request.method == "POST":
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"id": "x", "status": state["status"]})

    a = OvhAdapter(1, "acct", "ak:as:ck", http=httpx.MockTransport(handler))
    res = await a.perform_action(
        Capability.POWER_OFF, "cloud:proj:22222222-1111-1111-1111-111111111111", {})
    assert "STOPPED" in (res.detail or "")
    await a.close()


async def test_ovh_price_failure_not_cached():
    """A regional-listing failure must not blank that (project, region)'s
    prices for 24h - the retry after recovery gets the real price."""
    fail = {"fail": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/time"):
            return httpx.Response(200, json=int(__import__("time").time()))
        if fail["fail"]:
            return httpx.Response(500, json={})
        return httpx.Response(200, json=[
            {"id": "iid", "pricings": [
                {"type": "month", "price": {"value": 27.72, "currencyCode": "EUR",
                                            "includeVat": False}}]}])

    a = OvhAdapter(1, "acct", "ak:as:ck", http=httpx.MockTransport(handler))
    assert await a._monthly_price("proj", "GRA", "iid") is None
    fail["fail"] = False
    p = await a._monthly_price("proj", "GRA", "iid")
    assert p is not None and str(p.amount).startswith("27.72")
    await a.close()


async def test_ovh_catalog_missing_price_skips_not_zero():
    """A pricing entry without a price must not publish 0.00 EUR - the plan
    is skipped (price not exposed / not sold), never invented."""
    from server.adapters.ovh import OvhCatalogAdapter
    a = OvhCatalogAdapter()
    pricings = [{"capacities": ["renew"], "interval": 1,
                 "intervalUnit": "month", "price": None}]
    assert a._monthly_price(pricings) is None
    await a.close()


# -- hetzner ------------------------------------------------------------------

async def test_hetzner_unsupported_action_kind_is_refused_cleanly():
    """firewall passes api.py's capability check (all caps advertised) but
    is not a /servers/{id}/actions verb - must raise UnsupportedAction, not
    a raw KeyError."""
    from server.adapters.base import UnsupportedAction

    transport = httpx.MockTransport(
        lambda r: httpx.Response(404, json={}))
    a = HetznerAdapter(1, "acct", TEST_TOKEN, http=transport)
    with pytest.raises(UnsupportedAction):
        await a.perform_action(Capability.FIREWALL, "11111111", {})
    await a.close()


async def test_hetzner_catalog_failure_degrades_not_kills():
    """A /server_types outage must not kill the fleet view - servers are
    served with monthly_price=None (not exposed)."""
    from conftest import mock_hetzner_transport

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/servers"):
            return httpx.Response(200, json={
                "servers": [{"id": 11111111, "name": "s", "status": "running",
                             "server_type": {"name": "cx22"},
                             "location": {"name": "fsn1", "country": "DE"}}],
                "meta": {"pagination": {"last_page": 1}}})
        return httpx.Response(500, json={})  # server_types 5xx

    a = HetznerAdapter(1, "acct", TEST_TOKEN, http=httpx.MockTransport(handler))
    servers = await a.list_servers()
    assert len(servers) == 1
    assert servers[0].monthly_price is None  # degraded, not crashed
    await a.close()


async def test_hetzner_delete_polls_returned_action():
    """DELETE returns an action like every endpoint - polling it is the
    base protocol; the raw 2xx was never success."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(path)
        if request.method == "DELETE":
            return httpx.Response(200, json={"action": {"id": 999, "status": "running"}})
        if path.startswith("/v1/actions/"):
            return httpx.Response(200, json={"action": {"id": 999, "status": "success"}})
        return httpx.Response(404, json={})

    a = HetznerAdapter(1, "acct", TEST_TOKEN, http=httpx.MockTransport(handler))
    res = await a.perform_action(Capability.DELETE, "11111111", {})
    assert "success" in (res.detail or "")
    assert any(p.startswith("/v1/actions/") for p in calls), "must poll the action"
    await a.close()


async def test_hetzner_no_overage_fallback_invention():
    """Missing catalog overage price -> None (not exposed), never the old
    1.19 EUR/TB constant the provider never stated for this server."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/servers"):
            return httpx.Response(200, json={
                "servers": [{"id": 11111111, "name": "s", "status": "running",
                             "server_type": {"name": "cx22"},
                             "location": {"name": "fsn1", "country": "DE"},
                             "included_traffic": 1000, "outgoing_traffic": 2000}],
                "meta": {"pagination": {"last_page": 1}}})
        # server_types: price WITHOUT price_per_tb_traffic
        return httpx.Response(200, json={
            "server_types": [{"name": "cx22", "prices": [
                {"location": "fsn1", "price_monthly": {"gross": "3.92"},
                 "included_traffic": 1000}]}],
            "meta": {"pagination": {"last_page": 1}}})

    a = HetznerAdapter(1, "acct", TEST_TOKEN, http=httpx.MockTransport(handler))
    servers = await a.list_servers()
    assert servers[0].allowance.overage_price is None  # not exposed, not 1.19
    await a.close()


async def test_hetzner_page_cap_exhausted_raises():
    """More pages than the paginator's cap must raise - a truncated fleet is
    silent data loss, never a partial success."""
    # last_page far beyond the cap: the break never fires, the for-else raises
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "servers": [{"id": 1, "name": f"s{request.url.params.get('page')}",
                         "status": "running"}],
            "meta": {"pagination": {"last_page": 999}}})

    a = HetznerAdapter(1, "acct", TEST_TOKEN, http=httpx.MockTransport(handler))
    with pytest.raises(AdapterError, match="pagination cap"):
        await a.list_servers()
    await a.close()


# -- gcore --------------------------------------------------------------------

async def test_gcore_zero_per_minute_price_is_published_not_none():
    """A real 0 USD/min flavor is a PUBLISHED price (free tier) - rendering
    it as not-published inverts the data-honesty contract."""
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/regions"):
            return httpx.Response(200, json={"count": 1, "results": [
                {"id": 7, "name": "Frankfurt", "technical_name": "FRN-2"}]})
        if "basic_vms/flavors" in url:
            return httpx.Response(200, json={"count": 1, "results": [
                {"name": "free-tier", "vcpus": 1, "ram": 1, "disk": 25}]})
        if "vcc-items" in url:
            return httpx.Response(200, json=[
                {"name": "free-tier", "vmType": "standard", "priceMinute": 0}])
        return httpx.Response(404)

    a = GcoreCatalogAdapter(http=httpx.MockTransport(handler))
    plans = await a.list_plans()
    p = plans[0]
    assert p.price_monthly is not None, "0 USD/min is a price, not 'missing'"
    assert str(p.price_monthly.amount) == "0"
    assert p.price_hourly is not None and str(p.price_hourly.amount) == "0"
    await a.close()
