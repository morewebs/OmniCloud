"""OVHcloud catalog adapter - LIVE, tokenless (verified 2026-10-03).

Source: https://eu.api.ovh.com/1.0/order/catalog/public/vps?ovhSubsidiary=FR
(noAuthentication: true per /1.0/order.json; verified by plain curl).
- 198 plans for FR: planCode, invoiceName, pricings[] (price in MICRO-CENTS,
  e.g. 299000000 = 2.99 EUR), configurations[] with vps_datacenter values.
- NO traffic field anywhere in the API - marketing pages say "unlimited
  traffic" with per-model bandwidth caps; we publish that as plain text
  (included_bytes stays None - never invent a number).
- Extra IPs live in the SEPARATE tokenless IP catalog
  /order/catalog/formatted/ip: ip-failover-ripe/arin at 1.99 EUR/IP/mo
  (max 16 per VPS per the Additional IP page; blocks not supported on VPS).
- Monthly rental billing only (1/12/24-month terms); no hourly VPS pricing.
"""
from __future__ import annotations

from decimal import Decimal

import httpx

from .base import IpOffer, Money, Plan, ProviderAdapter

CATALOG_URL = "https://eu.api.ovh.com/1.0/order/catalog/public/vps"
IP_CATALOG_URL = "https://eu.api.ovh.com/1.0/order/catalog/formatted/ip"

# Per the Additional IP page: up to 16 individual IPs on a VPS; blocks not
# supported at VPS level. Price from the IP catalog (1.99 EUR/IP/mo).
VPS_IP_LIMIT = 16

# Marketing-published bandwidth caps per VPS model tier (public VPS page).
BANDWIDTH_NOTE = "unlimited traffic (fair-use); public bandwidth 500 Mbps-3 Gbps by model"


class OvhCatalogAdapter(ProviderAdapter):
    """Catalog-only: no server management in v2 (full adapter blocked on
    OVH's 3-part credential model)."""
    key = "ovh"
    display_name = "OVHcloud"
    capabilities = frozenset()

    def __init__(self, account_id: int = 0, account_name: str = "catalog",
                 token: str = "", http=None):
        # token unused: catalog endpoints are unauthenticated.
        super().__init__(account_id, account_name, token, http)
        transport = http
        self._client = httpx.AsyncClient(timeout=20.0, transport=transport)

    async def list_plans(self) -> list[Plan]:
        data = await self._get_json(CATALOG_URL, params={"ovhSubsidiary": "FR"})
        ip_price = await self._ip_price()
        currency = (data.get("locale") or {}).get("currencyCode", "EUR")
        plans = []
        for p in data.get("plans", []):
            monthly = self._monthly_price(p.get("pricings", []))
            if monthly is None:
                continue  # installations/upgrades only - not a plan we sell
            dcs = self._config_values(p, "vps_datacenter")
            for dc in dcs or [""]:
                plans.append(Plan(
                    adapter=self.key,
                    name=p.get("planCode", ""),
                    location=dc,
                    price_monthly=Money(amount=monthly, currency=currency,
                                        vat_inclusive=False),
                    included_traffic_bytes=None,  # unlimited; no number published
                    counting=None,
                    extra_ip=IpOffer(
                        kind="failover",
                        included=1,
                        price=ip_price,
                        limit=VPS_IP_LIMIT,
                        note="Additional IP (RIPE/ARIN); blocks not supported on VPS",
                    ) if ip_price else IpOffer(kind="failover", included=1, price=None,
                                                limit=VPS_IP_LIMIT,
                                                note="Additional IP orderable"),
                    billing_model="monthly invoice, 1/12/24-month terms",
                ))
        return plans

    async def _ip_price(self) -> Money | None:
        """Additional-IP catalog is tokenless: ip-failover-ripe per-IP price."""
        try:
            data = await self._get_json(IP_CATALOG_URL, params={"ovhSubsidiary": "FR"})
        except Exception:  # noqa: BLE001 - IP price is best-effort, not critical
            return None
        for p in data.get("plans", []):
            if p.get("planCode") == "ip-failover-ripe":
                price = self._monthly_price(p.get("pricings", []))
                if price is not None:
                    return Money(amount=price, currency="EUR", vat_inclusive=False)
        return None

    @staticmethod
    def _monthly_price(pricings: list[dict]) -> Decimal | None:
        """First monthly-renew pricing (micro-cents int -> EUR decimal,
        quantized to 2 places so 580000000 renders as 5.80, not 5.8)."""
        for pr in pricings:
            if ("renew" in (pr.get("capacities") or [])
                    and pr.get("interval") == 1
                    and pr.get("intervalUnit") == "month"):
                return (Decimal(str(pr.get("price", 0))) / Decimal(10**8)).quantize(Decimal("0.01"))
        return None

    @staticmethod
    def _config_values(plan: dict, name: str) -> list[str]:
        for c in plan.get("configurations", []):
            if c.get("name") == name:
                return [str(v) for v in c.get("values", [])]
        return []

    async def _get_json(self, url: str, params: dict | None = None):
        r = await self._client.get(url, params=params)
        r.raise_for_status()
        return r.json()

    # unused abstract surface (catalog-only provider)
    async def list_servers(self):  # type: ignore[return]
        return []
    async def get_server(self, provider_id: str):  # type: ignore[return]
        return None
    async def perform_action(self, cap, server_id, params):  # type: ignore[return]
        return None

    async def close(self) -> None:
        await self._client.aclose()
