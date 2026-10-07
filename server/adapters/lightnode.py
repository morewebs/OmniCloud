"""LightNode fleet adapter (openapi.lightnode.com).

Facts (apidoc.lightnode.com, verified 2026-10-07; docs/provider-truth.md):
- Auth: header "x-open-token: <token>" (requested in the console under
  Account -> Token list; issued after review). No signing.
- GET /region/list -> {regions[{regionCode, regionName, zones[{zoneCode,
  zoneName}]}]}. GET /instance/list?regionCode&zoneCode (both REQUIRED)
  &page&pageSize(<=50) -> {instances[], rowCount}: so the fleet is listed
  zone by zone.
- Instance: ecsResourceUUID, instanceName, ecsStatus (only "STARTED" is
  documented), ecsPendingStatus ("NONE" when idle), publicIpAddress,
  secondaryPublicIpInfoList (strings), regionCode, zoneCode, cpu, memory
  (no unit stated), freeFlow ("流量 单位GB" - the monthly quota in GB),
  usedFlow (unit not stated; read as the quota's GB), createTime.
- Power: POST /instance/start {ecsResourceUUID} -> {asyncTaskUUID}; GET
  /asynctask/getResult?asyncTaskUUID -> {asyncTaskInfo{taskStatus
  PROCESSING|FINISHED, processResult SUCCESS|FAIL|RETRY|CANCEL}}.
  /instance/stop and /instance/reboot follow the start page's shape
  (their pages exist; paths unverified).
- No API for extra IPs, billing, balance or expiry - IPs are listed only,
  billing reads "not exposed".
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from . import http as phttp
from .base import (
    ActionResult, ActionTimeout, AdapterError, Allowance, Billing, Capability,
    Facet, IpAddress, ProviderAdapter, Server, ServerStatus, TrafficCounting,
    UnsupportedAction,
)

API = "https://openapi.lightnode.com"
GB = 1_000_000_000

CAPABILITIES = frozenset({
    Capability.POWER_ON, Capability.POWER_OFF, Capability.REBOOT, Capability.SHUTDOWN,
})
VERB = {Capability.POWER_ON: "start", Capability.POWER_OFF: "stop",
        Capability.SHUTDOWN: "stop", Capability.REBOOT: "reboot"}
STATUS = {"STARTED": ServerStatus.RUNNING, "STOPPED": ServerStatus.OFF}

POLL_BUDGET_S = 180
POLL_INTERVAL_S = 3.0
PAGE_SIZE = 50


class LightNodeAdapter(ProviderAdapter):
    key = "lightnode"
    display_name = "LightNode"
    capabilities = CAPABILITIES

    def __init__(self, account_id: int, account_name: str, token: str, http=None):
        super().__init__(account_id, account_name, token, http)
        self.h = phttp.ProviderHttpClient(
            API, lambda req: req.headers.__setitem__("x-open-token", token), transport=http)

    async def close(self) -> None:
        await self.h.aclose()

    async def _zone_instances(self, region: str, zone: str) -> list[dict]:
        rows, page = [], 1
        while True:
            data = await self.h.get_json("/instance/list", {
                "regionCode": region, "zoneCode": zone, "page": page, "pageSize": PAGE_SIZE})
            batch = data.get("instances") or []
            rows.extend(batch)
            if not batch or len(rows) >= int(data.get("rowCount") or 0):
                return rows
            page += 1

    async def list_servers(self) -> list[Server]:
        regions = (await self.h.get_json("/region/list")).get("regions") or []
        zones = [(r["regionCode"], z["zoneCode"]) for r in regions
                 for z in r.get("zones") or [] if r.get("regionCode") and z.get("zoneCode")]
        batches = await asyncio.gather(*[self._zone_instances(r, z) for r, z in zones])
        return [self._server(row) for batch in batches for row in batch]

    async def get_server(self, provider_id: str) -> Server:
        data = await self.h.get_json("/instance/detail", {"ecsResourceUUID": provider_id})
        row = data.get("instance") or data
        if not row.get("ecsResourceUUID"):
            raise AdapterError(f"no such instance: {provider_id}", status_code=404)
        return self._server(row)

    def _server(self, row: dict) -> Server:
        pending = str(row.get("ecsPendingStatus") or "NONE")
        status = (ServerStatus.UNKNOWN if pending != "NONE"
                  else STATUS.get(str(row.get("ecsStatus") or ""), ServerStatus.UNKNOWN))
        primary = row.get("publicIpAddress")
        ips = [IpAddress(address=primary, primary=True, kind="primary")] if primary else []
        ips += [IpAddress(address=a, primary=False, kind="secondary")
                for a in row.get("secondaryPublicIpInfoList") or [] if a]
        allowance = None
        if row.get("freeFlow") is not None:
            used = row.get("usedFlow")
            allowance = Allowance(
                included_bytes=int(row["freeFlow"]) * GB,
                used_bytes=int(float(used) * GB) if used is not None else None,
                counting=TrafficCounting.INGRESS_AND_EGRESS,
                window="monthly traffic quota, both directions (LightNode pricing page)")
        facets = []
        if pending != "NONE":
            facets.append(Facet(label="operation", value=pending))
        if row.get("cpu") is not None:
            facets.append(Facet(label="cpu", value=str(row["cpu"])))
        if row.get("memory") is not None:
            facets.append(Facet(label="memory", value=str(row["memory"])))  # unit unstated
        created = None
        if row.get("createTime"):
            try:  # '2026-03-12 10:15:17' - no zone stated
                created = datetime.strptime(row["createTime"], "%Y-%m-%d %H:%M:%S") \
                    .replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        return Server(
            provider_id=str(row["ecsResourceUUID"]),
            name=row.get("instanceName") or str(row["ecsResourceUUID"]),
            adapter=self.key, account_id=self.account_id, status=status,
            ipv4=primary, region=f"{row.get('regionCode', '')} / {row.get('zoneCode', '')}".strip(" /"),
            created=created, allowance=allowance, facets=facets, ips=ips,
            not_exposed=["monthly_price", "labels", "server_type"],
        )

    async def perform_action(self, cap: Capability, server_id: str,
                             params: dict[str, Any]) -> ActionResult:
        if cap not in VERB:
            raise UnsupportedAction(self.key, cap)
        r = await self.h.post_json(f"/instance/{VERB[cap]}", {"ecsResourceUUID": server_id})
        task = r.json().get("asyncTaskUUID")
        if not task:
            raise AdapterError(f"lightnode {VERB[cap]} returned no task id")
        deadline = time.monotonic() + POLL_BUDGET_S
        while True:
            info = (await self.h.get_json("/asynctask/getResult",
                                          {"asyncTaskUUID": task})).get("asyncTaskInfo") or {}
            if info.get("taskStatus") == "FINISHED":
                if info.get("processResult") != "SUCCESS":
                    raise AdapterError(f"lightnode {VERB[cap]} on {server_id}: "
                                       f"{info.get('processResult', '?')}")
                break
            if time.monotonic() >= deadline:
                raise ActionTimeout(f"lightnode {VERB[cap]} on {server_id}")
            await asyncio.sleep(POLL_INTERVAL_S)
        s = await self.get_server(server_id)
        want = ServerStatus.OFF if cap in (Capability.POWER_OFF, Capability.SHUTDOWN) \
            else ServerStatus.RUNNING
        if s.status != want:
            raise AdapterError(f"task succeeded but the instance reads {s.status.value}")
        return ActionResult(detail=f"status now {want.value}")

    async def get_billing(self) -> Billing:
        return Billing(model="prepaid wallet, charged hourly (LightNode pricing page)",
                       not_exposed=["balance", "invoices", "month_to_date",
                                    "upcoming", "renewals"])
