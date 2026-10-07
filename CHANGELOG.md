# Changelog

## Unreleased

- **IP change API** (`POST /api/ips/{ip}/change`, docs/ip-change.md): an
  operator's own server/script swaps one of a server's extra IPs for a
  fresh one and gets the new address back; testing it (e.g. for DPI
  filtering) stays in the caller's script. The primary IP is never
  touched. New-first by default (acquire, then release), `release_first`
  for servers at their extra-IP cap; an acquisition that fails part-way
  is cleaned up and the response says exactly what state the server is
  in. Guarded by a per-account **Purchases** switch (off by default), a
  per-account daily acquisition cap, one IP operation per server at a
  time, and **scoped API tokens** (`ip_change` reaches only this API).
  Providers: Gcore Cloud (reserved IPs, billed per minute), Hetzner
  (floating IPs), Gcore Hosting (BILLmanager additional IPs), OVH VPS
  (unpaid cart order -> 202 + pay link). Server dialog gains an IP section
  (change / release / add, cost per provider unit).
- **Gcore Hosting adapter** (hosting.gcore.com, BILLmanager 6) from the
  panel's own calls: servers, IPs, root password, delete, balance,
  payments, renewals. Signs in with the panel username + password.
- **Netlen, Tube-hosting, LightNode fleet adapters**: servers, IPs, power
  actions (each confirmed on the provider's view); Netlen can add an IP
  (no release in its API, so no change); billing where the API has it.
- **Billing per account**: balance, invoices (due date, open amount,
  provider status), unpaid orders with pay links, renewals - hourly on
  each account's own timer; overview alerts for overdue/unpaid invoices,
  unpaid orders, renewals without auto-renew and a low-balance threshold.
  Providers whose API has no billing say "not exposed", never zero.
- **Real orders**: an order bound to an account with purchases on is
  executed at the provider (Hetzner, LeaseWeb, OVH as an unpaid order),
  extra IPs bought on the new server; typed "Buy" confirm; orders parked
  in `awaiting_payment` settle when the provider delivers.
- Credential forms come from each adapter (`credential_fields`): username
  + password panels no longer pack secrets into one token string.
- Schema v4 with an in-place migration from v3 (orders history kept).
- Fixes: "This month's bill" added each currency's overage once per
  provider; a failed order could stay stuck in `executing` (audit write
  deadlocked its transaction); Gcore made the order dialog 500 (no
  `list_images`); Hetzner advertised every new capability automatically;
  LightNode's seed offered extra IPs on one plan only.
- **MCP server** at `/mcp` (Streamable HTTP, stateless JSON): AI agents
  drive the panel through 50 tools covering the whole REST surface —
  fleet, allowances, billing, catalog, orders, server actions, IPs,
  firewalls, accounts, users, settings, audit, updates — plus three prompts
  (fleet triage, cost review, plan comparison). Auth is personal API tokens
  only (`Authorization: Bearer`; session cookies are refused, so no CSRF
  surface), re-checked on every call. Each tool calls the REST route it
  wraps, so roles, validation, audit records and error text match the API
  exactly; viewer tokens get read-only. Server actions return
  `in_progress` with an `action_id` to poll (`get_action`) when the
  provider hasn't confirmed within `wait_seconds` — never reported as
  success early. Destructive tools carry `destructiveHint`. Reference:
  [docs/mcp.md](docs/mcp.md). The Vite dev server proxies `/mcp` as well.
- `GET /api/actions/{id}`: single action row (IP changes add their
  structured `result`). `ip_change`-scoped tokens are refused at `/mcp`.
- **Fix:** a provider account created or re-enabled at runtime now starts
  syncing immediately. Its sync loop was never spawned until the next
  restart, while Refresh / force-sync still answered ok. Force-sync's wake
  signal now also reaches the loop thread-safely.
- App startup/shutdown moved from `on_event` to a lifespan handler.
- **Security:** the Gcore Hosting panel URL (the panel password is POSTed
  to it) must be `https://` on the default port with no userinfo; its host
  is resolved before every login and refused if it points at loopback,
  private, link-local (cloud metadata), CGNAT, multicast or reserved
  addresses - nothing, not even the login, is sent there. Provider-supplied
  pay/invoice links are kept and rendered only if `https://`, so a hostile
  panel can't plant a `javascript:` link.

## 0.4.2 (2026-10-05)

- **Gcore fleet adapter** (basic VMs across all projects/regions under one
  account token): power actions, rename, relabel, delete — each confirmed
  by the provider's own view (instance status poll / task poll /
  post-delete 404), never by an accepted response. Auth uses Gcore's
  `Authorization: APIKey` scheme (not Bearer). provider_id is compound
  (`project:region:uuid`) and self-routes like OVH cloud ids. Per-instance
  discounted monthly price from the pricing endpoint (missing → "-",
  never zero). Traffic is unmetered by product design — allowance renders
  as a window note, not fake bytes. Labels come from PATCHable tags
  (RFC 7386 merge patch). No rebuild — Gcore's spec has no VM rebuild
  endpoint. The tokenless plan catalog stays as-is. All facts cited in
  docs/provider-truth.md.

