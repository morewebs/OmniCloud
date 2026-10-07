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
- Extra IPv4 = Floating IPs: POST /floating_ips {type, server} creates one
  already assigned (201 + floating_ip + nullable action); DELETE
  /floating_ips/{id} releases it (auto-unassigns). The server's own
  public_net.ipv4 is its Primary IP - never touched here. Floating IPs are
  billed monthly (API spec) and need configuring on the server's OS.
  Price per location: GET /pricing -> pricing.floating_ips[].prices[].
"""
from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Any

from . import http as phttp
from .base import (
    ActionTimeout, ActionResult, AdapterError, Allowance, Billing, Capability, Facet,
    IpAddress, IpCost, IpOffer, Money, Plan, ProviderAdapter, Server,
    ServerStatus, TrafficCounting, UnsupportedAction, parse_dt,
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

# Overage price: per (server_type, location) from prices[].price_per_tb_traffic
# (provider-truth). NO fallback constant - when the catalog value is missing,
# the overage price is not-exposed (None), never computed from a typical
# number the provider never stated for this server.

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
    # explicit, never frozenset(Capability): a new enum member must not be
    # advertised for Hetzner before this adapter implements it
    capabilities = frozenset({
        Capability.POWER_ON, Capability.POWER_OFF, Capability.REBOOT,
        Capability.SHUTDOWN, Capability.RENAME, Capability.RELABEL,
        Capability.FIREWALL, Capability.REBUILD, Capability.DELETE,
        Capability.IP_ADD, Capability.IP_RELEASE, Capability.IP_CHANGE,
    })

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
        else:
            # the paginator's page cap ran out while last_page said more:
            # a truncated fleet is silent data loss - raise, never partial.
            raise AdapterError(
                "/servers: more pages than the pagination cap "
                f"(last_page {last}) - refusing a truncated fleet view")
        # The catalog join is auxiliary: a /server_types 429/5xx must not kill
        # the fleet view - degrade to price/traffic-allowance not-exposed
        # (monthly_price=None, included from the server object when present).
        try:
            catalog = await self._catalog()
        except AdapterError:
            catalog = {}
        floating = await self._floating_by_server()
        return [self._server(row, catalog, floating) for row in servers]

    async def get_server(self, provider_id: str) -> Server:
        data = await self.h.get_json(f"/servers/{provider_id}")
        row = data.get("server", data)  # GET /servers/{id} wraps in an envelope
        try:
            catalog = await self._catalog()
        except AdapterError:
            catalog = {}  # auxiliary join failing must not kill the read
        return self._server(row, catalog, await self._floating_by_server())

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
        else:
            raise AdapterError(
                "/server_types: more pages than the pagination cap "
                f"(last_page {last}) - refusing a truncated catalog")
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
        else:
            raise AdapterError(
                "/images: more pages than the pagination cap "
                f"(last_page {last}) - refusing a truncated image list")
        return out

    async def _floating_list(self) -> list[dict]:
        rows = []
        for path, params in phttp.iter_pages_hetzner("/floating_ips"):
            data = await self.h.get_json(path, params)
            rows.extend(data.get("floating_ips", []))
            last = data.get("meta", {}).get("pagination", {}).get("last_page", 1)
            if params["page"] >= last:
                break
        return rows

    async def _floating_by_server(self) -> dict[str, list[dict]]:
        """Floating IPs grouped by assigned server id. Auxiliary like the
        catalog join: unreadable -> no floating IPs listed, never a failed
        fleet sync."""
        try:
            rows = await self._floating_list()
        except AdapterError:
            return {}
        out: dict[str, list[dict]] = {}
        for f in rows:
            if f.get("server") is not None:
                out.setdefault(str(f["server"]), []).append(f)
        return out

    def _server(self, row: dict, catalog: dict,
                floating: dict[str, list[dict]] | None = None) -> Server:
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
        # no catalog value -> overage_price stays None (not exposed) - the
        # 1.19 fallback invented a per-TB price the provider never stated.

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

        ips = []
        primary = (public_net.get("ipv4") or {}).get("ip")
        if primary:
            ips.append(IpAddress(address=primary, primary=True, kind="primary"))
        for f in (floating or {}).get(str(row.get("id")), []):
            if f.get("type") == "ipv4" and f.get("ip"):
                ips.append(IpAddress(address=f["ip"], primary=False, kind="floating",
                                     provider_ip_id=str(f.get("id"))))

        return Server(
            provider_id=str(row["id"]),
            name=row.get("name", ""),
            adapter=self.key,
            account_id=self.account_id,
            status=STATUS_MAP.get(row.get("status", "unknown"), ServerStatus.UNKNOWN),
            ipv4=(public_net.get("ipv4") or {}).get("ip"),
            region=f"{loc} / {country}".strip(" /") or None,
            server_type=st,
            created=parse_dt(row.get("created")),
            labels=row.get("labels") or None,
            monthly_price=(
                Money(amount=Decimal(str(price_gross)), currency="EUR", vat_inclusive=True)
                if price_gross is not None else None
            ),
            allowance=allowance,
            facets=facets,
            ips=ips,
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
            r = await self.h.delete(f"/servers/{server_id}")
            action = (r.json() or {}).get("action") or {}
            if action.get("id"):
                # DELETE returns an action like every other endpoint - poll it
                # (a 2xx alone is never success; base protocol).
                return await self._poll_action(int(action["id"]), "delete",
                                                server_id,
                                                POLL_BUDGET[Capability.DELETE])
            # spec allows a null action: confirm gone by the provider's view
            row = await self.h.request("GET", f"/servers/{server_id}")
            if row.status_code == 404:
                return ActionResult(detail="deleted")
            raise AdapterError(
                f"delete returned no action and server still lists ({row.status_code})")
        # POWER family only. Anything else reaching this point (firewall) is
        # dispatched via apply_firewall, never POST /servers/{id}/actions - a
        # raw KeyError here would surface as an unclassified internal error.
        if cap not in (Capability.POWER_ON, Capability.POWER_OFF,
                       Capability.REBOOT, Capability.SHUTDOWN):
            raise UnsupportedAction("hetzner", cap)
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

    async def _poll_action(self, action_id: int, kind: str, server_id: str,
                           budget: float) -> ActionResult:
        """Poll GET /actions/{id} to success|error - DELETE returns an action
        too; a 2xx response alone is never success."""
        deadline = time.monotonic() + budget
        while time.monotonic() < deadline:
            st = await self.h.get_json(f"/actions/{action_id}")
            a = st.get("action", st)
            if a.get("status") == "success":
                return ActionResult(detail=f"{kind} success")
            if a.get("status") == "error":
                raise AdapterError(f"{kind} failed: {a.get('error', {}).get('message', 'unknown error')}")
            await asyncio.sleep(POLL_INTERVAL)
        raise ActionTimeout(f"hetzner {kind} on server {server_id}")

    # -- IPs: floating IPs are the swappable extras ----------------------------

    async def add_ip(self, server_id: str) -> IpAddress:
        # created already assigned to the server; sent once - it's a purchase
        r = await self.h.request("POST", "/floating_ips", retry=False, json={
            "type": "ipv4", "server": int(server_id), "description": "omnicloud extra IP"})
        self.h._raise_for_status(r)
        body = r.json()
        fip = body.get("floating_ip") or {}
        fid = fip.get("id")
        if not fid or not fip.get("ip"):
            raise AdapterError("hetzner created a floating IP but returned no id/address - "
                               "check Floating IPs in the console (it bills monthly)")
        try:
            action = body.get("action") or {}
            if action.get("id"):
                await self._poll_action(action["id"], "assign floating IP", server_id, 60)
            # confirm on the provider's own view of the floating IP
            got = (await self.h.get_json(f"/floating_ips/{fid}")).get("floating_ip", {})
            if str(got.get("server")) != str(server_id):
                raise AdapterError(f"floating IP {fip['ip']} is not assigned to {server_id}")
        except AdapterError as e:
            try:
                await self.h.delete(f"/floating_ips/{fid}")
            except AdapterError as cleanup:
                raise AdapterError(f"{e}; floating IP {fip['ip']} could NOT be deleted "
                                   f"({cleanup}) - delete it in the console")
            raise
        return IpAddress(address=fip["ip"], primary=False, kind="floating",
                         provider_ip_id=str(fid))

    async def release_ip(self, server_id: str, address: str) -> None:
        fip = next((f for f in await self._floating_list() if f.get("ip") == address), None)
        if fip is None:
            row = (await self.h.get_json(f"/servers/{server_id}")).get("server", {})
            if ((row.get("public_net") or {}).get("ipv4") or {}).get("ip") == address:
                raise AdapterError(f"{address} is the server's primary IP - never released here")
            return  # already gone
        if str(fip.get("server")) not in (str(server_id), "None"):
            raise AdapterError(f"{address} belongs to server {fip.get('server')}, not {server_id}")
        # DELETE auto-unassigns (spec); confirmed by the provider's 404
        await self.h.delete(f"/floating_ips/{fip['id']}")
        r = await self.h.request("GET", f"/floating_ips/{fip['id']}")
        if r.status_code != 404:
            raise AdapterError(f"floating IP {address} still exists after delete")

    async def ip_cost(self, server_id: str) -> IpCost | None:
        try:
            row = (await self.h.get_json(f"/servers/{server_id}")).get("server", {})
            loc = ((row.get("location") or (row.get("datacenter") or {}).get("location")
                    or {}).get("name"))
            pricing = (await self.h.get_json("/pricing")).get("pricing", {})
        except AdapterError:
            return None
        note = "billed monthly (Hetzner API spec); configure the new IP on the server's OS"
        for entry in pricing.get("floating_ips") or []:
            if entry.get("type") != "ipv4":
                continue
            for p in entry.get("prices") or []:
                if p.get("location") == loc and (p.get("price_monthly") or {}).get("gross"):
                    return IpCost(price=Money(amount=Decimal(str(p["price_monthly"]["gross"])),
                                              currency=pricing.get("currency", "EUR"),
                                              vat_inclusive=True),
                                  per="month", note=note)
        return IpCost(price=None, per="month", note=note)

    async def provision(self, plan_name: str, location: str, options: dict) -> str:
        """POST /servers. Every SSH key in the project is attached: with no
        key Hetzner would answer with a root password, and the panel never
        handles root passwords."""
        keys = []
        for path, params in phttp.iter_pages_hetzner("/ssh_keys"):
            data = await self.h.get_json(path, params)
            keys.extend(k["id"] for k in data.get("ssh_keys", []))
            if params["page"] >= data.get("meta", {}).get("pagination", {}).get("last_page", 1):
                break
        if not keys:
            raise AdapterError("add an SSH key to this Hetzner project first - the panel "
                               "never handles root passwords")
        if not options.get("image"):
            raise AdapterError("choose an image for the new server")
        r = await self.h.request("POST", "/servers", retry=False, json={
            "name": options.get("hostname") or f"omni-{plan_name}-{location}",
            "server_type": plan_name, "location": location, "image": str(options["image"]),
            "ssh_keys": keys, "start_after_create": True,
            "public_net": {"enable_ipv4": True, "enable_ipv6": True}})
        self.h._raise_for_status(r)
        body = r.json()
        sid = str((body.get("server") or {}).get("id") or "")
        if not sid:
            raise AdapterError("hetzner accepted the order but returned no server id - "
                               "check the console before ordering again")
        action = body.get("action") or {}
        if action.get("id"):
            await self._poll_action(action["id"], "create server", sid, 300)
        return sid

    async def get_billing(self) -> Billing:
        # the Cloud API has no billing endpoints at all (cloud.spec.json)
        return Billing(model="monthly invoice for the calendar month, in arrears "
                             "(or prepaid credit)",
                       not_exposed=["balance", "invoices", "month_to_date",
                                    "upcoming", "renewals"])

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
                    # the actual rule list, for in-UI viewing (rule count alone
                    # can't answer "is 22 open to the world?"). Adapters that
                    # don't expose rules simply omit this key - the UI then
                    # shows 'not exposed', never a guess.
                    "rule_detail": f.get("rules", []),
                    "applied_to_count": len(ids),
                    "applied_server_ids": sorted(x for x in ids if x is not None),
                })
            last = data.get("meta", {}).get("pagination", {}).get("last_page", 1)
            if params["page"] >= last:
                break
        else:
            raise AdapterError(
                "/firewalls: more pages than the pagination cap "
                f"(last_page {last}) - refusing a truncated firewall list")
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


def _money(gross: str | None, vat_inclusive: bool = True) -> Money:
    return Money(amount=Decimal(str(gross)), currency="EUR", vat_inclusive=vat_inclusive)
