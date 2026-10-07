"""Canonical entity model + provider adapter protocol.

Both adapters (Hetzner, Leaseweb, later OVH/Gcore/...) map their API onto these
entities; the UI renders only canonical entities. If a provider offers no
equivalent for a field, the adapter omits it and lists the field name in
`Server.not_exposed` so the UI can say "not exposed" (a value the provider's
API genuinely lacks) instead of "-" (momentarily missing - see design.md 6).

The adapter, never the panel, decides billing/counting semantics and when an
action is complete: `perform_action` returns only after observing the
provider's own view of the change. A 202 accepted response is never success.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class Capability(StrEnum):
    POWER_ON = "power_on"
    POWER_OFF = "power_off"
    REBOOT = "reboot"
    SHUTDOWN = "shutdown"
    RENAME = "rename"
    RELABEL = "relabel"
    FIREWALL = "firewall"
    REBUILD = "rebuild"
    DELETE = "delete"
    SET_PASSWORD = "set_password"  # perform_action params: {"password": str}
    # IP management runs on its own routes (api.py "ips" section), never
    # through perform_action: acquiring an IP spends money and has its own
    # guards (purchases_enabled, daily change cap).
    IP_ADD = "ip_add"          # acquire one more swappable IP on a server
    IP_RELEASE = "ip_release"  # release one swappable IP
    IP_CHANGE = "ip_change"    # swap one swappable IP for a fresh one (add + release)


class ServerStatus(StrEnum):
    RUNNING = "running"
    OFF = "off"
    REBUILDING = "rebuilding"
    UNKNOWN = "unknown"


class TrafficCounting(StrEnum):
    OUTGOING_ONLY = "outgoing_only"
    INGRESS_AND_EGRESS = "ingress_and_egress"


class Money(BaseModel):
    amount: Decimal
    currency: str = "EUR"
    vat_inclusive: bool | None = None


class Allowance(BaseModel):
    included_bytes: int | None = None
    used_bytes: int | None = None
    counting: TrafficCounting | None = None
    # Plain-language window definition, adapter-authored. The UI renders it
    # verbatim and never re-derives a provider's billing model.
    window: str | None = None
    reset_at: datetime | None = None
    overage_price: Money | None = None  # per TB over the included amount
    projected_overage_cost: Money | None = None  # adapter-computed; None = not supported


class Facet(BaseModel):
    """Generic labeled attachment: floating IPs, private networks, pack,
    port speed, anything provider-specific. Rendered as a plain label/value
    row; no provider-specific UI ever exists (design.md 4)."""
    label: str
    value: str


class IpAddress(BaseModel):
    """One address on a server. `primary` marks the address the provider
    ties to the server itself: the IP-change path never touches it. The
    swappable addresses are the extra ones (additional/floating/reserved)."""
    address: str
    version: int = 4
    primary: bool = False
    kind: str = ""                     # provider's own word: "additional", "floating", ...
    provider_ip_id: str | None = None  # provider handle when it differs from the address
    monthly_price: Money | None = None
    # on-link gateway and prefix length, when the provider states them: an
    # extra IP from another subnet needs its own source route on the server
    gateway: str | None = None
    prefix: int | None = None


class Server(BaseModel):
    provider_id: str
    name: str
    adapter: str
    account_id: int
    status: ServerStatus = ServerStatus.UNKNOWN
    ipv4: str | None = None
    region: str | None = None
    server_type: str | None = None
    created: datetime | None = None
    labels: dict[str, str] | None = None
    monthly_price: Money | None = None
    allowance: Allowance | None = None
    facets: list[Facet] = Field(default_factory=list)
    not_exposed: list[str] = Field(default_factory=list)
    # every public address the provider reports; empty + "ips" in
    # not_exposed when the API has no per-server IP list
    ips: list[IpAddress] = Field(default_factory=list)


class ActionResult(BaseModel):
    detail: str | None = None  # what the provider confirmed


class IpOffer(BaseModel):
    """Extra-IP capability of a plan, in the provider's own vocabulary.
    price=None means NOT PUBLISHED by the provider - rendered as such,
    never invented (design.md 6)."""
    kind: str                     # provider's own word: "floating", "primary", "failover"
    included: int                 # IPs included with the base plan
    price: Money | None = None    # per additional IP, if published
    limit: int | None = None      # max per server, if documented
    note: str | None = None      # e.g. "available on request via support"


class IpCost(BaseModel):
    """What acquiring one more IP costs, in the provider's own billing unit:
    a reserved IP billed by the minute is cheap to churn, a monthly one is
    a fresh month's rent per change. price=None = not published."""
    price: Money | None = None
    per: str                      # "hour" | "day" | "month" | "purchase"
    note: str | None = None


