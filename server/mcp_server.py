"""MCP server: the REST surface as Model Context Protocol tools at /mcp.

Every tool calls the api.py route it wraps, so validation, audit records,
sync side effects and error text are the REST API's own - MCP is a second
transport, not a second implementation. The route's own
Depends(require_user / require_admin) is the role contract: a viewer
token gets the read tools, admin tools answer "403: Admin role required".

Auth is Bearer personal API tokens only - never the session cookie, so
there is no CSRF surface - and the token is re-checked on every tool call,
so a revoked token or a disabled user stops working mid-session.
"""
from __future__ import annotations

import asyncio
import contextlib
import functools
import inspect
from typing import Annotated, Any, Literal

import anyio.to_thread
from fastapi import HTTPException
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from pydantic import Field
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from . import api, auth, sync, version

INSTRUCTIONS = """\
OmniCloud is a multi-provider cloud control panel (servers, traffic
allowances, billing, a plan marketplace, orders). These tools act as the
user who owns the API token: viewer tokens are read-only, and admin tools
answer "403: Admin role required" for them.

Rules the panel's data follows - keep them when you report on it:
- A server is identified by the pair (account_id, provider_id); names are
  not unique across accounts.
- null, a missing field, or a field listed in a server's `not_exposed` is
  an unknown value, never zero. A server that is not reporting is not idle.
- Money is per currency: never add EUR and USD amounts together.
- A server action is complete only when its status is "done" (the provider
  confirmed the change). "in_progress" means poll get_action - never
  report it as success.
- Destructive tools (rebuild_server, delete_server, delete_account,
  apply_update) are irreversible: get the human's explicit confirmation,
  naming the exact target, before calling one.
- Tools that spend money (change_ip, add_ip, execute_order on a real
  order) need the human's explicit go-ahead with the cost from describe_ip
  or the order's estimate. A server's primary IP is never changeable.
"""

mcp = MCPServer("OmniCloud", instructions=INSTRUCTIONS, version=version.VERSION)

READ = ToolAnnotations(read_only_hint=True, idempotent_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False)
IDEMPOTENT = ToolAnnotations(read_only_hint=False, destructive_hint=False,
                             idempotent_hint=True)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True)

AccountId = Annotated[int, Field(description="Provider account id (list_accounts)")]
ProviderId = Annotated[str, Field(min_length=1,
                                  description="The server's id at its provider (list_fleet)")]
WaitSeconds = Annotated[int, Field(
    ge=0, le=600,
    description="How long to wait for the provider to confirm before "
                "returning status in_progress (then poll get_action)")]


# -- plumbing ------------------------------------------------------------------

@contextlib.contextmanager
def _http_errors():
    """Route HTTPExceptions -> tool errors the model can read (same status
    and text the REST client would get)."""
    try:
        yield
    except HTTPException as e:
        raise ToolError(f"{e.status_code}: {e.detail}") from None


def _full_scope_user(header: str | None) -> auth.User | None:
    """Bearer -> user, full-scope tokens only. MCP authenticates outside
    require_user, so it must apply the scope rule itself: an ip_change
    token (held by a remote rotation script) reaches nothing here."""
    user = auth.user_for_bearer(header)
    return user if user is not None and user.scope == "full" else None


async def _user(ctx: Context) -> auth.User:
    header = (ctx.headers or {}).get("authorization")
    user = await anyio.to_thread.run_sync(_full_scope_user, header)
    if user is None:
        raise ToolError("401: Not signed in")
    return user


async def _call(ctx: Context, route, **kwargs) -> Any:
    """Run an api.py route as the token's user, enforcing the route's own
    role dependency."""
    user = await _user(ctx)
    for name, p in inspect.signature(route).parameters.items():
        dep = getattr(p.default, "dependency", None)
        if dep is auth.require_admin and user.role != "admin":
            raise ToolError("403: Admin role required")
        if dep in (auth.require_user, auth.require_admin):
            kwargs[name] = user
    with _http_errors():
        if inspect.iscoroutinefunction(route):
            return await route(**kwargs)
        # sync routes do blocking sqlite work: worker thread, as FastAPI does
        return await anyio.to_thread.run_sync(functools.partial(route, **kwargs))


