# OmniCloud

[![CI](https://github.com/morewebs/OmniCloud/actions/workflows/ci.yml/badge.svg)](../../actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An open-source, multi-provider cloud control panel. OmniCloud aggregates
server fleets, traffic allowances, billing exposure, and a plan marketplace
across cloud providers behind one canonical data model.

**Server management:** Hetzner Cloud, LeaseWeb.
**Plan marketplace (7 providers):** Hetzner, LeaseWeb, OVHcloud, Gcore,
Tube-hosting (live pricing from public APIs) + Netlen, LightNode (curated
public list prices, stamped with source and last-verified date).

## Quick start

```bash
# backend (Python 3.12+, uv)
uv sync
export OMNICLOUD_MASTER_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
uv run uvicorn server.main:app --port 8000

# frontend dev server (separate terminal)
cd web && npm install && npm run dev   # http://localhost:5173, proxies /api

# or: build the SPA once and serve everything from one process
cd web && npm run build                # outputs to server/static/
uv run uvicorn server.main:app --port 8000   # http://localhost:8000
```

First visit shows a one-time setup screen that creates the first admin
account. Add provider accounts under Credentials (API tokens are stored
Fernet-encrypted with `OMNICLOUD_MASTER_KEY`; only the last 4 characters are
ever displayed).

## How it works

- **Adapters** map each provider API onto canonical entities
  (`Server`, `Allowance`, `Money`, `Facet`, `Plan`). The UI renders only
  canonical entities - adding a provider adds zero new UI patterns.
- **Capabilities** (power, rename, rebuild, firewall, delete...) are declared
  per adapter. A capability an adapter lacks is absent from the UI, never a
  disabled button.
- **Plan marketplace:** every provider's plans with prices, included
  traffic, and extra-IP cost - live from public APIs where one exists
  (OVH order catalog, Gcore public API, Tube-hosting's pricing asset),
  otherwise curated with a visible source badge. Compare plans across
  providers and place orders (prototype pipeline: simulated execution,
  clearly labeled; the `mode` column is the go-live flip).
- **Data honesty:** a value the provider API does not expose reads
  *not exposed*; a momentarily missing value reads *-*. Neither is ever zero.
  A `202 Accepted` from a provider is never success - actions complete only
  when the provider's own view confirms the change.
- **Sync** runs per account at a configurable interval; each source keeps its
  own timestamp and one broken account never blocks the others. Updates
  stream to the UI over SSE.
- **Traffic semantics** are adapter-owned: Hetzner counts outgoing only,
  LeaseWeb counts both directions. The panel displays each provider's
  counting rule in plain language and never guesses a billing model.
- **Audit:** every mutation records who, when, what, and the before/after
  state.

## Configuration

| Env var | Purpose | Default |
|---|---|---|
| `OMNICLOUD_MASTER_KEY` | Fernet key encrypting provider tokens | required for credentials |
| `OMNICLOUD_DB` | SQLite database path | `./omnicloud.db` |
| `OMNICLOUD_SESSION_TTL_DAYS` | Session lifetime | 30 |
| `OMNICLOUD_SYNC_INTERVAL_MIN` | Default sync interval (minutes) | 5 |
| `OMNICLOUD_COOKIE_SECURE` | Secure session cookie flag (`0` for local HTTP dev only) | `1` |

**Production:** see [DEPLOY.md](DEPLOY.md) — TLS, backups (DB + master key
together), reverse proxy (SSE unbuffered, ≥960s read timeout for long server
actions), Docker.

## Built-in updates

Settings → Panel update checks GitHub (daily, automatic) and can apply a
release in one click: pull → dependencies → SPA build → restart. The restart
is your supervisor's job (Docker `--restart`, systemd, or a loop script) —
the panel exits with code **78** after applying; see DEPLOY.md for each
setup's restart line.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) — provider facts need citations
(docs/provider-truth.md), tests never touch real infrastructure, and the
data-honesty rules in design.md are enforced by the suite. MIT licensed.

## Tests

```bash
uv run pytest
cd web && npx tsc --noEmit
```

Adapter tests run against recorded fixture JSON (no network). All fixture
data uses placeholder identifiers and documentation-range IPs.

## Open-source policy

This repository must contain **no real company names, credentials, IP
addresses, or customer data** - in code, fixtures, screenshots, or docs.
Example data uses placeholder identifiers only (`srv-fsn1-01`,
`account-a7f3`, `203.0.113.x`). See `design.md` for the full contracts.

## Reverse proxy note

SSE (`/api/stream`) needs unbuffered pass-through - for nginx:
`proxy_buffering off;` or `X-Accel-Buffering: no` (the app sets the header).
If a proxy kills SSE, the UI degrades gracefully to periodic refetch.
