"""Gcore Cloud adapters: fleet (account-backed) + catalog (tokenless).

Fleet adapter - all facts verified against the official OpenAPI 3.1 spec
(cloud_api.yaml, see docs/provider-truth.md Gcore section) and docs.gcore.com:
- Base: https://api.gcore.com (single host for every service).
- Auth: permanent API token, header "Authorization: APIKey <token>" - NOT
  Bearer (Bearer yields "Given token not valid for any token type"). Created
  at gcore.com -> Profile -> API tokens; one token covers all products.
- Resources are scoped by project AND region in the path:
  /cloud/v1/instances/{project_id}/{region_id}/... - provider_id is compound
  and self-routing: "{project}:{region}:{uuid}" (same pattern as OVH cloud).
- GET /cloud/v1/projects -> {count, results:[{id, name, is_default, state}]}.
- GET /cloud/v1/instances/{p}/{r} -> {count, results:[...]}, limit/offset
  pagination (default/max 1000). InstanceSerializer fields: name (flat),
  flavor (NESTED: flavor_id/flavor_name/vcpus/ram in MiB - no top-level
  flavor_id), addresses (map network->[{addr, type}]; public IP = the
  type:"floating" entry), created_at, status (uppercase OpenStack-style) AND
  vm_state (lowercase), tags ([{key, value, read_only}]).
- Actions: POST /cloud/v2/instances/{p}/{r}/{id}/action {"action":
  "start"|"stop"|"reboot"|"reboot_hard"|"resume"|"suspend"} -> 200/202
  {"tasks": ["<uuid>"]}; poll GET /cloud/v1/tasks/{id} until FINISHED
  (NEW/RUNNING/ERROR; ERROR carries an error string). A task id is never
  success. Power actions poll the task to FINISHED, THEN the instance's own
  status to the action's target (a REBOOT may read ACTIVE while stale).
- Rename/relabel: PATCH /cloud/v1/instances/{p}/{r}/{id} accepts name and
  tags (RFC 7386 JSON Merge Patch: key:value sets, null removes the key,
  unspecified keys and read-only tags are preserved), returns 200 + the
  serializer.
- Delete: DELETE .../instances/{p}/{r}/{id} -> 200 {"tasks":[...]} (NOT 204),
  then the task; confirmed gone when the instance GET 404s.
- Price: GET /cloud/v1/pricing/{p}/{r}/instances/{id} -> price_per_hour,
  price_per_month (discounted), price_without_discount_per_month,
  discount_percent, tax_percent, currency_code.
- Extra public IPs = reserved fixed IPs (type "external"): POST
  /cloud/v1/reserved_fixed_ips/{p}/{r} -> tasks (the task's
  created_resources names the new port), GET .../{port_id} ->
  fixed_ip_address; attach with POST /cloud/v1/instances/{p}/{r}/{id}/
  attach_interface {"type": "reserved_fixed_ip", "port_id"}; detach with
  .../detach_interface {"ip_address", "port_id"}; DELETE
  /cloud/v1/reserved_fixed_ips/{p}/{r}/{port_id} releases it. Billed per
  minute from creation to deletion, attached or not (docs.gcore.com cloud
  billing + reserved-IP pages) - so a reserved IP that failed to attach is
  deleted at once, never left billing.
- No VM rebuild endpoint in the spec (bare metal only) -> REBUILD not
  offered. Traffic: free and unmetered (ingress AND egress) per docs - see
  UNMETERED_NOTE below; no byte usage exists to fetch.

Catalog adapter - LIVE, tokenless (verified 2026-10-03).
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

import asyncio
import ipaddress
import time
from decimal import Decimal
from typing import Any

import httpx

from . import http as phttp
from .base import (
    ActionTimeout, ActionResult, AdapterError, Allowance, Capability, Facet,
    IpAddress, IpCost, IpOffer, Money, Plan, ProviderAdapter, Server,
    ServerStatus, parse_dt,
)

API = "https://api.gcore.com"

REGIONS_URL = "https://api.gcore.com/cloud/public/v1/regions"
FLAVORS_URL = "https://api.gcore.com/cloud/public/v1/basic_vms/flavors"
BFF_ITEMS_URL = "https://bff.gcore.pro/cloud/vcc-items"

# Verified uniformly across 12 tested regions (2026-10-03).
PUBLIC_IP_MONTHLY_USD = Decimal("2.7504")
UNMETERED_NOTE = ("unmetered (free ingress and egress); "
                  "bandwidth capped by flavor")

# Status enum verified in the spec (InstanceStatus). Unmapped -> UNKNOWN,
# never a guess. REBUILD/RESIZE/VERIFY_RESIZE/REVERT_RESIZE/MIGRATING are
# in-flight rebuild-class states; REBOOT/HARD_REBOOT are not "confirmed
# running" until the task finishes (poll target is status, not hope).
STATE_MAP = {
    "ACTIVE": ServerStatus.RUNNING,
    "SHUTOFF": ServerStatus.OFF,
    "PAUSED": ServerStatus.OFF,
    "SUSPENDED": ServerStatus.OFF,
    "SHELVED": ServerStatus.OFF,
    "SHELVED_OFFLOADED": ServerStatus.OFF,
    "SOFT_DELETED": ServerStatus.OFF,
    "BUILD": ServerStatus.REBUILDING,
    "REBUILD": ServerStatus.REBUILDING,
    "RESIZE": ServerStatus.REBUILDING,
    "VERIFY_RESIZE": ServerStatus.REBUILDING,
    "REVERT_RESIZE": ServerStatus.REBUILDING,
    "MIGRATING": ServerStatus.REBUILDING,
    "PASSWORD": ServerStatus.UNKNOWN,
    "RESCUE": ServerStatus.UNKNOWN,
    "REBOOT": ServerStatus.UNKNOWN,
    "HARD_REBOOT": ServerStatus.UNKNOWN,
    "ERROR": ServerStatus.UNKNOWN,
    "DELETED": ServerStatus.UNKNOWN,
    "UNKNOWN": ServerStatus.UNKNOWN,
}

POLL_BUDGET_S = 120
POLL_INTERVAL_S = 2.0

POWER_ACTION = {
    Capability.POWER_ON: "start",
    Capability.POWER_OFF: "stop",
    Capability.SHUTDOWN: "stop",
    Capability.REBOOT: "reboot",
}

# Poll target on `status` per power action (the provider's own view).
POWER_TARGET = {
    Capability.POWER_ON: "ACTIVE",
    Capability.POWER_OFF: "SHUTOFF",
    Capability.REBOOT: "ACTIVE",
    Capability.SHUTDOWN: "SHUTOFF",
}

CAPABILITIES = frozenset({
    Capability.POWER_ON, Capability.POWER_OFF, Capability.REBOOT,
    Capability.SHUTDOWN, Capability.RENAME, Capability.RELABEL,
    Capability.DELETE,
    Capability.IP_ADD, Capability.IP_RELEASE, Capability.IP_CHANGE,
})

RESERVED_IP_NOTE = ("billed per minute from creation to deletion, attached or "
                    "not - a change costs only the minutes each IP existed")


# Not reachable from the internet: RFC 1918, CGNAT, loopback, link-local,
# IPv6 ULA/link-local. Everything else on an instance is a public address
# (documentation ranges included - they are what fixtures use).
_NON_PUBLIC = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10",
    "127.0.0.0/8", "169.254.0.0/16", "fc00::/7", "fe80::/10", "::1/128"))


def _is_public(addr: str | None) -> bool:
    try:
        ip = ipaddress.ip_address(addr or "")
    except ValueError:
        return False
    return not any(ip in n for n in _NON_PUBLIC if n.version == ip.version)


class GcoreAdapter(ProviderAdapter):
    """Fleet adapter: basic VM instances across all projects/regions under
    one account token. provider_id "{project}:{region}:{uuid}" is compound
    and self-routing (a bare uuid cannot say which project/region owns it)."""

    key = "gcore"
    display_name = "Gcore"
    capabilities = CAPABILITIES

    def __init__(self, account_id: int, account_name: str, token: str, http=None):
        super().__init__(account_id, account_name, token, http)
        # Verified: APIKey scheme, NOT Bearer.
        self.h = phttp.ProviderHttpClient(
            API, lambda req: req.headers.__setitem__(
                "Authorization", f"APIKey {self._token}"),
            transport=http,
        )

    # -- reads -----------------------------------------------------------

    async def list_servers(self) -> list[Server]:
        projects = await self.h.get_json("/cloud/v1/projects")
        regions = await self._regions()
        # regions x projects are independent listings - run them concurrently
        # (most pairs are empty; sequential would be dozens of round trips).
        # gather keeps the refuse-partial rule: the first failure still fails
        # the whole sync, never a silently partial fleet.
        jobs = [self._list_region_instances(project.get("id"), region)
                for project in projects.get("results", [])
                if project.get("id") is not None
                for region in regions]
        return [s for batch in await asyncio.gather(*jobs) for s in batch]

    async def get_server(self, provider_id: str) -> Server:
        pid, region, iid = self._split_id(provider_id)
        row = await self.h.get_json(
            f"/cloud/v1/instances/{pid}/{region}/{iid}")
        return await self._server(row, pid, region,
                                  await self._reserved_addresses(pid, region))

    async def _regions(self) -> list[str]:
        """All region ids (public endpoint, no project scope needed)."""
        # The tokenless public regions endpoint is the same one the catalog
        # adapter uses; the authenticated /cloud/v1/regions returns the
        # richer {id, display_name, state, ...} shape - either fits, take ids.
        data = await self.h.get_json("/cloud/v1/regions")
        out = [str(r.get("id")) for r in data.get("results", [])
               if r.get("id") is not None]
        if out:
            return out
        # public endpoint fallback (region list must never be the reason the
        # whole fleet sync fails)
        try:
            data = await self.h.get_json("/cloud/public/v1/regions")
        except AdapterError:
            raise AdapterError("gcore region list unavailable")
        return [str(r.get("id")) for r in data.get("results", [])
                if r.get("id") is not None]

    async def _list_region_instances(self, pid: Any, region: str) -> list[Server]:
        rows, offset = [], 0
        while True:
            data = await self.h.get_json(
                f"/cloud/v1/instances/{pid}/{region}",
                params={"limit": 100, "offset": offset})
            rows.extend(data.get("results", []))
            count = data.get("count", len(rows))
            offset += 100
            if len(rows) >= count or not data.get("results"):
                break
        reserved = await self._reserved_addresses(pid, region) if rows else set()
        # per-instance pricing GETs are independent - concurrent, not N
        # sequential round trips (each is its own HTTP call)
        return list(await asyncio.gather(
            *[self._server(row, pid, region, reserved) for row in rows]))

    async def _reserved_list(self, pid: Any, region: str) -> list[dict]:
        data = await self.h.get_json(f"/cloud/v1/reserved_fixed_ips/{pid}/{region}")
        return data.get("results", []) if isinstance(data, dict) else data

    async def _reserved_addresses(self, pid: Any, region: str) -> set[str]:
        """Addresses that are reserved IPs (the swappable kind). One call per
        project x region with instances; unreadable -> empty (every IP then
        reads primary: protected, never wrongly released)."""
        try:
            return {r.get("fixed_ip_address") for r in await self._reserved_list(pid, region)
                    if r.get("fixed_ip_address")}
        except AdapterError:
            return set()

    @staticmethod
    def _ips(row: dict, reserved: set[str]) -> list[IpAddress]:
        """Public addresses from the instance's addresses map. Reserved IPs
        are the swappable extras; any other public address (the VM's own
        external interface, or its floating IP) is primary."""
        out: list[IpAddress] = []
        for entries in (row.get("addresses") or {}).values():
            for a in entries or []:
                addr = a.get("addr")
                if not _is_public(addr) or any(ip.address == addr for ip in out):
                    continue
                is_reserved = addr in reserved
                out.append(IpAddress(
                    address=addr, version=6 if ":" in addr else 4,
                    primary=not is_reserved,
                    kind="reserved" if is_reserved else (a.get("type") or "fixed")))
        return out

    async def _server(self, row: dict, pid: Any, region: str,
                      reserved: set[str] | None = None) -> Server:
        iid = str(row.get("id"))
        flavor = row.get("flavor") or {}
        fname = flavor.get("flavor_name") or flavor.get("flavor_id")

        price = await self._price_for(pid, region, iid)
        ips = self._ips(row, reserved or set())

        facets = []
        if fname:
            facets.append(Facet(label="instance type", value=str(fname)))
        if flavor.get("vcpus"):
            facets.append(Facet(label="vcpus", value=str(flavor["vcpus"])))
        if flavor.get("ram"):
            # spec: ram is in MiB - render the provider's unit, never guess GB
            facets.append(Facet(label="ram", value=f"{flavor['ram']} MiB"))
        # read-only tags are the provider's own metadata: merge patch always
        # preserves them, so they are facets (visible, uneditable), never labels
        for t in row.get("tags") or []:
            if t.get("key") and t.get("read_only"):
                facets.append(Facet(label=f"tag: {t['key']} (read-only)",
                                    value=str(t.get("value", ""))))

        return Server(
            provider_id=f"{pid}:{region}:{iid}",
            name=row.get("name") or iid,
            adapter=self.key,
            account_id=self.account_id,
            status=STATE_MAP.get(str(row.get("status", "")).upper(),
                                 ServerStatus.UNKNOWN),
            ipv4=self._ipv4(row) or next((i.address for i in ips
                                          if i.primary and i.version == 4), None),
            # the serializer carries the provider's own region display name
            # (row.region, e.g. "Frankfurt"); the path id stays in provider_id
            region=row.get("region") or str(region),
            server_type=fname,
            created=parse_dt(row.get("created_at")),
            labels=self._labels(row.get("tags")),
            monthly_price=price,
            allowance=Allowance(
                included_bytes=None,
                used_bytes=None,
                window=UNMETERED_NOTE,
            ),
            facets=facets,
            ips=ips,
            # Gcore exposes name, addresses, tags, created_at, flavor and
            # price - nothing on the Server model is genuinely missing.
        )

    @staticmethod
    def _ipv4(row: dict) -> str | None:
        """Public IPv4: the type:"floating" address in the addresses map
        wins. Without one, list_servers falls back to the primary entry of
        _ips: a fixed addr outside the private ranges (the VM's own external
        interface). A private fixed addr is never reported as public."""
        for entries in (row.get("addresses") or {}).values():
            for a in entries or []:
                addr = a.get("addr")
                if addr and "." in addr and a.get("type") == "floating":
                    return addr
        return None

    @staticmethod
    def _labels(tags: list | None) -> dict[str, str] | None:
        """User-editable tags only: read-only tags are the provider's own
        metadata (merge patch can never change them) - rendered as facets in
        _server, never as labels the UI would offer for editing."""
        if not tags:
            return None
        return {t.get("key", ""): t.get("value", "")
                for t in tags if t.get("key") and not t.get("read_only")}

    async def _price_for(self, pid: Any, region: str, iid: str) -> Money | None:
        """Per-instance monthly price (discounted price_per_month). Failure or
        a missing price -> None ("-" in the UI), never zero."""
        try:
            data = await self.h.get_json(
                f"/cloud/v1/pricing/{pid}/{region}/instances/{iid}")
        except AdapterError:
            return None
        monthly = data.get("price_per_month")
        if monthly is None:
            return None
        return Money(amount=Decimal(str(monthly)),
                     currency=data.get("currency_code") or "USD",
                     vat_inclusive=None)

    # -- actions ----------------------------------------------------------

    async def perform_action(self, cap: Capability, server_id: str,
                             params: dict[str, Any]) -> ActionResult:
        if cap not in self.capabilities:
            raise AdapterError(f"gcore does not support {cap.value}")
        pid, region, iid = self._split_id(server_id)
        base = f"/cloud/v1/instances/{pid}/{region}/{iid}"
        if cap in POWER_ACTION:
            # Verified: v2 action endpoint; verbs start/stop/reboot.
            r = await self.h.post_json(
                f"/cloud/v2/instances/{pid}/{region}/{iid}/action",
                {"action": POWER_ACTION[cap]})
            # poll the returned tasks to FINISHED first: for REBOOT the
            # instance may still read ACTIVE (stale) before the task even
            # starts, so a bare status poll could confirm a no-op
            await self._poll_tasks(r.json().get("tasks", []))
            return await self._poll(cap, server_id)
        if cap == Capability.RENAME:
            r = await self.h.request("PATCH", base, json={"name": params["name"]})
            if r.status_code != 200:
                raise AdapterError(f"gcore rename failed: {r.status_code}")
            # confirm by the provider's view, never the 200 alone
            row = await self.h.get_json(base)
            if row.get("name") != params["name"]:
                raise AdapterError("rename not confirmed by the provider")
            return ActionResult(detail="renamed")
        if cap == Capability.RELABEL:
            # RFC 7386 merge patch (spec: UpdateTagsSerializer): unspecified
            # keys stay, null removes a key. The UI sends the full desired
            # label set, so send removals (null) for keys the patch must drop;
            # read-only tags are always preserved by the provider.
            row = await self.h.get_json(base)
            current = self._labels(row.get("tags")) or {}
            wanted = params["labels"]
            patch = dict(wanted)
            for k in current:
                if k not in patch:
                    patch[k] = None  # merge-patch removal
            r = await self.h.request("PATCH", base, json={"tags": patch})
            if r.status_code != 200:
                raise AdapterError(f"gcore relabel failed: {r.status_code}")
            row = await self.h.get_json(base)
            if self._labels(row.get("tags")) != wanted:
                raise AdapterError("relabel not confirmed by the provider")
            return ActionResult(detail="relabeled")
        if cap == Capability.DELETE:
            # Verified: 200 + {"tasks":[...]} (not 204), then the task.
            r = await self.h.delete(base)
            if r.status_code != 200:
                raise AdapterError(f"gcore delete failed: {r.status_code}")
            await self._poll_tasks(r.json().get("tasks", []))
            # confirmed gone only when the provider 404s the instance - the
            # status code itself, never "404" substring-matched in a message
            # (a uuid can contain 404; a 500 would then read as deleted)
            r = await self.h.request("GET", base)
            if r.status_code == 404:
                return ActionResult(detail="deleted")
            if r.status_code >= 400:
                raise AdapterError(
                    f"gcore delete verify GET failed: {r.status_code}")
            raise AdapterError("delete task finished but instance still lists")
        raise AdapterError(f"gcore does not support {cap.value}")

    async def _poll(self, cap: Capability, server_id: str) -> ActionResult:
        """Poll the task list to FINISHED, then the instance's own status to
        the action's target. A finished task with the wrong status is not
        success either - the provider's view decides."""
        pid, region, iid = self._split_id(server_id)
        want = POWER_TARGET[cap]
        deadline = time.monotonic() + POLL_BUDGET_S
        last = "?"
        while time.monotonic() < deadline:
            row = await self.h.get_json(
                f"/cloud/v1/instances/{pid}/{region}/{iid}")
            last = str(row.get("status", "?"))
            if last.upper() == want:
                return ActionResult(detail=f"status now {want}")
            await asyncio.sleep(POLL_INTERVAL_S)
        raise ActionTimeout(
            f"gcore {cap.value} on {server_id} (last status {last})")

    async def _poll_tasks(self, task_ids: list[str]) -> list[dict]:
        """All tasks to FINISHED; ERROR carries an error string (a real
        failure, never success). Returns the finished task bodies."""
        done = []
        for tid in task_ids:
            deadline = time.monotonic() + POLL_BUDGET_S
            while time.monotonic() < deadline:
                t = await self.h.get_json(f"/cloud/v1/tasks/{tid}")
                state = str(t.get("state", "")).upper()
                if state == "FINISHED":
                    done.append(t)
                    break
                if state == "ERROR":
                    raise AdapterError(
                        f"gcore task {tid} failed: {t.get('error', '?')}")
                await asyncio.sleep(POLL_INTERVAL_S)
            else:
                raise ActionTimeout(f"gcore task {tid} did not finish")
        return done

    # -- IPs: reserved public IPs are the swappable extras ------------------

    async def _tasks_of(self, r) -> list[dict]:
        if r.status_code >= 400:
            raise AdapterError(f"{r.request.method} {r.request.url.path}: {r.status_code} - "
                               f"{r.text[:200]}", status_code=r.status_code)
        return await self._poll_tasks(r.json().get("tasks", []))

    async def add_ip(self, server_id: str) -> IpAddress:
        pid, region, iid = self._split_id(server_id)
        # 1) reserve: sent once - this is the purchase
        r = await self.h.request("POST", f"/cloud/v1/reserved_fixed_ips/{pid}/{region}",
                                 json={"type": "external", "ip_family": "ipv4",
                                       "is_vip": False}, retry=False)
        tasks = await self._tasks_of(r)
        port = next((p for t in tasks
                     for key in ("ports", "reserved_fixed_ips")
                     for p in (t.get("created_resources") or {}).get(key) or []), None)
        if not port:
            raise AdapterError("gcore reserved the IP but the task named no port - "
                               "check Reserved IPs in the portal (it bills per minute)")
        try:
            res = await self.h.get_json(f"/cloud/v1/reserved_fixed_ips/{pid}/{region}/{port}")
            addr = res.get("fixed_ip_address")
            if not addr:
                raise AdapterError(f"reserved port {port} has no address")
            # 2) attach to the VM
            r = await self.h.request(
                "POST", f"/cloud/v1/instances/{pid}/{region}/{iid}/attach_interface",
                json={"type": "reserved_fixed_ip", "port_id": port}, retry=False)
            await self._tasks_of(r)
            # 3) confirm on the instance's own address map
            await self._await_address(pid, region, iid, addr, present=True)
        except AdapterError as e:
            # an unattached reserved IP bills per minute: give it back now
            try:
                await self._delete_reserved(pid, region, port)
            except AdapterError as cleanup:
                raise AdapterError(f"{e}; the reserved IP (port {port}) could NOT be "
                                   f"deleted ({cleanup}) - delete it in the portal")
            raise
        return IpAddress(address=addr, primary=False, kind="reserved", provider_ip_id=port)

    async def release_ip(self, server_id: str, address: str) -> None:
        pid, region, iid = self._split_id(server_id)
        entry = next((x for x in await self._reserved_list(pid, region)
                      if x.get("fixed_ip_address") == address), None)
        if entry is None:
            raise AdapterError(f"{address} is not a reserved IP - the VM's own address "
                               "is never released here")
        port = entry.get("port_id")
        row = await self.h.get_json(f"/cloud/v1/instances/{pid}/{region}/{iid}")
        if address in {a.get("addr") for es in (row.get("addresses") or {}).values()
                       for a in es or []}:
            r = await self.h.request(
                "POST", f"/cloud/v1/instances/{pid}/{region}/{iid}/detach_interface",
                json={"ip_address": address, "port_id": port})
            await self._tasks_of(r)
            await self._await_address(pid, region, iid, address, present=False)
        await self._delete_reserved(pid, region, port)

    async def _delete_reserved(self, pid: Any, region: str, port: str) -> None:
        r = await self.h.request("DELETE", f"/cloud/v1/reserved_fixed_ips/{pid}/{region}/{port}")
        if r.status_code == 404:
            return
        await self._tasks_of(r)
        r = await self.h.request("GET", f"/cloud/v1/reserved_fixed_ips/{pid}/{region}/{port}")
        if r.status_code != 404:
            raise AdapterError(f"reserved IP port {port} still exists after delete")

    async def _await_address(self, pid: Any, region: str, iid: str, addr: str,
                             present: bool) -> None:
        deadline = time.monotonic() + POLL_BUDGET_S
        while True:
            row = await self.h.get_json(f"/cloud/v1/instances/{pid}/{region}/{iid}")
            seen = addr in {a.get("addr") for es in (row.get("addresses") or {}).values()
                            for a in es or []}
            if seen == present:
                return
            if time.monotonic() >= deadline:
                raise ActionTimeout(f"{addr} {'attach' if present else 'detach'} on {iid}")
            await asyncio.sleep(POLL_INTERVAL_S)

    async def ip_cost(self, server_id: str) -> IpCost:
        pid, region, _ = self._split_id(server_id)
        try:
            r = await self.h.request("POST", f"/cloud/v1/pricing/{pid}/{region}/reserved_fixed_ips",
                                     json={"type": "external", "ip_family": "ipv4"})
            data = r.json() if r.status_code < 400 else {}
        except (AdapterError, ValueError):
            data = {}
        cur = data.get("currency_code") or "USD"
        if data.get("price_per_hour") is not None:
            return IpCost(price=Money(amount=Decimal(str(data["price_per_hour"])), currency=cur),
                          per="hour", note=RESERVED_IP_NOTE)
        # the catalog's verified uniform public-IP price (externalip_min)
        return IpCost(price=Money(amount=PUBLIC_IP_MONTHLY_USD, currency="USD"),
                      per="month", note=RESERVED_IP_NOTE)

    @staticmethod
    def _split_id(provider_id: str) -> tuple[str, str, str]:
        parts = provider_id.split(":")
        if len(parts) != 3:
            raise AdapterError(
                f"gcore provider_id must be project:region:instance, "
                f"got {provider_id!r}")
        return parts[0], parts[1], parts[2]

    async def close(self) -> None:
        await self.h.aclose()


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
                    # `is not None`, never truthiness: a real 0 USD/min
                    # flavor is a published price, not "not published".
                    price_monthly=(
                        Money(amount=per_min * Decimal(43200), currency="USD")
                        if per_min is not None else None),
                    price_hourly=(Money(amount=per_min * Decimal(60), currency="USD")
                                  if per_min is not None else None),
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
            price_min = item.get("priceMinute")
            if price_min is None:
                price_min = item.get("price")
            if name and price_min is not None:  # a real 0 IS a published price
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
