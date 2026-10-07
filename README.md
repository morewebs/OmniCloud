# OmniCloud

[![CI](https://github.com/morewebs/OmniCloud/actions/workflows/ci.yml/badge.svg)](../../actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An open-source, multi-provider cloud control panel. OmniCloud aggregates
server fleets, traffic allowances, billing exposure, and a plan marketplace
across cloud providers behind one canonical data model. The dashboard is
ordered by what needs attention first — is anything down, what will this
month cost — with technical depth one click down and a JSON API for
scripts and tools.

**Server management:** Hetzner Cloud, LeaseWeb, OVHcloud (VPS + Public Cloud),
Gcore (basic VMs).
**Plan marketplace (7 providers):** Hetzner, LeaseWeb, OVHcloud, Gcore,
Tube-hosting (live pricing from public APIs) + Netlen, LightNode (curated
public list prices, stamped with source and last-verified date).

## Quick start

```bash
# backend (Python 3.12+, uv)
uv sync
export OMNICLOUD_MASTER_KEY=$(uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
uv run uvicorn server.main:app --port 8000

# frontend dev server (separate terminal)
cd web && npm install && npm run dev   # http://localhost:5173, proxies /api

# or: build the SPA once and serve everything from one process
cd web && npm run build                # outputs to server/static/
uv run uvicorn server.main:app --port 8000   # http://localhost:8000
```

**Just want to look around?** A demo with mock data (200 servers, orders,
billing, down servers) runs the real UI with zero setup:

```bash
uv run python demo_server.py          # http://localhost:8080, no login
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
- **Traffic semantics** are adapter-owned: Hetzner, LeaseWeb and OVH
  Public Cloud count outgoing traffic only; OVH VPS is unmetered. The panel
  displays each provider's counting rule in plain language and never
  guesses a billing model.
- **Audit:** every mutation records who, when, what, and the before/after
  state.
- **API access:** every route the UI uses is a JSON API. Personal tokens
  (Settings → API tokens) authenticate as `Authorization: Bearer <token>` -
  same roles as your login, stored hashed, shown once, revocable. Interactive
  docs at `/api/docs` (they require auth like everything else). The same
  tokens drive the [MCP server](docs/mcp.md) at `/mcp` for AI agents.

## MCP server

AI agents can drive the panel over the Model Context Protocol at `/mcp`
(Streamable HTTP). This works with Claude Code or any MCP client that can
send an `Authorization` header. Create a personal API token in Settings →
API tokens, then:

```bash
claude mcp add --transport http omnicloud https://panel.example.net/mcp \
  --header "Authorization: Bearer <token>"
```

- **Tools:** 44 of them cover the whole REST surface: fleet, allowances,
  billing, catalog, orders, server actions, firewalls, accounts, users,
  settings, audit and updates. There are also three prompts.
- **Same rules as the REST API:** each tool calls the REST route it wraps,
  so validation, audit records and error text are identical.
- **Roles:** a token carries its owner's role. With a viewer token, the
  admin tools answer `403: Admin role required`.
- **Server actions:** they wait for the provider's confirmation. If that
  takes longer than `wait_seconds`, they return `in_progress` with an
  `action_id` to poll. They never report success before the provider confirms.

A token can do everything its role can, so give agents a viewer token unless
they need to operate. The full tool reference, auth details and client
examples are in [docs/mcp.md](docs/mcp.md).

## Configuration

| Env var | Purpose | Default |
|---|---|---|
| `OMNICLOUD_MASTER_KEY` | Fernet key encrypting provider tokens | required for credentials |
| `OMNICLOUD_DB` | SQLite database path | `./omnicloud.db` |
| `OMNICLOUD_SESSION_TTL_DAYS` | Session lifetime | 30 |
| `OMNICLOUD_SYNC_INTERVAL_MIN` | Default sync interval (minutes) | 5 |
| `OMNICLOUD_COOKIE_SECURE` | Secure session cookie flag (`0` for local HTTP dev only) | `1` |
| `OMNICLOUD_UPDATE_REPO` | Repo checked for panel updates (`owner/name`) | `morewebs/OmniCloud` |
| `OMNICLOUD_TRUST_PROXY` | Rate-limit by `X-Forwarded-For` when behind a reverse proxy | `0` |

**Production:** see [DEPLOY.md](DEPLOY.md) — TLS, backups (DB + master key
together), reverse proxy (SSE unbuffered, ≥960s read timeout for long server
actions, `/mcp` proxied like `/api`), Docker.

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
cd web && npx tsc -b
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
