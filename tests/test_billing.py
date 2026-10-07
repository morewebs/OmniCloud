"""Per-account billing: adapter snapshots (OVH /me, LeaseWeb invoices),
storage + failure isolation, overview alerts, and awaiting-payment orders
settling when the provider reports them delivered."""
import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from server import accounts, billing, db
from server.adapters.base import AdapterError, Billing, Invoice, Money, Renewal
from server.adapters.leaseweb import LeasewebAdapter
from server.adapters.ovh import OvhAdapter
from server.main import create_app

from conftest import TEST_TOKEN, FakeAdapter, fixture

HDRS = {"X-Requested-With": "XMLHttpRequest"}
OVH_CRED = "aaaaaaaaaaaa1111:bbbbbbbbbbbb2222:cccccccccccc3333"


def mock_ovh_me():
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path.removeprefix("/1.0")
        eur = lambda v: {"value": v, "currencyCode": "EUR", "text": f"{v} €"}  # noqa: E731
        routes = {
            "/auth/time": fixture("ovh/auth_time.json"),
            "/me/credit/balance": ["PREPAID-1"],
            "/me/credit/balance/PREPAID-1": {"type": "PREPAID_ACCOUNT", "amount": eur(12.5)},
            "/me/bill": ["FR111", "FR112"],
            "/me/bill/FR112": {"billId": "FR112", "date": "2026-10-01T00:00:00+02:00",
                               "priceWithTax": eur(14.39), "url": "https://bill.example.test/FR112"},
            "/me/bill/FR111": {"billId": "FR111", "date": "2026-09-01T00:00:00+02:00",
                               "priceWithTax": eur(14.39), "url": "https://bill.example.test/FR111"},
            "/me/bill/FR112/debt": {"dueAmount": eur(14.39), "dueDate": "2026-10-10T00:00:00+02:00",
                                    "status": "TO_BE_PAID"},
            "/me/order": [501, 502],
            "/me/order/502/status": "notPaid",
            "/me/order/501/status": "delivered",
            "/me/order/502": {"orderId": 502, "date": "2026-10-06T10:00:00+02:00",
                              "expirationDate": "2026-10-20T10:00:00+02:00",
                              "priceWithTax": eur(2.39), "url": "https://pay.example.test/502"},
            "/vps": ["vps-demo1.demo.ovh.net"],
            "/vps/vps-demo1.demo.ovh.net/serviceInfos": {
                "expiration": "2026-10-15", "renew": {"automatic": False}},
        }
        if path in routes:
            return httpx.Response(200, json=routes[path])
        return httpx.Response(404, json={"message": "not found"})
    return httpx.MockTransport(handler)


async def test_ovh_billing_reads_debt_balance_orders_and_expiry():
    b = await OvhAdapter(1, "a", OVH_CRED, http=mock_ovh_me()).get_billing()
    assert b.balance.amount == Decimal("12.5") and b.balance.currency == "EUR"
    inv = {i.id: i for i in b.invoices}
    assert inv["FR112"].status == "TO_BE_PAID" and inv["FR112"].open_amount.amount == Decimal("14.39")
    assert inv["FR111"].status == "no debt" and inv["FR111"].open_amount is None
    assert [o.id for o in b.unpaid_orders] == ["502"]
    assert b.unpaid_orders[0].url == "https://pay.example.test/502"
    assert b.renewals[0].auto is False and b.renewals[0].date.isoformat().startswith("2026-10-15")


async def test_ovh_order_status_maps_provider_words():
    a = OvhAdapter(1, "a", OVH_CRED, http=mock_ovh_me())
    assert await a.order_status("501") == "delivered"
    assert await a.order_status("502") == "unpaid"


async def test_leaseweb_billing_invoices_and_proforma():
    def handler(req):
        if req.url.path == "/invoices/v1/invoices":
            return httpx.Response(200, json={"invoices": [
                {"id": "00000001", "date": "2026-10-01", "dueDate": "2026-10-15",
                 "total": 120.5, "openAmount": 120.5, "currency": "EUR", "status": "OPEN"},
                {"id": "00000000", "date": "2026-09-01", "dueDate": "2026-09-15",
                 "total": 99.0, "openAmount": 0, "currency": "EUR", "status": "PAID"}]})
        if req.url.path == "/invoices/v1/invoices/proforma":
            return httpx.Response(200, json={"total": 64.2, "currency": "EUR"})
        return httpx.Response(404)
    b = await LeasewebAdapter(1, "a", TEST_TOKEN, http=httpx.MockTransport(handler)).get_billing()
    assert [i.status for i in b.invoices] == ["OPEN", "PAID"]
    assert b.invoices[0].open_amount.amount == Decimal("120.5")
    assert b.upcoming.amount == Decimal("64.2") and "balance" in b.not_exposed