# strong refs: a bare create_task can be garbage-collected mid-action
_inflight: set[asyncio.Task] = set()


async def _server_action(ctx: Context, account_id: int, provider_id: str, kind: str,
                         params: dict, wait_seconds: int) -> dict:
    """POST /servers/.../actions, minus the blocking: the confirm loop can
    outlast any MCP client timeout, so the action runs as its own task and
    the call returns in_progress (with the id to poll) if it isn't done."""
    user = await _user(ctx)
    if user.role != "admin":
        raise ToolError("403: Admin role required")
    with _http_errors():
        api.check_action(account_id, kind)
    action_id, before = sync.begin_action(account_id, provider_id, kind, user.id)
    task = asyncio.create_task(sync.finish_action(
        action_id, account_id, provider_id, kind, user.id, params, before))
    _inflight.add(task)
    task.add_done_callback(_inflight.discard)
    try:
        # shield: a client timeout or disconnect must not cancel the provider
        # action halfway and strand its row in_progress
        await asyncio.wait_for(asyncio.shield(task), wait_seconds)
    except TimeoutError:
        return {"action_id": action_id, "status": "in_progress",
                "detail": "provider has not confirmed yet - poll get_action"}
    row = await _call(ctx, api.get_action, action_id=action_id)
    if row["status"] == "failed":
        raise ToolError(f"502: {row['detail'] or 'action failed'}")
    return row


# -- panel ---------------------------------------------------------------------

@mcp.tool(annotations=READ)
async def whoami(ctx: Context) -> dict:
    """The user this API token acts as, and their role (admin or viewer)."""
    return await _call(ctx, api.me)


@mcp.tool(annotations=READ)
async def get_panel_status(ctx: Context) -> dict:
    """Panel version, database health and the cached update status."""
    return {"health": await _call(ctx, api.health),
            "update": await _call(ctx, api.update_status)}


@mcp.tool(annotations=READ)
async def check_for_update(ctx: Context) -> dict:
    """Ask GitHub now whether a newer panel release exists."""
    return await _call(ctx, api.update_check)


@mcp.tool(annotations=DESTRUCTIVE)
async def apply_update(ctx: Context) -> dict:
    """Admin. Pull the newer release, rebuild, and restart the panel (it
    exits for its supervisor to restart; in-flight work is interrupted).
    Run check_for_update first. Confirm with the human before calling."""
    return await _call(ctx, api.update_apply)


# -- fleet ---------------------------------------------------------------------

@mcp.tool(annotations=READ)
async def get_overview(ctx: Context) -> dict:
    """Start here. Dashboard aggregate: server status counts, monthly spend
    per adapter and currency, projected overage, alerts (down servers,
    allowances above 80%, sync errors), 30-day traffic and spend, recent
    actions and orders."""
    return await _call(ctx, api.overview)


@mcp.tool(annotations=READ)
async def list_fleet(
        ctx: Context,
        adapter: Annotated[str | None, Field(
            description="Only accounts of this provider, e.g. 'hetzner' (list_adapters)")] = None,
        account_id: Annotated[int | None, Field(description="Only this account")] = None,
        status: Literal["running", "off", "rebuilding", "unknown"] | None = None,
        query: Annotated[str | None, Field(
            description="Case-insensitive substring of name, IPv4 or provider_id")] = None,
) -> dict:
    """Servers per provider account (from the panel's sync cache, each with
    last_seen_at), per-account sync state and in-flight actions. With a
    status or query filter, accounts with no matching server are dropped."""
    data = await _call(ctx, api.fleet)
    q = query.lower() if query else None

    def keep(s: dict) -> bool:
        return ((status is None or s.get("status") == status)
                and (q is None or any(q in str(s.get(k) or "").lower()
                                      for k in ("name", "ipv4", "provider_id"))))

    accts = []
    for a in data["accounts"]:
        if (adapter and a["adapter"] != adapter) or \
                (account_id is not None and a["id"] != account_id):
            continue
        servers = [s for s in a["servers"] if keep(s)]
        if (status or q) and not servers:
            continue
        accts.append({**a, "servers": servers})
    data["accounts"] = accts
    return data


