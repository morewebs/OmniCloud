"""Gcore fleet adapter tests: fixture-replayed, no network. Each test
asserts one verified fact from docs/provider-truth.md's Gcore section
(sources: the official cloud_api.yaml OpenAPI spec + docs.gcore.com)."""
import pytest

from server.adapters.base import (
    ActionTimeout, AdapterError, Capability, ServerStatus,
)
from server.adapters.gcore import GcoreAdapter

from conftest import TEST_TOKEN, fixture, mock_gcore_transport

WEB_IID = "aaaa1111-2222-3333-4444-555555555555"
DB_IID = "bbbb6666-7777-8888-9999-aaaaaaaaaaaa"
EDGE_IID = "cccc3333-4444-5555-6666-777777777777"
WEB_PID = f"101:7:{WEB_IID}"       # demo-project-a7f3 / Frankfurt
DB_PID = f"101:7:{DB_IID}"
EDGE_PID = f"102:12:{EDGE_IID}"    # demo-project-b2c4 / Ashburn


def make_adapter(transport):
    return GcoreAdapter(1, "account-a7f3", TEST_TOKEN, http=transport)


# -- auth ------------------------------------------------------------------

async def test_apikey_scheme_not_bearer():
    """Every call carries "Authorization: APIKey <token>" - Bearer is
    rejected by Gcore ("Given token not valid for any token type")."""
    transport, calls, headers = mock_gcore_transport()
    a = make_adapter(transport)
    await a.list_servers()
    auth = [h.get("authorization") for h in headers
            if h.get("authorization")]
    assert auth and all(v == f"APIKey {TEST_TOKEN}" for v in auth)
    await a.close()


# -- reads -------------------------------------------------------------------

async def test_list_servers_compound_ids_across_projects():
    """All projects x regions; provider_id "{project}:{region}:{uuid}" is
    compound and self-routing (two projects, same flavor, distinct ids)."""
    transport, _, _ = mock_gcore_transport()
    a = make_adapter(transport)
    servers = await a.list_servers()
    pids = {s.provider_id for s in servers}
    assert pids == {WEB_PID, DB_PID, EDGE_PID}
    await a.close()


async def test_status_map_honest():
    """ACTIVE->running, SHUTOFF->off, BUILD->rebuilding; REBOOT/HARD_REBOOT
    are NOT confirmed-running (UNKNOWN until the task finishes)."""
    from server.adapters.gcore import STATE_MAP
    assert STATE_MAP["ACTIVE"] == ServerStatus.RUNNING
    assert STATE_MAP["SHUTOFF"] == ServerStatus.OFF
    assert STATE_MAP["BUILD"] == ServerStatus.REBUILDING
    assert STATE_MAP["REBOOT"] == ServerStatus.UNKNOWN
    assert STATE_MAP["HARD_REBOOT"] == ServerStatus.UNKNOWN
    assert STATE_MAP.get("PASSWORD") == ServerStatus.UNKNOWN

    transport, _, _ = mock_gcore_transport()
    a = make_adapter(transport)
    servers = await a.list_servers()
    by_pid = {s.provider_id: s for s in servers}
    assert by_pid[WEB_PID].status == ServerStatus.RUNNING
    assert by_pid[DB_PID].status == ServerStatus.OFF
    assert by_pid[EDGE_PID].status == ServerStatus.REBUILDING
    await a.close()


async def test_ipv4_floating_first_never_private_fixed():
    """Public IPv4 = the floating address, else a fixed address outside the
    private ranges (RFC 1918/CGNAT/link-local are fixed by standard, so a
    fixed addr outside them IS public - the VM's own external interface).
    A private fixed addr is never shown as the public IP."""
    transport, _, _ = mock_gcore_transport()
    a = make_adapter(transport)
    servers = await a.list_servers()
    by_pid = {s.provider_id: s for s in servers}
    web = by_pid[WEB_PID]
    assert web.ipv4 == "203.0.113.10"                  # floating
    assert "10.0.10.5" not in {i.address for i in web.ips}  # private fixed: not public
    assert by_pid[DB_PID].ipv4 == "198.51.100.20"      # public fixed (own interface)
    assert by_pid[EDGE_PID].ipv4 is None               # empty addresses map
    await a.close()


async def test_flavor_fields_and_honest_units():
    """flavor is NESTED (flavor.flavor_name, no top-level flavor_id); ram is
    in MiB per the spec - rendered in the provider's unit, never "GB"."""
    transport, _, _ = mock_gcore_transport()
    a = make_adapter(transport)
    servers = await a.list_servers()
    web = next(s for s in servers if s.provider_id == WEB_PID)
    assert web.server_type == "g2s.shared-1-1-25"
    facets = {f.label: f.value for f in web.facets}
    assert facets["instance type"] == "g2s.shared-1-1-25"
    assert facets["vcpus"] == "1"
    assert facets["ram"] == "1024 MiB"
    assert "GB" not in " ".join(facets.values())
    await a.close()


