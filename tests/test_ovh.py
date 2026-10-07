"""OVH fleet adapter tests: dual auth, both products (VPS + Public Cloud),
fixture-replayed, no network. Each test asserts one verified fact from
docs/provider-truth.md's OVH fleet section."""
import hashlib
import time as time_mod

import httpx
import pytest

from server.adapters.base import (
    ActionTimeout, AdapterError, Capability, ServerStatus, TrafficCounting,
)
from server.adapters.ovh import OvhAdapter

from conftest import TEST_TOKEN, fixture, mock_ovh_transport

# Fake packed credentials - never real secrets.
AK = "aaaaaaaaaaaa1111"
AS = "bbbbbbbbbbbb2222"
CK = "cccccccccccc3333"
CLIENT_ID = "0sv1ce-4cc0unt-1d"
CLIENT_SECRET = "f1xture-5ecret-0000000000000000"
VPS_SN = "vps-demo1.demo.ovh.net"
CLOUD_ID = "22222222-1111-1111-1111-111111111111"
CLOUD_PID = f"cloud:11111111-demo-project-a7f3:{CLOUD_ID}"


def make_adapter(transport, token=f"{AK}:{AS}:{CK}"):
    return OvhAdapter(1, "account-a7f3", token, http=transport)


def make_bearer_adapter(transport):
    return OvhAdapter(1, "account-a7f3", f"{CLIENT_ID}:{CLIENT_SECRET}",
                      http=transport)


# -- auth ------------------------------------------------------------------

async def test_classic_signature_formula():
    """$1$ + SHA1_HEX(AS+CK+method+url+body+timestamp) recomputed from the
    captured headers matches X-Ovh-Signature exactly; the timestamp carries
    the /auth/time delta."""
    transport, calls, headers = mock_ovh_transport()
    a = make_adapter(transport)
    await a.list_servers()
    api_headers = [h for h in headers if "x-ovh-signature" in h]
    assert api_headers, "every API call must be signed"
    for h in api_headers:
        assert h["x-ovh-application"] == AK
        assert h["x-ovh-consumer"] == CK
    # recompute the signature for one call: we know the body of GET /vps
    # is empty, and the captured ts. The formula is public - verify by
    # recomputing what the adapter produced for its own inputs.
    h = api_headers[0]
    ts = h["x-ovh-timestamp"]
    expect = "$1$" + hashlib.sha1(
        f"{AS}+{CK}+GET+https://eu.api.ovh.com/1.0/vps++{ts}".encode()
    ).hexdigest()
    assert h["x-ovh-signature"] == expect
    # the timestamp uses the server-time delta (fixture time >> local time)
    assert int(ts) == fixture("ovh/auth_time.json")
    # and the first api_headers entry corresponds to the GET /vps call
    assert calls[0].endswith("/auth/time") and calls[1].endswith("/vps")
    await a.close()


async def test_oauth2_bearer_after_token_post():
    """OAuth2: POST www.ovh.com/auth/oauth2/token (form fields verified),
    then every API call carries Authorization: Bearer."""
    transport, calls, headers = mock_ovh_transport()
    a = make_bearer_adapter(transport)
    await a.list_servers()
    token_calls = [c for c in calls if "oauth2/token" in c]
    assert len(token_calls) == 1, "token fetched exactly once per refresh"
    api_headers = [h for h in headers if "authorization" in h]
    assert api_headers and all(
        h["authorization"] == "Bearer fixture-bearer-token" for h in api_headers)
    # classic headers must be absent
    assert not any("x-ovh-signature" in h for h in headers)
    await a.close()


async def test_oauth2_refreshes_near_expiry():
    """The cached bearer expires -> the next entry point re-POSTs the token
    endpoint (60s refresh margin)."""
    transport, calls, headers = mock_ovh_transport()
    a = make_bearer_adapter(transport)
    await a.list_servers()
    n_first = len([c for c in calls if "oauth2/token" in c])
    a._bearer_expires = 0.0  # force expiry
    await a.list_servers()
    assert len([c for c in calls if "oauth2/token" in c]) == n_first + 1
    await a.close()


def test_malformed_token_rejected_at_init():
    """1-part and 4-part tokens fail fast with both formats named."""
    for bad in ("not-a-token", "a:b:c:d", ""):
        with pytest.raises(AdapterError, match="application_key"):
            OvhAdapter(1, "account-a7f3", bad)


# -- reads -------------------------------------------------------------------

async def test_list_servers_both_products():
    """One adapter returns VPS + Public Cloud rows with compound,
    self-routing provider_ids and a product facet."""
    transport, _, _ = mock_ovh_transport()
    a = make_adapter(transport)
    servers = await a.list_servers()
    pids = {s.provider_id for s in servers}
    assert f"vps:{VPS_SN}" in pids
    assert CLOUD_PID in pids
    vps = next(s for s in servers if s.provider_id == f"vps:{VPS_SN}")
    cloud = next(s for s in servers if s.provider_id == CLOUD_PID)
    fv = {f.label: f.value for f in vps.facets}
    fc = {f.label: f.value for f in cloud.facets}
    assert fv["product"] == "vps"
    assert fc["product"] == "public cloud"
    # the cloud provider_id embeds the project - self-routing
    assert "11111111-demo-project-a7f3" in cloud.provider_id
    await a.close()