## 0.4.0 (2026-10-04)

- **OVHcloud fleet adapter** (VPS + Public Cloud instances under one
  account): power actions, rename, cloud-instance delete — each confirmed
  by the provider's own view (task poll / status poll), never by an
  accepted response. Both auth schemes via one packed credential string:
  `AK:AS:CK` (classic SHA1-signed API keys) or `client_id:client_secret`
  (OAuth2 service account). Per-instance monthly price for monthly-billed
  cloud instances (regional pricing join); hourly-billed instances
  honestly show no monthly price. Cloud instances expose real per-instance
  outgoing traffic for the current month; VPS is unmetered — traffic,
  price and labels read *not exposed* there. VPS deletion stays a
  deliberate two-step in the OVH manager (the panel refuses, pointing
  there). All facts cited in docs/provider-truth.md.

## 0.3.0 (2026-10-04)

One interface tuned for owner, engineer, and developer at once:
attention on money and health first, technical depth one click down,
API access for scripts and tools.

- **Money & health first**: Overview leads with a down-server banner and
  a "This month's bill" tile (base + projected overage, per currency,
  never summed across currencies) plus a spend-over-time chart. New
  servers never backfill spend history.
- **Fleet depth one click down**: traffic sparklines in the fleet table,
  per-day traffic numbers with CSV export, server labels as chips with an
  inline relabel editor, firewall rules viewable where the adapter exposes
  them ("not exposed" otherwise), raw server JSON with download, and a
  full actions log toggle on Overview.
- **Developer unlocked**: personal API tokens (hashed at rest, shown once,
  revocable, Bearer auth alongside the session cookie; bearer requests
  skip CSRF — no cookie to forge), interactive API docs at `/api/docs`,
  and an "extend it" footer on the Adapters view.
- **Data exports**: billing CSV and traffic CSV.
- **DB schema v3** (additive — `api_tokens` table, no migration needed).

## 0.2.0 (2026-10-03)

Plan marketplace, prototype ordering, production hardening.

- **7-provider plan catalog**: Hetzner, LeaseWeb, OVHcloud, Gcore,
  Tube-hosting (live public APIs) + Netlen, LightNode (curated public
  prices with source + last-verified stamps). Prices, declared traffic,
  extra-IP capability — written the way each provider actually bills.
- **Prototype ordering**: draft → confirmed → executing → provisioned, with
  plan snapshots, per-currency estimates, audit trail, and a `mode` column
  as the go-live flip. No server is ever created or billed.
- **Production hardening**: thread-safe SSE, atomic order transitions,
  scrypt-2^17 auth with lockout + constant-time login, session revocation,
  per-currency billing, partial-catalog refusal (a failed region keeps the
  previous catalog), retention sweep, DEPLOY.md, Dockerfile.
- **Built-in updates**: daily GitHub release check, one-click apply
  (pull → deps → SPA build → restart via supervisor).
- **UI overhaul**: Material theme with dark mode, Overview dashboard,
  command palette, onboarding checklist, skeletons/retry/empty states
  everywhere, confirmed destructive actions with typed names.

## 0.1.0

First release: multi-provider fleet panel — verified Hetzner + LeaseWeb
adapters, capability-gated actions, traffic allowances with per-provider
counting rules, SSE live updates, multi-user auth.
