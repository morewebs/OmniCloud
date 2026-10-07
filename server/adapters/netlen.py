"""Netlen fleet adapter (api.netlen.com.tr/v2) - Istanbul VDS.

Facts (netlen.com.tr/api, v2.0.3, verified 2026-10-07; docs/provider-truth.md):
- Auth: "Authorization: Bearer <api_key>"; the key is bound to an IP
  allowlist - the panel's egress IP must be on it (403 AUTH_IP_NOT_ALLOWED).
- Envelope: success = {data, meta{request_id, version, pagination{page,
  per_page, total, total_pages}}}; errors = {error{code, message, errors[]}}.
  Pagination page/per_page (max 100).
- Server: id ("NET10231"), name, status active|suspended|pending|cancelled,
  power_state running|stopped|starting|stopping|provisioning|migrating|
  error|unknown (null in LIST results - the detail GET carries it), specs
  {cpu_cores, ram_mb, storage_gb}, network{ipv4{address,...}, ipv6{...},
  extra_ips[{address, version}]}, plan{id, name}, billing{amount, currency,
  cycle monthly|yearly, next_billing_at, deletable}, created_at.
- Actions: POST /servers/{id}/actions/{start|stop|reboot} -> 202
  data.operation{id, status}; GET /operations/{id} queued|running|
  completed|failed. Confirmed on the server's own power_state.
- Extra IP: POST /servers/{id}/ips {version: 4} -> 201 {address, price,
  billing_cycle, charged, balance}: charged to the account balance at once
  (422 INSUFFICIENT_BALANCE / CAPACITY_UNAVAILABLE). There is NO endpoint
  to release an IP - so add only, never change.
- Billing: GET /billing/balance {balance{amount, currency}}; invoices are
  panel-only (the API answers 501).
"""
from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Any

from . import http as phttp
from .base import (
    ActionResult, ActionTimeout, AdapterError, Billing, Capability, Facet,
    IpAddress, IpCost, Money, ProviderAdapter, Renewal, Server, ServerStatus,
    UnsupportedAction, parse_dt,
)

API = "https://api.netlen.com.tr/v2"

CAPABILITIES = frozenset({
    Capability.POWER_ON, Capability.POWER_OFF, Capability.REBOOT,
    Capability.SHUTDOWN, Capability.IP_ADD,
})

VERB = {Capability.POWER_ON: "start", Capability.POWER_OFF: "stop",
        Capability.SHUTDOWN: "stop", Capability.REBOOT: "reboot"}
TARGET = {Capability.POWER_ON: "running", Capability.POWER_OFF: "stopped",
          Capability.SHUTDOWN: "stopped", Capability.REBOOT: "running"}

POWER_MAP = {
    "running": ServerStatus.RUNNING,
    "stopped": ServerStatus.OFF,
    "provisioning": ServerStatus.REBUILDING,
    "migrating": ServerStatus.REBUILDING,
}

POLL_BUDGET_S = 180
POLL_INTERVAL_S = 3.0