@mcp.tool(annotations=READ)
async def get_server(ctx: Context, account_id: AccountId, provider_id: ProviderId) -> dict:
    """One cached server in full: facets, allowance, labels, firewalls and
    daily traffic history."""
    return await _call(ctx, api.fleet_server, account_id=account_id,
                       provider_id=provider_id)


@mcp.tool(annotations=READ)
async def list_allowances(ctx: Context) -> dict:
    """Traffic allowance per server: bytes used / included, the provider's
    counting rule and window in plain language, projected overage cost."""
    return {"allowances": await _call(ctx, api.allowances)}


@mcp.tool(annotations=READ)
async def get_billing_summary(ctx: Context) -> dict:
    """Monthly base spend and projected overage per (adapter, currency).
    price_not_exposed=true means some servers' prices are unknown, so the
    total is a lower bound."""
    return {"billing": await _call(ctx, api.billing_summary)}


@mcp.tool(annotations=READ)
async def get_account_billing(ctx: Context) -> dict:
    """Per-account billing as each provider reports it: model, balance,
    invoices (due date, open amount, provider's status), unpaid orders with
    pay links, renewals. Fields in a snapshot's not_exposed are unknown,
    never zero."""
    return {"accounts": await _call(ctx, api.billing_accounts)}


@mcp.tool(annotations=IDEMPOTENT)
async def refresh_account_billing(ctx: Context, account_id: AccountId) -> dict:
    """Admin. Re-read one account's billing from the provider now."""
    return await _call(ctx, api.billing_refresh, account_id=account_id)


@mcp.tool(annotations=IDEMPOTENT)
async def sync_account(ctx: Context, account_id: AccountId) -> dict:
    """Ask the account's sync loop to re-fetch from the provider now. Returns
    immediately; fresh data shows in list_fleet once the provider answers."""
    return await _call(ctx, api.force_sync, account_id=account_id)


# -- server actions ------------------------------------------------------------

@mcp.tool(annotations=WRITE)
async def power_server(ctx: Context, account_id: AccountId, provider_id: ProviderId,
                       kind: Annotated[Literal["power_on", "power_off", "reboot", "shutdown"], Field(
                           description="shutdown is a graceful OS shutdown; power_off "
                                       "cuts power immediately")],
                       wait_seconds: WaitSeconds = 50) -> dict:
    """Admin. Power on, power off, reboot or shut down a server. Only the
    kinds the adapter supports work (list_adapters -> capabilities)."""
    return await _server_action(ctx, account_id, provider_id, kind, {}, wait_seconds)


@mcp.tool(annotations=IDEMPOTENT)
async def rename_server(ctx: Context, account_id: AccountId, provider_id: ProviderId,
                        name: Annotated[str, Field(min_length=1)],
                        wait_seconds: WaitSeconds = 50) -> dict:
    """Admin. Rename a server at its provider."""
    return await _server_action(ctx, account_id, provider_id, "rename",
                                {"name": name}, wait_seconds)


@mcp.tool(annotations=IDEMPOTENT)
async def relabel_server(ctx: Context, account_id: AccountId, provider_id: ProviderId,
                         labels: Annotated[dict[str, str], Field(
                             description="The complete new label set")],
                         wait_seconds: WaitSeconds = 50) -> dict:
    """Admin. Replace a server's labels/tags at its provider."""
    return await _server_action(ctx, account_id, provider_id, "relabel",
                                {"labels": labels}, wait_seconds)


