# Changelog

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
