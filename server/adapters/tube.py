"""Tube-hosting catalog adapter - LIVE, tokenless (verified 2026-10-03).

Source: GET https://www.tube-hosting.com/assets/data/templates.json - the
static data asset their pricing page itself fetches (no auth, CORS-open,
stable path). Shape: {"kvm": [{name, price, cores, ram, disk}... 12 plans],
"dedicated": {...}} - prices are integer EURO-CENTS (price: 500 = EUR 5.00).
Caveat honestly noted: a static asset, not a versioned API. Traffic and
extra-IP terms are not in the asset - rendered as not-published, never
invented (verify on their pricing page at order time).
"""
from __future__ import annotations

from decimal import Decimal

import httpx

from .base import IpOffer, Money, Plan, ProviderAdapter

TEMPLATES_URL = "https://www.tube-hosting.com/assets/data/templates.json"


class TubeCatalogAdapter(ProviderAdapter):
    key = "tube"
    display_name = "Tube-hosting"
    capabilities = frozenset()

    def __init__(self, account_id: int = 0, account_name: str = "catalog",
                 token: str = "", http=None):
        super().__init__(account_id, account_name, token, http)
        self._client = httpx.AsyncClient(timeout=20.0, transport=http)

    async def list_plans(self) -> list[Plan]:
        data = await self._get_json(TEMPLATES_URL)
        plans = []
        for kvm in data.get("kvm", []):
            price = kvm.get("price")
            if price is None:
                continue
            plans.append(Plan(
                adapter=self.key,
                name=kvm.get("name", ""),
                location="NL",  # their KVM plans are Netherlands-based
                cpu_cores=kvm.get("cores"),
                ram_gb=kvm.get("ram"),
                disk_gb=kvm.get("disk"),
                price_monthly=Money(amount=(Decimal(str(price)) / 100).quantize(Decimal("0.01")),
                                   currency="EUR"),
                included_traffic_bytes=None,  # not in the asset - not published
                counting=None,
                extra_ip=IpOffer(kind="public ipv4", included=1, price=None,
                                 note="offered; price not published - verify at order"),
                billing_model="prepaid",
            ))
        return plans

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
