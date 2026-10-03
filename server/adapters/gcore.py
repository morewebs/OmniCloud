"""Gcore catalog adapter - LIVE, tokenless (verified 2026-10-03).

Sources (all verified by direct curl with no token):
- GET https://api.gcore.com/cloud/public/v1/regions - 33 regions (public API).
- GET https://api.gcore.com/cloud/public/v1/basic_vms/flavors?region_id={id}
  - basic VM flavors per region (public API).
- GET https://bff.gcore.pro/cloud/vcc-items?regionCode={code} - the pricing
  calculator backend: ~100 flavor entries with per-minute USD prices.
  (A BFF, not a versioned API - if it moves, flavors still list per region;
  prices would degrade to None rather than break.)
Facts: VM traffic is free and unlimited (ingress AND egress) per docs;
extra public IPv4 = $2.7504/mo uniformly across regions (externalip_min);
billing = prepaid PAYG wallet charged per minute (~4 USD deduction steps).
"""
from __future__ import annotations

from decimal import Decimal

import httpx

from .base import IpOffer, Money, Plan, ProviderAdapter

REGIONS_URL = "https://api.gcore.com/cloud/public/v1/regions"
FLAVORS_URL = "https://api.gcore.com/cloud/public/v1/basic_vms/flavors"
BFF_ITEMS_URL = "https://bff.gcore.pro/cloud/vcc-items"

# Verified uniformly across 12 tested regions (2026-10-03).
PUBLIC_IP_MONTHLY_USD = Decimal("2.7504")
UNMETERED_NOTE = "unmetered (free ingress and egress); bandwidth capped by flavor"


class GcoreCatalogAdapter(ProviderAdapter):
    key = "gcore"
    display_name = "Gcore"
    capabilities = frozenset()

    def __init__(self, account_id: int = 0, account_name: str = "catalog",
                 token: str = "", http=None):
        super().__init__(account_id, account_name, token, http)
        self._client = httpx.AsyncClient(timeout=20.0, transport=http)

    async def list_plans(self) -> list[Plan]:
        regions = await self._get_json(REGIONS_URL)
        plans: list[Plan] = []
        region_failures: list[str] = []
        for region in regions.get("results", []):
            rid, rcode = region.get("id"), region.get("technical_name")
            if rid is None:
                continue
            try:
                flavors = await self._get_json(FLAVORS_URL, params={"region_id": rid})
            except httpx.HTTPError:
                # Data honesty: a partial catalog is not a success. Track and
                # raise at the end so the previous catalog is kept instead of
                # silently losing a region's plans while showing "fresh".
                region_failures.append(str(rcode or rid))
                continue
            prices = await self._region_prices(rcode)
            for f in flavors.get("results", []):
                name = f.get("name", "")
                per_min = prices.get(name)
                plans.append(Plan(
                    adapter=self.key,
                    name=name,
                    location=rcode or str(rid),
                    cpu_cores=f.get("vcpus"),
                    ram_gb=f.get("ram"),
                    disk_gb=f.get("disk"),
                    disk_type="nvme" if "nvme" in str(f.get("volume_types", "")).lower() else None,
                    price_monthly=(
                        Money(amount=per_min * Decimal(43200), currency="USD")
                        if per_min else None),
                    price_hourly=(Money(amount=per_min * Decimal(60), currency="USD")
                                  if per_min else None),
                    included_traffic_bytes=None,  # unmetered - no number to publish
                    counting=None,
                    extra_ip=IpOffer(
                        kind="public ipv4",
                        included=1,
                        price=Money(amount=PUBLIC_IP_MONTHLY_USD, currency="USD"),
                        note="floating/public IPv4",
                    ),
                    billing_model="prepaid pay-as-you-go wallet (per-minute)",
                ))
        if region_failures:
            from .base import AdapterError
            raise AdapterError(
                f"partial catalog refused: {len(region_failures)} region(s) failed "
                f"({', '.join(region_failures[:5])}) - keeping the previous catalog")
        return plans

    async def _region_prices(self, region_code: str | None) -> dict[str, Decimal]:
        """Flavor name -> USD per minute, from the calculator BFF (best-effort;
        a BFF outage degrades prices to None, never breaks the catalog)."""
        if not region_code:
            return {}
        try:
            data = await self._get_json(BFF_ITEMS_URL, params={"regionCode": region_code})
        except httpx.HTTPError:
            return {}
        out: dict[str, Decimal] = {}
        for item in data if isinstance(data, list) else data.get("items", []):
            name = item.get("name") or item.get("itemName") or ""
            if item.get("vmType") not in (None, "standard", "shared"):
                continue
            price_min = item.get("priceMinute") or item.get("price")
            if name and price_min:
                try:
                    out[name] = Decimal(str(price_min))
                except Exception:  # noqa: BLE001
                    continue
        return out

    async def _get_json(self, url: str, params: dict | None = None):
        r = await self._client.get(url, params=params)
        r.raise_for_status()
        return r.json()

    async def list_servers(self):  # type: ignore[return]
        return []
    async def get_server(self, provider_id: str):  # type: ignore[return]
        return None
    async def perform_action(self, cap, server_id, params):  # type: ignore[return]
        return None

    async def close(self) -> None:
        await self._client.aclose()