@mcp.tool(annotations=DESTRUCTIVE)
async def rebuild_server(ctx: Context, account_id: AccountId, provider_id: ProviderId,
                         image: Annotated[str, Field(min_length=1, description=(
                             "OS image id/name (list_os_images)"))],
                         wait_seconds: WaitSeconds = 50) -> dict:
    """Admin. IRREVERSIBLE: reinstall the server from an image, erasing its
    disks. Confirm the exact server with the human before calling."""
    return await _server_action(ctx, account_id, provider_id, "rebuild",
                                {"image": image}, wait_seconds)


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_server(ctx: Context, account_id: AccountId, provider_id: ProviderId,
                        wait_seconds: WaitSeconds = 50) -> dict:
    """Admin. IRREVERSIBLE: delete the server at its provider. Some
    providers schedule it for contract end instead (the detail says so).
    Confirm the exact server with the human before calling."""
    return await _server_action(ctx, account_id, provider_id, "delete", {}, wait_seconds)


@mcp.tool(annotations=READ)
async def list_actions(ctx: Context) -> dict:
    """The 100 most recent server actions with status and provider detail."""
    return {"actions": await _call(ctx, api.list_actions)}


@mcp.tool(annotations=READ)
async def get_action(ctx: Context, action_id: int) -> dict:
    """One server action: in_progress, done (provider confirmed) or failed."""
    return await _call(ctx, api.get_action, action_id=action_id)


# -- firewalls -----------------------------------------------------------------

@mcp.tool(annotations=READ)
async def list_firewalls(ctx: Context, account_id: AccountId) -> dict:
    """Live from the provider: the account's firewalls, their rules where
    exposed, and which servers each is applied to."""
    return {"firewalls": await _call(ctx, api.list_firewalls, account_id=account_id)}


@mcp.tool(annotations=IDEMPOTENT)
async def attach_firewall(ctx: Context, account_id: AccountId, firewall_id: int,
                          provider_id: ProviderId) -> dict:
    """Admin. Apply an existing firewall to a server."""
    return await _call(ctx, api.attach_firewall, account_id=account_id,
                       firewall_id=firewall_id, provider_id=provider_id)


@mcp.tool(annotations=IDEMPOTENT)
async def detach_firewall(ctx: Context, account_id: AccountId, firewall_id: int,
                          provider_id: ProviderId) -> dict:
    """Admin. Remove a firewall from a server (the firewall itself stays)."""
    return await _call(ctx, api.detach_firewall, account_id=account_id,
                       firewall_id=firewall_id, provider_id=provider_id)


@mcp.tool(annotations=WRITE)
async def apply_firewall(
        ctx: Context, account_id: AccountId, provider_id: ProviderId,
        rules: Annotated[list[dict[str, Any]], Field(description=(
            "Provider rule objects, e.g. {\"direction\": \"in\", \"protocol\": \"tcp\", "
            "\"port\": \"22\", \"source_ips\": [\"0.0.0.0/0\", \"::/0\"]}. Outbound rules "
            "use destination_ips; icmp takes no port. At least one inbound rule "
            "is required, or the server would be locked out"))],
        attach: bool = True,
) -> dict:
    """Admin. Create a firewall with these rules and (by default) attach it
    to the server."""
    return await _call(ctx, api.apply_firewall, account_id=account_id,
                       provider_id=provider_id,
                       body=api.FirewallBody(rules=rules, attach=attach))


# -- provider accounts -----------------------------------------------------------

@mcp.tool(annotations=READ)
async def list_accounts(ctx: Context) -> dict:
    """Connected provider accounts: adapter, enabled, credential scope and
    last 4 characters (never the token), last sync success/error."""
    return {"accounts": await _call(ctx, api.get_accounts)}


@mcp.tool(annotations=READ)
async def list_adapters(ctx: Context) -> dict:
    """Every provider the panel knows: fleet adapters with their action
    capabilities, and marketplace-only catalog providers."""
    return {"adapters": await _call(ctx, api.adapters)}


