"""Tube-hosting catalog adapter - LIVE, tokenless (verified 2026-10-03).

Source: GET https://www.tube-hosting.com/assets/data/templates.json - the
static data asset their pricing page itself fetches (no auth, CORS-open,
stable path). Shape: {"kvm": [{name, price, cores, ram, disk}... 12 plans],
"dedicated": {...}} - prices are integer EURO-CENTS (price: 500 = EUR 5.00).
Caveat honestly noted: a static asset, not a versioned API. Traffic and
extra-IP terms are not in the asset - rendered as not-published, never
invented (verify on their pricing page at order time).

Fleet adapter - api.tube-hosting.com (OpenAPI at /docs, "v0", generated,
unlisted on their site; verified against that spec 2026-10-07):
- Auth: POST /login {mail, password, device{type, name}} -> {accessToken,
  refreshToken, userData}; the access token is sent as a Bearer header
  (the spec declares no scheme - unverified until tested live); re-login
  once on 401.
- GET /servicegroups/currents -> the user's active service groups (typed
  only as "object" in the spec, so parsed defensively: every nested
  Service {id, name, type VPS|DEDICATED|IPV4BUNDLE|BYOIP, serviceGroupId,
  endDate, price, runtime}). GET /vps/{id} -> VPS {coreCount, memory,
  diskSpace, diskType, osDisplayName, primaryIPv4{ipv4{ipv4}}}; GET
  /vps/{id}/status -> {status} (running|stopped).
- POST /vps/{id}/start|stop|shutdown|restart; PUT /vps/{id}/password
  {password}. No endpoint adds or releases an IP - listed only.
- Billing: GET /me -> User.balance (integer; read as euro-cents like the
  verified templates.json prices), GET /payments/invoices (no due date or
  paid status in the schema). Service groups renew from the balance
  (PUT /servicegroups/{id}/extend) - endDate is the expiry.
"""
from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Any

import httpx

from . import http as phttp
from .base import (
    ActionResult, ActionTimeout, AdapterError, Billing, Capability, CredentialField,
    Facet, Invoice, IpAddress, IpOffer, Money, Plan, ProviderAdapter, Renewal, Server,
    ServerStatus, UnsupportedAction, parse_dt,
)

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


API = "https://api.tube-hosting.com"
VERB = {Capability.POWER_ON: "start", Capability.POWER_OFF: "stop",
        Capability.SHUTDOWN: "shutdown", Capability.REBOOT: "restart"}
TARGET = {Capability.POWER_ON: "running", Capability.POWER_OFF: "stopped",
          Capability.SHUTDOWN: "stopped", Capability.REBOOT: "running"}
POLL_BUDGET_S = 180
POLL_INTERVAL_S = 3.0


def _cents(v: Any) -> Decimal | None:
    try:
        return (Decimal(str(v)) / 100).quantize(Decimal("0.01")) if v is not None else None
    except ArithmeticError:
        return None


def _services(obj: Any, group: dict | None = None):
    """(Service, owning group) pairs anywhere in the service-group payload."""
    if isinstance(obj, dict):
        if obj.get("type") in ("VPS", "DEDICATED") and "id" in obj and "serviceGroupId" in obj:
            yield obj, group
            return
        if "groupData" in obj or "metaData" in obj:
            group = obj
        for v in obj.values():
            yield from _services(v, group)
    elif isinstance(obj, list):
        for v in obj:
            yield from _services(v, group)