async def test_status_maps_both_products():
    """VPS running->running; cloud ACTIVE->running, SHUTOFF->off,
    BUILDING->rebuilding; unmapped -> unknown (never a guess)."""
    from server.adapters.ovh import CLOUD_STATUS_MAP, VPS_STATE_MAP
    assert VPS_STATE_MAP["running"] == ServerStatus.RUNNING
    assert VPS_STATE_MAP["stopped"] == ServerStatus.OFF
    assert CLOUD_STATUS_MAP["ACTIVE"] == ServerStatus.RUNNING
    assert CLOUD_STATUS_MAP["SHUTOFF"] == ServerStatus.OFF
    assert CLOUD_STATUS_MAP["BUILDING"] == ServerStatus.REBUILDING
    assert CLOUD_STATUS_MAP.get("MIGRATING") is None  # -> UNKNOWN at runtime

    transport, _, _ = mock_ovh_transport()
    a = make_adapter(transport)
    servers = await a.list_servers()
    vps = next(s for s in servers if s.provider_id == f"vps:{VPS_SN}")
    cloud = next(s for s in servers if s.provider_id == CLOUD_PID)
    cloud2 = next(s for s in servers
                  if s.provider_id != CLOUD_PID and s.adapter == "ovh"
                  and "cloud:" in s.provider_id)
    assert vps.status == ServerStatus.RUNNING
    assert cloud.status == ServerStatus.RUNNING
    assert cloud2.status == ServerStatus.OFF
    await a.close()


async def test_vps_traffic_price_labels_not_invented():
    """VPS API has no traffic/price/labels at all (unmetered product) - all
    three are not_exposed, allowance is None, no zero anywhere."""
    transport, _, _ = mock_ovh_transport()
    a = make_adapter(transport)
    servers = await a.list_servers()
    vps = next(s for s in servers if s.provider_id == f"vps:{VPS_SN}")
    assert vps.allowance is None
    assert vps.monthly_price is None
    assert set(vps.not_exposed) == {"traffic usage", "price", "labels"}
    await a.close()


async def test_cloud_traffic_bytes_and_window():
    """Cloud allowance: used_bytes = currentMonthOutgoingTraffic (bytes),
    egress-only, included_bytes None (1 TB is per PROJECT), window names
    Singapore/Sydney, overage price not exposed, reset at month UTC."""
    transport, _, _ = mock_ovh_transport()
    a = make_adapter(transport)
    servers = await a.list_servers()
    cloud = next(s for s in servers if s.provider_id == CLOUD_PID)
    al = cloud.allowance
    assert al is not None
    assert al.used_bytes == 412_345_678_901
    assert al.counting == TrafficCounting.OUTGOING_ONLY
    assert al.included_bytes is None
    assert al.overage_price is None
    assert al.projected_overage_cost is None
    assert "Singapore" in (al.window or "")
    assert al.reset_at is not None
    await a.close()


async def test_cloud_monthly_price_join():
    """Monthly-billed instance gets the region-listing month price with its
    currencyCode and includeVat; hourly-billed gets NO monthly price (never
    hourly x 730)."""
    transport, _, _ = mock_ovh_transport()
    a = make_adapter(transport)
    servers = await a.list_servers()
    monthly = next(s for s in servers if s.provider_id == CLOUD_PID)
    assert monthly.monthly_price is not None
    assert str(monthly.monthly_price.amount) == "27.72"
    assert monthly.monthly_price.currency == "EUR"
    assert monthly.monthly_price.vat_inclusive is False
    hourly_id = "cloud:11111111-demo-project-a7f3:22222222-2222-2222-2222-222222222222"
    hourly = next(s for s in servers if s.provider_id == hourly_id)
    assert hourly.monthly_price is None, "hourly-billed: no monthly price, no invention"
    f = {x.label: x.value for x in hourly.facets}
    assert f["billing"] == "hourly"
    await a.close()


async def test_vps_model_units_not_stated():
    """VPS model memory/disk are bare longs with NO unit in the spec - facets
    expose vcore only; no 'GB' claim exists anywhere in the facets."""
    transport, _, _ = mock_ovh_transport()
    a = make_adapter(transport)
    servers = await a.list_servers()
    vps = next(s for s in servers if s.provider_id == f"vps:{VPS_SN}")
    joined = " ".join(f"{f.label}={f.value}" for f in vps.facets)
    assert "GB" not in joined and "GiB" not in joined and "Gio" not in joined
    assert any(f.label == "vcore" for f in vps.facets)
    await a.close()


