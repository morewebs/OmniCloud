# Security Policy

## Reporting a vulnerability

Email the maintainer directly (see the GitHub profile on the repo) or open a
**private** security advisory: repo page → Security → Advisories. Please do
not open a public issue for anything exploitable.

You will get an acknowledgment within 72 hours. We target disclosure after a
fix ships; credited reports welcome, coordinated disclosure expected.

## Scope

OmniCloud is a self-hosted panel that stores **provider credentials** - API
tokens, and for providers without tokens (Gcore Hosting, Tube-hosting) the
account login itself, which cannot be scoped down. It can also **spend money**
at providers. Treat an install as critical infrastructure:

- The database (`omnicloud.db`) holds Fernet-encrypted tokens. It is only as
  safe as the master key (`OMNICLOUD_MASTER_KEY`) and the disk it sits on.
  See [DEPLOY.md](DEPLOY.md) — back up key and DB together.
- Tokens are never returned by any API (only the last 4 characters), never
  logged, never rendered by the UI. Any finding that breaks this is
  high-severity.
- Auth: scrypt-hashed passwords, opaque session cookies (HttpOnly,
  SameSite=Strict, Secure by default), per-username lockout, CSRF via the
  required `X-Requested-With` header on every non-GET `/api` request.
- **Personal API tokens** (0.3.0): sha256-hashed at rest, plaintext shown
  exactly once at creation, bearer-authenticated, carry the creator's role
  (a viewer's token cannot mutate), revocable at any time. Bearer requests
  skip the CSRF header check — no cookie is attached, so there is nothing
  to forge. A leaked token is high-severity: revoke it in Settings → API
  tokens.
- **Token scopes**: `full` (the creator's role) or `ip_change`, which reaches
  only `GET /api/ips/{ip}`, `POST /api/ips/{ip}/change`, `GET
  /api/actions/{id}` and `GET /api/auth/me` - the token to hand a remote
  rotation script. Any route reachable by an `ip_change` token beyond those
  is a high-severity finding.
- **Purchases**: nothing is bought unless an admin enabled purchases on that
  account (audited). IP acquisitions are capped per account per 24 h, one IP
  operation per server at a time, and purchase requests to providers are
  sent exactly once (never retried - a retry could buy twice). Panel
  passwords are POSTed to the provider, never placed in a URL.
- **Operator-entered panel URLs** (Gcore Hosting / BILLmanager): https on
  the default port only, no userinfo, and the host is resolved before every
  login - loopback, private, link-local (cloud metadata), CGNAT, multicast
  and reserved addresses are refused, so the panel can't be pointed at its
  own network. Provider-supplied links (pay/invoice URLs) are kept and
  rendered only if they are `https://`.
- **MCP endpoint** (`/mcp`): accepts personal API tokens only, as
  `Authorization: Bearer`. Session cookies are refused there, so it has no
  CSRF surface.
  - **Checked first:** the token is verified before any MCP message is
    parsed (no token means `401`), and again on every tool call.
  - **Same roles as REST:** each tool runs the REST route it wraps,
    including that route's role check, so a viewer token cannot reach a
    mutating path through MCP.
  - **Same secret rules:** provider tokens are never returned by a tool,
    the same as `/api`.
  - **No Host/Origin allowlist:** the SDK's DNS-rebinding check is off,
    because it would reject every deployed hostname. A rebound browser
    holds no Bearer token.
  - **Full-scope tokens only:** `ip_change`-scoped tokens are refused at
    `/mcp` (MCP authenticates outside the REST scope check).
  - **Reportable:** any MCP path that reaches a mutation without an admin
    token, or that returns a secret, is high-severity.

## Hardening checklist for operators

1. Serve behind TLS only (`OMNICLOUD_COOKIE_SECURE=1`, the default).
2. Keep `OMNICLOUD_MASTER_KEY` out of the repo and out of env dumps.
3. Give scripts `ip_change` tokens, not full ones, and keep purchases off
   on accounts that don't need them.
4. Restrict network exposure — the panel needs no inbound port beyond your
   reverse proxy.
5. Update promptly (Settings → Panel update, or see DEPLOY.md).
6. Give AI agents (MCP clients) **viewer** tokens unless they must
   operate. An admin token lets the agent rebuild and delete servers. Use
   one token per agent so each can be revoked on its own.