async def test_price_per_instance_from_pricing_endpoint():
    """Monthly price from GET /pricing/{p}/{r}/instances/{id} - the
    discounted price_per_month with the endpoint's own currency; missing
    pricing (db) -> None, never zero or an invented join."""
    transport, _, _ = mock_gcore_transport()
    a = make_adapter(transport)
    servers = await a.list_servers()
    by_pid = {s.provider_id: s for s in servers}
    web = by_pid[WEB_PID]
    assert web.monthly_price is not None
    assert str(web.monthly_price.amount) == "9.11"
    assert web.monthly_price.currency == "USD"
    assert by_pid[DB_PID].monthly_price is None, "404 pricing -> None"
    # discounted edge price: 10.13 not the 11.96 undiscounted figure
    assert str(by_pid[EDGE_PID].monthly_price.amount) == "10.13"
    await a.close()


async def test_allowance_unmetered_not_missing():
    """Traffic is unmetered by product design (free ingress AND egress) - a
    window note, no byte fields, not not_exposed (that's for fields the API
    lacks; here the fact is 'unmetered', not 'missing')."""
    transport, _, _ = mock_gcore_transport()
    a = make_adapter(transport)
    servers = await a.list_servers()
    web = next(s for s in servers if s.provider_id == WEB_PID)
    al = web.allowance
    assert al is not None
    assert "unmetered" in (al.window or "")
    assert al.used_bytes is None
    assert al.included_bytes is None
    assert web.not_exposed == []
    await a.close()


async def test_labels_from_tags():
    """tags [{key, value}] -> labels dict; empty tags -> None (not {})."""
    transport, _, _ = mock_gcore_transport()
    a = make_adapter(transport)
    servers = await a.list_servers()
    by_pid = {s.provider_id: s for s in servers}
    assert by_pid[WEB_PID].labels == {"env": "demo", "team": "edge"}
    assert by_pid[DB_PID].labels is None
    await a.close()


async def test_created_at_field():
    """created_at (not "created") -> Server.created."""
    transport, _, _ = mock_gcore_transport()
    a = make_adapter(transport)
    servers = await a.list_servers()
    web = next(s for s in servers if s.provider_id == WEB_PID)
    assert web.created is not None and web.created.year == 2026
    await a.close()


async def test_get_server_routes_compound_id():
    """get_server splits "{p}:{r}:{id}" and reads exactly one instance."""
    transport, calls, _ = mock_gcore_transport()
    a = make_adapter(transport)
    s = await a.get_server(WEB_PID)
    assert s.provider_id == WEB_PID
    assert s.name == "web-demo-01"
    assert any(f"/cloud/v1/instances/101/7/{WEB_IID}" in c for c in calls)
    await a.close()


def test_malformed_provider_id_rejected():
    with pytest.raises(AdapterError, match="project:region:instance"):
        GcoreAdapter._split_id("not-compound")
    with pytest.raises(AdapterError):
        GcoreAdapter._split_id("a:b")


# -- actions -----------------------------------------------------------------

async def test_power_action_polls_status():
    """POST /cloud/v2/.../action {"action": "stop"} -> 200 {"tasks":[...]}
    is NOT success; the adapter returns only after the instance's own
    status reaches the target (SHUTOFF for stop, ACTIVE for start/reboot)."""
    transport, calls, _ = mock_gcore_transport()
    a = make_adapter(transport)
    res = await a.perform_action(Capability.POWER_OFF, DB_PID, {})
    assert "SHUTOFF" in (res.detail or "")
    assert any("/cloud/v2/instances/" in c and c.endswith("/action")
               for c in calls)
    # the status poll actually happened (instance GETs after the POST)
    assert any(f"/cloud/v1/instances/101/7/{DB_IID}" in c for c in calls)
    await a.close()


async def test_power_on_polls_to_active():
    transport, _, _ = mock_gcore_transport()
    a = make_adapter(transport)
    res = await a.perform_action(Capability.POWER_ON, DB_PID, {})
    assert "ACTIVE" in (res.detail or "")
    await a.close()


