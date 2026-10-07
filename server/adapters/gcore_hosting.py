"""Gcore Hosting adapter: VPS on hosting.gcore.com - a BILLmanager 6 panel.

A different product from Gcore Cloud (gcore.py, api.gcore.com): separate
account, separate API, prepaid balance with per-service expiry dates.
Facts (BILLmanager 6 API, docs.ispsystem.com + the panel's own JS calls;
see docs/provider-truth.md "Gcore Hosting"):
- One endpoint, the panel URL itself: {url}?func=<name>&out=json.
- No API-token UI: func=auth with username + password returns a session id
  (doc.auth.$) passed as auth=<id>; valid ~1h after the LAST request. An
  auth error re-logs once. The password travels in a POST body, never a
  query string (design.md 7: secrets never in URLs/logs).
- JSON shape: {"doc": {...}}; scalars arrive as {"$": "value"}; list
  functions return doc.elem (a single row may come as a bare object);
  errors as doc.error.msg.$.
- func=vds: servers (id, domain, ip, pricelist, cost, expiredate,
  autoprolong, item_status 1 ordered/2 active/3 suspended/4 deleted/5
  processing). Live panels (hosting.gcore.com, 2026-10) send item_status as
  the localized word with the code beside it ({"$orig": "2", "$": "Active"})
  and sometimes omit it, leaving the code in item_real_status. A daily-billed
  service (billdaily on) reads expiredate "Daily charges"; its date is in
  real_expiredate. BILLmanager knows the SERVICE state, not the VM's power
  state - power state is not exposed here.
- func=service.ip elid=<server>: the server's IPs (id, name = the address,
  is_main, no_delete, type, gateway, mask).
- func=expense: the charge log (amount, intname vds/ip, main_item = the
  server, realdate). On a daily-billed server each add-on IP is a daily
  line, which is what an extra IP costs there.
- func=service.ip.edit plid=<server>: the add-IP form (slist "type" +
  domain); the same func with sok=ok orders one IP. The address is unknown
  until the order completes - there is no preview. Each order is a fresh
  IP purchase; refunds only via support (docs.gcore.com hosting payments).
- func=service.ip.delete elid=<ip id> plid=<server> sok=ok releases one.
- func=service.changepassword elid=<server> passwd confirm sok=ok.
- func=vds.delete elid=<id> sok=ok deletes (virtual servers have a one-month
  minimum term; the panel refuses earlier deletion).
- Billing: func=payment (number, create_date, subaccountamount_iso, status
  1 new/2 paid/3 promised/4 credited/5 awaiting refund/6 refunded/7
  fraudulent/8 initiated/9 cancelled), func=subaccount (balance - access
  level unverified for client accounts: a refusal reads "not exposed").
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import time
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

from . import http as phttp
from .base import (
    ActionResult, ActionTimeout, AdapterError, Billing, Capability,
    CredentialField, Facet, Invoice, IpAddress, IpCost, Money, PaymentRequired,
    ProviderAdapter, Renewal, Server, ServerStatus, UnsupportedAction, parse_dt,
)

DEFAULT_URL = "https://hosting.gcore.com/billmgr"

CAPABILITIES = frozenset({
    Capability.SET_PASSWORD, Capability.DELETE,
    Capability.IP_ADD, Capability.IP_RELEASE, Capability.IP_CHANGE,
})

# item_status -> (canonical status, plain word). Only "suspended" says
# anything about the VM itself (a suspended service is stopped); "active"
# is a billing state, not a power state.
ITEM_STATUS = {
    "1": (ServerStatus.UNKNOWN, "ordered"),
    "2": (ServerStatus.UNKNOWN, "active"),
    "3": (ServerStatus.OFF, "suspended"),
    "4": (ServerStatus.UNKNOWN, "deleted"),
    "5": (ServerStatus.REBUILDING, "processing"),
}

PAYMENT_STATUS = {
    "1": "new", "2": "paid", "3": "promised", "4": "credited",
    "5": "awaiting refund", "6": "refunded", "7": "fraudulent",
    "8": "initiated", "9": "cancelled",
}
UNPAID_PAYMENT = {"1", "3", "8"}

# The panel URL is operator-entered and the panel password is POSTed to it,
# so it must never reach the panel's own network: loopback, RFC 1918,
# CGNAT, link-local (cloud metadata 169.254.169.254), multicast, reserved.
_BLOCKED_NETS = tuple(ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
    "172.16.0.0/12", "192.168.0.0/16", "224.0.0.0/4", "240.0.0.0/4",
    "::/128", "::1/128", "fc00::/7", "fe80::/10", "ff00::/8"))


def _blocked(addr: str) -> bool:
    ip = ipaddress.ip_address(addr)
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in n for n in _BLOCKED_NETS if n.version == ip.version)


async def resolve(host: str) -> list[str]:
    """Every address the panel host resolves to (module-level: the test seam)."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return [i[4][0] for i in infos]


