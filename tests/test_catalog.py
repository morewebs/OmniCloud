"""Catalog tests: live list_plans (both adapters) + seeded loader honesty."""
from server import catalog
from server.adapters.base import TrafficCounting

from conftest import TEST_TOKEN, mock_hetzner_transport, mock_leaseweb_transport


async def test_hetzner_list_plans_per_location():
    """One Plan per (server_type, location) with the verified per-location
    price/traffic fields from prices[]."""
    from server.adapters.hetzner import HetznerAdapter
    transport, _ = mock_hetzner_transport()
    a = HetznerAdapter(1, "account-a7f3", TEST_TOKEN, http=transport)
    plans = await a.list_plans()
    # 1 type x 3 locations from the fixture
    assert len(plans) == 3
    fsn1 = next(p for p in plans if p.location == "fsn1")
    assert fsn1.name == "cx22"
    assert str(fsn1.price_monthly.amount) == "3.92"
    assert fsn1.included_traffic_bytes == 2199102253056
    assert str(fsn1.overage_price.amount).startswith("1.19")
    assert fsn1.counting == TrafficCounting.OUTGOING_ONLY
    assert fsn1.cpu_cores == 2  # from the server_type object (fixture asserts shape)
    ash = next(p for p in plans if p.location == "ash")
    assert str(ash.price_monthly.amount) == "4.51"
    assert ash.included_traffic_bytes == 1099511627776


async def test_hetzner_extra_ip_honesty():
    """The API does not publish floating-IP prices in server_types - the
    offer must say so, never invent a number."""
    from server.adapters.hetzner import HetznerAdapter
    transport, _ = mock_hetzner_transport()
    a = HetznerAdapter(1, "account-a7f3", TEST_TOKEN, http=transport)
    plans = await a.list_plans()
    ip = plans[0].extra_ip
    assert ip is not None
    assert ip.price is None, "unpublished IP price must be None, never guessed"
    assert "not published" in (ip.note or "")


async def test_leaseweb_list_plans_per_region():
    from server.adapters.leaseweb import LeasewebAdapter
    transport, _ = mock_leaseweb_transport()
    a = LeasewebAdapter(1, "account-a7f3", TEST_TOKEN, http=transport)
    plans = await a.list_plans()
    medium = next(p for p in plans if p.name == "lsw.m3.medium")
    assert str(medium.price_monthly.amount) == "14.90"
    # Public Cloud allowance is per account, not per plan - must stay None
    assert medium.included_traffic_bytes is None
    assert medium.counting == TrafficCounting.OUTGOING_ONLY


async def test_seed_loader_stamps_and_honesty():
    """Seeded plans load with source/last_verified and never claim to be live."""
    plans, raw = catalog.load_seed("netlen")
    assert raw["last_verified"]
    assert raw["source_url"]
    p = plans[0]
    assert p.adapter == "netlen"
    assert p.billing_model == raw["billing_model"]
    assert p.extra_ip.price is None, "seed without published IP price must be None"
    # verified public list prices are not placeholders
    assert p.deprecated is False
    assert str(p.price_monthly.amount) == "2.99"


async def test_store_and_read_roundtrip():
    plans, raw = catalog.load_seed("netlen")
    catalog.store("netlen", plans, "seeded", raw["last_verified"])
    data = catalog.read()
    netlen_rows = [r for r in data["plans"] if r["adapter"] == "netlen"]
    assert len(netlen_rows) == len(plans)
    assert all(r["source"] == "seeded" for r in netlen_rows)
    assert netlen_rows[0]["last_verified"] == raw["last_verified"]


async def test_providers_info_separates_live_and_seeded():
    info = catalog.providers_info()
    sources = {p["key"]: p["source"] for p in info}
    assert sources["hetzner"] == "live"
    assert sources["leaseweb"] == "live"
    for key in ("ovh", "gcore", "tube"):
        assert sources[key] == "live", f"{key} has tokenless endpoints (verified)"
    for key in ("netlen", "lightnode"):
        assert sources[key] == "seeded"
    # catalog-only providers declare no capabilities (gating preserved)
    for key in ("ovh", "gcore", "tube", "netlen", "lightnode"):
        entry = next(p for p in info if p["key"] == key)
        assert entry["capabilities"] == []
