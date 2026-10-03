# Changelog

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