def check_panel_url(url: str) -> tuple[str, str, str]:
    """(origin, host, path) for a usable panel URL, else AdapterError:
    https, default port, no userinfo, no internal IP literal."""
    parts = urlsplit(url)
    host = (parts.hostname or "").rstrip(".").lower()
    if parts.scheme != "https" or not host:
        raise AdapterError("panel URL must be https:// (the password travels to it)")
    if parts.username or parts.password or parts.port not in (None, 443):
        raise AdapterError("panel URL must be a plain https://host/path (no login, default port)")
    try:
        if _blocked(host):
            raise AdapterError("panel URL points at an internal address - refused")
    except ValueError:
        pass  # a hostname, not an IP literal: resolved and checked before connecting
    return f"https://{host}", host, parts.path or "/"


CURRENCY_SYMBOLS = {"€": "EUR", "$": "USD", "£": "GBP", "₽": "RUB"}

POLL_BUDGET_S = 300   # an IP order is provisioned by the panel, not instant
POLL_INTERVAL_S = 5.0


def _v(x: Any, key: str) -> str:
    """A BILLmanager field: {"$": "v"} or a bare "v"; missing -> ""."""
    if not isinstance(x, dict):
        return ""
    val = x.get(key)
    if isinstance(val, dict):
        val = val.get("$", "")
    return "" if val is None else str(val).strip()


def _elems(doc: dict) -> list[dict]:
    e = doc.get("elem", [])
    return e if isinstance(e, list) else [e]


def _status_code(row: dict) -> str:
    """The service's item_status code: the $orig beside the localized word,
    a bare numeric value, or item_real_status when the field is missing."""
    st = row.get("item_status")
    if isinstance(st, dict) and st.get("$orig"):
        return str(st["$orig"]).strip()
    code = _v(row, "item_status")
    if code.isdigit():
        return code
    return _v(row, "item_real_status")


def _deleted(row: dict) -> bool:
    return _status_code(row) == "4"


def _daily(row: dict) -> bool:
    return _v(row, "billdaily") == "on"


def _expiry(row: dict) -> str:
    """The paid-until date: expiredate, or real_expiredate when expiredate is
    a phrase ("Daily charges") instead of a date."""
    exp = _v(row, "expiredate")
    return exp if parse_dt(exp) else _v(row, "real_expiredate")


def _prefix(mask: str) -> int | None:
    try:
        return ipaddress.ip_network(f"0.0.0.0/{mask}").prefixlen if mask else None
    except ValueError:
        return None


def parse_money(text: str) -> Money | None:
    """'5.00 EUR', '€5.00', '5,00 EUR' -> Money. No recognisable currency ->
    None (an amount without its currency is not a price)."""
    m = re.search(r"([€$£₽])?\s*(-?\d+(?:[.,]\d+)?)\s*([A-Z]{3}|[€$£₽])?", text or "")
    if not m:
        return None
    cur = m.group(3) or m.group(1)
    if not cur:
        return None
    try:
        amount = Decimal(m.group(2).replace(",", "."))
    except InvalidOperation:
        return None
    return Money(amount=amount, currency=CURRENCY_SYMBOLS.get(cur, cur))