@mcp.tool(annotations=WRITE)
async def create_account(
        ctx: Context,
        adapter: Annotated[str, Field(description="Fleet adapter key (list_adapters)")],
        name: Annotated[str, Field(min_length=1)],
        token: Annotated[str | None, Field(min_length=1, description=(
            "The provider API token, for adapters whose credential_fields is a "
            "single 'token'. Stored encrypted; never returned afterwards"))] = None,
        fields: Annotated[dict[str, str] | None, Field(description=(
            "For adapters with several credential_fields (list_adapters), e.g. "
            "{username, password}. Stored encrypted"))] = None,
        scope: str | None = None,
) -> dict:
    """Admin. Connect a provider account and start syncing its fleet."""
    return await _call(ctx, api.create_account, body=api.AccountBody(
        adapter=adapter, name=name, token=token, fields=fields, scope=scope))


@mcp.tool(annotations=IDEMPOTENT)
async def update_account(ctx: Context, account_id: AccountId,
                         name: Annotated[str | None, Field(min_length=1)] = None,
                         enabled: bool | None = None,
                         purchases_enabled: bool | None = None) -> dict:
    """Admin. Rename an account, enable/disable its sync, or switch real
    purchases (IP changes, real orders) on/off - turning purchases on lets
    the panel spend money there: only with the human's explicit go-ahead."""
    return await _call(ctx, api.patch_account, account_id=account_id,
                       body=api.AccountPatch(name=name, enabled=enabled,
                                             purchases_enabled=purchases_enabled))


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_account(ctx: Context, account_id: AccountId) -> dict:
    """Admin. IRREVERSIBLE: remove the account, its stored credential and
    its cached servers from the panel (servers at the provider are not
    touched; order history is kept). Confirm with the human first."""
    return await _call(ctx, api.delete_account, account_id=account_id)


# -- catalog / orders -------------------------------------------------------------

@mcp.tool(annotations=READ)
async def get_catalog(
        ctx: Context,
        adapter: Annotated[str | None, Field(description="Only this provider's plans")] = None,
        location: Annotated[str | None, Field(description="Only this location code")] = None,
) -> dict:
    """Marketplace plans per provider: CPU, RAM, disk, monthly/hourly price,
    included traffic or traffic_note, overage and extra-IP prices, plus each
    provider's source (live API or curated list) and freshness."""
    data = await _call(ctx, api.get_catalog)
    data["plans"] = {
        k: [p for p in plans if location is None or p.get("location") == location]
        for k, plans in data["plans"].items() if adapter is None or k == adapter
    }
    return data


@mcp.tool(annotations=READ)
async def list_os_images(ctx: Context, adapter: str) -> dict:
    """Orderable OS images for a provider (live; needs an enabled account
    of that provider)."""
    return {"images": await _call(ctx, api.catalog_images, adapter=adapter)}


@mcp.tool(annotations=IDEMPOTENT)
async def sync_catalog(ctx: Context) -> dict:
    """Admin. Refresh the plan catalog from every provider now."""
    return await _call(ctx, api.catalog_sync)


@mcp.tool(annotations=READ)
async def list_orders(ctx: Context) -> dict:
    """Orders with status (draft, confirmed, executing, provisioned, failed,
    cancelled), plan snapshot and estimated monthly price."""
    return {"orders": await _call(ctx, api.list_orders)}


@mcp.tool(annotations=READ)
async def get_order(ctx: Context, order_id: int) -> dict:
    """One order with its status history."""
    return await _call(ctx, api.get_order, order_id=order_id)


@mcp.tool(annotations=WRITE)
async def create_order(
        ctx: Context, adapter: str,
        plan_name: Annotated[str, Field(description="Plan name from get_catalog")],
        location: str,
        options: Annotated[dict[str, Any], Field(description=(
            "Optional: hostname (str), extra_ips (int), image (list_os_images), "
            "account_id (int) - an account with purchases enabled makes the "
            "order real"))] = {},
) -> dict:
    """Admin. Draft an order for a catalog plan. Nothing is ordered until
    confirm_order then execute_order; the draft's mode says whether
    executing it buys at the provider ("real") or rehearses ("prototype")."""
    return await _call(ctx, api.create_order, body=api.OrderBody(
        adapter=adapter, plan_name=plan_name, location=location, options=options))


@mcp.tool(annotations=WRITE)
async def confirm_order(ctx: Context, order_id: int) -> dict:
    """Admin. Confirm a draft order (draft -> confirmed)."""
    return await _call(ctx, api.confirm_order, order_id=order_id)


