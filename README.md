# OmniCloud

An open-source, multi-provider cloud control panel. OmniCloud aggregates
server fleets, traffic allowances, and billing exposure across cloud
providers behind one canonical data model.

**First-class adapters:** Hetzner Cloud, LeaseWeb.
**Roadmap:** OVH, Gcore, Netlen, Lightnode, Tube-hosting.

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
  (`Server`, `Allowance`, `Money`, `Facet`). The UI renders only canonical
  entities - adding a provider adds zero new UI patterns.
- **Capabilities** (power, rename, rebuild, firewall, delete...) are declared
  per adapter. A capability an adapter lacks is absent from the UI, never a
  disabled button.
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
