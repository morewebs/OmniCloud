"""v2 catalog-provider tests: OVH/Gcore/Tube live adapters + verified seeds."""
import httpx

from server import catalog
from server.adapters.gcore import GcoreCatalogAdapter
from server.adapters.ovh import OvhCatalogAdapter
from server.adapters.tube import TubeCatalogAdapter


def _fixture(name: str):
    import conftest
    return conftest.fixture(name)


def _http(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


async def test_ovh_catalog_microcents_and_datacenters():
    """OVH prices are MICRO-CENTS (580000000 = 5.80 EUR); plans expand per
    vps_datacenter; no traffic number exists (unlimited - None + note)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "order/catalog/public/vps" in str(request.url):
            return httpx.Response(200, json=_fixture("ovh/vps_catalog.json"))
        if "order/catalog/formatted/ip" in str(request.url):
            return httpx.Response(200, json=_fixture("ovh/ip_catalog.json"))
        return httpx.Response(404)

    a = OvhCatalogAdapter(http=_http(handler))
    plans = await a.list_plans()
    # value plan expands to 2 datacenters, essential to 1
    assert len(plans) == 3
    gra = next(p for p in plans if p.name == "vps-value-1-2-40" and p.location == "GRA")
    assert str(gra.price_monthly.amount).startswith("5.80")
    assert gra.price_monthly.currency == "EUR"
    assert gra.included_traffic_bytes is None, "OVH API has no traffic field"
    # IP price joined from the separate IP catalog: 1.99 EUR
    assert str(gra.extra_ip.price.amount).startswith("1.99")
    assert gra.extra_ip.limit == 16
    await a.close()


async def test_gcore_catalog_per_minute_pricing():
    """Gcore: flavors per region; per-minute USD from the BFF -> monthly
    (x43200) and hourly (x60); public IP price uniform $2.7504."""
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/regions"):
            return httpx.Response(200, json={"count": 1, "results": [
                {"id": 7, "name": "Frankfurt", "technical_name": "FRN-2", "country": "DE"}]})
        if "basic_vms/flavors" in url:
            return httpx.Response(200, json={"count": 2, "results": [
                {"name": "g2s-shared-1-1-25", "vcpus": 1, "ram": 1, "disk": 25,
                 "volume_types": "nvme"},
                {"name": "g2s-shared-2-4-50", "vcpus": 2, "ram": 4, "disk": 50,
                 "volume_types": "nvme"}]})
        if "vcc-items" in url:
            return httpx.Response(200, json=[
                {"name": "g2s-shared-1-1-25", "vmType": "standard",
                 "priceMinute": "0.00107"},
            ])
        return httpx.Response(404)

    a = GcoreCatalogAdapter(http=_http(handler))
    plans = await a.list_plans()
    assert len(plans) == 2
    with_price = next(p for p in plans if p.name == "g2s-shared-1-1-25")
    assert str(with_price.price_monthly.amount).startswith("46.22")  # 0.00107*43200
    assert with_price.price_monthly.currency == "USD"
    assert str(with_price.extra_ip.price.amount).startswith("2.7504")
    # the flavor without a BFF price stays honestly None, not zero
    without = next(p for p in plans if p.name == "g2s-shared-2-4-50")
    assert without.price_monthly is None
    assert without.included_traffic_bytes is None  # unmetered - no number exists
    await a.close()


async def test_tube_catalog_euro_cents():
    """Tube prices are integer EURO-CENTS (500 = 5.00 EUR)."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"kvm": [
            {"name": "Starter", "price": 500, "cores": 2, "ram": 4, "disk": 30},
        ], "dedicated": {}})

    a = TubeCatalogAdapter(http=_http(handler))
    plans = await a.list_plans()
    assert len(plans) == 1
    assert str(plans[0].price_monthly.amount) == "5.00"
    assert plans[0].extra_ip.price is None, "IP price not in the asset - not published"
    await a.close()


async def test_registry_split():
    """OVH/Gcore/Tube are live; Netlen/LightNode seeded with stamps."""
    info = {p["key"]: p for p in catalog.providers_info()}
    for key in ("ovh", "gcore", "tube"):
        assert info[key]["source"] == "live"
        assert info[key]["capabilities"] == []
    for key in ("netlen", "lightnode"):
        assert info[key]["source"] == "seeded"


async def test_seeds_carry_traffic_notes_not_numbers():
    """Netlen's unmetered plans carry a traffic_note (no byte number
    published); LightNode's carry real byte allowances."""
    netlen_plans, netlen_raw = catalog.load_seed("netlen")
    assert netlen_plans[0].included_traffic_bytes is None
    assert netlen_plans[0].traffic_note and "unlimited" in netlen_plans[0].traffic_note
    assert netlen_raw["last_verified"] == "2026-10-03"

    light_plans, _ = catalog.load_seed("lightnode")
    assert light_plans[0].included_traffic_bytes == 1_000_000_000_000  # 1 TB
    # Start tier: IPs not offered as an add-on - the offer says so honestly
    # (kind + note), never a fabricated price.
    # every plan says the same thing: 1 IPv4 included, none extra (limit 0)
    for p in light_plans:
        ip = p.extra_ip
        assert ip is not None and ip.price is None and ip.limit == 0
        assert "not offered" in (ip.note or "")