class TubeAdapter(ProviderAdapter):
    key = "tube"
    display_name = "Tube-hosting"
    capabilities = frozenset(set(VERB) | {Capability.SET_PASSWORD})
    credential_fields = (
        CredentialField(name="mail", label="Account e-mail", secret=False),
        CredentialField(name="password", label="Account password"),
    )

    def __init__(self, account_id: int, account_name: str, token: str, http=None):
        super().__init__(account_id, account_name, token, http)
        cred = self.credential()
        self._mail, self._pw = cred.get("mail", ""), cred.get("password", "")
        self._jwt: str | None = None
        self.h = phttp.ProviderHttpClient(API, self._auth, transport=http)

    def _auth(self, req: httpx.Request) -> None:
        if self._jwt and req.url.path != "/login":
            req.headers["Authorization"] = f"Bearer {self._jwt}"

    async def close(self) -> None:
        await self.h.aclose()

    async def _login(self) -> None:
        r = await self.h.request("POST", "/login", json={
            "mail": self._mail, "password": self._pw,
            "device": {"type": "WEB", "name": "omnicloud"}})
        if r.status_code >= 400 or not (r.json() if r.content else {}).get("accessToken"):
            raise AdapterError(f"tube-hosting login failed ({r.status_code})",
                               status_code=r.status_code)
        self._jwt = r.json()["accessToken"]

    async def _req(self, method: str, path: str, **kw) -> httpx.Response:
        for attempt in (0, 1):
            if self._jwt is None:
                await self._login()
            r = await self.h.request(method, path, **kw)
            if r.status_code == 401 and attempt == 0:
                self._jwt = None
                continue
            self.h._raise_for_status(r)
            return r
        raise AdapterError(f"{method} {path}: session could not be re-established")

    async def _json(self, path: str, params: dict | None = None) -> Any:
        r = await self._req("GET", path, params=params)
        return r.json() if r.content else None

    async def _service_rows(self) -> list[tuple[dict, dict | None]]:
        return list(_services(await self._json("/servicegroups/currents")))

    async def list_servers(self) -> list[Server]:
        rows = [(svc, grp) for svc, grp in await self._service_rows() if svc.get("type") == "VPS"]
        return list(await asyncio.gather(*[self._server(svc, grp) for svc, grp in rows]))

    async def get_server(self, provider_id: str) -> Server:
        for svc, grp in await self._service_rows():
            if str(svc.get("id")) == provider_id:
                return await self._server(svc, grp)
        raise AdapterError(f"no such server: {provider_id}", status_code=404)

    async def _server(self, svc: dict, grp: dict | None) -> Server:
        sid = str(svc["id"])
        vps = await self._json(f"/vps/{sid}") or {}
        try:
            st = str((await self._json(f"/vps/{sid}/status") or {}).get("status") or "")
        except AdapterError:
            st = ""
        primary = ((vps.get("primaryIPv4") or {}).get("ipv4") or {}).get("ipv4")
        facets = []
        for label, key in (("cores", "coreCount"), ("memory", "memory"),
                           ("disk", "diskSpace"), ("disk type", "diskType"),
                           ("os", "osDisplayName"), ("virtualization", "vpsType")):
            if vps.get(key) is not None:
                facets.append(Facet(label=label, value=str(vps[key])))  # units unstated
        end = svc.get("endDate") or (grp or {}).get("endDate")
        if end:
            facets.append(Facet(label="paid until", value=str(end)[:10]))
        price = _cents(svc.get("price"))
        if price is not None:
            facets.append(Facet(label="price (panel)",
                                value=f"{price} EUR / {svc.get('runtime') or '?'}"))
        return Server(
            provider_id=sid, name=svc.get("name") or vps.get("name") or sid,
            adapter=self.key, account_id=self.account_id,
            status={"running": ServerStatus.RUNNING, "stopped": ServerStatus.OFF}
            .get(st, ServerStatus.UNKNOWN),
            ipv4=primary, region="NL", server_type=vps.get("vpsType"),
            created=parse_dt(svc.get("startDate")), facets=facets,
            ips=[IpAddress(address=primary, primary=True, kind="primary")] if primary else [],
            not_exposed=["allowance", "monthly_price", "labels"],
        )

    async def perform_action(self, cap: Capability, server_id: str,
                             params: dict[str, Any]) -> ActionResult:
        if cap == Capability.SET_PASSWORD:
            pw = params.get("password") or ""
            if len(pw) < 8:
                raise AdapterError("password must be at least 8 characters")
            await self._req("PUT", f"/vps/{server_id}/password", json={"password": pw})
            return ActionResult(detail="provider accepted the new root password")
        if cap not in VERB:
            raise UnsupportedAction(self.key, cap)
        await self._req("POST", f"/vps/{server_id}/{VERB[cap]}")
        deadline = time.monotonic() + POLL_BUDGET_S
        last = "?"
        while time.monotonic() < deadline:
            await asyncio.sleep(POLL_INTERVAL_S)
            last = str((await self._json(f"/vps/{server_id}/status") or {}).get("status"))
            if last == TARGET[cap]:
                return ActionResult(detail=f"status now {last}")
        raise ActionTimeout(f"tube {VERB[cap]} on {server_id} (last status {last})")

    async def get_billing(self) -> Billing:
        me = await self._json("/me") or {}
        bal = _cents(me.get("balance"))
        invoices = []
        try:
            raw = await self._json("/payments/invoices")
            for inv in raw if isinstance(raw, list) else (raw or {}).get("content", []) or []:
                total = sum((_cents(i.get("unitPrice")) or 0) * int(i.get("quantity") or 1)
                            for i in inv.get("items") or [])
                invoices.append(Invoice(
                    id=str(inv.get("id")), date=parse_dt(inv.get("time")),
                    total=Money(amount=Decimal(total), currency="EUR") if inv.get("items") else None,
                    status="finished" if inv.get("finished") else "open"))
        except AdapterError:
            pass
        renewals = []
        for svc, grp in await self._service_rows():
            end = svc.get("endDate") or (grp or {}).get("endDate")
            if end:
                renewals.append(Renewal(provider_id=str(svc["id"]),
                                        name=svc.get("name") or str(svc["id"]),
                                        date=parse_dt(end), auto=None))
        return Billing(model="prepaid balance; service groups are extended from it",
                       balance=Money(amount=bal, currency="EUR") if bal is not None else None,
                       invoices=invoices, renewals=renewals,
                       # invoices carry no due date or open amount in the schema
                       not_exposed=["month_to_date", "upcoming"]
                                   + ([] if bal is not None else ["balance"]))
