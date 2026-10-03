"""LeaseWeb PublicCloud v1 adapter (api.leaseweb.com, X-LSW-Auth header).

Semantics verified against the official developer docs (developer.leaseweb.com,
Oct 2026) and the LeaseWeb metering/billing KB - see docs/provider-truth.md:
- Resource: GET /publicCloud/v1/instances (NOT the legacy /vps). Instance
  fields: reference (human name, not "name"), region (not "datacenter"),
  type (e.g. lsw.m3.large), state, ips[], contract, startedAt.
- Traffic counting: Public Cloud instances bill EGRESS ONLY ("Public Cloud,
  VPS and Object Storage do not charge incoming traffic") - same direction
  as Hetzner, NOT both directions. upPublic is the billable series.
- Metrics response: {metrics: {downPublic: {values: [{value, timestamp}], unit},
  upPublic: {...}}, _metadata: {summary: {downPublic: {total}, upPublic: {total}}}}.
  values are integers in BYTES. Monthly usage = upPublic (egress only).
- Allowance: Public Cloud includes 1 TB per ACCOUNT (KB), not per instance;
  instance contracts carry no dataTraffic field (that exists only on legacy
  /vps). included_bytes is therefore marked not-exposed at the server level.
- Power: POST /instances/{id}/start|stop|reboot (all 202, empty body);
  there is no powerOn/powerOff/shutdown. State enum: CREATING, DESTROYED,
  DESTROYING, FAILED, RUNNING, STARTING, STOPPED, STOPPING, UNKNOWN.
- Rename: PUT /instances/{id} {"reference": ...} - "name" is not a field.
- Delete: DELETE /instances/{id}; MONTHLY contracts REQUIRE a reasonCode body
  and terminate at contract end (DELETE_SCHEDULED); HOURLY terminate now.
- Price: not on the instance, but GET /instanceTypes?region= exposes
  {hourly, monthly} per type - fetched per region and joined by type.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from . import http as phttp
from .base import (
    ActionTimeout, ActionResult, AdapterError, Allowance, Capability, Facet,
    IpOffer, Money, Plan, ProviderAdapter, Server, ServerStatus,
    TrafficCounting,
)

API = "https://api.leaseweb.com/publicCloud/v1"

# Verified state enum (developer.leaseweb.com instance schema).
STATE_MAP = {
    "RUNNING": ServerStatus.RUNNING,
    "STARTING": ServerStatus.UNKNOWN,
    "STOPPING": ServerStatus.UNKNOWN,
    "STOPPED": ServerStatus.OFF,
    "CREATING": ServerStatus.UNKNOWN,
    "DESTROYING": ServerStatus.UNKNOWN,
    "DESTROYED": ServerStatus.OFF,
    "FAILED": ServerStatus.UNKNOWN,
    "UNKNOWN": ServerStatus.UNKNOWN,
}

POLL_BUDGET = {
    Capability.POWER_ON: 120, Capability.POWER_OFF: 120,
    Capability.REBOOT: 120, Capability.SHUTDOWN: 120,
    Capability.RENAME: 30, Capability.DELETE: 60,
}
POLL_INTERVAL = 2.0

# Verified power verbs: start/stop/reboot only.
POWER_VERB = {
    Capability.POWER_ON: "start",
    Capability.POWER_OFF: "stop",
    Capability.REBOOT: "reboot",
    Capability.SHUTDOWN: "stop",  # no distinct shutdown; stop is the verb
}

CAPABILITIES = frozenset({
    Capability.POWER_ON, Capability.POWER_OFF, Capability.REBOOT, Capability.SHUTDOWN,
    Capability.RENAME, Capability.DELETE,
    # relabel removed: instances have no labels in the API
})

# Public Cloud includes 1 TB egress per ACCOUNT (KB) - allowance is an
# account-level fact, rendered at server level as not-exposed.
ACCOUNT_ALLOWANCE_NOTE = "included per account, not per instance"

# reasonCode required for MONTHLY-contract termination (docs: terminateInstance).
DELETE_REASON_CODE = "TERMINATED_BY_CUSTOMER"

_instance_types: dict[float, dict[str, dict]] = {}  # monotonic-ts -> {type_name: {monthly, hourly}}
TYPES_TTL = 24 * 3600


class LeasewebAdapter(ProviderAdapter):
    key = "leaseweb"
    display_name = "LeaseWeb"
    capabilities = CAPABILITIES

    def __init__(self, account_id: int, account_name: str, token: str, http=None):
        super().__init__(account_id, account_name, token, http)
        self.h = phttp.ProviderHttpClient(
            API, lambda req: req.headers.__setitem__("X-LSW-Auth", token),
            transport=http,
        )

    # -- reads -----------------------------------------------------------

    async def list_servers(self) -> list[Server]:
        rows, offset, total = [], 0, 1
        while offset < total:
            data = await self.h.get_json("/instances", params={"limit": 50, "offset": offset})
            rows.extend(data.get("instances", []))
            total = data.get("_metadata", {}).get("totalCount", len(rows))
            offset += 50
        return [await self._server(row) for row in rows]

    async def get_server(self, provider_id: str) -> Server:
        row = await self.h.get_json(f"/instances/{provider_id}")
        return await self._server(row)

    async def _server(self, row: dict) -> Server:
        sid = str(row.get("id"))
        contract = row.get("contract") or {}
        region = row.get("region")
        itype = row.get("type")

        used_bytes = await self._monthly_egress(sid)
        price = await self._price_for(region, itype)

        allowance = None
        if used_bytes is not None:
            allowance = Allowance(
                included_bytes=None,  # allowance is per account (1TB), not per instance
                used_bytes=used_bytes,
                counting=TrafficCounting.OUTGOING_ONLY,
                window="outgoing (egress) traffic, current calendar month; "
                       "1 TB included per account",
                reset_at=_next_month_utc(),
            )

        ipv4 = next((ip["ip"] for ip in (row.get("ips") or [])
                     if ip.get("version") == 4 and ip.get("mainIp") is not False), None)
        # fallback: any v4
        if ipv4 is None:
            ipv4 = next((ip["ip"] for ip in (row.get("ips") or [])
                         if ip.get("version") == 4), None)

        facets = []
        if itype:
            facets.append(Facet(label="instance type", value=str(itype)))
        speed = (row.get("resources") or {}).get("publicNetworkSpeed") or {}
        if speed.get("value"):
            facets.append(Facet(label="port speed", value=f"{speed['value']} {speed.get('unit', 'Gbps')}"))
        if contract.get("type"):
            facets.append(Facet(label="contract", value=f"{contract['type'].lower()} / "
                                                       f"{contract.get('billingFrequency', '?')} mo"))
        if row.get("rootDiskSize"):
            facets.append(Facet(label="root disk", value=f"{row['rootDiskSize']} GB"))

        return Server(
            provider_id=sid,
            name=row.get("reference") or sid,  # "name" is not a field; reference is
            adapter=self.key,
            account_id=self.account_id,
            status=STATE_MAP.get(str(row.get("state", "")).upper(), ServerStatus.UNKNOWN),
            ipv4=ipv4,
            region=region,
            server_type=itype,
            created=_dt(row.get("startedAt")),
            labels=None,
            monthly_price=price,
            allowance=allowance,
            facets=facets,
            not_exposed=["labels", "allowance_bytes_per_instance"],
        )

    async def _monthly_egress(self, sid: str) -> int | None:
        """Egress-only monthly usage: sum of upPublic values (bytes) over the
        current calendar month. Ingress (downPublic) is free for Public Cloud."""
        now = datetime.now(timezone.utc)
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        params = {
            "from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "granularity": "DAY", "aggregation": "SUM",
        }
        try:
            data = await self.h.get_json(f"/instances/{sid}/metrics/datatraffic", params)
        except AdapterError:
            return None  # metric missing = "-", never zero
        metrics = data.get("metrics") or {}
        up = (metrics.get("upPublic") or {}).get("values") or []
        total = sum(int(v.get("value", 0)) for v in up)
        return total if up else None

    async def _price_for(self, region: str | None, itype: str | None) -> Money | None:
        """Per-instance monthly price via GET /instanceTypes?region= - not on
        the instance object itself."""
        if not region or not itype:
            return None
        cache = _instance_types.get("t")
        ts = _instance_types.get("ts", 0)
        if cache is None or time.monotonic() - ts > TYPES_TTL:
            cache = {}
            try:
                data = await self.h.get_json("/instanceTypes", params={"region": region})
                for t in data.get("instanceTypes", []):
                    cache[t.get("name", "")] = t.get("prices") or {}
            except AdapterError:
                _instance_types.clear()
                _instance_types["t"] = {}
                _instance_types["ts"] = time.monotonic()
                return None
            _instance_types.clear()
            _instance_types["t"] = cache
            _instance_types["ts"] = time.monotonic()
        prices = (cache or {}).get(itype, {})
        monthly = prices.get("monthly")
        if monthly is None:
            return None
        return Money(amount=Decimal(str(monthly)), currency="EUR", vat_inclusive=None)

    # -- plan catalog -----------------------------------------------------

    async def list_plans(self) -> list[Plan]:
        """All instance types across the regions the API exposes, with the
        resources (cpu/ram/disk) the type schema carries. One Plan per
        (type, region)."""
        plans = []
        for region in await self._regions():
            try:
                data = await self.h.get_json("/instanceTypes", params={"region": region})
            except AdapterError:
                continue  # one region's failure must not kill the catalog
            for t in data.get("instanceTypes", []):
                res = t.get("resources") or {}
                prices = t.get("prices") or {}
                monthly = prices.get("monthly")
                hourly = prices.get("hourly")
                disk = res.get("rootDiskSize") or t.get("rootDiskSize")
                plans.append(Plan(
                    adapter=self.key,
                    name=t.get("name", ""),
                    location=region,
                    cpu_cores=(res.get("cpu") or {}).get("value"),
                    ram_gb=(res.get("memory") or {}).get("value"),
                    disk_gb=disk,
                    disk_type=t.get("rootDiskStorageType", "").lower() or None,
                    price_monthly=Money(amount=Decimal(str(monthly)), currency="EUR",
                                        vat_inclusive=None) if monthly else None,
                    price_hourly=Money(amount=Decimal(str(hourly)), currency="EUR",
                                       vat_inclusive=None) if hourly else None,
                    included_traffic_bytes=None,  # allowance is per account (1TB), not per plan
                    counting=TrafficCounting.OUTGOING_ONLY,
                    extra_ip=IpOffer(
                        kind="public ipv4",
                        included=1,
                        price=None,  # not published in the API
                        note="additional IPs orderable; price not published in the API",
                    ),
                    billing_model="monthly invoice (term contracts) or hourly prepaid",
                ))
        return plans

    async def _regions(self) -> list[str]:
        try:
            data = await self.h.get_json("/regions")
            return [r.get("name", "") for r in data.get("regions", []) if r.get("name")]
        except AdapterError:
            # common known set as fallback; regions endpoint verified at impl time
            return ["eu-west-3"]

    async def list_images(self) -> list[dict]:
        """Orderable images: GET /images with PUBLIC availability."""
        try:
            data = await self.h.get_json("/images", params={"limit": 100, "offset": 0})
        except AdapterError:
            return []
        out = []
        for img in data.get("images", []):
            if img.get("availability") == "PUBLIC":
                out.append({"id": str(img.get("id")), "name": img.get("name", ""),
                            "os": img.get("family", ""), "version": img.get("version")})
        return out

    # -- actions ----------------------------------------------------------

    async def perform_action(self, cap: Capability, server_id: str,
                             params: dict[str, Any]) -> ActionResult:
        if cap not in self.capabilities:
            raise AdapterError(f"leaseweb does not support {cap.value}")
        if cap == Capability.RENAME:
            # "reference" is the update field; "name" is not a schema field.
            r = await self.h.put_json(f"/instances/{server_id}",
                                      {"reference": params["name"]})
            if r.status_code != 200:
                raise AdapterError("rename not confirmed")
            return ActionResult(detail="renamed")
        if cap == Capability.RELABEL:
            raise AdapterError("leaseweb does not support relabel")
        if cap == Capability.DELETE:
            # Monthly contracts require a reasonCode; hourly instances accept a
            # bare DELETE. Pass it always - hourly accepts it too.
            row = await self.h.get_json(f"/instances/{server_id}")
            contract = (row.get("contract") or {})
            if contract.get("type") == "MONTHLY":
                r = await self.h.request("DELETE", f"/instances/{server_id}",
                                         json={"reasonCode": DELETE_REASON_CODE})
            else:
                r = await self.h.delete(f"/instances/{server_id}")
            if r.status_code >= 400:
                raise AdapterError(f"delete failed: {r.status_code}")
            # Monthly termination is DEFERRED to contract end - state becomes
            # DELETE_SCHEDULED, not DESTROYED. Report which happened.
            detail = "delete scheduled at contract end" if contract.get("type") == "MONTHLY" \
                else "deleted"
            return ActionResult(detail=detail)
        # power family: verified verbs start/stop/reboot, 202 + empty body,
        # then poll the instance state.
        verb = POWER_VERB[cap]
        await self.h.post_json(f"/instances/{server_id}/{verb}", {})
        return await self._poll_state(server_id, cap)

    async def _poll_state(self, server_id: str, cap: Capability) -> ActionResult:
        """Poll until the instance state reflects the request. Verified enum:
        CREATING DESTROYED DESTROYING FAILED RUNNING STARTING STOPPED STOPPING
        UNKNOWN. start/reboot -> RUNNING; stop -> STOPPED."""
        want = {
            Capability.POWER_ON: {"RUNNING"},
            Capability.POWER_OFF: {"STOPPED"},
            Capability.REBOOT: {"RUNNING"},
            Capability.SHUTDOWN: {"STOPPED"},
        }[cap]
        deadline = time.monotonic() + POLL_BUDGET[cap]
        last = ""
        while time.monotonic() < deadline:
            row = await self.h.get_json(f"/instances/{server_id}")
            last = str(row.get("state", "")).upper()
            if last in want:
                return ActionResult(detail=f"state now {last}")
            await asyncio.sleep(POLL_INTERVAL)
        raise ActionTimeout(f"leaseweb power action on {server_id} (last state {last})")

    async def close(self) -> None:
        await self.h.aclose()


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _next_month_utc() -> datetime:
    now = datetime.now(timezone.utc)
    if now.month == 12:
        return now.replace(year=now.year + 1, month=1, day=1,
                           hour=0, minute=0, second=0, microsecond=0)
    return now.replace(month=now.month + 1, day=1,
                       hour=0, minute=0, second=0, microsecond=0)