@mcp.tool(annotations=WRITE)
async def cancel_order(ctx: Context, order_id: int) -> dict:
    """Admin. Cancel a draft or confirmed order."""
    return await _call(ctx, api.cancel_order, order_id=order_id)


@mcp.tool(annotations=WRITE)
async def execute_order(ctx: Context, order_id: int) -> dict:
    """Admin. Submit a confirmed order for provisioning (-> executing; poll
    get_order). A mode=real order SPENDS MONEY at the provider; it may end
    awaiting_payment with a pay_url. Confirm plan and price with the human
    first."""
    return await _call(ctx, api.execute_order, order_id=order_id)


# -- IPs ----------------------------------------------------------------------------

@mcp.tool(annotations=READ)
async def describe_ip(ctx: Context, address: str) -> dict:
    """Which server/account owns an IP, whether it is the primary (never
    changeable) or a swappable extra, what one change costs in the
    provider's own unit, and the account's 24 h acquisition count vs cap."""
    return await _call(ctx, api.ip_describe, address=address)


@mcp.tool(annotations=WRITE)
async def change_ip(ctx: Context, address: str, release_first: bool = False) -> dict:
    """Admin. SPENDS MONEY: swap a swappable IP for a fresh one on the same
    server (acquire, then release the old one; release_first for servers at
    their IP cap). Returns the new address, or status awaiting_payment with
    a pay_url (OVH). Get the human's go-ahead with the cost first."""
    return await _call(ctx, api.ip_change, address=address,
                       body=api.IpChangeBody(release_first=release_first))


@mcp.tool(annotations=WRITE)
async def add_ip(ctx: Context, account_id: AccountId, provider_id: ProviderId) -> dict:
    """Admin. SPENDS MONEY: buy one more public IPv4 for a server."""
    return await _call(ctx, api.ip_add, account_id=account_id, provider_id=provider_id)


@mcp.tool(annotations=DESTRUCTIVE)
async def release_ip(ctx: Context, account_id: AccountId, provider_id: ProviderId,
                     address: str) -> dict:
    """Admin. IRREVERSIBLE: give a swappable IP back to the provider. The
    primary IP is refused."""
    return await _call(ctx, api.ip_release, account_id=account_id,
                       provider_id=provider_id, address=address)


# -- users / tokens / settings / audit ---------------------------------------------

@mcp.tool(annotations=READ)
async def list_users(ctx: Context) -> dict:
    """Admin. Panel users with role and disabled flag."""
    return {"users": await _call(ctx, api.list_users)}


@mcp.tool(annotations=WRITE)
async def create_user(ctx: Context, username: Annotated[str, Field(min_length=1)],
                      password: Annotated[str, Field(min_length=auth.MIN_PASSWORD_LEN)],
                      role: Literal["admin", "viewer"] = "viewer") -> dict:
    """Admin. Create a panel user."""
    return await _call(ctx, api.create_user, body=api.UserBody(
        username=username, password=password, role=role))


@mcp.tool(annotations=IDEMPOTENT)
async def update_user(ctx: Context, user_id: int,
                      role: Literal["admin", "viewer"] | None = None,
                      disabled: bool | None = None,
                      password: Annotated[str | None, Field(
                          min_length=auth.MIN_PASSWORD_LEN)] = None) -> dict:
    """Admin. Change a user's role, disable/enable them, or reset their
    password (disabling or a reset signs them out everywhere)."""
    return await _call(ctx, api.patch_user, user_id=user_id, body=api.UserPatch(
        role=role, disabled=disabled, password=password))


@mcp.tool(annotations=READ)
async def list_api_tokens(ctx: Context) -> dict:
    """Your own personal API tokens (names and dates only)."""
    return {"tokens": await _call(ctx, api.list_tokens)}