# -- storage, isolation, alerts ------------------------------------------------

class BillingFake(FakeAdapter):
    snap: Billing | None = None
    fail = False
    statuses: dict = {}

    async def get_billing(self):
        if self.fail:
            raise AdapterError("billing endpoint down")
        return self.snap

    async def order_status(self, ref):
        return self.statuses.get(ref)


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setitem(accounts.ADAPTERS, "fake", BillingFake)
    BillingFake.fail = False
    BillingFake.statuses = {}
    BillingFake.snap = Billing(
        model="prepaid wallet",
        balance=Money(amount=Decimal("3.10"), currency="EUR"),
        invoices=[Invoice(id="INV-9", due_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                          total=Money(amount=Decimal("9"), currency="EUR"),
                          open_amount=Money(amount=Decimal("9"), currency="EUR"),
                          status="unpaid"),
                  Invoice(id="INV-8", total=Money(amount=Decimal("5"), currency="EUR"),
                          open_amount=Money(amount=Decimal("0"), currency="EUR"),
                          status="paid")],
        renewals=[Renewal(provider_id="p1", name="srv-01",
                          date=datetime.now(timezone.utc), auto=False),
                  Renewal(provider_id="p2", name="srv-02",
                          date=datetime.now(timezone.utc), auto=True)])
    return accounts.create_account("fake", "acct-a7f3", "tok-0000abcd")


async def test_refresh_stores_snapshot_and_alerts(fake):
    await billing.refresh_now(fake)
    row = next(r for r in billing.snapshots() if r["account_id"] == fake)
    assert row["supported"] and row["billing"]["balance"]["amount"] == "3.10"
    msgs = [a["error"] for a in billing.alerts()]
    assert any("INV-9 OVERDUE" in m for m in msgs)
    assert not any("INV-8" in m for m in msgs)            # paid: no alert
    assert any("srv-01 expires" in m for m in msgs)       # manual renewal soon
    assert not any("srv-02" in m for m in msgs)           # auto-renews
    assert not any("balance" in m for m in msgs)          # no threshold set
    db.set_setting(f"billing_low_balance:{fake}", "5")
    assert any("below the 5 alert threshold" in a["error"] for a in billing.alerts())


async def test_billing_failure_is_recorded_and_never_raises(fake):
    BillingFake.fail = True
    assert await billing.refresh_now(fake) is None
    row = next(r for r in billing.snapshots() if r["account_id"] == fake)
    assert "billing endpoint down" in row["last_error"] and row["billing"] is None


async def test_due_respects_interval(fake):
    assert billing.is_due(fake)
    await billing.refresh_now(fake)
    assert not billing.is_due(fake)


async def test_awaiting_payment_order_settles_when_delivered(fake, uid):
    ts = db.now()
    with db.connect() as conn:
        for ref in ("O-1", "O-2", "O-3"):
            conn.execute(
                """INSERT INTO orders(mode, status, kind, adapter, account_id, plan_name, location,
                   options, plan_snapshot, estimated_monthly, provider_ref, requested_by,
                   created_at, updated_at) VALUES('real','awaiting_payment','ip','fake',?,
                   'extra-ip','x','{}','{}','{}',?,?,?,?)""", (fake, ref, uid, ts, ts))
    BillingFake.statuses = {"O-1": "delivered", "O-2": "cancelled", "O-3": "unpaid"}
    await billing.refresh_now(fake)
    with db.connect() as conn:
        st = dict(conn.execute("SELECT provider_ref, status FROM orders").fetchall())
    assert st == {"O-1": "provisioned", "O-2": "cancelled", "O-3": "awaiting_payment"}


def test_billing_api_and_overview_alerts(fake):
    with TestClient(create_app()) as c:
        c.post("/api/auth/setup", json={"username": "admin", "password": "pw123456"},
               headers=HDRS)
        r = c.post(f"/api/billing/accounts/{fake}/refresh", headers=HDRS)
        assert r.status_code == 200, r.text
        rows = c.get("/api/billing/accounts").json()
        assert rows[0]["billing"]["model"] == "prepaid wallet"
        alerts = c.get("/api/overview").json()["alerts"]
        assert any(a["kind"] == "billing" and a["account_id"] == fake for a in alerts)
        BillingFake.fail = True
        assert c.post(f"/api/billing/accounts/{fake}/refresh", headers=HDRS).status_code == 502
        assert c.put("/api/settings", headers=HDRS,
                     json={f"billing_low_balance:{fake}": "abc"}).status_code == 400