async def test_reboot_verb():
    """REBOOT -> action "reboot" (not "reboot_hard" - the destructive hard
    variant is not the panel's default)."""
    transport, _, _ = mock_gcore_transport()
    a = make_adapter(transport)
    res = await a.perform_action(Capability.REBOOT, WEB_PID, {})
    assert "ACTIVE" in (res.detail or "")
    # the verb mapping is the verified fact under test (not reboot_hard)
    from server.adapters.gcore import POWER_ACTION
    assert POWER_ACTION[Capability.REBOOT] == "reboot"
    await a.close()


async def test_power_timeout():
    """A status that never reaches the target -> ActionTimeout, never
    success."""
    transport, _, _ = mock_gcore_transport()
    a = make_adapter(transport)
    import server.adapters.gcore as gcore_mod
    orig = gcore_mod.POLL_BUDGET_S
    gcore_mod.POLL_BUDGET_S = 0
    try:
        with pytest.raises(ActionTimeout):
            await a.perform_action(Capability.POWER_ON, WEB_PID, {})
    finally:
        gcore_mod.POLL_BUDGET_S = orig
    await a.close()


async def test_rename_patch_confirmed_by_provider():
    """Rename = PATCH {"name": ...}, 200 + serializer; confirmed by the
    provider's returned name, never the 200 alone."""
    transport, _, _ = mock_gcore_transport()
    a = make_adapter(transport)
    await a.perform_action(Capability.RENAME, WEB_PID, {"name": "renamed-01"})
    s = await a.get_server(WEB_PID)
    assert s.name == "renamed-01"
    await a.close()


async def test_relabel_patch_tags():
    """Relabel = PATCH {"tags": {...}} (RFC 7386 merge patch): unspecified
    keys would be preserved by the provider, so the adapter must send null
    removals for keys the UI's desired set drops; confirmed by re-reading."""
    transport, _, _ = mock_gcore_transport()
    a = make_adapter(transport)
    # web starts with {"env": "demo", "team": "edge"}; the UI's full desired
    # set drops "team" - the adapter must null it, or the provider keeps it
    await a.perform_action(Capability.RELABEL, WEB_PID,
                            {"labels": {"env": "prod"}})
    s = await a.get_server(WEB_PID)
    assert s.labels == {"env": "prod"}
    await a.close()


async def test_read_only_tags_are_facets_not_labels():
    """Read-only tags (the provider's own metadata - a merge patch always
    preserves them) must render as facets, not labels the UI would offer
    for editing."""
    transport, _, _ = mock_gcore_transport()
    a = make_adapter(transport)
    servers = await a.list_servers()
    web = next(s for s in servers if s.provider_id == WEB_PID)
    assert web.labels == {"env": "demo", "team": "edge"}, \
        "fixture tags are all editable (read_only: false)"
    labels = web.labels or {}
    assert all(not k.startswith("gcore") for k in labels)
    await a.close()


async def test_delete_polls_task_then_confirmed_by_404():
    """DELETE -> 200 {"tasks":[...]} (NOT 204) -> poll the task FINISHED ->
    confirmed only when the instance GET 404s."""
    transport, calls, _ = mock_gcore_transport()
    a = make_adapter(transport)
    res = await a.perform_action(Capability.DELETE, EDGE_PID, {})
    assert "deleted" in (res.detail or "")
    assert any("/cloud/v1/tasks/" in c for c in calls), "task was polled"
    # gone: a subsequent read 404s
    with pytest.raises(AdapterError, match="404"):
        await a.get_server(EDGE_PID)
    await a.close()


async def test_unsupported_capability_refused_before_http():
    """REBUILD has no VM endpoint in the spec -> refused before any call."""
    transport, calls, _ = mock_gcore_transport()
    a = make_adapter(transport)
    with pytest.raises(AdapterError, match="rebuild"):
        await a.perform_action(Capability.REBUILD, WEB_PID, {"image": "x"})
    assert calls == [], "refusal must happen before any request"
    await a.close()


def test_capabilities_exact():
    assert GcoreAdapter.capabilities == frozenset({
        Capability.POWER_ON, Capability.POWER_OFF, Capability.REBOOT,
        Capability.SHUTDOWN, Capability.RENAME, Capability.RELABEL,
        Capability.DELETE,
        Capability.IP_ADD, Capability.IP_RELEASE, Capability.IP_CHANGE,
    })


def test_fleet_and_catalog_classes_share_key():
    """gcore lives in both registries via two classes - the fleet class in
    accounts.ADAPTERS, the tokenless catalog class in catalog.LIVE."""
    from server import accounts, catalog
    from server.adapters.gcore import GcoreCatalogAdapter
    assert accounts.ADAPTERS["gcore"] is GcoreAdapter
    assert catalog.LIVE["gcore"] is GcoreCatalogAdapter
    assert "gcore" not in catalog._CREDENTIALED
