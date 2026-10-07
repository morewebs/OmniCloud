"""OVHcloud adapters: fleet (VPS + Public Cloud) and tokenless catalog.

Fleet semantics verified 2026-10-04 against api.ovh.com/1.0/vps.json +
/1.0/cloud.json (fetched live) - see docs/provider-truth.md, "OVHcloud
fleet" section. Catalog semantics verified 2026-10-03 (section below).

Auth: the credential is ONE packed string (the panel's credential chain is
single-token end to end):
- "AK:AS:CK" (3 parts)  -> classic API keys, SHA1 request signing
- "client_id:secret" (2 parts) -> OAuth2 service account, Bearer
Signature: "$1$" + SHA1_HEX(AS + "+" + CK + "+" + METHOD + "+" + URL
+ "+" + BODY + "+" + TIMESTAMP); timestamp is SERVER time (GET /1.0/auth/time,
delta cached for the adapter's life). Hash the wire bytes actually sent.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx

from . import http as phttp
from .base import (
    ActionTimeout, ActionResult, AdapterError, Allowance, Billing, Capability,
    Facet, Invoice, IpAddress, IpCost, IpOffer, Money, PaymentRequired, Plan,
    ProviderAdapter, Renewal, Server, ServerStatus, TrafficCounting, parse_dt,
)

API = "https://eu.api.ovh.com/1.0"
AUTH_TIME_URL = f"{API}/auth/time"
OAUTH_TOKEN_URL = "https://www.ovh.com/auth/oauth2/token"

# Poll budgets (seconds); timeout = failed, never "done".
POLL_BUDGET_POWER = 120
POLL_BUDGET_RENAME = 30
POLL_BUDGET_DELETE = 60
POLL_INTERVAL = 2.0

# VPS state enum (vps.VpsStateEnum) -> canonical.
VPS_STATE_MAP = {
    "running": ServerStatus.RUNNING,
    "stopped": ServerStatus.OFF,
    "stopping": ServerStatus.UNKNOWN,
    "rebooting": ServerStatus.UNKNOWN,
    "backuping": ServerStatus.UNKNOWN,
    "installing": ServerStatus.REBUILDING,
    "upgrading": ServerStatus.REBUILDING,
    "rescued": ServerStatus.UNKNOWN,
    "maintenance": ServerStatus.UNKNOWN,
}

# Public Cloud instance status (cloud.instance.InstanceStatusEnum) -> canonical.
CLOUD_STATUS_MAP = {
    "ACTIVE": ServerStatus.RUNNING,
    "SHUTOFF": ServerStatus.OFF,
    "STOPPED": ServerStatus.OFF,
    "BUILD": ServerStatus.REBUILDING,
    "BUILDING": ServerStatus.REBUILDING,
    "REBUILD": ServerStatus.REBUILDING,
    "DELETED": ServerStatus.OFF,
    "DELETING": ServerStatus.OFF,
}

# VPS task terminal outcomes (vps.TaskStateEnum): todo/doing are pending;
# everything terminal except done is a failure.
_TASK_FAILED = {"error", "blocked", "cancelled"}
_TASK_PENDING = {"todo", "doing", "paused", "waitingAck"}

# Module-level price cache shared by all OVH account instances:
# (project, region) -> {instanceId: monthly Money}
_region_pricings: dict[tuple[float, dict]] | None = None
REGION_PRICINGS_TTL = 24 * 3600

# Egress included in all regions except Singapore/Sydney (1 TB per PROJECT
# there) - pricing page "Public Traffic Instance". No per-instance quota
# number exists, so included_bytes is never a number.
CLOUD_WINDOW = ("outgoing (egress) traffic, current calendar month; "
                "egress included in all regions except Singapore/Sydney, "
                "where 1 TB is included per project")

CAPABILITIES = frozenset({
    Capability.POWER_ON, Capability.POWER_OFF, Capability.REBOOT,
    Capability.SHUTDOWN, Capability.RENAME, Capability.DELETE,
    Capability.IP_ADD, Capability.IP_RELEASE, Capability.IP_CHANGE,
})

# Additional IP for a VPS: the order cart (the old /order/vps/{sn}/ip path
# is gone). Checkout NEVER auto-pays: it creates an unpaid order whose
# url the operator pays - a wrong order can simply be left unpaid.
# Unverified until tested live: the cart configuration labels (destination
# = the VPS serviceName, country = its datacenter's country).
IP_PLAN_CODE = "ip-failover-ripe"
POLL_IP_RELEASE_S = 120


class OvhAdapter(ProviderAdapter):
    """Fleet adapter: OVH VPS + Public Cloud instances under one account.

    provider_id is compound and self-routing (a bare cloud uuid cannot say
    which project owns it): "vps:{serviceName}" / "cloud:{project}:{uuid}".
    """
    key = "ovh"
    display_name = "OVHcloud"
    capabilities = CAPABILITIES

    def __init__(self, account_id: int, account_name: str, token: str, http=None):
        super().__init__(account_id, account_name, token, http)
        parts = token.split(":")
        if len(parts) == 3:
            self._ak, self._as, self._ck = parts  # classic API keys
            self._oauth = None
        elif len(parts) == 2:
            self._ak = None
            self._oauth = tuple(parts)  # client_id:client_secret
        else:
            raise AdapterError(
                "OVH credential must be 'application_key:application_secret:"
                "consumer_key' (API keys) or 'client_id:client_secret' "
                "(OAuth2 service account)")
        # Bootstrap client: auth/time + token endpoint must NOT be signed/Bearer'd
        self._raw = httpx.AsyncClient(timeout=phttp.TIMEOUT_S, transport=http)
        self._time_delta: int | None = None
        self._bearer: str | None = None
        self._bearer_expires: float = 0.0
        self._bearer_lock = asyncio.Lock()
        self.h = phttp.ProviderHttpClient(API, self._auth, transport=http)

    # -- auth ------------------------------------------------------------

    def _auth(self, req: httpx.Request) -> None:
        """Sync auth callable (http.py calls it per request, per retry):
        classic computes a fresh SHA1 signature; OAuth2 reads the cached
        bearer (refresh happens in the async _ensure_auth preamble)."""
        if self._oauth is not None:
            if self._bearer:
                req.headers["Authorization"] = f"Bearer {self._bearer}"
            return
        ts = str(int(time.time() + (self._time_delta or 0)))
        body = req.content.decode("utf-8", "replace") if req.content else ""
        digest = "$1$" + hashlib.sha1(
            f"{self._as}+{self._ck}+{req.method}+{str(req.url)}+{body}+{ts}"
            .encode()).hexdigest()
        req.headers.update({
            "X-Ovh-Application": self._ak,
            "X-Ovh-Consumer": self._ck,
            "X-Ovh-Timestamp": ts,
            "X-Ovh-Signature": digest,
        })

    async def _ensure_auth(self) -> None:
        """Async preamble (called at the top of every fleet entry point):
        classic syncs the server-time delta once; OAuth2 refreshes the
        bearer near expiry (60s margin, under a lock - the sync loop and
        an occasional run_action can overlap)."""
        if self._oauth is not None:
            if self._bearer and time.monotonic() < self._bearer_expires:
                return
            async with self._bearer_lock:
                if self._bearer and time.monotonic() < self._bearer_expires:
                    return
                cid, secret = self._oauth
                r = await self._raw.post(OAUTH_TOKEN_URL, data={
                    "grant_type": "client_credentials",
                    "client_id": cid, "client_secret": secret, "scope": "all",
                })
                if r.status_code != 200:
                    raise AdapterError(f"OVH OAuth2 token request failed: "
                                       f"{r.status_code}")
                data = r.json()
                self._bearer = data["access_token"]
                self._bearer_expires = time.monotonic() + data.get("expires_in", 3599) - 60
            return
        if self._time_delta is None:
            r = await self._raw.get(AUTH_TIME_URL)
            if r.status_code != 200:
                raise AdapterError(f"OVH /auth/time failed: {r.status_code}")
            self._time_delta = int(r.json()) - int(time.time())

    # -- reads -----------------------------------------------------------

    async def list_servers(self) -> list[Server]:
        await self._ensure_auth()
        servers = []
        # VPS: one list + N+1 per service (no pagination on /1.0)
        for sn in await self.h.get_json("/vps"):
            servers.append(await self._vps_server(sn))
        # Public Cloud: one project list + instances per project
        for project in await self.h.get_json("/cloud/project"):
            rows = await self.h.get_json(f"/cloud/project/{project}/instance")
            for row in rows:
                servers.append(await self._cloud_server(project, row))
        return servers

    async def get_server(self, provider_id: str) -> Server:
        await self._ensure_auth()
        if provider_id.startswith("vps:"):
            return await self._vps_server(provider_id[4:])
        if provider_id.startswith("cloud:"):
            _, project, instance_id = provider_id.split(":", 2)
            row = await self.h.get_json(
                f"/cloud/project/{project}/instance/{instance_id}")
            return await self._cloud_server(project, row)
        raise AdapterError(f"unknown OVH provider_id: {provider_id}")

    async def _vps_ips(self, sn: str) -> list[IpAddress]:
        """Every address on the VPS: type primary|additional per the spec."""
        out: list[IpAddress] = []
        for ip in await self.h.get_json(f"/vps/{sn}/ips"):
            d = await self.h.get_json(f"/vps/{sn}/ips/{ip}")
            addr = d.get("ipAddress") or ip
            if any(x.address == addr for x in out):
                continue
            out.append(IpAddress(address=addr, version=6 if d.get("version") == "v6" else 4,
                                 primary=d.get("type") != "additional",
                                 kind=str(d.get("type") or "primary")))
        return out

    async def _vps_server(self, sn: str) -> Server:
        row = await self.h.get_json(f"/vps/{sn}")
        infos = await self.h.get_json(f"/vps/{sn}/serviceInfos")
        ips = await self._vps_ips(sn)
        ipv4 = next((i.address for i in ips if i.version == 4 and i.primary),
                    next((i.address for i in ips if i.version == 4), None))
        model = row.get("model") or {}
        facets = [
            Facet(label="product", value="vps"),
            Facet(label="offer", value=str(row.get("offerType") or "?")),
        ]
        if model.get("name"):
            facets.append(Facet(label="model", value=str(model["name"])))
        # vcore is a raw count; memory/disk are bare longs with NO unit
        # stated in the spec - rendered, never "GB" the API didn't say.
        if model.get("vcore") is not None:
            facets.append(Facet(label="vcore", value=str(model["vcore"])))
        lock = (row.get("lockStatus") or {}).get("locked")
        if lock:
            reason = (row.get("lockStatus") or {}).get("reason") or "?"
            facets.append(Facet(label="lock", value=str(reason)))
        return Server(
            provider_id=f"vps:{row['name']}",
            name=row.get("displayName") or row["name"],
            adapter=self.key,
            account_id=self.account_id,
            status=VPS_STATE_MAP.get(row.get("state"), ServerStatus.UNKNOWN),
            ipv4=ipv4,
            region=row.get("zone"),
            server_type=str(model.get("name") or row.get("offerType") or ""),
            created=parse_dt(infos.get("creation")),
            labels=None,  # only IAM tags exist (computed, not free-form)
            monthly_price=None,  # the /vps API carries no price at all
            allowance=None,      # unmetered product - no usage endpoint
            facets=facets,
            ips=ips,
            not_exposed=["traffic usage", "price", "labels"],
        )

    async def _cloud_server(self, project: str, row: dict) -> Server:
        sid = str(row["id"])
        region = row.get("region")
        flavor = row.get("flavor") or {}
        price = None
        if (row.get("monthlyBilling") or {}).get("status") == "ok":
            price = await self._monthly_price(project, region, sid)
        facets = [
            Facet(label="product", value="public cloud"),
            Facet(label="project", value=project),
        ]
        if flavor.get("name"):
            facets.append(Facet(label="flavor", value=str(flavor["name"])))
        if flavor.get("vcpus") is not None:
            facets.append(Facet(label="vcore", value=str(flavor["vcpus"])))
        # ram's spec description says Gio - citable, unlike the VPS model
        if flavor.get("ram") is not None:
            facets.append(Facet(label="ram", value=f"{flavor['ram']} Gio"))
        monthly_billing = row.get("monthlyBilling") or {}
        if monthly_billing.get("status") == "ok":
            facets.append(Facet(label="billing", value="monthly"))
        elif monthly_billing:
            facets.append(Facet(label="billing", value="hourly"))
        ipv4 = None
        ips = []
        for ip in row.get("ipAddresses") or []:
            if ip.get("version") == 4 and not ipv4:
                ipv4 = ip.get("ip")
            if ip.get("ip") and ip.get("type") != "private":
                ips.append(IpAddress(address=ip["ip"], version=int(ip.get("version") or 4),
                                     primary=True, kind=str(ip.get("type") or "public")))
        allowance = None
        used = row.get("currentMonthOutgoingTraffic")
        if used is not None:
            allowance = Allowance(
                included_bytes=None,  # 1 TB is per PROJECT (SGP1/SYD1 only)
                used_bytes=int(used),
                counting=TrafficCounting.OUTGOING_ONLY,
                window=CLOUD_WINDOW,
                reset_at=_next_month_utc(),
            )
        return Server(
            provider_id=f"cloud:{project}:{sid}",
            name=row.get("name", ""),
            adapter=self.key,
            account_id=self.account_id,
            status=CLOUD_STATUS_MAP.get(row.get("status"), ServerStatus.UNKNOWN),
            ipv4=ipv4,
            region=region,
            server_type=str(flavor.get("name") or row.get("flavorId") or ""),
            created=parse_dt(row.get("created")),
            labels=None,
            monthly_price=price,
            allowance=allowance,
            facets=facets,
            ips=ips,
            not_exposed=["labels", "overage price"],
        )

    async def _monthly_price(self, project: str, region: str | None,
                             instance_id: str) -> Money | None:
        """Regional listing is the ONLY price source (instance/flavor carry
        none). Cached per (project, region), 24h - the leaseweb
        /instanceTypes pattern. Only called for monthly-billed instances;
        hourly instances keep monthly_price=None (never hourly x 730)."""
        global _region_pricings
        if region is None:
            return None
        cache = (_region_pricings or {}).get("t") or {}
        ts = (_region_pricings or {}).get("ts", 0.0)
        if _region_pricings is None or time.monotonic() - ts > REGION_PRICINGS_TTL:
            cache = {}
            _region_pricings = {"t": cache, "ts": time.monotonic()}
        key = (project, region)
        if key not in cache:
            try:
                rows = await self.h.get_json(
                    f"/cloud/project/{project}/region/{region}/instance")
            except AdapterError:
                return None  # not cached: a blip = one missed price, not a day
            prices: dict[str, Money] = {}
            for r in rows:
                for pr in r.get("pricings") or []:
                    if pr.get("type") == "month":
                        p = (pr.get("price") or {})
                        if p.get("value") is not None:
                            prices[str(r.get("id"))] = Money(
                                amount=Decimal(str(p["value"])),
                                currency=p.get("currencyCode", "EUR"),
                                vat_inclusive=p.get("includeVat"),
                            )
            cache[key] = prices
        return cache.get(key, {}).get(instance_id)

    # -- actions ----------------------------------------------------------

    async def perform_action(self, cap: Capability, server_id: str,
                             params: dict[str, Any]) -> ActionResult:
        if cap not in self.capabilities:
            raise AdapterError(f"ovh does not support {cap.value}")
        # the VPS-delete refusal happens before ANY http call (not even
        # the auth preamble) - a refusal must not touch the provider
        if cap == Capability.DELETE and server_id.startswith("vps:"):
            raise AdapterError(
                "OVH VPS deletion is a deliberate two-step confirmation "
                "(terminate + confirmTermination) in the OVH manager - "
                "the panel does not automate it")
        await self._ensure_auth()
        if server_id.startswith("vps:"):
            return await self._vps_action(cap, server_id[4:], params)
        if server_id.startswith("cloud:"):
            _, project, sid = server_id.split(":", 2)
            return await self._cloud_action(cap, project, sid, params)
        raise AdapterError(f"unknown OVH provider_id: {server_id}")

    # -- IPs: VPS additional IPs ------------------------------------------------

    @staticmethod
    def _vps_sn(server_id: str) -> str:
        if not server_id.startswith("vps:"):
            raise AdapterError("additional IPs are managed for OVH VPS only, "
                               "not Public Cloud instances")
        return server_id[4:]

    async def add_ip(self, server_id: str) -> IpAddress:
        sn = self._vps_sn(server_id)
        await self._ensure_auth()
        me = await self.h.get_json("/me")
        dc = await self.h.get_json(f"/vps/{sn}/datacenter")
        country = str(dc.get("country") or me.get("ovhSubsidiary") or "FR").upper()
        await self._cart_checkout("ip", {"planCode": IP_PLAN_CODE, "duration": "P1M",
                                         "pricingMode": "default", "quantity": 1},
                                  [("destination", sn), ("country", country)],
                                  f"extra IP for {sn}")
        raise AdapterError("unreachable")  # _cart_checkout always raises

    async def _cart_checkout(self, product: str, item: dict,
                             config: list[tuple[str, str]], what: str) -> None:
        """Cart -> item -> configuration -> checkout, every POST sent once.
        Checkout never auto-pays: it always ends in PaymentRequired with
        the order's pay URL (a wrong order is simply left unpaid)."""
        me = await self.h.get_json("/me")

        async def post(path: str, body: dict | None = None) -> dict:
            r = await self.h.request("POST", path, json=body, retry=False)
            self.h._raise_for_status(r)
            return r.json() if r.content else {}

        cart = await post("/order/cart", {"ovhSubsidiary": me.get("ovhSubsidiary") or "FR",
                                          "description": f"omnicloud: {what}"})
        cid = cart["cartId"]
        await post(f"/order/cart/{cid}/assign")
        added = await post(f"/order/cart/{cid}/{product}", item)
        iid = added["itemId"]
        for label, value in config:
            await post(f"/order/cart/{cid}/item/{iid}/configuration",
                       {"label": label, "value": value})
        order = await post(f"/order/cart/{cid}/checkout", {
            "autoPayWithPreferredPaymentMethod": False, "waiveRetractationPeriod": False})
        raise PaymentRequired(what, str(order.get("orderId", "?")), order.get("url"))

    async def provision(self, plan_name: str, location: str, options: dict) -> str:
        """VPS through the order cart (planCode + vps_datacenter [+ vps_os]):
        an unpaid order; the VPS appears in the fleet once paid and
        delivered. Unverified until tested live: the vps_os value format."""
        await self._ensure_auth()
        config = [("vps_datacenter", location)]
        if options.get("image"):
            config.append(("vps_os", str(options["image"])))
        await self._cart_checkout("vps", {"planCode": plan_name, "duration": "P1M",
                                          "pricingMode": "default", "quantity": 1},
                                  config, f"VPS {plan_name} in {location}")
        raise AdapterError("unreachable")  # _cart_checkout always raises

    async def release_ip(self, server_id: str, address: str) -> None:
        sn = self._vps_sn(server_id)
        await self._ensure_auth()
        ips = {i.address: i for i in await self._vps_ips(sn)}
        if address not in ips:
            return  # already gone
        if ips[address].primary:
            raise AdapterError(f"{address} is the VPS's primary IP - never released here")
        await self.h.delete(f"/vps/{sn}/ips/{address}")
        deadline = time.monotonic() + POLL_IP_RELEASE_S
        while address in await self.h.get_json(f"/vps/{sn}/ips"):
            if time.monotonic() >= deadline:
                raise ActionTimeout(f"release {address} from {sn}")
            await asyncio.sleep(POLL_INTERVAL)

    async def ip_cost(self, server_id: str) -> IpCost | None:
        try:
            data = await self.h.get_json("/order/catalog/formatted/ip",
                                         params={"ovhSubsidiary": "FR"})
        except AdapterError:
            data = {}
        note = ("ordered as an unpaid OVH order (pay it at the order link); "
                "billed monthly from delivery")
        for p in data.get("plans", []):
            if p.get("planCode") == IP_PLAN_CODE:
                price = OvhCatalogAdapter._monthly_price(p.get("pricings", []))
                if price is not None:
                    return IpCost(price=Money(amount=price, currency="EUR",
                                              vat_inclusive=False), per="month", note=note)
        return IpCost(price=None, per="month", note=note)

    # -- billing (/me): pay-per-order against a registered payment method ------

    @staticmethod
    def _money(p: dict | None) -> Money | None:
        """OVH order.Price {value, currencyCode, text}."""
        if not isinstance(p, dict) or p.get("value") is None:
            return None
        return Money(amount=Decimal(str(p["value"])), currency=p.get("currencyCode") or "EUR")

    async def get_billing(self) -> Billing:
        await self._ensure_auth()
        not_exposed = ["month_to_date", "upcoming"]
        now = datetime.now(timezone.utc)

        # prepaid credit (most accounts have none - they pay each order)
        balance = None
        try:
            for name in await self.h.get_json("/me/credit/balance"):
                b = await self.h.get_json(f"/me/credit/balance/{name}")
                m = self._money(b.get("amount"))
                if m is None or b.get("type") not in ("PREPAID_ACCOUNT", "DEPOSIT"):
                    continue
                if balance is None:
                    balance = m
                elif balance.currency == m.currency:
                    balance = Money(amount=balance.amount + m.amount, currency=m.currency)
        except AdapterError:
            pass
        if balance is None:
            not_exposed.append("balance")

        invoices = []
        since = (now - timedelta(days=90)).date().isoformat()
        for bid in sorted(await self.h.get_json("/me/bill", params={"date.from": since}),
                          reverse=True)[:12]:
            b = await self.h.get_json(f"/me/bill/{bid}")
            r = await self.h.request("GET", f"/me/bill/{bid}/debt")
            debt = r.json() if r.status_code == 200 else None
            invoices.append(Invoice(
                id=str(b.get("billId", bid)), date=parse_dt(b.get("date")),
                due_date=parse_dt(debt.get("dueDate")) if debt else None,
                total=self._money(b.get("priceWithTax")),
                open_amount=self._money(debt.get("dueAmount")) if debt else None,
                # the provider's own debt status; no debt record = nothing owed on it
                status=str(debt.get("status")) if debt else "no debt",
                url=b.get("url"),
            ))

        unpaid = []
        since = (now - timedelta(days=30)).date().isoformat()
        for oid in sorted(await self.h.get_json("/me/order", params={"date.from": since}),
                          reverse=True)[:15]:
            if await self.h.get_json(f"/me/order/{oid}/status") != "notPaid":
                continue
            o = await self.h.get_json(f"/me/order/{oid}")
            price = self._money(o.get("priceWithTax"))
            unpaid.append(Invoice(id=str(oid), date=parse_dt(o.get("date")),
                                  due_date=parse_dt(o.get("expirationDate")),
                                  total=price, open_amount=price, status="notPaid",
                                  url=o.get("url")))

        renewals = []
        for sn in await self.h.get_json("/vps"):
            infos = await self.h.get_json(f"/vps/{sn}/serviceInfos")
            renew = infos.get("renew") or {}
            renewals.append(Renewal(provider_id=f"vps:{sn}", name=sn,
                                    date=parse_dt(infos.get("expiration")),
                                    auto=renew.get("automatic") if renew else None))
        return Billing(model="pay per order; services renew monthly against the "
                             "registered payment method",
                       balance=balance, invoices=invoices, unpaid_orders=unpaid,
                       renewals=renewals, not_exposed=not_exposed)

    async def order_status(self, order_ref: str) -> str | None:
        await self._ensure_auth()
        st = await self.h.get_json(f"/me/order/{order_ref}/status")
        return {"delivered": "delivered", "cancelled": "cancelled",
                "notPaid": "unpaid"}.get(st)

    async def _vps_action(self, cap: Capability, sn: str,
                          params: dict) -> ActionResult:
        if cap == Capability.RENAME:
            r = await self.h.put_json(f"/vps/{sn}", {"displayName": params["name"]})
            row = r.json()
            if row.get("displayName") != params["name"]:
                raise AdapterError("rename not confirmed by provider view")
            return ActionResult(detail="renamed")
        # power family: POST start/stop/reboot -> vps.Task; poll to done.
        verb = {Capability.POWER_ON: "start", Capability.POWER_OFF: "stop",
                Capability.REBOOT: "reboot", Capability.SHUTDOWN: "stop"}[cap]
        r = await self.h.post_json(f"/vps/{sn}/{verb}", {})
        task = r.json()
        return await self._poll_task(sn, int(task["id"]))

    async def _poll_task(self, sn: str, task_id: int) -> ActionResult:
        """Poll GET /vps/{sn}/tasks/{id} until the task's own state is
        terminal. progress is a bare long with NO unit - never rendered."""
        deadline = time.monotonic() + POLL_BUDGET_POWER
        last = ""
        while time.monotonic() < deadline:
            t = await self.h.get_json(f"/vps/{sn}/tasks/{task_id}")
            last = str(t.get("state", ""))
            if last == "done":
                return ActionResult(detail=f"task {task_id} done")
            if last in _TASK_FAILED:
                raise AdapterError(f"OVH task {task_id} {last}")
            await asyncio.sleep(POLL_INTERVAL)
        raise ActionTimeout(f"OVH task {task_id} (last state {last})")

    async def _cloud_action(self, cap: Capability, project: str, sid: str,
                            params: dict) -> ActionResult:
        base = f"/cloud/project/{project}/instance/{sid}"
        if cap == Capability.RENAME:
            r = await self.h.put_json(base, {"instanceName": params["name"]})
            # void-style PUT: confirm via the provider's own view
            fresh = await self.h.get_json(base)
            if fresh.get("name") != params["name"]:
                raise AdapterError("rename not confirmed by provider view")
            return ActionResult(detail="renamed")
        if cap == Capability.DELETE:
            await self.h.delete(base)  # void response - never success
            deadline = time.monotonic() + POLL_BUDGET_DELETE
            while time.monotonic() < deadline:
                try:
                    row = await self.h.get_json(base)
                except AdapterError as e:
                    # Only a real 404 confirms deletion. An auth failure or
                    # persistent 5xx re-raises - treating any error as
                    # "deleted" would hide real failures as successes.
                    if e.status_code == 404:
                        return ActionResult(detail="deleted (instance gone)")
                    raise
                if str(row.get("status", "")).startswith("DELETED"):
                    return ActionResult(detail="deleted")
                await asyncio.sleep(POLL_INTERVAL)
            raise ActionTimeout(f"OVH delete of instance {sid}")
        # power family: void responses - poll the instance status
        verb = {Capability.POWER_ON: "start", Capability.POWER_OFF: "stop",
                Capability.REBOOT: "reboot", Capability.SHUTDOWN: "stop"}[cap]
        body = {"type": "soft"} if verb == "reboot" else {}
        await self.h.post_json(f"{base}/{verb}", body)
        # stop lands in SHUTOFF or STOPPED (both map to OFF on the read
        # side, CLOUD_STATUS_MAP) - accept either, never just one.
        want = ("ACTIVE",) if verb in ("start", "reboot") else ("SHUTOFF", "STOPPED")
        deadline = time.monotonic() + POLL_BUDGET_POWER
        last = ""
        while time.monotonic() < deadline:
            row = await self.h.get_json(base)
            last = str(row.get("status", ""))
            if last in want:
                return ActionResult(detail=f"status now {last}")
            await asyncio.sleep(POLL_INTERVAL)
        raise ActionTimeout(f"OVH {verb} on instance {sid} (last status {last})")

    async def list_images(self) -> list[dict]:
        # /api/catalog/images calls this when an enabled ovh account exists
        # (ovh is in catalog.LIVE); no OVH images needed for the prototype
        # order pipeline. An honest empty list, never an error.
        return []

    async def close(self) -> None:
        await self.h.aclose()
        await self._raw.aclose()