class NetlenAdapter(ProviderAdapter):
    key = "netlen"
    display_name = "Netlen"
    capabilities = CAPABILITIES

    def __init__(self, account_id: int, account_name: str, token: str, http=None):
        super().__init__(account_id, account_name, token, http)
        self.h = phttp.ProviderHttpClient(
            API, lambda req: req.headers.__setitem__("Authorization", f"Bearer {token}"),
            transport=http)

    async def close(self) -> None:
        await self.h.aclose()

    async def _data(self, path: str, params: dict | None = None) -> Any:
        return (await self.h.get_json(path, params)).get("data")

    async def _rows(self) -> list[dict]:
        rows, page = [], 1
        while True:
            body = await self.h.get_json("/servers", {"page": page, "per_page": 100})
            rows.extend(body.get("data") or [])
            pages = ((body.get("meta") or {}).get("pagination") or {}).get("total_pages", 1)
            if page >= pages:
                return rows
            page += 1

    async def list_servers(self) -> list[Server]:
        rows = await self._rows()
        # power_state is null in list results - the detail GET has it
        details = await asyncio.gather(*[self._data(f"/servers/{r['id']}") for r in rows])
        return [self._server(d or r) for r, d in zip(rows, details)]

    async def get_server(self, provider_id: str) -> Server:
        return self._server(await self._data(f"/servers/{provider_id}"))

    def _server(self, row: dict) -> Server:
        net = row.get("network") or {}
        primary = (net.get("ipv4") or {}).get("address")
        ips = []
        if primary:
            ips.append(IpAddress(address=primary, primary=True, kind="primary"))
        for x in net.get("extra_ips") or []:
            if x.get("address"):
                ips.append(IpAddress(address=x["address"], version=int(x.get("version") or 4),
                                     primary=False, kind="extra"))
        status = POWER_MAP.get(str(row.get("power_state") or ""), ServerStatus.UNKNOWN)
        if row.get("status") in ("suspended", "cancelled"):
            status = ServerStatus.OFF
        specs = row.get("specs") or {}
        bill = row.get("billing") or {}
        facets = [Facet(label="service", value=str(row.get("status") or "?"))]
        for label, key, unit in (("cores", "cpu_cores", ""), ("ram", "ram_mb", " MB"),
                                 ("disk", "storage_gb", " GB")):
            if specs.get(key) is not None:
                facets.append(Facet(label=label, value=f"{specs[key]}{unit}"))
        if bill.get("next_billing_at"):
            facets.append(Facet(label="next billing", value=str(bill["next_billing_at"])[:10]))
        monthly = None
        if bill.get("amount") is not None:
            if bill.get("cycle") == "monthly":
                monthly = Money(amount=Decimal(str(bill["amount"])),
                                currency=bill.get("currency") or "USD")
            else:  # a yearly price is shown as billed, never divided into months
                facets.append(Facet(label="billing", value=f"{bill['amount']} "
                                    f"{bill.get('currency', '')} / {bill.get('cycle', '?')}"))
        return Server(
            provider_id=str(row["id"]), name=row.get("name") or str(row["id"]),
            adapter=self.key, account_id=self.account_id, status=status,
            ipv4=primary, region=str(row["location_id"]) if row.get("location_id") else None,
            server_type=(row.get("plan") or {}).get("name"),
            created=parse_dt(row.get("created_at")), monthly_price=monthly,
            facets=facets, ips=ips,
            # Netlen's servers carry no traffic counters (plans are unmetered)
            not_exposed=["allowance", "labels"],
        )

    async def perform_action(self, cap: Capability, server_id: str,
                             params: dict[str, Any]) -> ActionResult:
        if cap not in VERB:
            raise UnsupportedAction(self.key, cap)
        r = await self.h.post_json(f"/servers/{server_id}/actions/{VERB[cap]}", {})
        op = ((r.json().get("data") or {}).get("operation") or {}).get("id")
        deadline = time.monotonic() + POLL_BUDGET_S
        if op:
            while True:
                st = (((await self.h.get_json(f"/operations/{op}")).get("data") or {})
                      .get("operation") or {}).get("status")
                if st == "completed":
                    break
                if st == "failed":
                    raise AdapterError(f"netlen {VERB[cap]} on {server_id} failed")
                if time.monotonic() >= deadline:
                    raise ActionTimeout(f"netlen {VERB[cap]} on {server_id}")
                await asyncio.sleep(POLL_INTERVAL_S)
        while True:  # the operation finishing is not the server being there
            s = await self._data(f"/servers/{server_id}")
            if s.get("power_state") == TARGET[cap]:
                return ActionResult(detail=f"power_state now {TARGET[cap]}")
            if time.monotonic() >= deadline:
                raise ActionTimeout(f"netlen {VERB[cap]} on {server_id}")
            await asyncio.sleep(POLL_INTERVAL_S)

    # -- IPs: add only (the API has no release) -----------------------------------

    async def add_ip(self, server_id: str) -> IpAddress:
        r = await self.h.request("POST", f"/servers/{server_id}/ips", retry=False,
                                 json={"version": 4})
        if r.status_code >= 400:
            err = (r.json() if r.content else {}).get("error") or {}
            raise AdapterError(f"netlen add IP: {err.get('code', r.status_code)} - "
                               f"{err.get('message', r.text[:200])}", status_code=r.status_code)
        addr = ((r.json().get("data") or r.json()) or {}).get("address")
        if not addr:
            raise AdapterError("netlen charged an IP but returned no address - check the panel")
        s = await self._data(f"/servers/{server_id}")
        if addr not in {x.get("address") for x in (s.get("network") or {}).get("extra_ips") or []}:
            raise AdapterError(f"{addr} not yet listed on {server_id} - it was charged; "
                               "the next sync shows it")
        return IpAddress(address=addr, primary=False, kind="extra")

    async def ip_cost(self, server_id: str) -> IpCost | None:
        try:
            addons = await self._data(f"/servers/{server_id}/addons")
        except AdapterError:
            return None
        v4 = ((addons or {}).get("extra_ip") or {}).get("ipv4") or {}
        price = v4.get("price")
        money = (Money(amount=Decimal(str(price["amount"])), currency=price.get("currency", "USD"))
                 if isinstance(price, dict) and price.get("amount") is not None else None)
        return IpCost(price=money, per="month",
                      note="charged from the balance at once (pro rata), then each billing "
                           "cycle; Netlen's API cannot release an IP")

    async def get_billing(self) -> Billing:
        bal = await self._data("/billing/balance")
        b = (bal or {}).get("balance") or {}
        balance = (Money(amount=Decimal(str(b["amount"])), currency=b.get("currency", "USD"))
                   if b.get("amount") is not None else None)
        # list rows carry billing.next_billing_at - no per-server detail GETs
        renewals = [Renewal(provider_id=str(r["id"]), name=r.get("name") or str(r["id"]),
                            date=parse_dt((r.get("billing") or {}).get("next_billing_at")),
                            auto=None)
                    for r in await self._rows()]
        return Billing(model="prepaid balance: servers and IPs are charged from it each "
                             "billing cycle",
                       balance=balance, renewals=[r for r in renewals if r.date],
                       not_exposed=["invoices", "month_to_date", "upcoming"]
                                   + ([] if balance else ["balance"]))
