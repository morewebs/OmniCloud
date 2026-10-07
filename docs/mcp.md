# MCP server

OmniCloud serves the [Model Context Protocol](https://modelcontextprotocol.io)
at `/mcp` on the panel's own port. AI agents such as Claude Code, or any MCP
client that speaks Streamable HTTP and can send an `Authorization` header,
use it to read the fleet and operate the panel as the token's owner.

## Connect

1. In the panel, open **Settings → API tokens** and create a token. It is
   shown once.
2. Point your client at `https://<your-panel>/mcp` and send that token as
   `Authorization: Bearer <token>`.

**Claude Code:**

```bash
claude mcp add --transport http omnicloud https://panel.example.net/mcp \
  --header "Authorization: Bearer <token>"
```

**Python (official `mcp` SDK, 2.x):**

```python
import asyncio

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


async def main() -> None:
    http = httpx2.AsyncClient(headers={"Authorization": "Bearer <token>"})
    async with Client(streamable_http_client("https://panel.example.net/mcp",
                                             http_client=http)) as client:
        result = await client.call_tool("get_overview", {})
        print(result.content[0].text)


asyncio.run(main())
```

**Local development:** use the backend port, `http://localhost:8000/mcp`.
The Vite dev server (`:5173`) proxies `/mcp` to it as well.

## Auth and roles

- **Credential:** only personal API tokens are accepted. A browser session
  cookie is not, so the endpoint has no CSRF surface.
- **No token, or an unknown, revoked or disabled one:** HTTP `401` with
  `{"detail": "Not signed in"}` and `WWW-Authenticate: Bearer`. This happens
  before any MCP message is handled, so the caller can't even list tools.
- **Per call:** the token is re-checked on every request, so revoking it or
  disabling its user takes effect immediately.
- **Role:** a token carries its owner's role.
  - **Viewer:** gets every read tool, plus `sync_account` and the
    tools for their own API tokens.
  - **Admin tools called with a viewer token:** return
    `403: Admin role required`, the same as the REST API.
- **Attribution:** every mutation is written to the audit log under the
  token's user.

> A token is a credential for everything its role can do. An admin token lets
> the agent rebuild and delete servers and add provider credentials. Give
> agents a viewer token unless they need to operate, and revoke tokens you no
> longer use.

## How it behaves

- **Same rules as the REST API.** Each tool calls the REST route it wraps,
  so validation, side effects (sync restarts, audit records) and error
  messages are the same as `/api`.
- **Errors.** A failed call returns `isError: true` with the REST status and
  detail, e.g. `Error executing tool get_server: 404: server not in cache`.
- **Server actions are never reported as done early.**
  - `power_server`, `rename_server`, `relabel_server`, `rebuild_server` and
    `delete_server` wait up to `wait_seconds` for the provider to confirm.
    The default is 50 and the maximum 600.
  - If the provider confirms in time, the result is the action row with
    `status: "done"`.
  - If it hasn't, the tool returns `status: "in_progress"` and an
    `action_id`. Poll `get_action` until it reads `done` or `failed`.
  - The action keeps running if the client disconnects.
  - A provider failure returns `502: <provider detail>`.
- **Stateless JSON.** Every request is independent: no MCP session and no
  server-sent notifications. Watch long work by polling `get_action` or
  `get_order`.
- **Data rules.** The server sends the panel's data-honesty rules to the
  agent when it connects:
  - `null` and `not_exposed` mean unknown, never zero.
  - Money is never summed across currencies.
  - Servers are identified by `(account_id, provider_id)`.
  - Destructive tools need the human's explicit confirmation first.
- **Tool hints.** Tools carry MCP annotations: `readOnlyHint` on reads, and
  `destructiveHint` on `rebuild_server`, `delete_server`, `delete_account`,
  `apply_update` and `revoke_api_token`.

## Tools

The Role column shows who can call each tool. Arguments marked `?` are
optional.

### Panel

| Tool | Role | Arguments | What it does |
|---|---|---|---|
| `whoami` | viewer | | The token's user and role |
| `get_panel_status` | viewer | | Version, database health, cached update status |
| `check_for_update` | viewer | | Ask GitHub for a newer release now |
| `apply_update` | admin | | Pull, rebuild and restart the panel (exits code 78) |

### Fleet

| Tool | Role | Arguments | What it does |
|---|---|---|---|
| `get_overview` | viewer | | Status counts, spend per currency, projected overage, alerts, 30-day traffic and spend, recent actions and orders |
| `list_fleet` | viewer | `adapter?`, `account_id?`, `status?`, `query?` | Cached servers per account, sync state, in-flight actions. `query` matches name, IPv4 or provider_id |
| `get_server` | viewer | `account_id`, `provider_id` | One server: facets, allowance, labels, firewalls, daily traffic history |
| `list_allowances` | viewer | | Traffic used / included, counting rule, window, projected overage |
| `get_billing_summary` | viewer | | Base spend and overage per (adapter, currency) |
| `sync_account` | viewer | `account_id` | Wake the account's sync loop now (409 if the account is disabled) |

### Server actions

| Tool | Role | Arguments | What it does |
|---|---|---|---|
| `power_server` | admin | `account_id`, `provider_id`, `kind`, `wait_seconds?` | `power_on`, `power_off`, `reboot` or `shutdown` |
| `rename_server` | admin | `account_id`, `provider_id`, `name`, `wait_seconds?` | Rename at the provider |
| `relabel_server` | admin | `account_id`, `provider_id`, `labels`, `wait_seconds?` | Replace the label set |
| `rebuild_server` | admin | `account_id`, `provider_id`, `image`, `wait_seconds?` | **Irreversible:** reinstall from an image |
| `delete_server` | admin | `account_id`, `provider_id`, `wait_seconds?` | **Irreversible:** delete at the provider |
| `list_actions` | viewer | | The 100 most recent actions |
| `get_action` | viewer | `action_id` | One action: `in_progress`, `done` or `failed` |

Only the kinds an adapter supports work. `list_adapters` shows each adapter's
capabilities, and an unsupported kind returns `409`.

### Firewalls

| Tool | Role | Arguments | What it does |
|---|---|---|---|
| `list_firewalls` | viewer | `account_id` | Live from the provider: firewalls, rules where exposed, servers applied to |
| `attach_firewall` | admin | `account_id`, `firewall_id`, `provider_id` | Apply an existing firewall to a server |
| `detach_firewall` | admin | `account_id`, `firewall_id`, `provider_id` | Remove it from a server |
| `apply_firewall` | admin | `account_id`, `provider_id`, `rules`, `attach?` | Create a firewall from rules and attach it. At least one inbound rule is required |

### Provider accounts

| Tool | Role | Arguments | What it does |
|---|---|---|---|
| `list_accounts` | viewer | | Accounts with credential scope, last 4 characters, sync status |
| `list_adapters` | viewer | | Fleet adapters with capabilities, plus catalog-only providers |
| `create_account` | admin | `adapter`, `name`, `token`, `scope?` | Connect an account. It starts syncing immediately |
| `update_account` | admin | `account_id`, `name?`, `enabled?` | Rename, or enable/disable sync |
| `delete_account` | admin | `account_id` | **Irreversible:** remove the account, its credential and cached servers (servers at the provider are untouched) |

`create_account` sends the provider credential through the agent's context.
The panel stores it encrypted and never returns it from any tool. Even so,
entering credentials in the web UI keeps them out of the conversation.

### Catalog and orders

| Tool | Role | Arguments | What it does |
|---|---|---|---|
| `get_catalog` | viewer | `adapter?`, `location?` | Plans with specs, prices, traffic, extra-IP offers, plus each provider's source and freshness |
| `list_os_images` | viewer | `adapter` | Orderable images (needs an enabled account of that provider) |
| `sync_catalog` | admin | | Refresh every provider's plans now |
| `list_orders` | viewer | | Orders with status and estimate |
| `get_order` | viewer | `order_id` | One order with its status history |
| `create_order` | admin | `adapter`, `plan_name`, `location`, `options?` | Draft an order. `options`: `hostname`, `extra_ips`, `image` |
| `confirm_order` | admin | `order_id` | draft → confirmed |
| `cancel_order` | admin | `order_id` | Cancel a draft or confirmed order |
| `execute_order` | admin | `order_id` | confirmed → executing |

### Users, tokens, settings, audit

| Tool | Role | Arguments | What it does |
|---|---|---|---|
| `list_users` | admin | | Users with role and disabled flag |
| `create_user` | admin | `username`, `password`, `role?` | New user (viewer by default) |
| `update_user` | admin | `user_id`, `role?`, `disabled?`, `password?` | Change role, disable, reset password (signs the user out) |
| `list_api_tokens` | viewer | | Your own tokens (no plaintext) |
| `create_api_token` | viewer | `name` | New token for yourself. The plaintext appears in this result once |
| `revoke_api_token` | viewer | `token_id` | Revoke one of your own tokens |
| `get_settings` | admin | | `sync_*` and `update_*` settings |
| `update_settings` | admin | `settings` | Write settings (keys must start with `sync_` or `update_`) |
| `list_audit_log` | admin | | The 200 most recent mutations, with before/after state |

### Prompts

| Prompt | Arguments | Purpose |
|---|---|---|
| `fleet_triage` | | Read-only pass over what needs attention first |
| `cost_review` | | Spend per currency, overage drivers, lower-bound totals |
| `compare_plans` | `requirements` | Compare marketplace plans for a workload |

### Not available over MCP

These stay in the browser:
- first-run setup, login, logout and password change (they manage browser
  sessions);
- the live event stream (`/api/stream`).

`/api/catalog/providers` and `/api/update/status` are covered by
`get_catalog` and `get_panel_status`.

## Deployment

- **Reverse proxy:** proxy `/mcp` like `/api`, with a read timeout above the
  largest `wait_seconds` you expect. See [DEPLOY.md](../DEPLOY.md#reverse-proxy).
- **TLS:** serve `/mcp` over TLS only, because the token travels in a header.
- **Host checks:** the SDK's DNS-rebinding Host/Origin allowlist is turned
  off, since it would reject every non-localhost hostname. Without a token,
  `/mcp` refuses every request.