@mcp.tool(annotations=WRITE)
async def create_api_token(ctx: Context,
                           name: Annotated[str, Field(min_length=1, max_length=100)],
                           scope: Annotated[str, Field(description=(
                               "full (your role) or ip_change (only the IP-change API "
                               "- for a remote rotation script)"))] = "full") -> dict:
    """Mint a personal API token for yourself. The plaintext is in this
    result exactly once - it cannot be retrieved later."""
    return await _call(ctx, api.create_token, body=api.TokenBody(name=name, scope=scope))


@mcp.tool(annotations=DESTRUCTIVE)
async def revoke_api_token(ctx: Context, token_id: int) -> dict:
    """Revoke one of your own API tokens. Revoking the token this session
    uses ends the session."""
    return await _call(ctx, api.revoke_token, token_id=token_id)


@mcp.tool(annotations=READ)
async def get_settings(ctx: Context) -> dict:
    """Admin. Editable settings: sync_* (intervals), update_*,
    ip_change_daily_cap[:<account_id>] and billing_* (billing_interval_min,
    billing_low_balance:<account_id>)."""
    return await _call(ctx, api.get_settings)


@mcp.tool(annotations=IDEMPOTENT)
async def update_settings(ctx: Context, settings: Annotated[dict[str, str], Field(description=(
        "Keys must start with sync_, update_, ip_ or billing_, e.g. "
        "{\"sync_default_interval\": \"10\"} (minutes)"))]) -> dict:
    """Admin. Write settings; one invalid key rejects the whole write."""
    return await _call(ctx, api.put_settings, body=settings)


@mcp.tool(annotations=READ)
async def list_audit_log(ctx: Context) -> dict:
    """Admin. The 200 most recent mutations: who, what, when, before/after."""
    return {"entries": await _call(ctx, api.list_audit)}


# -- prompts -----------------------------------------------------------------------

@mcp.prompt()
def fleet_triage() -> str:
    """What in the fleet needs attention first."""
    return ("Call get_overview, then list what needs attention, most urgent "
            "first: servers that are off or unknown, allowances above 80% with "
            "their projected overage, accounts with sync errors, failed recent "
            "actions. Identify each server by name, account_id and provider_id. "
            "Read only - do not call any tool that changes state.")


@mcp.prompt()
def cost_review() -> str:
    """Where this month's money goes."""
    return ("Call get_billing_summary and get_overview. Report spend per "
            "currency (never summed across currencies), the largest adapters, "
            "projected overage and which servers drive it, and any totals that "
            "are lower bounds because prices are not exposed.")


@mcp.prompt()
def compare_plans(requirements: str) -> str:
    """Compare marketplace plans across providers for a workload."""
    return (f"Workload requirements: {requirements}\n\n"
            "Call get_catalog and pick the plans across providers that meet "
            "the requirements. Compare monthly price per currency, included "
            "traffic (or the provider's traffic note), overage and extra-IP "
            "prices, and each provider's data source and freshness. Say which "
            "values are not published instead of guessing them.")


# -- HTTP transport ----------------------------------------------------------------

class _BearerGate:
    """In front of the MCP transport: no valid personal API token, no MCP
    at all (not even tools/list). Session cookies are deliberately ignored."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            header = Headers(scope=scope).get("authorization")
            if await anyio.to_thread.run_sync(_full_scope_user, header) is None:
                await JSONResponse({"detail": "Not signed in"}, 401,
                                   headers={"WWW-Authenticate": "Bearer"})(
                    scope, receive, send)
                return
        await self.app(scope, receive, send)


def http_app():
    """(ASGI endpoint for /mcp, its session manager). A fresh manager per
    call: the SDK's can't be restarted, and every create_app() runs one."""
    mcp.streamable_http_app(
        # stateless JSON request/response: works across workers, no
        # long-lived SSE stream for a reverse proxy to buffer
        stateless_http=True, json_response=True,
        # the SDK's Host/Origin allowlist (localhost only by default) guards
        # unauthenticated local servers against DNS rebinding; a rebound
        # browser holds no Bearer token, and the allowlist would reject
        # every deployed panel's hostname
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    manager = mcp.session_manager
    return _BearerGate(manager.handle_request), manager
