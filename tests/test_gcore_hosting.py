"""Gcore Hosting (BILLmanager 6) adapter against a stateful panel double.
No network: every call goes through mock_gcore_hosting_transport."""
from decimal import Decimal

import pytest

from server.adapters import gcore_hosting as gh
from server.adapters.base import (AdapterError, Capability, PaymentRequired,
                                  ServerStatus)
from server.adapters.gcore_hosting import GcoreHostingAdapter, parse_money

from conftest import GCORE_HOSTING_CRED, mock_gcore_hosting_transport


def _adapter(**kw):
    transport, calls, state = mock_gcore_hosting_transport(**kw)
    return GcoreHostingAdapter(1, "panel-a7f3", GCORE_HOSTING_CRED, http=transport), calls, state


@pytest.fixture(autouse=True)
def fast_polls(monkeypatch):
    monkeypatch.setattr(gh, "POLL_INTERVAL_S", 0)


async def test_lists_servers_with_ips_and_skips_deleted():
    a, calls, _ = _adapter()
    servers = {s.provider_id: s for s in await a.list_servers()}
    assert set(servers) == {"5101", "5102"}  # 5099 is item_status 4 (deleted)
    s = servers["5101"]
    assert s.name == "srv-ams-01" and s.ipv4 == "203.0.113.10"
    assert [(i.address, i.primary) for i in s.ips] == [
        ("203.0.113.10", True), ("203.0.113.11", False)]
    # a billing panel knows the service state, not the VM's power state
    assert s.status is ServerStatus.UNKNOWN and "power_state" in s.not_exposed
    assert {f.label: f.value for f in s.facets}["paid until"] == "2026-11-01"
    assert servers["5102"].status is ServerStatus.OFF  # suspended
    # single-row service.ip (bare object) parses; no_delete marks the primary
    assert servers["5102"].ips[0].primary
    # login is a POST: the password never rides in a query string
    assert calls[0][:2] == ("POST", "auth")


async def test_expired_session_relogs_once():
    a, calls, _ = _adapter(expire_session_once=True)
    await a.list_servers()
    assert [c[1] for c in calls].count("auth") == 2


async def test_wrong_password_is_a_clear_error():
    import json
    transport, _, _ = mock_gcore_hosting_transport()
    cred = json.loads(GCORE_HOSTING_CRED) | {"password": "wrong-pw"}
    a = GcoreHostingAdapter(1, "x", json.dumps(cred), http=transport)
    with pytest.raises(AdapterError, match="login failed: Invalid username"):
        await a.list_servers()


def test_plain_http_panel_url_is_refused():
    import json
    cred = json.loads(GCORE_HOSTING_CRED) | {"url": "http://panel.example.test/billmgr"}
    with pytest.raises(AdapterError, match="https"):
        GcoreHostingAdapter(1, "x", json.dumps(cred))


async def test_add_ip_orders_once_and_waits_for_the_address():
    a, calls, _ = _adapter(ip_appears_after=2)
    ip = await a.add_ip("5101")
    assert ip.address == "203.0.113.30" and not ip.primary
    orders = [c for c in calls if c[1] == "service.ip.edit" and c[2].get("sok") == "ok"]
    assert len(orders) == 1  # a purchase is never retried
    assert orders[0][0] == "POST" and orders[0][2]["type"] == "7"


async def test_add_ip_behind_a_payment_raises_payment_required():
    a, _, _ = _adapter(payment_required=True)
    with pytest.raises(PaymentRequired) as e:
        await a.add_ip("5101")
    assert e.value.order_ref == "BO-77" and e.value.pay_url.startswith("https://")


async def test_add_ip_that_never_appears_times_out_not_succeeds(monkeypatch):
    monkeypatch.setattr(gh, "POLL_BUDGET_S", 0)
    a, _, _ = _adapter(ip_appears_after=99)
    with pytest.raises(AdapterError, match="not assigned yet"):
        await a.add_ip("5101")


async def test_release_ip_confirms_gone_and_refuses_primary():
    a, _, state = _adapter()
    await a.release_ip("5101", "203.0.113.11")
    assert [i.address for i in await a.list_ips("5101")] == ["203.0.113.10"]
    with pytest.raises(AdapterError, match="primary"):
        await a.release_ip("5101", "203.0.113.10")
    await a.release_ip("5101", "203.0.113.250")  # already gone: no error


async def test_set_password_and_delete():
    a, calls, state = _adapter()
    res = await a.perform_action(Capability.SET_PASSWORD, "5101", {"password": "n3w-root-pw"})
    assert "accepted" in res.detail and state["passwords"]["5101"] == "n3w-root-pw"
    with pytest.raises(AdapterError):
        await a.perform_action(Capability.SET_PASSWORD, "5101", {"password": "short"})
    res = await a.perform_action(Capability.DELETE, "5102", {})
    assert "gone" in res.detail
    assert "5102" not in {s.provider_id for s in await a.list_servers()}


