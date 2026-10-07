"""Real orders: an order bound to a purchases-enabled account executes
through adapter.provision (and buys its extra IPs on the new server); an
unpaid provider order parks in awaiting_payment; nothing real happens for
accounts with purchases off."""
import json
import time

import httpx
import pytest

from server import accounts, catalog, db, orders
from server.adapters import hetzner
from server.adapters.base import AdapterError, IpAddress, PaymentRequired
from server.adapters.hetzner import HetznerAdapter
from server.adapters.ovh import OvhAdapter

from conftest import FakeAdapter, TEST_TOKEN, fixture


class Provisioner(FakeAdapter):
    calls: list = []
    mode = "ok"

    async def provision(self, plan_name, location, options):
        Provisioner.calls.append(("provision", plan_name, location, options.get("hostname")))
        if Provisioner.mode == "unpaid":
            raise PaymentRequired("server", "ORD-7", "https://pay.example.test/ORD-7")
        if Provisioner.mode == "fail":
            raise AdapterError("out of stock in this location")
        return "srv-new-1"

    async def add_ip(self, server_id):
        Provisioner.calls.append(("add_ip", server_id))
        return IpAddress(address=f"203.0.113.{100 + len(Provisioner.calls)}")


@pytest.fixture
def plan(monkeypatch):
    monkeypatch.setitem(accounts.ADAPTERS, "fake", Provisioner)
    Provisioner.calls, Provisioner.mode = [], "ok"
    from server.adapters.base import IpOffer, Money, Plan
    catalog.store("fake", [Plan(adapter="fake", name="vm-1", location="loc-1",
                                price_monthly=Money(amount="5.00", currency="EUR"),
                                extra_ip=IpOffer(kind="floating", included=1, limit=2,
                                                 price=Money(amount="1.00", currency="EUR")))],
                  source="live", last_verified=None)
    return accounts.create_account("fake", "acct-a7f3", "tok-0000abcd")


def _run(order_id, uid):
    orders.confirm(order_id, uid)
    orders.execute(order_id, uid)
    for _ in range(100):
        if orders.get_order(order_id)["status"] not in ("executing", "confirmed"):
            break
        time.sleep(0.05)
    return orders.get_order(order_id)


def test_account_with_purchases_off_stays_prototype(plan, uid):
    oid = orders.create_order(uid, "fake", "vm-1", "loc-1", {"account_id": plan})
    o = _run(oid, uid)
    assert o["mode"] == "prototype" and o["status"] == "provisioned"
    assert Provisioner.calls == []  # nothing reached the provider


def test_real_order_provisions_and_buys_extra_ips(plan, uid):
    accounts.set_purchases_enabled(plan, True)
    oid = orders.create_order(uid, "fake", "vm-1", "loc-1",
                              {"account_id": plan, "hostname": "edge-01", "extra_ips": 2})
    o = _run(oid, uid)
    assert o["mode"] == "real" and o["status"] == "provisioned"
    assert o["resulting_provider_id"] == "srv-new-1"
    assert [c[0] for c in Provisioner.calls] == ["provision", "add_ip", "add_ip"]
    assert "extra IPs" in o["events"][-1]["detail"]


def test_unpaid_provider_order_awaits_payment(plan, uid):
    accounts.set_purchases_enabled(plan, True)
    Provisioner.mode = "unpaid"
    o = _run(orders.create_order(uid, "fake", "vm-1", "loc-1", {"account_id": plan}), uid)
    assert o["status"] == "awaiting_payment"
    assert o["provider_ref"] == "ORD-7" and o["pay_url"].endswith("ORD-7")


def test_provider_refusal_fails_the_order(plan, uid):
    accounts.set_purchases_enabled(plan, True)
    Provisioner.mode = "fail"
    o = _run(orders.create_order(uid, "fake", "vm-1", "loc-1", {"account_id": plan}), uid)
    assert o["status"] == "failed" and "out of stock" in o["events"][-1]["detail"]