def https_url(u: str | None) -> str | None:
    """A provider-supplied link the UI may render: https only. Anything else
    (javascript:, data:, http:) is dropped - a hostile or compromised panel
    must not plant a script link in the operator's browser."""
    from urllib.parse import urlsplit
    if not u or not isinstance(u, str):
        return None
    u = u.strip()
    try:
        parts = urlsplit(u)
    except ValueError:
        return None
    return u if parts.scheme == "https" and parts.netloc else None


class Invoice(BaseModel):
    """A bill/invoice/unpaid order as the provider reports it. status is the
    provider's own word (paid, unpaid, OVERDUE, notPaid...) - rendered
    verbatim, never mapped onto a guessed common vocabulary."""
    id: str
    date: datetime | None = None
    due_date: datetime | None = None
    total: Money | None = None
    open_amount: Money | None = None  # still to pay; None = not exposed
    status: str = ""
    url: str | None = None            # provider's own view/pay page (https only)

    @field_validator("url")
    @classmethod
    def _https_only(cls, v: str | None) -> str | None:
        return https_url(v)


class Renewal(BaseModel):
    provider_id: str
    name: str
    date: datetime | None = None
    auto: bool | None = None          # auto-renew from balance, if exposed


class Billing(BaseModel):
    """Per-account billing snapshot. Every field the provider's API lacks is
    listed in not_exposed (rendered "not exposed", never zero)."""
    model: str                        # plain text, adapter-authored: "prepaid wallet"
    balance: Money | None = None
    month_to_date: Money | None = None
    upcoming: Money | None = None     # the provider's own next-invoice estimate (proforma)
    invoices: list[Invoice] = Field(default_factory=list)
    unpaid_orders: list[Invoice] = Field(default_factory=list)
    renewals: list[Renewal] = Field(default_factory=list)
    not_exposed: list[str] = Field(default_factory=list)


class CredentialField(BaseModel):
    """One input of an account's credential form. Adapters with more than
    one field receive their credential as a JSON object string."""
    name: str
    label: str
    secret: bool = True
    default: str | None = None
    help: str | None = None


TOKEN_FIELD = CredentialField(name="token", label="API token")


class Plan(BaseModel):
    """One row = (adapter, plan, location): Hetzner prices per location,
    LeaseWeb per region - the honest unit for both."""
    adapter: str
    name: str                     # "cx22", "lsw.m3.medium"
    location: str                  # provider location/region code
    cpu_cores: int | None = None
    cpu_arch: str | None = None
    ram_gb: float | None = None
    disk_gb: int | None = None
    disk_type: str | None = None
    price_monthly: Money | None = None
    price_hourly: Money | None = None
    included_traffic_bytes: int | None = None
    counting: TrafficCounting | None = None
    # Plain-language traffic statement for unmetered plans ("unlimited
    # traffic, 10 Gbit/s port") where no byte number is published. Rendered
    # INSTEAD of a number - never alongside a guessed one.
    traffic_note: str | None = None
    overage_price: Money | None = None   # per TB
    extra_ip: IpOffer | None = None      # None = not offered or undocumented
    billing_model: str = ""              # plain text: "prepaid wallet", "monthly invoice" -
                                         # adapter-authored, UI renders verbatim
    deprecated: bool = False


