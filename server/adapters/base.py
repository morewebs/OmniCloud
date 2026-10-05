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

from pydantic import BaseModel, Field


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


class ActionTimeout(AdapterError):
    """Provider accepted the action but did not reach the requested state
    within the poll budget. Never rendered as success."""
    def __init__(self, what: str):
        super().__init__(f"{what}: provider did not confirm completion in time")