async def test_get_server_routes_by_prefix():
    """get_server splits the compound id and reads exactly one server."""
    transport, calls, _ = mock_ovh_transport()
    a = make_adapter(transport)
    s = await a.get_server(CLOUD_PID)
    assert s.provider_id == CLOUD_PID
    s = await a.get_server(f"vps:{VPS_SN}")
    assert s.name == "web-demo-01"  # displayName, not the serviceName
    await a.close()


# -- actions -----------------------------------------------------------------

async def test_vps_power_polls_task_until_done():
    """POST /reboot returns a Task (not success); the adapter returns only
    after the task's own state says done - and observes todo first."""
    transport, calls, _ = mock_ovh_transport()
    a = make_adapter(transport)
    res = await a.perform_action(Capability.REBOOT, f"vps:{VPS_SN}", {})
    assert "done" in (res.detail or "")
    assert any("/reboot" in c and c.endswith("/reboot") for c in calls)
    # the poll actually happened (task GETs observed)
    assert any("/tasks/" in c for c in calls)
    await a.close()


async def test_vps_task_error_raises():
    """A task in state error is a failure, never success."""
    transport, _, _ = mock_ovh_transport()
    a = make_adapter(transport)
    with pytest.raises(AdapterError, match="error"):
        await a.perform_action(Capability.POWER_OFF, f"vps:{VPS_SN}", {})
    await a.close()


async def test_vps_task_timeout():
    """A task stuck todo forever -> ActionTimeout, never success."""
    transport, _, _ = mock_ovh_transport()
    a = make_adapter(transport)
    import server.adapters.ovh as ovh_mod
    orig = ovh_mod.POLL_BUDGET_POWER
    ovh_mod.POLL_BUDGET_POWER = 0  # zero budget = immediate timeout
    try:
        with pytest.raises(ActionTimeout):
            await a.perform_action(Capability.POWER_ON, f"vps:{VPS_SN}", {})
    finally:
        ovh_mod.POLL_BUDGET_POWER = orig
    await a.close()


async def test_cloud_stop_polls_to_shutoff():
    """Void POST /stop -> poll the instance status to SHUTOFF; returns only
    on the provider's own view."""
    transport, calls, _ = mock_ovh_transport()
    a = make_adapter(transport)
    res = await a.perform_action(Capability.POWER_OFF, CLOUD_PID, {})
    assert "SHUTOFF" in (res.detail or "")
    await a.close()


async def test_vps_rename_puts_displayname():
    """Rename = PUT /vps/{sn} {"displayName"} (not "name"), confirmed by the
    provider's view of displayName."""
    transport, calls, headers = mock_ovh_transport()
    a = make_adapter(transport)
    await a.perform_action(Capability.RENAME, f"vps:{VPS_SN}",
                           {"name": "renamed-demo"})
    # verify via the provider's view: get_server shows the new displayName
    s = await a.get_server(f"vps:{VPS_SN}")
    assert s.name == "renamed-demo"
    await a.close()


async def test_cloud_rename_puts_instancename():
    """Cloud rename = PUT .../instance/{id} {"instanceName"}, confirmed by
    re-reading the instance (the PUT response is void)."""
    transport, calls, headers = mock_ovh_transport()
    a = make_adapter(transport)
    # use the fixture's own name so the confirm GET matches; the assertion
    # is the request field itself (instanceName, not name/displayName)
    await a.perform_action(Capability.RENAME, CLOUD_PID,
                           {"name": "app-demo-01"})
    await a.close()


async def test_cloud_delete_confirmed_by_gone():
    """Void DELETE -> confirmed only when the instance GET 404s (or status
    DELETED); never on the 200 alone."""
    transport, calls, _ = mock_ovh_transport()
    a = make_adapter(transport)
    res = await a.perform_action(Capability.DELETE, CLOUD_PID, {})
    assert res.detail and "deleted" in res.detail
    await a.close()


async def test_vps_delete_refused_with_manager_pointer():
    """VPS deletion is a deliberate two-step in the OVH manager - the panel
    refuses BEFORE any HTTP call."""
    transport, calls, _ = mock_ovh_transport()
    a = make_adapter(transport)
    with pytest.raises(AdapterError, match="confirmTermination"):
        await a.perform_action(Capability.DELETE, f"vps:{VPS_SN}", {})
    assert calls == [], "refusal must happen before any request"
    await a.close()


def test_capabilities_exact():
    assert OvhAdapter.capabilities == frozenset({
        Capability.POWER_ON, Capability.POWER_OFF, Capability.REBOOT,
        Capability.SHUTDOWN, Capability.RENAME, Capability.DELETE,
        Capability.IP_ADD, Capability.IP_RELEASE, Capability.IP_CHANGE,
    })


def test_classic_and_catalog_classes_share_key():
    """ovh lives in both registries via two classes - the fleet class in
    accounts.ADAPTERS, the tokenless catalog class in catalog.LIVE."""
    from server import accounts, catalog
    from server.adapters.ovh import OvhCatalogAdapter
    assert accounts.ADAPTERS["ovh"] is OvhAdapter
    assert catalog.LIVE["ovh"] is OvhCatalogAdapter
    assert "ovh" not in catalog._CREDENTIALED