class ProviderAdapter(ABC):
    """One instance per connected provider account. Capabilities the adapter
    lacks are absent from the UI, never rendered as disabled buttons."""

    key: str = "abstract"
    display_name: str = "abstract"
    capabilities: frozenset[Capability] = frozenset()
    credential_fields: tuple[CredentialField, ...] = (TOKEN_FIELD,)

    def __init__(self, account_id: int, account_name: str, token: str, http=None):
        # `http` injection is the test seam (MockTransport); production leaves it None.
        self.account_id = account_id
        self.account_name = account_name
        self._token = token  # never logged, never serialized
        self._http = http

    @abstractmethod
    async def list_servers(self) -> list[Server]:
        """Full snapshot incl. allowance + traffic facts."""

    @abstractmethod
    async def get_server(self, provider_id: str) -> Server:
        """Fresh single-server read (used to confirm actions)."""

    @abstractmethod
    async def perform_action(self, cap: Capability, server_id: str,
                             params: dict[str, Any]) -> ActionResult:
        """Execute and confirm. Returns only after the provider's own view
        reflects the requested change; raises ActionTimeout otherwise.

        `params` per capability:
          rename: {"name": str}
          relabel: {"labels": dict[str, str]}
          rebuild: {"image": str}
          firewall: (via apply_firewall, not perform_action)
        """

    async def apply_firewall(self, server_id: str, rules: list[dict[str, Any]],
                             attach: bool = True) -> None:
        raise UnsupportedAction(self.key, Capability.FIREWALL)

    # -- IPs: each returns only after the provider's own view confirms ----

    async def add_ip(self, server_id: str) -> IpAddress:
        """Acquire one more public IPv4 and attach it to the server. Spends
        money. Raises PaymentRequired when the provider holds the IP behind
        an unpaid order."""
        raise UnsupportedAction(self.key, Capability.IP_ADD)

    async def release_ip(self, server_id: str, address: str) -> None:
        """Detach and give back one swappable IP. Never the primary."""
        raise UnsupportedAction(self.key, Capability.IP_RELEASE)

    async def ip_cost(self, server_id: str) -> IpCost | None:
        """What one add_ip costs; None = the provider doesn't say."""
        return None

    async def get_billing(self) -> Billing | None:
        """Account billing snapshot; None = adapter has no billing support."""
        return None

    # Server ordering is duck-typed like list_firewalls: an adapter that can
    # order servers defines
    #   async def provision(self, plan_name, location, options) -> str
    # returning the new server's provider_id once the provider's own view
    # lists it, or raising PaymentRequired when the provider holds it behind
    # an unpaid order. The order POST is sent exactly once (never retried).
    # options: {"hostname", "image"} from the order dialog.

    async def order_status(self, order_ref: str) -> str | None:
        """Where a provider order left awaiting_payment stands now:
        "unpaid" | "delivered" | "cancelled" | None (unknown/unsupported)."""
        return None

    def credential(self) -> dict[str, str]:
        """Multi-field credentials arrive as a JSON object string."""
        return _parse_credential(self._token)


def _parse_credential(token: str) -> dict[str, str]:
    import json
    try:
        data = json.loads(token)
    except ValueError:
        raise AdapterError("stored credential is not in this provider's field format")
    if not isinstance(data, dict):
        raise AdapterError("stored credential is not in this provider's field format")
    return {k: str(v) for k, v in data.items()}


class AdapterError(Exception):
    """Base for adapter failures; message is safe to show in the UI.
    status_code (when set by the HTTP layer) lets callers distinguish
    e.g. a real 404 from auth failures or persistent 5xx."""
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def parse_dt(s: str | None) -> datetime | None:
    """Parse a provider ISO datetime ("2026-09-01T12:34:56Z" or offset form).
    None/unparseable -> None, never a crash over a cosmetic field."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


class UnsupportedAction(AdapterError):
    def __init__(self, adapter: str, cap: Capability):
        super().__init__(f"{adapter} does not support {cap.value}",
                         status_code=None)


class PaymentRequired(AdapterError):
    """Provider accepted the order but delivers only after payment (an OVH
    order left notPaid, a BILLmanager payment with a pay URL). Never
    rendered as success: the caller records awaiting_payment."""
    def __init__(self, what: str, order_ref: str, pay_url: str | None = None):
        super().__init__(f"{what}: provider order {order_ref} awaits payment")
        self.order_ref = order_ref
        self.pay_url = https_url(pay_url)


class ActionTimeout(AdapterError):
    """Provider accepted the action but did not reach the requested state
    within the poll budget. Never rendered as success."""
    def __init__(self, what: str):
        super().__init__(f"{what}: provider did not confirm completion in time")