class GcoreHostingAdapter(ProviderAdapter):
    key = "gcore_hosting"
    display_name = "Gcore Hosting"
    capabilities = CAPABILITIES
    credential_fields = (
        CredentialField(name="url", label="Panel URL", secret=False, default=DEFAULT_URL,
                        help="BILLmanager endpoint of the hosting panel"),
        CredentialField(name="username", label="Panel username", secret=False),
        # BILLmanager has no API tokens - the panel login is the credential
        CredentialField(name="password", label="Panel password"),
    )

    def __init__(self, account_id: int, account_name: str, token: str, http=None):
        super().__init__(account_id, account_name, token, http)
        cred = self.credential()
        origin, self._host, self._path = check_panel_url(cred.get("url") or DEFAULT_URL)
        self._user = cred.get("username", "")
        self._password = cred.get("password", "")
        self._sid: str | None = None
        self.h = phttp.ProviderHttpClient(origin, lambda req: None, transport=http)

    async def close(self) -> None:
        await self.h.aclose()

    # -- transport -------------------------------------------------------------

    async def _login(self) -> None:
        # every connection starts with a login: the host is re-checked here,
        # before the password is sent anywhere
        try:
            addrs = await resolve(self._host)
        except OSError as e:
            raise AdapterError(f"panel host {self._host} does not resolve ({e.strerror or e})")
        if not addrs or any(_blocked(a) for a in addrs):
            raise AdapterError(f"panel host {self._host} resolves to an internal address - refused")
        r = await self.h.request("POST", self._path, data={
            "func": "auth", "username": self._user, "password": self._password,
            "out": "json"})
        doc = self._doc(r)
        sid = _v(doc, "auth")
        if not sid:
            raise AdapterError(f"panel login failed: {self._error(doc) or 'no session returned'}")
        self._sid = sid

    async def _call(self, func: str, *, mutate: bool = False, **params: str) -> dict:
        """One panel function -> doc. Reads retry transient failures; a
        mutating call (sok=ok) is sent once - it may spend money."""
        for attempt in (0, 1):
            if self._sid is None:
                await self._login()
            data = {"func": func, "out": "json", "auth": self._sid, **params}
            if mutate:
                r = await self.h.request("POST", self._path, data=data, retry=False)
            else:
                r = await self.h.request("GET", self._path, params=data)
            doc = self._doc(r)
            err = doc.get("error")
            if err and attempt == 0 and self._is_auth_error(err):
                self._sid = None  # session expired (1h idle): log in again once
                continue
            if err:
                raise AdapterError(f"{func}: {self._error(doc)}")
            return doc
        raise AdapterError(f"{func}: panel session could not be re-established")

    @staticmethod
    def _doc(r) -> dict:
        if r.status_code >= 400:
            raise AdapterError(f"panel HTTP {r.status_code}", status_code=r.status_code)
        try:
            body = r.json()
        except ValueError:
            raise AdapterError("panel answered with non-JSON (is the URL the billmgr endpoint?)")
        doc = body.get("doc") if isinstance(body, dict) else None
        if not isinstance(doc, dict):
            raise AdapterError("panel answered without a doc envelope")
        return doc

    @staticmethod
    def _error(doc: dict) -> str:
        err = doc.get("error")
        if not isinstance(err, dict):
            return ""
        return _v(err, "msg") or _v(err, "$object") or str(err.get("$type", "error"))

    @staticmethod
    def _is_auth_error(err: Any) -> bool:
        if not isinstance(err, dict):
            return False
        kind = str(err.get("$type", "")).lower()
        msg = _v(err, "msg").lower()
        # "access" is a privilege refusal, not an expired session - no re-login
        return kind == "auth" or "authoriz" in msg or "session" in msg

    # -- servers ----------------------------------------------------------------

    async def list_servers(self) -> list[Server]:
        doc = await self._call("vds")
        servers = []
        for row in _elems(doc):
            sid = _v(row, "id")
            if not sid or _deleted(row):
                continue  # deleted services linger in the list; not fleet
            ips = await self.list_ips(sid)
            servers.append(self._to_server(row, ips))
        return servers

    async def get_server(self, provider_id: str) -> Server:
        doc = await self._call("vds")
        for row in _elems(doc):
            if _v(row, "id") == provider_id:
                return self._to_server(row, await self.list_ips(provider_id))
        raise AdapterError(f"no such server: {provider_id}", status_code=404)

    def _to_server(self, row: dict, ips: list[IpAddress]) -> Server:
        code = _status_code(row)
        status, word = ITEM_STATUS.get(code, (ServerStatus.UNKNOWN, code or "?"))
        facets = [Facet(label="service", value=word)]
        if _v(row, "pricelist"):
            facets.append(Facet(label="plan", value=_v(row, "pricelist")))
        if _daily(row):
            facets.append(Facet(label="billing", value="daily from balance"))
        if _expiry(row):
            facets.append(Facet(label="paid until", value=_expiry(row)))
        if _v(row, "autoprolong"):
            facets.append(Facet(label="auto-renew", value=_v(row, "autoprolong")))
        if _v(row, "cost"):
            # the panel's own text: its period isn't stated beside it, so it
            # is shown verbatim instead of being passed off as a monthly price
            facets.append(Facet(label="cost (panel)", value=_v(row, "cost")))
        primary = next((ip.address for ip in ips if ip.primary), None)
        return Server(
            provider_id=_v(row, "id"),
            name=_v(row, "domain") or _v(row, "name") or _v(row, "id"),
            adapter=self.key,
            account_id=self.account_id,
            status=status,
            ipv4=primary or _v(row, "ip") or None,
            region=_v(row, "datacentername") or _v(row, "datacenter") or None,
            server_type=_v(row, "pricelist") or None,
            created=parse_dt(_v(row, "createdate")),
            facets=facets,
            ips=ips,
            # VM power state, traffic and a period-stated price are not in
            # the billing panel's API
            not_exposed=["power_state", "allowance", "monthly_price", "labels"],
        )

    async def perform_action(self, cap: Capability, server_id: str,
                             params: dict[str, Any]) -> ActionResult:
        if cap == Capability.SET_PASSWORD:
            pw = params.get("password") or ""
            if len(pw) < 8:
                raise AdapterError("password must be at least 8 characters")
            await self._call("service.changepassword", mutate=True, elid=server_id,
                             passwd=pw, confirm=pw, sok="ok")
            # the panel's ok IS its confirmation: the password is not readable back
            return ActionResult(detail="panel accepted the new root password")
        if cap == Capability.DELETE:
            await self._call("vds.delete", mutate=True, elid=server_id, sok="ok")
            deadline = time.monotonic() + POLL_BUDGET_S
            while True:
                doc = await self._call("vds")
                rows = {_v(r, "id"): r for r in _elems(doc)}
                if server_id not in rows or _deleted(rows[server_id]):
                    return ActionResult(detail="gone from the panel's server list")
                if time.monotonic() >= deadline:
                    raise ActionTimeout(f"delete {server_id}")
                await asyncio.sleep(POLL_INTERVAL_S)
        raise UnsupportedAction(self.key, cap)

    # -- IPs ------------------------------------------------------------------------

    async def list_ips(self, server_id: str) -> list[IpAddress]:
        doc = await self._call("service.ip", elid=server_id)
        rows = [r for r in _elems(doc) if _v(r, "name") or _v(r, "ip")]
        # primary = the is_main row; panels that don't flag one mark it
        # no_delete instead. With neither flag the first row is treated as
        # primary: wrongly protecting an extra IP is recoverable, releasing
        # the server's own address is not.
        if any(_v(r, "is_main") == "on" for r in rows):
            marked = [_v(r, "is_main") == "on" for r in rows]
        elif any(_v(r, "no_delete") == "on" for r in rows):
            marked = [_v(r, "no_delete") == "on" for r in rows]
        else:
            marked = [i == 0 for i in range(len(rows))]
        out = []
        for row, primary in zip(rows, marked):
            addr = _v(row, "name") or _v(row, "ip")
            out.append(IpAddress(
                address=addr,
                version=6 if ":" in addr else 4,
                primary=primary,
                kind="main" if primary else (_v(row, "type") or "additional"),
                provider_ip_id=_v(row, "id") or None,
                gateway=_v(row, "gateway") or None,
                prefix=_prefix(_v(row, "mask")),
            ))
        return out

    async def add_ip(self, server_id: str) -> IpAddress:
        before = {ip.address for ip in await self.list_ips(server_id)}
        form = await self._call("service.ip.edit", plid=server_id)
        ip_type = self._form_type(form)
        if not ip_type:
            raise AdapterError("the panel's add-IP form offered no IP type for this server")
        resp = await self._call("service.ip.edit", mutate=True, plid=server_id,
                                type=ip_type, domain=_v(form, "domain"),
                                count="1", sok="ok")
        order = _v(resp, "billorder") or _v(resp, "payment_id")
        if order:
            # the panel put the IP behind a payment instead of charging the
            # balance; PaymentRequired keeps the pay link only if it's https
            raise PaymentRequired("add IP", order, _v(resp, "ok") or None)
        deadline = time.monotonic() + POLL_BUDGET_S
        while True:
            new = [ip for ip in await self.list_ips(server_id) if ip.address not in before]
            if new:
                return new[0]
            if time.monotonic() >= deadline:
                raise ActionTimeout(f"add IP to {server_id} (ordered; address not assigned yet)")
            await asyncio.sleep(POLL_INTERVAL_S)

    @staticmethod
    def _form_type(form: dict) -> str:
        sl = form.get("slist", [])
        for s in sl if isinstance(sl, list) else [sl]:
            if isinstance(s, dict) and s.get("$name") == "type":
                vals = s.get("val", [])
                vals = vals if isinstance(vals, list) else [vals]
                if vals and isinstance(vals[0], dict):
                    return str(vals[0].get("$key", ""))
        return ""

    async def release_ip(self, server_id: str, address: str) -> None:
        ips = await self.list_ips(server_id)
        ip = next((i for i in ips if i.address == address), None)
        if ip is None:
            return  # already gone - the outcome the caller wanted
        if ip.primary:
            raise AdapterError(f"{address} is the server's primary IP - never released here")
        try:
            await self._call("service.ip.delete", mutate=True, elid=ip.provider_ip_id or "",
                             plid=server_id, sok="ok")
        except AdapterError as e:
            if "does not exist" not in str(e):
                raise
        deadline = time.monotonic() + POLL_BUDGET_S
        while address in {i.address for i in await self.list_ips(server_id)}:
            if time.monotonic() >= deadline:
                raise ActionTimeout(f"release {address}")
            await asyncio.sleep(POLL_INTERVAL_S)

    async def ip_cost(self, server_id: str) -> IpCost | None:
        server = next((r for r in _elems(await self._call("vds"))
                       if _v(r, "id") == server_id), None)
        if server is not None and _daily(server):
            # pay as you go: each add-on IP is a daily charge from the
            # balance, so a change costs the days each IP was held
            price = None
            for row in _elems(await self._call("expense")):
                if _v(row, "intname") == "ip" and _v(row, "main_item") == server_id:
                    price = parse_money(_v(row, "amount"))
                    break  # newest first
            return IpCost(price=price, per="day",
                          note="billed daily from the balance while the IP is held")
        return IpCost(price=None, per="purchase",
                      note="each change orders a new IP at the panel's price; "
                           "refunds for released IPs only via a support request")

    # -- billing -----------------------------------------------------------------

    async def get_billing(self) -> Billing:
        not_exposed = ["month_to_date", "upcoming"]
        balance = None
        try:
            sub = await self._call("subaccount")
            rows = _elems(sub)
            if rows:
                balance = parse_money(_v(rows[0], "balance"))
        except AdapterError:
            pass
        if balance is None:
            not_exposed.append("balance")
        invoices = []
        try:
            for row in _elems(await self._call("payment")):
                code = _v(row, "status")
                total = parse_money(_v(row, "subaccountamount_iso")
                                    or _v(row, "paymethodamount_iso"))
                open_amount = None
                if total is not None:
                    open_amount = total if code in UNPAID_PAYMENT else \
                        Money(amount=Decimal("0"), currency=total.currency)
                invoices.append(Invoice(
                    id=_v(row, "number") or _v(row, "id"),
                    date=parse_dt(_v(row, "create_date")),
                    total=total, open_amount=open_amount,
                    status=PAYMENT_STATUS.get(code, code),
                ))
        except AdapterError:
            not_exposed.append("invoices")
        renewals = []
        for row in _elems(await self._call("vds")):
            if _deleted(row) or not _expiry(row):
                continue
            ap = _v(row, "autoprolong")
            # a daily-billed service charges the balance every day: it renews
            # automatically whether or not autoprolong is set
            auto = True if _daily(row) else (None if not ap else ap not in ("off", "0", "null"))
            renewals.append(Renewal(
                provider_id=_v(row, "id"), name=_v(row, "domain") or _v(row, "id"),
                date=parse_dt(_expiry(row)), auto=auto,
            ))
        return Billing(model="prepaid balance; each server renews from it at its expiry date",
                       balance=balance, invoices=invoices, renewals=renewals,
                       not_exposed=not_exposed)
