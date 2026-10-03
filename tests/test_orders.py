"""Order pipeline tests: prototype lifecycle, cancels, failure, audit."""
import asyncio
import json

import pytest

from server import audit, auth, catalog, db, orders
from server.adapters.base import IpOffer, Money, Plan, TrafficCounting


def _seed_catalog():
    """One live-labeled plan with a published IP price, one without."""
    plans = [
        Plan(adapter="fake", name="plan-a", location="loc-1",
             price_monthly=Money(amount="10.00", currency="EUR"),
             included_traffic_bytes=1_000_000_000_000,
             counting=TrafficCounting.OUTGOING_ONLY,
             extra_ip=IpOffer(kind="floating", included=1,
                              price=Money(amount="0.50", currency="EUR"), limit=5),
             billing_model="monthly invoice"),
        Plan(adapter="fake", name="plan-b", location="loc-1",
             price_monthly=Money(amount="20.00", currency="USD"),
             extra_ip=IpOffer(kind="public ipv4", included=1, price=None,
                              note="not published"),
             billing_model="prepaid wallet"),
    ]
    catalog.store("fake", plans, "live", None)


@pytest.fixture(autouse=True)
def seed():
    _seed_catalog()
    yield


async def test_full_prototype_lifecycle(uid):
    oid = orders.create_order(uid, "fake", "plan-a", "loc-1",
                              {"hostname": "srv-x", "extra_ips": 2})
    assert orders.get_order(oid)["status"] == "draft"
    orders.confirm(oid, uid)
    assert orders.get_order(oid)["status"] == "confirmed"
    orders.execute(oid, uid)
    await asyncio.sleep(4)  # prototype executor sleeps 3s
    o = orders.get_order(oid)
    assert o["status"] == "provisioned"
    assert o["resulting_provider_id"] == f"proto-{oid}"
    # events timeline walks the whole pipeline
    statuses = [e["status"] for e in o["events"]]
    assert statuses == ["draft", "confirmed", "executing", "provisioned"]


async def test_estimated_monthly_includes_ip_prices(uid):
    oid = orders.create_order(uid, "fake", "plan-a", "loc-1", {"extra_ips": 2})
    est = json.loads(orders.get_order(oid)["estimated_monthly"])
    assert est["amount"] == "11.00"  # 10.00 + 2x0.50
    assert est["partial"] is False


async def test_unpublished_ip_price_makes_estimate_partial_not_invented(uid):
    oid = orders.create_order(uid, "fake", "plan-b", "loc-1", {"extra_ips": 3})
    est = json.loads(orders.get_order(oid)["estimated_monthly"])
    assert est["amount"] == "20.00"  # plan only - IP price never guessed
    assert est["partial"] is True


async def test_cancel_from_draft_and_confirmed_but_not_executing(uid):
    oid = orders.create_order(uid, "fake", "plan-a", "loc-1", {})
    orders.cancel(oid, uid)
    assert orders.get_order(oid)["status"] == "cancelled"

    oid2 = orders.create_order(uid, "fake", "plan-a", "loc-1", {})
    orders.confirm(oid2, uid)
    orders.cancel(oid2, uid)
    assert orders.get_order(oid2)["status"] == "cancelled"

    oid3 = orders.create_order(uid, "fake", "plan-a", "loc-1", {})
    orders.confirm(oid3, uid)
    orders.execute(oid3, uid)
    with pytest.raises(orders.OrderError, match="cannot cancel"):
        orders.cancel(oid3, 1)


async def test_invalid_plan_rejected(uid):
    with pytest.raises(orders.OrderError, match="no plan"):
        orders.create_order(uid, "fake", "nope", "loc-1", {})


async def test_plan_without_ip_offer_rejects_extra_ips(uid):
    plan_c = [Plan(adapter="fake", name="plan-c", location="loc-1",
                   price_monthly=Money(amount="5.00", currency="EUR"))]
    catalog.store("fake", plan_c, "live", None)
    with pytest.raises(orders.OrderError, match="additional IPs"):
        orders.create_order(uid, "fake", "plan-c", "loc-1", {"extra_ips": 1})


async def test_audit_and_no_fleet_pollution(uid):
    oid = orders.create_order(uid, "fake", "plan-a", "loc-1", {})
    orders.confirm(oid, uid)
    orders.execute(oid, uid)
    await asyncio.sleep(4)
    # audit trail exists
    entries = audit.list_entries()
    assert any(a["action"] == "order.create" for a in entries)
    assert any(a["action"] == "order.provisioned" for a in entries)
    # prototype orders NEVER appear in the fleet cache (servers table)
    with db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM servers").fetchone()[0]
    assert n == 0
