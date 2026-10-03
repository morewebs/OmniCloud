"""Hetzner Cloud adapter (api.hetzner.cloud/v1, Bearer token).

Semantics verified against the official API docs (docs.hetzner.cloud, Oct 2026)
and the billing/firewalls FAQ (docs.hetzner.com) - see docs/provider-truth.md:
- Traffic billing: OUTGOING only; incoming and internal traffic are free.
  The server object's own included_traffic is the authoritative per-server
  allowance for the current billing period (already reflects type+location).
- server_type top-level included_traffic was REMOVED (2024); per-location
  values live in server_type.prices[] (price_monthly, included_traffic,
  price_per_tb_traffic per location). Used as a fallback + for overage price.
- Server objects carry a top-level `location` (the `datacenter` field was
  deprecated 2025-12 and removed 2026); we read both for compatibility.
- Action endpoints return 201 with an action; the action status enum is
  running|success|error. Poll GET /actions/{id} until success - a 201 is
  never success.
- Firewalls are STATEFUL allow-lists: default in=DROP, out=ACCEPT. A firewall
  with no inbound rule blocks inbound only; a server with NO firewall has no
  filtering at all. public_net.firewalls[] carries only {id, status} - names
  require a join against GET /firewalls.
- Rate limit: 3600 req/h per project; headers RateLimit-Remaining /
  RateLimit-Reset (UNIX timestamp of recovery).
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from decimal import Decimal
from typing import Any

from . import http as phttp
from .base import (
    ActionTimeout, ActionResult, AdapterError, Allowance, Capability, Facet,
    IpOffer, Money, Plan, ProviderAdapter, Server, ServerStatus,
    TrafficCounting,
)

API = "https://api.hetzner.cloud/v1"

# Poll budgets (seconds) per capability; timeout = failed, never "done".
POLL_BUDGET = {
    Capability.POWER_ON: 120, Capability.POWER_OFF: 120,
    Capability.REBOOT: 120, Capability.SHUTDOWN: 120,
    Capability.RENAME: 30, Capability.RELABEL: 30,
    Capability.REBUILD: 900, Capability.DELETE: 30,
}
POLL_INTERVAL = 2.0

# Module-level catalog cache shared by all Hetzner account instances:
# (type, location) -> {price_monthly, included_traffic, price_per_tb}
_catalog: tuple[dict, float] | None = None
CATALOG_TTL = 24 * 3600

# Documented typical value (hetzner.com pricing data shows 1.00 EUR/TB net for
# DE and US); the authoritative value is per (type, location) from the API -
# this is only the fallback when the catalog is unavailable.
OVERAGE_FALLBACK = Money(amount=Decimal("1.19"), currency="EUR", vat_inclusive=True)

# Verified server status enum (docs.hetzner.cloud cloud.spec.json).
STATUS_MAP = {
    "running": ServerStatus.RUNNING,
    "off": ServerStatus.OFF,
    "starting": ServerStatus.UNKNOWN,
    "stopping": ServerStatus.UNKNOWN,
    "initializing": ServerStatus.UNKNOWN,
    "deleting": ServerStatus.UNKNOWN,
    "migrating": ServerStatus.UNKNOWN,
    "rebuilding": ServerStatus.REBUILDING,
    "unknown": ServerStatus.UNKNOWN,
}


class HetznerAdapter(ProviderAdapter):
    key = "hetzner"
    display_name = "Hetzner Cloud"
    capabilities = frozenset(Capability)

    def __init__(self, account_id: int, account_name: str, token: str, http=None):
        super().__init__(account_id, account_name, token, http)
        self.h = phttp.ProviderHttpClient(
            API, lambda req: req.headers.__setitem__("Authorization", f"Bearer {token}"),
            transport=http,
        )

    # -- reads -----------------------------------------------------------

    async def list_servers(self) -> list[Server]:
        servers = []
        for path, params in phttp.iter_pages_hetzner("/servers"):
            data = await self.h.get_json(path, params)
            servers.extend(data.get("servers", []))
            last = data.get("meta", {}).get("pagination", {}).get("last_page", 1)
            if params["page"] >= last:
                break
        catalog = await self._catalog()
        return [self._server(row, catalog) for row in servers]

    async def get_server(self, provider_id: str) -> Server:
        data = await self.h.get_json(f"/servers/{provider_id}")
        row = data.get("server", data)  # GET /servers/{id} wraps in an envelope
        return self._server(row, await self._catalog())

    async def _catalog(self) -> dict:
        """server_types.prices[] joined per (type name, location name).
        NOTE: per-location, NOT the removed server_type.included_traffic."""
        global _catalog
        if _catalog and time.monotonic() - _catalog[1] < CATALOG_TTL:
            return _catalog[0]
        catalog: dict[tuple[str, str], dict] = {}
        for row in await self._server_types_raw():
            for price in row.get("prices", []):
                catalog[(row["name"], price.get("location", ""))] = {
                    "price_monthly": price.get("price_monthly", {}).get("gross"),
                    "included_traffic": price.get("included_traffic"),
                    "price_per_tb": (price.get("price_per_tb_traffic", {}) or {}).get("gross"),
                }
        _catalog = (catalog, time.monotonic())
        return catalog

    async def _server_types_raw(self) -> list[dict]:
        """Raw server_type objects (with prices[]), paginated."""
        rows = []
        for path, params in phttp.iter_pages_hetzner("/server_types"):
            data = await self.h.get_json(path, params)
            rows.extend(data.get("server_types", []))
            last = data.get("meta", {}).get("pagination", {}).get("last_page", 1)
            if params["page"] >= last:
                break
        return rows

    async def list_plans(self) -> list[Plan]:
        """Full plan catalog: one Plan per (server_type, location) from
        server_types.prices[] - the verified per-location source (see
        docs/provider-truth.md)."""
        plans = []
        for st in await self._server_types_raw():
            for price in st.get("prices", []):
                monthly = (price.get("price_monthly") or {}).get("gross")
                hourly = (price.get("price_hourly") or {}).get("gross")
                per_tb = (price.get("price_per_tb_traffic") or {}).get("gross")
                plans.append(Plan(
                    adapter=self.key,
                    name=st["name"],
                    location=price.get("location", ""),
                    cpu_cores=st.get("cores"),
                    cpu_arch=st.get("architecture"),
                    ram_gb=st.get("memory"),
                    disk_gb=st.get("disk"),
                    disk_type=("local nvme" if st.get("deprecation") is None and st.get("disk") else None),
                    price_monthly=_money(monthly, True) if monthly else None,
                    price_hourly=_money(hourly, True) if hourly else None,
                    included_traffic_bytes=price.get("included_traffic"),
                    counting=TrafficCounting.OUTGOING_ONLY,
                    overage_price=_money(per_tb, True) if per_tb else None,
                    extra_ip=IpOffer(
                        kind="floating",
                        included=1,
                        price=None,  # floating-IP price not in this API - not published here
                        note="floating IPs available; price not published in the API",
                    ),
                    billing_model="monthly invoice or prepaid credit",
                    deprecated=bool(st.get("deprecated")),
                ))
        return plans

    async def list_images(self) -> list[dict]:
        """Orderable system images (type=system) for the order dialog."""
        out = []
        for path, params in phttp.iter_pages_hetzner("/images", ):
            data = await self.h.get_json(path, params)
            for img in data.get("images", []):
                if img.get("type") == "system" and img.get("status") == "available":
                    out.append({"id": str(img["id"]), "name": img.get("name", ""),
                                "os": img.get("os_flavor", ""), "version": img.get("version")})
            last = data.get("meta", {}).get("pagination", {}).get("last_page", 1)
            if params["page"] >= last:
                break
        return out

    def _server(self, row: dict, catalog: dict) -> Server:
        st = (row.get("server_type") or {}).get("name")
        # Server objects now carry top-level `location`; older payloads used
        # datacenter.location. Read both (docs: datacenter removed 2026).
        loc_obj = row.get("location") or (row.get("datacenter") or {}).get("location") or {}
        loc = loc_obj.get("name", "")
        country = loc_obj.get("country", "")
        cat = catalog.get((st, loc), {})

        # The server's own field is authoritative for its CURRENT billing
        # period; the per-location catalog value is the fallback.
        included = row.get("included_traffic")
        if included is None:
            included = cat.get("included_traffic")
        outgoing = row.get("outgoing_traffic")
        price_gross = cat.get("price_monthly")

        overage_price = None
        if cat.get("price_per_tb"):
            overage_price = Money(amount=Decimal(str(cat["price_per_tb"])),
                                  currency="EUR", vat_inclusive=True)
        elif included is not None:
            overage_price = OVERAGE_FALLBACK

        allowance = None
        if included is not None or outgoing is not None:
            allowance = Allowance(
                included_bytes=included,
                used_bytes=outgoing,
                counting=TrafficCounting.OUTGOING_ONLY if outgoing is not None else None,
                # Docs say only "current billing period"; billing FAQ works in
                # calendar months. No creation-anniversary claim.
                window="outgoing traffic, current billing period"
                if included is not None else None,
                overage_price=overage_price,
                projected_overage_cost=self._project(overage_price, included, outgoing),
            )

        facets = []
        if loc:
            facets.append(Facet(label="location", value=loc))
        public_net = row.get("public_net") or {}
        # public_net.firewalls[] entries carry ONLY {id, status} - no name.
        # Names are joined by the caller via GET /firewalls.
        for fw in public_net.get("firewalls") or []:
            facets.append(Facet(label="firewall", value=f"fw-{fw.get('id')}"))

        return Server(
            provider_id=str(row["id"]),
            name=row.get("name", ""),
            adapter=self.key,
            account_id=self.account_id,
            status=STATUS_MAP.get(row.get("status", "unknown"), ServerStatus.UNKNOWN),
            ipv4=(public_net.get("ipv4") or {}).get("ip"),
            region=f"{loc} / {country}".strip(" /") or None,
            server_type=st,
            created=_dt(row.get("created")),
            labels=row.get("labels") or None,
            monthly_price=(
                Money(amount=Decimal(str(price_gross)), currency="EUR", vat_inclusive=True)
                if price_gross is not None else None
            ),
            allowance=allowance,
            facets=facets,
            not_exposed=[],
        )

    @staticmethod
    def _project(overage: Money | None, included: int | None,
                 used: int | None) -> Money | None:
        """Same-month projection at the API's per-location overage price.
        The API exposes no window start, so this is the conservative
        already-used projection; adapter-labeled in the UI."""
        if overage is None or included is None or used is None:
            return None
        extra_tb = Decimal(str(max(0, used - included))) / Decimal(1_000_000_000_000)
        if extra_tb <= 0:
            return None
        return overage.model_copy(update={"amount": extra_tb * Decimal(str(overage.amount))})

    # -- actions ----------------------------------------------------------

    async def perform_action(self, cap: Capability, server_id: str,
                             params: dict[str, Any]) -> ActionResult:
        if cap not in self.capabilities:
            raise AdapterError(f"hetzner does not support {cap.value}")
        if cap == Capability.RENAME:
            r = await self.h.put_json(f"/servers/{server_id}", {"name": params["name"]})
            row = r.json().get("server", r.json())
            if row.get("name") != params["name"]:
                raise AdapterError("rename not confirmed by provider view")
            return ActionResult(detail="renamed")
        if cap == Capability.RELABEL:
            await self.h.put_json(f"/servers/{server_id}", {"labels": params["labels"]})
            return ActionResult(detail="labels updated")
        if cap == Capability.REBUILD:
            return await self._run_action(
                server_id, "rebuild", {"image": params["image"]}, cap=cap)
        if cap == Capability.DELETE:
            await self.h.delete(f"/servers/{server_id}")
            return ActionResult(detail="deleted")
        kind = {Capability.POWER_ON: "poweron", Capability.POWER_OFF: "poweroff",
                Capability.REBOOT: "reboot", Capability.SHUTDOWN: "shutdown"}[cap]
        return await self._run_action(server_id, kind, {}, cap=cap)

    async def _run_action(self, server_id: str, kind: str, body: dict,
                          expect_status: ServerStatus | None = None,
                          cap: Capability | None = None) -> ActionResult:
        """POST action (201 + action object) -> poll GET /actions/{id} until
        the provider's status is success|error. A 201 is never success."""
        r = await self.h.post_json(f"/servers/{server_id}/actions/{kind}", body)
        action = r.json().get("action", {})
        action_id = action.get("id")
        budget = POLL_BUDGET.get(cap or Capability.REBOOT, 120)
        deadline = time.monotonic() + budget
        while time.monotonic() < deadline:
            if action_id:
                st = await self.h.get_json(f"/actions/{action_id}")
                a = st.get("action", st)
                if a.get("status") == "success":
                    return ActionResult(detail=f"{kind} success")
                if a.get("status") == "error":
                    raise AdapterError(f"{kind} failed: {a.get('error', {}).get('message', 'unknown error')}")
            await asyncio.sleep(POLL_INTERVAL)
        raise ActionTimeout(f"hetzner {kind} on server {server_id}")

    async def _confirm_put(self, r, server_id: str, new_name: str | None = None) -> None:  # noqa: ARG002
        """PUT /servers/{id} responds 200 with the updated server."""
        row = r.json()
        row = row.get("server", row)
        if new_name is not None and row.get("name") != new_name:
            raise AdapterError("rename not confirmed by provider view")

    # -- firewalls (shared resources, batches of servers) ------------------

    async def list_firewalls(self) -> list[dict[str, Any]]:
        """All firewalls on the account with applied-server ids, so the UI can
        mark which shared firewalls a given server already has."""
        out = []
        for path, params in phttp.iter_pages_hetzner("/firewalls"):
            data = await self.h.get_json(path, params)
            for f in data.get("firewalls", []):
                ids = {a.get("server", {}).get("id")
                       for a in f.get("applied_to", []) if a.get("type") == "server"}
                out.append({
                    "id": f["id"],
                    "name": f.get("name", ""),
                    "rules": len(f.get("rules", [])),
                    "applied_to_count": len(ids),
                    "applied_server_ids": sorted(x for x in ids if x is not None),
                })
            last = data.get("meta", {}).get("pagination", {}).get("last_page", 1)
            if params["page"] >= last:
                break
        return out

    async def apply_firewall(self, server_id: str, rules: list[dict[str, Any]],
                             attach: bool = True) -> None:
        """Create a dedicated firewall for this server and attach it.

        Firewalls are stateful allow-lists: inbound defaults to DROP. We
        refuse to create a firewall with zero inbound allow rules - that
        would block all inbound traffic to the server."""
        allow_rules = [r for r in rules if r.get("direction") == "in" or r.get("direction") is None]
        if attach and not allow_rules:
            raise AdapterError(
                "refusing to create a firewall with no inbound allow rules - "
                "Hetzner firewalls drop all inbound traffic by default"
            )
        fw_body = {
            "name": f"omni-{server_id}-{int(time.time())}",
            "rules": rules,
            "apply_to": [{"type": "server", "server": {"id": int(server_id)}}] if attach else [],
        }
        r = await self.h.post_json("/firewalls", fw_body)
        fw = r.json().get("firewall", {})
        # Confirm via the provider's own view of this firewall (single GET,
        # not the paginated list).
        fresh = await self.h.get_json(f"/firewalls/{fw.get('id')}")
        fresh = fresh.get("firewall", fresh)
        if attach and not any(
            a.get("server", {}).get("id") == int(server_id)
            for a in fresh.get("applied_to", [])
        ):
            raise AdapterError("firewall created but attachment not confirmed by provider")

    async def attach_firewall(self, firewall_id: int, server_id: str) -> None:
        """Attach an EXISTING shared firewall to this server. Returns 201 with
        {actions: [...]}; poll the first action until success."""
        body = {"apply_to": [{"type": "server", "server": {"id": int(server_id)}}]}
        r = await self.h.post_json(f"/firewalls/{firewall_id}/actions/apply_to_resources", body)
        await self._poll_fw_actions(r)

    async def detach_firewall(self, firewall_id: int, server_id: str) -> None:
        """Detach this server from a shared firewall. Body key is
        `remove_from` (NOT remove_from_resources)."""
        body = {"remove_from": [{"type": "server", "server": {"id": int(server_id)}}]}
        r = await self.h.post_json(
            f"/firewalls/{firewall_id}/actions/remove_from_resources", body)
        await self._poll_fw_actions(r)

    async def _poll_fw_actions(self, r) -> None:
        """Firewall actions return {actions: [...]}; poll each until the
        action status enum (running|success|error) reaches a terminal state."""
        actions = r.json().get("actions", [])
        if not actions:
            raise AdapterError("firewall action returned no action to track")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            done, failed = 0, None
            for a in actions:
                st = await self.h.get_json(f"/actions/{a['id']}")
                cur = st.get("action", st)
                if cur.get("status") == "success":
                    done += 1
                elif cur.get("status") == "error":
                    failed = cur.get("error", {}).get("message", "unknown error")
            if failed:
                raise AdapterError(f"firewall action failed: {failed}")
            if done == len(actions):
                return
            await asyncio.sleep(POLL_INTERVAL)
        raise ActionTimeout("firewall action not confirmed in time")

    async def close(self) -> None:
        await self.h.aclose()


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _money(gross: str | None, vat_inclusive: bool = True) -> Money:
    return Money(amount=Decimal(str(gross)), currency="EUR", vat_inclusive=vat_inclusive)