def _next_month_utc() -> datetime:
    now = datetime.now(timezone.utc)
    if now.month == 12:
        return now.replace(year=now.year + 1, month=1, day=1,
                           hour=0, minute=0, second=0, microsecond=0)
    return now.replace(month=now.month + 1, day=1,
                       hour=0, minute=0, second=0, microsecond=0)


CATALOG_URL = "https://eu.api.ovh.com/1.0/order/catalog/public/vps"
IP_CATALOG_URL = "https://eu.api.ovh.com/1.0/order/catalog/formatted/ip"

# Per the Additional IP page: up to 16 individual IPs on a VPS; blocks not
# supported at VPS level. Price from the IP catalog (1.99 EUR/IP/mo).
VPS_IP_LIMIT = 16

# Marketing-published bandwidth caps per VPS model tier (public VPS page).
BANDWIDTH_NOTE = "unlimited traffic (fair-use); public bandwidth 500 Mbps-3 Gbps by model"


class OvhCatalogAdapter(ProviderAdapter):
    """Catalog-only: VPS order catalog (tokenless public endpoint). Fleet
    management lives in OvhAdapter above (same key, both registries -
    accounts.ADAPTERS uses the fleet class, catalog.LIVE this one)."""
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
        quantized to 2 places so 580000000 renders as 5.80, not 5.8).
        A pricing without a price is skipped - publishing 0.00 would be an
        invented price, and callers already treat None as not-sold."""
        for pr in pricings:
            if ("renew" in (pr.get("capacities") or [])
                    and pr.get("interval") == 1
                    and pr.get("intervalUnit") == "month"
                    and pr.get("price") is not None):
                return (Decimal(str(pr["price"])) / Decimal(10**8)).quantize(Decimal("0.01"))
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