def test_purchases_disabled_between_draft_and_execute_sends_nothing(plan, uid):
    accounts.set_purchases_enabled(plan, True)
    oid = orders.create_order(uid, "fake", "vm-1", "loc-1", {"account_id": plan})
    accounts.set_purchases_enabled(plan, False)
    o = _run(oid, uid)
    assert o["status"] == "failed" and Provisioner.calls == []


def test_order_validation(plan, uid, monkeypatch):
    with pytest.raises(orders.OrderError, match="at most 2"):
        orders.create_order(uid, "fake", "vm-1", "loc-1", {"extra_ips": 3})
    other = accounts.create_account("fake", "acct-b", "tok-0000efgh")
    with db.connect() as conn:
        conn.execute("UPDATE accounts SET adapter='hetzner' WHERE id=?", (other,))
    with pytest.raises(orders.OrderError, match="not fake"):
        orders.create_order(uid, "fake", "vm-1", "loc-1", {"account_id": other})


# -- adapter provision paths ---------------------------------------------------

async def test_hetzner_provision_needs_an_ssh_key_and_sends_once(monkeypatch):
    monkeypatch.setattr(hetzner, "POLL_INTERVAL", 0)
    posts, keys = [], {"ssh_keys": []}

    def handler(req):
        if req.url.path == "/v1/ssh_keys":
            return httpx.Response(200, json=keys | {"meta": {"pagination": {"last_page": 1}}})
        if req.url.path == "/v1/servers" and req.method == "POST":
            posts.append(json.loads(req.content))
            return httpx.Response(201, json={"server": {"id": 777}, "action": {"id": 9},
                                             "root_password": None})
        if req.url.path == "/v1/actions/9":
            return httpx.Response(200, json={"action": {"id": 9, "status": "success"}})
        return httpx.Response(404, json={})
    a = HetznerAdapter(1, "a", TEST_TOKEN, http=httpx.MockTransport(handler))
    with pytest.raises(AdapterError, match="SSH key"):
        await a.provision("cx22", "fsn1", {"image": "debian-12"})
    assert posts == []
    keys["ssh_keys"] = [{"id": 31}, {"id": 32}]
    assert await a.provision("cx22", "fsn1", {"image": "debian-12", "hostname": "edge-01"}) == "777"
    assert posts == [{"name": "edge-01", "server_type": "cx22", "location": "fsn1",
                      "image": "debian-12", "ssh_keys": [31, 32], "start_after_create": True,
                      "public_net": {"enable_ipv4": True, "enable_ipv6": True}}]


async def test_ovh_provision_is_an_unpaid_cart_order():
    posts = []

    def handler(req):
        path = req.url.path.removeprefix("/1.0")
        if path == "/auth/time":
            return httpx.Response(200, json=fixture("ovh/auth_time.json"))
        if path == "/me":
            return httpx.Response(200, json={"ovhSubsidiary": "FR"})
        if req.method == "POST":
            posts.append((path, json.loads(req.content) if req.content else None))
            if path == "/order/cart":
                return httpx.Response(200, json={"cartId": "c1"})
            if path.endswith("/vps"):
                return httpx.Response(200, json={"itemId": 5})
            if path.endswith("/checkout"):
                return httpx.Response(200, json={"orderId": 8080, "url": "https://pay.example.test/8080"})
            return httpx.Response(200, json={})
        return httpx.Response(404, json={})
    a = OvhAdapter(1, "a", "aaaaaaaaaaaa1111:bbbbbbbbbbbb2222:cccccccccccc3333",
                   http=httpx.MockTransport(handler))
    with pytest.raises(PaymentRequired) as e:
        await a.provision("vps-2025-model1", "GRA", {})
    assert e.value.order_ref == "8080"
    assert ("/order/cart/c1/item/5/configuration",
            {"label": "vps_datacenter", "value": "GRA"}) in posts
    assert dict(posts)["/order/cart/c1/checkout"]["autoPayWithPreferredPaymentMethod"] is False