async def test_billing_balance_payments_and_renewals():
    a, _, _ = _adapter()
    b = await a.get_billing()
    assert b.balance.amount == Decimal("12.50") and b.balance.currency == "EUR"
    by_id = {i.id: i for i in b.invoices}
    assert by_id["P-0302"].status == "new" and by_id["P-0302"].open_amount.amount == Decimal("9.00")
    assert by_id["P-0301"].status == "paid" and by_id["P-0301"].open_amount.amount == 0
    assert {r.provider_id: r.auto for r in b.renewals} == {"5101": True, "5102": False}


async def test_billing_without_balance_access_reads_not_exposed():
    a, _, _ = _adapter(subaccount_denied=True)
    b = await a.get_billing()
    assert b.balance is None and "balance" in b.not_exposed


async def test_ip_cost_says_each_change_is_a_purchase():
    a, _, _ = _adapter()
    cost = await a.ip_cost("5101")
    assert cost.per == "purchase" and cost.price is None


# -- the field shapes hosting.gcore.com actually sends (2026-10) ----------------

async def test_live_status_shapes_and_daily_billing():
    a, _, _ = _adapter(live=True)
    servers = {s.provider_id: s for s in await a.list_servers()}
    # item_status {"$orig": "4", "$": "Deleted"} is deleted, not a server
    assert set(servers) == {"6201", "6202"}
    facets = {f.label: f.value for f in servers["6201"].facets}
    # item_status missing: the code comes from item_real_status
    assert facets["service"] == "active"
    # "Daily charges" is not a date - the real one is real_expiredate
    assert facets["billing"] == "daily from balance" and facets["paid until"] == "2026-10-08"
    assert {f.label: f.value for f in servers["6202"].facets}["service"] == "active"


async def test_live_ips_carry_gateway_and_prefix():
    a, _, _ = _adapter(live=True)
    ips = {i.address: i for i in await a.list_ips("6201")}
    assert ips["203.0.113.40"].primary and not ips["198.51.100.41"].primary
    # an extra IP from another subnet needs its own gateway on the server
    assert (ips["198.51.100.41"].gateway, ips["198.51.100.41"].prefix) == ("198.51.100.1", 24)


async def test_ip_cost_on_a_daily_server_is_the_daily_add_on_charge():
    a, _, _ = _adapter(live=True)
    cost = await a.ip_cost("6201")
    assert cost.per == "day" and cost.price.amount == Decimal("0.0774")
    assert (await a.ip_cost("6202")).per == "purchase"  # monthly server


async def test_daily_server_renews_automatically():
    a, _, _ = _adapter(live=True)
    by_id = {r.provider_id: r for r in (await a.get_billing()).renewals}
    assert by_id["6201"].auto is True and by_id["6201"].date.date().isoformat() == "2026-10-08"
    assert by_id["6202"].auto is True  # autoprolong "Month"
    assert "6203" not in by_id


@pytest.mark.parametrize("text,amount,cur", [
    ("5.00 EUR", "5.00", "EUR"), ("€5.00", "5.00", "EUR"), ("5,00 EUR", "5.00", "EUR"),
    ("12 USD", "12", "USD"),
])
def test_parse_money(text, amount, cur):
    m = parse_money(text)
    assert m.amount == Decimal(amount) and m.currency == cur


def test_parse_money_without_currency_is_not_a_price():
    assert parse_money("5.00") is None and parse_money("") is None


# -- the panel URL never reaches the panel's own network ---------------------

@pytest.mark.parametrize("url", [
    "http://panel.example.test/billmgr",            # plain http: password in clear
    "https://user:pw@panel.example.test/billmgr",   # userinfo
    "https://panel.example.test:8443/billmgr",      # non-default port
    "https://127.0.0.1/billmgr", "https://10.0.0.5/billmgr",
    "https://169.254.169.254/latest", "https://[::1]/billmgr",
])
def test_bad_panel_urls_refused_at_construction(url):
    import json
    cred = json.loads(GCORE_HOSTING_CRED) | {"url": url}
    with pytest.raises(AdapterError):
        GcoreHostingAdapter(1, "x", json.dumps(cred))


@pytest.mark.parametrize("addr", ["127.0.0.1", "10.1.2.3", "169.254.169.254",
                                  "192.168.1.1", "::1", "::ffff:10.0.0.1"])
async def test_host_resolving_internally_never_gets_the_password(monkeypatch, addr):
    async def internal(host):
        return ["203.0.113.250", addr]  # one bad address is enough
    monkeypatch.setattr(gh, "resolve", internal)
    a, calls, _ = _adapter()
    with pytest.raises(AdapterError, match="internal address"):
        await a.list_servers()
    assert calls == []  # nothing - not even the login - was sent


def test_account_create_refuses_internal_panel_url():
    from server import accounts
    with pytest.raises(ValueError, match="internal address"):
        accounts.pack_credential("gcore_hosting", None, {
            "url": "https://127.0.0.1/billmgr", "username": "u", "password": "p"})


async def test_non_https_pay_link_is_dropped(monkeypatch):
    from server.adapters.base import Invoice, PaymentRequired
    assert PaymentRequired("x", "1", "javascript:alert(1)").pay_url is None
    assert PaymentRequired("x", "1", "https://pay.example.test/1").pay_url.startswith("https://")
    assert Invoice(id="1", url="data:text/html,x").url is None
