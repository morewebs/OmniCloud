"""OVH VPS additional IPs: listed with their spec type, released by
DELETE, ordered through the cart as an UNPAID order (never auto-pay)."""
import json

import httpx
import pytest

from server.adapters import ovh
from server.adapters.base import AdapterError, PaymentRequired
from server.adapters.ovh import OvhAdapter

from conftest import fixture

SN = "vps-demo1.demo.ovh.net"
CRED = "aaaaaaaaaaaa1111:bbbbbbbbbbbb2222:cccccccccccc3333"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(ovh, "POLL_INTERVAL", 0)


def mock_ovh_ips():
    ips = {"203.0.113.10": "primary", "203.0.113.80": "additional"}
    state = {"posts": []}

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path.removeprefix("/1.0")
        if path == "/auth/time":
            return httpx.Response(200, json=fixture("ovh/auth_time.json"))
        if path == f"/vps/{SN}/ips" and req.method == "GET":
            return httpx.Response(200, json=list(ips))
        if path.startswith(f"/vps/{SN}/ips/"):
            ip = path.rsplit("/", 1)[1]
            if req.method == "DELETE":
                ips.pop(ip, None)
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"ipAddress": ip, "version": "v4",
                                              "type": ips[ip]})
        if path == f"/vps/{SN}/datacenter":
            return httpx.Response(200, json={"name": "gra", "country": "fr"})
        if path == "/me":
            return httpx.Response(200, json={"ovhSubsidiary": "FR"})
        if req.method == "POST" and path.startswith("/order/cart"):
            body = json.loads(req.content) if req.content else None
            state["posts"].append((path, body))
            if path == "/order/cart":
                return httpx.Response(200, json={"cartId": "cart-1"})
            if path.endswith("/ip"):
                return httpx.Response(200, json={"itemId": 42})
            if path.endswith("/checkout"):
                return httpx.Response(200, json={"orderId": 9001,
                                                 "url": "https://pay.example.test/order/9001"})
            return httpx.Response(200, json={})
        if path == "/order/catalog/formatted/ip":
            return httpx.Response(200, json=fixture("ovh/ip_catalog.json"))
        return httpx.Response(404, json={"message": f"unmocked {path}"})

    return httpx.MockTransport(handler), ips, state


async def test_vps_ips_carry_primary_and_additional():
    t, _, _ = mock_ovh_ips()
    ips = {i.address: i for i in await OvhAdapter(1, "a", CRED, http=t)._vps_ips(SN)}
    assert ips["203.0.113.10"].primary and not ips["203.0.113.80"].primary


async def test_add_ip_creates_an_unpaid_order_never_autopays():
    t, _, state = mock_ovh_ips()
    with pytest.raises(PaymentRequired) as e:
        await OvhAdapter(1, "a", CRED, http=t).add_ip(f"vps:{SN}")
    assert e.value.order_ref == "9001" and e.value.pay_url.endswith("/9001")
    posts = dict(state["posts"])
    assert posts["/order/cart/cart-1/checkout"]["autoPayWithPreferredPaymentMethod"] is False
    configs = [b for p, b in state["posts"] if p.endswith("/configuration")]
    assert {"label": "destination", "value": SN} in configs
    assert {"label": "country", "value": "FR"} in configs


async def test_release_additional_refuse_primary():
    t, ips, _ = mock_ovh_ips()
    a = OvhAdapter(1, "a", CRED, http=t)
    await a.release_ip(f"vps:{SN}", "203.0.113.80")
    assert "203.0.113.80" not in ips
    with pytest.raises(AdapterError, match="primary"):
        await a.release_ip(f"vps:{SN}", "203.0.113.10")


async def test_public_cloud_instances_are_refused():
    t, _, _ = mock_ovh_ips()
    with pytest.raises(AdapterError, match="VPS only"):
        await OvhAdapter(1, "a", CRED, http=t).add_ip("cloud:proj:uuid")


async def test_ip_cost_from_the_public_catalog():
    t, _, _ = mock_ovh_ips()
    cost = await OvhAdapter(1, "a", CRED, http=t).ip_cost(f"vps:{SN}")
    assert cost.per == "month" and str(cost.price.amount) == "1.99"
